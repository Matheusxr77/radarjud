import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from app.extensions import db
from app.models.company_search import CompanySearch, get_utc_now
from app.utils.sanitizer import sanitize_company_name, is_cnpj, fetch_cnpj_brasilapi
from app.utils.crypto import generate_movements_hash
from app.utils.rate_limiter import TokenBucketRateLimiter
from app.services.orchestrator import CNJMetadataOrchestrator


def test_sanitize_company_name_corporate_suffixes():
    """Valida remoção de acentos, pontuações e sufixos jurídicos empresariais."""
    assert sanitize_company_name("BANCO BRADESCO S/A") == "BANCO BRADESCO"
    assert sanitize_company_name("Magazine Luiza S.A.") == "MAGAZINE LUIZA"
    assert sanitize_company_name("Alpha Tecnologia e Inovação Ltda.") == "ALPHA TECNOLOGIA E INOVACAO"
    assert sanitize_company_name("Silva & Santos Comércio - EIRELI") == "SILVA SANTOS"
    assert sanitize_company_name("Hospital Santa Cruz ME") == "HOSPITAL SANTA CRUZ"
    assert sanitize_company_name("Posto de Combustíveis São Jorge EPP") == "POSTO DE COMBUSTIVEIS SAO JORGE"


def test_is_cnpj_detection():
    """Valida detecção de formatos de CNPJ com ou sem pontuação."""
    assert is_cnpj("12.345.678/0001-90") is True
    assert is_cnpj("12345678000190") is True
    assert is_cnpj("123") is False
    assert is_cnpj("EMPRESA LTDA") is False


def test_fetch_cnpj_brasilapi_mocked():
    """Valida triangulação de CNPJ em Razão Social via BrasilAPI."""
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "cnpj": "12345678000190",
            "razao_social": "MOCK LOGISTICA E TRANSPORTES LTDA",
            "nome_fantasia": "MOCK TRANSPORTES"
        }
        mock_get.return_value = mock_resp

        razao = fetch_cnpj_brasilapi("12.345.678/0001-90")
        assert razao == "MOCK LOGISTICA E TRANSPORTES LTDA"
        # Sanitização da razão retornada pela BrasilAPI
        sanitized = sanitize_company_name(razao)
        assert sanitized == "MOCK LOGISTICA E TRANSPORTES"


def test_generate_movements_hash_deterministic():
    """Valida que o hash SHA-256 é determinístico e invariante à ordem dos processos."""
    procs_1 = [
        {
            "numero_processo": "0000100",
            "dataHoraUltimaAtualizacao": "2024-01-01T10:00:00",
            "movimentos": [{"codigo": 219, "dataHora": "2023-12-01"}],
            "sentence_outcome": "condenacoes"
        },
        {
            "numero_processo": "0000200",
            "dataHoraUltimaAtualizacao": "2024-01-02T10:00:00",
            "movimentos": [{"codigo": 466, "dataHora": "2023-12-02"}],
            "sentence_outcome": "acordos"
        }
    ]

    # Mesma lista invertida
    procs_2 = [procs_1[1], procs_1[0]]

    hash_1 = generate_movements_hash(procs_1)
    hash_2 = generate_movements_hash(procs_2)
    assert hash_1 == hash_2
    assert len(hash_1) == 64  # SHA-256

    # Se um código de movimento mudar, o hash deve mudar
    procs_modified = [
        procs_1[0],
        {
            "numero_processo": "0000200",
            "dataHoraUltimaAtualizacao": "2024-01-02T10:00:00",
            "movimentos": [{"codigo": 220, "dataHora": "2023-12-02"}],
            "sentence_outcome": "improcedentes"
        }
    ]
    hash_3 = generate_movements_hash(procs_modified)
    assert hash_1 != hash_3


def test_company_search_is_recent_method(app):
    """Valida o método is_recent(hours=24) do modelo CompanySearch."""
    with app.app_context():
        search_recent = CompanySearch(
            company_name="Empresa Recente",
            normalized_name="EMPRESA RECENTE",
            last_checked_at=get_utc_now() - timedelta(hours=2),
            scan_status="COMPLETED"
        )
        assert search_recent.is_recent(hours=24) is True

        search_old = CompanySearch(
            company_name="Empresa Antiga",
            normalized_name="EMPRESA ANTIGA",
            last_checked_at=get_utc_now() - timedelta(hours=25),
            scan_status="COMPLETED"
        )
        assert search_old.is_recent(hours=24) is False


def test_short_circuit_l1_database(client, app):
    """
    Curto-Circuito Nível 1:
    Se a empresa já foi pesquisada há menos de 24 horas,
    retorna direto do banco de dados sem consultar CNJ ou IA.
    """
    with app.app_context():
        search = CompanySearch(
            company_name="Logística Express Ltda",
            normalized_name="LOGISTICA EXPRESS",
            total_processes=12,
            last_checked_at=get_utc_now() - timedelta(hours=1),
            scan_status="COMPLETED",
            outcomes_summary={"condenacoes": 4, "acordos": 3, "improcedentes": 3, "em_aberto": 2},
            ranking_motivos=[{"motivo": "Horas Extras", "quantidade": 8, "porcentagem": 66.7}],
            predictions_summary=[{
                "motivo": "Horas Extras",
                "processos_abertos": 2,
                "probabilidade_condenacao_pct": 65.0,
                "probabilidade_vencer_pct": 35.0,
                "diagnostico_chance_vitoria": "Risco elevado de condenação",
                "confianca_previsao_pct": 85.0
            }],
            overall_risk_score=52.0,
        )
        db.session.add(search)
        db.session.commit()

    # Faz a requisição na rota
    with patch("app.routes.api.orchestrator.analyze_company_by_name") as mock_analyze:
        resp = client.post("/api/v1/search-company", json={"company_name": "Logística Express Ltda"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("cached") is True
        assert data.get("cache_level") == "L1_DATABASE"
        assert data.get("total_processes") == 12
        # Garante que NENHUMA chamada externa ao CNJ foi feita
        mock_analyze.assert_not_called()


def test_infer_subject_from_pje_text_when_outros():
    """
    Valida inferência de assunto a partir do texto quando
    os metadados oficiais retornam apenas 'Outros Assuntos'.
    """
    orchestrator = CNJMetadataOrchestrator()

    # Caso com assunto genérico "Outros Motivos Trabalhistas" e texto com "horas extras"
    generic_subjects = ["Outros Motivos Trabalhistas"]
    pje_texts = ["Publicação de sentença: Diante do exposto, condeno ao pagamento de horas extras e reflexos."]

    subjects, observation = orchestrator.infer_subject_and_observation(generic_subjects, pje_texts)
    assert "Horas Extras (Inferido via PJe)" in subjects
    assert observation is not None
    assert "Horas Extras" in observation


def test_token_bucket_rate_limiter():
    """Valida o funcionamento do algoritmo TokenBucketRateLimiter."""
    limiter = TokenBucketRateLimiter(rate_per_second=10.0, capacity=2.0)
    assert limiter.acquire(tokens=1.0, block=False) is True
    assert limiter.acquire(tokens=1.0, block=False) is True
    # O balde esvaziou os 2 tokens iniciais
    assert limiter.acquire(tokens=1.0, block=False) is False


def test_status_endpoint(client, app):
    """Valida o endpoint de polling de status GET /api/v1/search_status/<id>."""
    with app.app_context():
        search = CompanySearch(
            company_name="Empresa Polling Teste",
            normalized_name="EMPRESA POLLING TESTE",
            total_processes=5,
            scan_status="PARTIAL_FAST_PATH",
            last_checked_at=get_utc_now()
        )
        db.session.add(search)
        db.session.commit()
        sid = search.id

    resp = client.get(f"/api/v1/search_status/{sid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["search_id"] == sid
    assert data["scan_status"] == "PARTIAL_FAST_PATH"
    assert data["is_completed"] is False


def test_comunica_pje_fetches_full_processes_without_20_item_cap():
    """
    Garante que a busca não trunca arbitrariamente em 20 publicações.
    Uma empresa com 19 processos (com múltiplas publicações) deve capturar todos os 19 processos.
    """
    orchestrator = CNJMetadataOrchestrator()

    # Simula 35 publicações que cobrem 19 processos distintos
    mock_items = []
    for i in range(1, 20):
        mock_items.append({
            "numero_processo": f"0000{i:03d}0020235020001",
            "siglaTribunal": "TRT2",
            "texto": f"Publicação 1 do processo {i}",
            "dataDisponibilizacao": "2023-01-01"
        })
    # Mais publicações duplicadas dos mesmos processos (comum no PJe)
    for i in range(1, 17):
        mock_items.append({
            "numero_processo": f"0000{i:03d}0020235020001",
            "siglaTribunal": "TRT2",
            "texto": f"Publicação 2 do processo {i}",
            "dataDisponibilizacao": "2023-02-01"
        })

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"items": mock_items}

    with patch.object(orchestrator.session, "get", return_value=mock_resp) as mock_get:
        processes_map = orchestrator._fetch_comunica_pje("Empresa Real 19 Processos", max_pages=2, page_size=100)

        # Garante que usou page_size=100 na chamada para capturar o lote real
        mock_get.assert_called()
        call_params = mock_get.call_args[1]["params"]
        assert call_params["itensPorPagina"] == 100

        # E capturou todos os 19 processos reais distintos, sem truncar em 11
        assert len(processes_map) == 19


def test_ai_analyzer_and_prediction_zero_processes_calibration():
    """
    Garante que para empresas sem processos localizados, o modelo não inventa taxas
    (ex: 28% de vitória ou 35% de acordos) e retorna None e score 0.0.
    """
    from app.services.ai_analyzer import LegalAIAnalyzer
    from app.services.prediction_engine import BayesianRiskPredictor

    # 1. Teste no analisador de IA
    analyzer = LegalAIAnalyzer()
    summary_empty = {
        "empresa": "Empresa Limpa Sem Processos",
        "total_processos": 0,
        "desfechos_sentencas": {"condenacoes": 0, "acordos": 0, "improcedentes": 0, "em_aberto": 0},
        "ranking_motivos": [],
    }
    report = analyzer.analyze_company(summary_empty)
    assert report["taxa_historica_vitoria_empresa_pct"] is None
    assert report["taxa_historica_acordos_pct"] is None
    assert "Não foram encontrados processos judiciais" in report["resumo_executivo"]

    # 2. Teste no motor de predição
    predictor = BayesianRiskPredictor()
    metrics = predictor.generate_dashboard_metrics("Empresa Limpa Sem Processos", [])
    assert metrics["score_risco_terceirizacao"] == 0.0
    assert metrics["total_processos"] == 0
    assert len(metrics["plus_previsao_condenacao"]) == 0


def test_ai_analyzer_zero_judged_processes_calibration():
    """
    Quando uma empresa possui processos em aberto mas nenhum julgado,
    não inventa taxas de vitória empírica anteriores.
    """
    from app.services.ai_analyzer import LegalAIAnalyzer
    analyzer = LegalAIAnalyzer()
    summary_open_only = {
        "empresa": "Empresa Nova Com Apenas Abertos",
        "total_processos": 5,
        "desfechos_sentencas": {"condenacoes": 0, "acordos": 0, "improcedentes": 0, "em_aberto": 5},
        "ranking_motivos": [{"motivo": "Horas Extras", "quantidade": 5}],
    }
    report = analyzer.analyze_company(summary_open_only)
    assert report["taxa_historica_vitoria_empresa_pct"] is None
    assert report["taxa_historica_acordos_pct"] is None
    assert "nenhum desfecho anterior transitado em julgado" in report["resumo_executivo"]


def test_infer_cause_from_text_and_process_class():
    """
    Garante que processos com 'Outros Motivos' identificam causas reais a partir
    do texto da publicação (ex: 7ª/8ª horas bancárias, verbas rescisórias, SISBAJUD/execução)
    ou da classe processual PJe (ex: Recurso Ordinário).
    """
    orchestrator = CNJMetadataOrchestrator()

    # 1. Caso bancário específico (Bradesco)
    texts_bank = ["Intimação: Discussão quanto ao enquadramento bancário do reclamante, 7ª e 8ª horas e cargo de confiança."]
    subs, obs = orchestrator.infer_subject_and_observation(["Outros Motivos Trabalhistas"], texts_bank)
    assert any("Bancária" in s or "7ª/8ª" in s for s in subs)
    assert obs is not None

    # 2. Caso de execução / bloqueio
    texts_exec = ["Expedição de ordem via SISBAJUD para penhora de ativos e cumprimento de sentença."]
    subs_exec, obs_exec = orchestrator.infer_subject_and_observation([], texts_exec)
    assert any("Execução" in s or "Liquidação" in s for s in subs_exec)

    # 3. Caso em que o texto é sucinto, mas a classe processual PJe identifica o rito
    subs_class, obs_class = orchestrator.infer_subject_and_observation(
        ["Outros Assuntos"],
        ["Publicação de edital de intimação das partes."],
        pje_classes=["RECURSO ORDINÁRIO TRABALHISTA"]
    )
    assert any("Recurso Ordinário" in s for s in subs_class)
    assert "Recurso Ordinário" in obs_class


def test_overall_condemnation_probability_metric():
    """
    Valida a existência e precisão da métrica de probabilidade de condenação geral
    em empresas com histórico de sentenças julgadas.
    """
    orchestrator = CNJMetadataOrchestrator()

    # Cria lote de processos simulados com 10 condenações, 2 improcedentes e 1 acordo (total 13 julgados)
    mock_processes = []
    for i in range(10):
        mock_processes.append({
            "numero_processo": f"00000{i:02d}0020235020001",
            "sentence_outcome": "condenacoes",
            "subjects": ["Horas Extras (Inferido via PJe)"],
            "nivelSigilo": 0,
        })
    for i in range(2):
        mock_processes.append({
            "numero_processo": f"00000{i+10:02d}0020235020001",
            "sentence_outcome": "improcedentes",
            "subjects": ["Horas Extras (Inferido via PJe)"],
            "nivelSigilo": 0,
        })
    mock_processes.append({
        "numero_processo": "00000990020235020001",
        "sentence_outcome": "acordos",
        "subjects": ["Horas Extras (Inferido via PJe)"],
        "nivelSigilo": 0,
    })

    metrics = orchestrator._calculate_metrics("Empresa Teste Condenação", mock_processes)
    assert "probabilidade_condenacao_geral_pct" in metrics
    assert metrics["probabilidade_condenacao_geral_pct"] is not None
    # 10 condenações em 13 julgados = 76.9%
    assert 70.0 <= metrics["probabilidade_condenacao_geral_pct"] <= 80.0



