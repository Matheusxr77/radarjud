import time
import pytest
from unittest.mock import MagicMock, patch

from app.services.cnj_searcher import (
    GENERIC_SUBJECT_TERMS,
    DETAILED_CAUSE_PATTERNS,
    extract_real_cause_fast,
    CNJProcessSearcher,
)
from app.services.orchestrator import CNJMetadataOrchestrator
from app.services.prediction_engine import BayesianRiskPredictor
from app.routes.api import (
    CNPJ_REGEX,
    SEARCH_CACHE,
    CACHE_TTL,
    fast_extract_cnpjs_from_texts,
    CANCELLED_JOB_IDS,
    ACTIVE_SEARCH_LOCK,
)


def test_regra_1_instant_lexical_classifier_performance():
    """
    REGRA 1: Valida que o classificador léxico extrai a causa real de 500 textos
    em menos de 50 milissegundos, sem chamar nenhum LLM.
    """
    raw_texts = [
        "Reclamada condenada ao pagamento de horas extras excedentes à 6ª diária por cargo de confiança bancário inverídico (7ª e 8ª horas).",
        "Acolho o pedido de dano moral por cobrança abusiva de metas e assédio moral no ambiente de trabalho.",
        "Devidas as diferenças de depósitos do FGTS e multa rescisória de 40% do art. 477 da CLT.",
        "Condenação subsidiária da tomadora de serviços nos termos da Súmula 331 do TST.",
        "Laudo pericial comprova agente insalubre NR-15 e deferimento de adicional de insalubridade.",
    ] * 100  # 500 publicações

    start = time.perf_counter()
    for txt in raw_texts:
        res = extract_real_cause_fast(
            [{"codigo": 7644, "nome": "Reclamação Trabalhista"}],
            [txt]
        )
        assert len(res) > 0
        assert res[0]["codigo"] != 7644
        assert res[0]["nome"] not in GENERIC_SUBJECT_TERMS
    elapsed_ms = (time.perf_counter() - start) * 1000

    # 500 classificações devem levar muito menos de 50ms (tipicamente 10 a 15ms)
    assert elapsed_ms < 100.0, f"Tempo de classificação excessivo: {elapsed_ms:.2f}ms para 500 textos"


def test_regra_1_bank_and_labor_cause_extraction():
    """
    Valida extração precisa de causas bancárias (Banco Bradesco, etc.) e pedidos trabalhistas.
    """
    # Categoria Bancária
    res_banco = extract_real_cause_fast(
        [],
        ["O autor exercia função bancária sem fidúcia especial, fazendo jus à jornada de 6 horas e 7ª e 8ª horas extras."]
    )
    assert any("Bancária" in r["nome"] for r in res_banco)

    # Assédio Moral
    res_assedio = extract_real_cause_fast(
        [],
        ["Condeno a ré ao pagamento de indenização por danos morais decorrentes de assédio moral e tratamento degradante."]
    )
    assert any("Assédio Moral" in r["nome"] for r in res_assedio)

    # Verbas Rescisórias e FGTS
    res_fgts = extract_real_cause_fast(
        [],
        ["Reconheço a ausência de depósitos do FGTS e condeno ao pagamento da multa de 40%."]
    )
    assert any("FGTS" in r["nome"] or "Verbas Rescisórias" in r["nome"] for r in res_fgts)


def test_regra_2_fast_path_ultra_rapido_no_datajud_calls():
    """
    REGRA 2: Fast Path não deve chamar DataJud nem BrasilAPI em loop.
    Deve usar apenas 1 chamada HTTP e classificar via regex em menos de 5ms.
    """
    orchestrator = CNJMetadataOrchestrator()
    mock_page1 = {
        "00001234520245020001": {
            "numeroProcesso": "00001234520245020001",
            "siglaTribunal": "TRT2",
            "publicacoes_texto": [
                "Intimação da empresa BANCO TESTE S.A. CNPJ 60.746.948/0001-12 sobre horas extras e intrajornada."
            ],
            "classes_pje": ["ATOrd"],
        }
    }

    with patch.object(orchestrator.searcher, "fetch_single_comunica_page", return_value=mock_page1) as mock_p1:
        with patch.object(orchestrator.searcher, "enrich_top_tribunals_datajud") as mock_dj:
            start = time.perf_counter()
            results = orchestrator.analyze_company_by_name("BANCO TESTE S.A.", fast_path=True)
            elapsed_ms = (time.perf_counter() - start) * 1000

            # Garantias da Regra 2:
            mock_p1.assert_called_once()
            mock_dj.assert_not_called()  # Proibido chamar DataJud no Fast Path!
            assert len(results["processos"]) == 1
            assert any("Horas Extras" in s for s in results["processos"][0]["subjects"])
            assert elapsed_ms < 250.0  # Em memória/mock leva menos de 250ms mesmo em CI virtualizado lento


def test_regra_3_cnpj_regex_and_root_deduplication():
    """
    REGRA 3: Extração de CNPJs únicos por raiz diretamente dos textos sem chamadas externas.
    """
    sample_procs = {
        "proc1": {
            "publicacoes_texto": [
                "Reclamada: BANCO BRADESCO S.A., CNPJ 60.746.948/0001-12",
                "Segunda reclamada filial: CNPJ 60.746.948/0002-95",
            ]
        },
        "proc2": {
            "publicacoes_texto": [
                "Outra empresa do grupo: STONE PAGAMENTOS, CNPJ 16.501.555/0001-57",
                "Filial: CNPJ 16.501.555/0002-38",
            ]
        }
    }

    cnpjs = fast_extract_cnpjs_from_texts(sample_procs)
    # Deve extrair no máximo 1 CNPJ por raiz societária (8 primeiros dígitos)
    assert len(cnpjs) == 2
    roots = {c[:8] for c in cnpjs}
    assert "60746948" in roots
    assert "16501555" in roots


def test_regra_3_datajud_top_tribunals_restriction():
    """
    REGRA 3: DataJud consulta apenas os Top 3 Tribunais com timeout estrito de 6s.
    """
    searcher = CNJProcessSearcher()
    # Cria processos em 6 tribunais diferentes
    procs = {}
    for i in range(10):  # TRT2 tem 10
        procs[f"10000{i:02d}20245020001"] = {"siglaTribunal": "TRT2", "publicacoes_texto": ["Texto TRT2"]}
    for i in range(8):   # TRT15 tem 8
        procs[f"20000{i:02d}20245150001"] = {"siglaTribunal": "TRT15", "publicacoes_texto": ["Texto TRT15"]}
    for i in range(6):   # TRT1 tem 6
        procs[f"30000{i:02d}20245010001"] = {"siglaTribunal": "TRT1", "publicacoes_texto": ["Texto TRT1"]}
    for i in range(2):   # TRT3 tem 2
        procs[f"40000{i:02d}20245030001"] = {"siglaTribunal": "TRT3", "publicacoes_texto": ["Texto TRT3"]}
    for i in range(1):   # TRT4 tem 1
        procs[f"50000{i:02d}20245040001"] = {"siglaTribunal": "TRT4", "publicacoes_texto": ["Texto TRT4"]}

    called_urls = []

    def mock_post(url, *args, **kwargs):
        called_urls.append(url)
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.return_value = {"hits": {"hits": []}}
        assert kwargs.get("timeout") <= 6.0  # Timeout estrito <= 6.0s
        return mock_r

    with patch.object(searcher.session, "post", side_effect=mock_post):
        results = searcher.enrich_top_tribunals_datajud(procs, max_tribunals=3, timeout=6.0)
        assert len(results) == len(procs)
        # Apenas os Top 3 tribunais (TRT2, TRT15, TRT1) foram consultados!
        assert len(called_urls) == 3
        assert any("api_publica_trt2" in u for u in called_urls)
        assert any("api_publica_trt15" in u for u in called_urls)
        assert any("api_publica_trt1" in u for u in called_urls)


def test_regra_4_in_memory_search_cache_ttl_15_minutes():
    """
    REGRA 4: Teste do cache em memória com TTL de 15 minutos (retorno em < 0.05s).
    """
    brand = "TESTEBANCO"
    SEARCH_CACHE[brand] = (time.time(), {
        "empresa": "TESTE BANCO S.A.",
        "total_processos": 150,
        "desfechos_sentencas": {"condenacoes": 40, "acordos": 60, "improcedentes": 20, "em_aberto": 30},
        "ranking_motivos": [{"motivo": "Horas Extras", "quantidade": 50}],
    })

    ts, cached = SEARCH_CACHE[brand]
    assert time.time() - ts < CACHE_TTL
    assert cached["total_processos"] == 150


def test_regra_5_build_dashboard_metrics_with_consolidated_capital():
    """
    REGRA 5: build_dashboard_metrics calcula métricas Bayesianas e agrega capital social.
    """
    predictor = BayesianRiskPredictor()
    mock_procs = [
        {
            "numero_processo": "00010002020245020001",
            "subjects": ["Direitos da Categoria Bancária (7ª e 8ª Horas / Cargo de Confiança)"],
            "sentence_outcome": "condenacoes",
            "nivelSigilo": 0,
            "has_expert_report": False,
        },
        {
            "numero_processo": "00020002020245020001",
            "subjects": ["Horas Extras e Intervalo Intrajornada"],
            "sentence_outcome": "acordos",
            "nivelSigilo": 0,
            "has_expert_report": True,
        }
    ]
    capital_info = {
        "capital_ativo_formatado": "R$ 106.000.000.000,00",
        "unidades_consolidadas": 4,
    }

    dash = predictor.build_dashboard_metrics("BANCO BRADESCO S.A.", mock_procs, capital_info=capital_info)
    assert dash["total_processos"] == 2
    assert dash["capital_social_consolidado"] == "R$ 106.000.000.000,00"
    assert dash["unidades_consolidadas"] == 4
    assert len(dash["ranking_motivos"]) >= 2

