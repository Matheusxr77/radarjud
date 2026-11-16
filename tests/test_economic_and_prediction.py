import pytest
from app.services.economic_service import EconomicStandingService, resolve_company_domain, extract_initials
from app.services.prediction_engine import BayesianRiskPredictor


def test_economic_service_logo_and_initials():
    service = EconomicStandingService()
    info = service.get_company_logo_info("BANCO BRADESCO S.A.")
    assert info["domain"] == "bradesco.com.br"
    assert info["fallback_initials"] == "BB"
    assert "bradesco.com.br" in info["logo_url"]
    assert "bradesco.com.br" in info["google_favicon_url"]


def test_economic_service_scenario_a():
    """
    Cenário A: Empresa com alta idoneidade econômica, capital social elevado,
    CNPJ ativo e ausência de falência/execução fiscal.
    Risco de calote subsidiário ao tomador é baixo (Cenário A).
    """
    service = EconomicStandingService()
    # Mocking company processes sem falência
    processes = [
        {"subjects": ["Horas Extras"], "sentence_outcome": "condenacoes"},
        {"subjects": ["FGTS"], "sentence_outcome": "acordos"},
    ]
    result = service.evaluate_economic_standing(
        company_name="EMPRESA SOLIDA ENGENHARIA S.A.",
        cnpj="33000167000101",
        company_processes=processes,
        p_condenacao_trabalhista=65.0,
    )
    assert result["cenario"] == "CENARIO_A"
    assert "Idoneidade" in result["cenario_titulo"]
    assert result["cenario_class"] == "seguro"
    assert result["probabilidade_insolvencia_pct"] < 35.0


def test_economic_service_scenario_b_insolvency_alert():
    """
    Cenário B: Empresa com alerta de insolvência (pedido de falência ou recuperação judicial)
    e alto risco de condenação trabalhista. Risco crítico de calote subsidiário (Súmula 331).
    """
    service = EconomicStandingService()
    processes_with_bankruptcy = [
        {"subjects": ["Pedido de Falência"], "observation": "Distribuição de pedido de falência", "sentence_outcome": "em_aberto"},
        {"subjects": ["Execução Fiscal"], "observation": "Execução fiscal da união", "sentence_outcome": "em_aberto"},
        {"subjects": ["Horas Extras"], "sentence_outcome": "condenacoes"},
    ]
    result = service.evaluate_economic_standing(
        company_name="PRESTADORA FRAGIL LTDA",
        cnpj="00000000000191",
        company_processes=processes_with_bankruptcy,
        p_condenacao_trabalhista=80.0,
    )
    assert result["cenario"] == "CENARIO_B"
    assert "Risco Crítico de Calote Subsidiário" in result["cenario_titulo"]
    assert result["cenario_class"] == "critico"
    assert result["insolvencia_judicial"]["pedidos_falencia"] >= 1
    assert "Súmula 331" in result["parecer_sumula_331"]


def test_zero_condemnation_rule_blocks_prediction():
    """
    REGRA CRÍTICA:
    Empresa que tem processos abertos e improcedências, mas ZERO condenações prévias,
    NÃO deve receber previsão de condenação (previsao_bloqueada = True e lista vazia).
    """
    predictor = BayesianRiskPredictor()
    processes = [
        {"numero_processo": "0001", "primary_subject": "Horas Extras", "sentence_outcome": "improcedentes"},
        {"numero_processo": "0002", "primary_subject": "Horas Extras", "sentence_outcome": "acordos"},
        {"numero_processo": "0003", "primary_subject": "Horas Extras", "sentence_outcome": "em_aberto"},
        {"numero_processo": "0004", "primary_subject": "Dano Moral", "sentence_outcome": "em_aberto"},
    ]
    metrics = predictor.generate_dashboard_metrics("EMPRESA SEM CONDENACOES LTDA", processes)
    
    assert metrics["desfechos_sentencas"]["condenacoes"] == 0
    assert metrics["previsao_bloqueada"] is True
    assert metrics["plus_previsao_condenacao"] == []
    assert metrics["probabilidade_condenacao_geral_pct"] == 0.0
    assert metrics["taxa_historica_condenacao_pct"] == 0.0
    assert "desativada por ausência de passivo" in metrics["motivo_bloqueio_previsao"]


def test_ranking_motivos_condenacao_generation():
    """
    Valida a geração do ranking específico de motivos de condenação prévia.
    """
    predictor = BayesianRiskPredictor()
    processes = [
        {"numero_processo": "0001", "primary_subject": "Horas Extras", "sentence_outcome": "condenacoes"},
        {"numero_processo": "0002", "primary_subject": "Horas Extras", "sentence_outcome": "condenacoes"},
        {"numero_processo": "0003", "primary_subject": "FGTS", "sentence_outcome": "condenacoes"},
        {"numero_processo": "0004", "primary_subject": "Dano Moral", "sentence_outcome": "improcedentes"},
    ]
    metrics = predictor.generate_dashboard_metrics("EMPRESA TESTE CONDENACOES", processes)
    
    assert metrics["previsao_bloqueada"] is False
    assert len(metrics["ranking_motivos_condenacao"]) == 2
    # Horas Extras deve estar em primeiro lugar (2 condenações vs 1 do FGTS)
    top_cond = metrics["ranking_motivos_condenacao"][0]
    assert top_cond["motivo"] == "Horas Extras"
    assert top_cond["quantidade_condenacoes"] == 2.0
    assert top_cond["porcentagem_sobre_condenacoes"] == 66.7


def test_company_search_to_dict_includes_economic_standing_and_logo(app):
    """
    Garante que to_dict() sempre retorna idoneidade_economica e logo preenchidos,
    evitando que o card desapareça da tela após polling ou consultas no histórico.
    """
    from app.models.company_search import CompanySearch, get_utc_now
    from app.extensions import db

    with app.app_context():
        search = CompanySearch(
            company_name="BRADESCO PARTICIPACOES S.A.",
            normalized_name="BRADESCO PARTICIPACOES",
            total_processes=10,
            scan_status="COMPLETED",
            last_checked_at=get_utc_now(),
        )
        db.session.add(search)
        db.session.commit()

        d = search.to_dict()
        assert "idoneidade_economica" in d
        assert d["idoneidade_economica"] is not None
        assert "logo" in d
        assert d["logo"] is not None
        assert d["idoneidade_economica"]["cenario"] in ["CENARIO_A", "CENARIO_B"]
        assert "ranking_motivos_condenacao" in d


def test_search_input_validation_names_cnpj_and_trade_names():
    """
    Garante que a validação de entrada de busca aceita nomes comuns (ex: Picpay),
    nomes em minúsculas (ex: banco bradesco), razões sociais completas e CNPJ,
    rejeitando apenas sequências numéricas curtas e caracteres proibidos.
    """
    from app.utils.sanitizer import is_valid_company_search_input

    # Casos válidos
    assert is_valid_company_search_input("Picpay") is True
    assert is_valid_company_search_input("picpay") is True
    assert is_valid_company_search_input("banco bradesco") is True
    assert is_valid_company_search_input("Banco Bradesco S.A.") is True
    assert is_valid_company_search_input("Nubank") is True
    assert is_valid_company_search_input("12.345.678/0001-90") is True
    assert is_valid_company_search_input("12345678000190") is True

    # Casos inválidos
    assert is_valid_company_search_input("123") is False
    assert is_valid_company_search_input("   ") is False
    assert is_valid_company_search_input("EMPRESA@!!!") is False
    assert is_valid_company_search_input("!") is False
    assert is_valid_company_search_input("") is False


def test_deep_cause_inference_and_ranking_extraction():
    """
    Valida a extração semântica profunda de causas trabalhistas específicas
    a partir de publicações PJe com HTML, siglas de classe e movimentações,
    e valida a geração do Ranking de Motivos de Condenação sem colapso genérico.
    """
    from app.services.orchestrator import CNJMetadataOrchestrator

    orchestrator = CNJMetadataOrchestrator()

    # 1. Teste de extração a partir de publicação PJe com HTML
    html_pje_texts = [
        "<p>PODER JUDICIÁRIO</p><p>Diante do exposto, acolho em parte os pedidos de <b>horas extras e reflexos em DSR</b>.</p>"
    ]
    subs_horas, obs_horas = orchestrator.infer_subject_and_observation(
        ["DIREITO DO TRABALHO"], html_pje_texts
    )
    assert "Falta de Pagamento de Horas Extras e Reflexos" in subs_horas
    assert "Horas Extras" in obs_horas

    # 2. Teste de direitos bancários a partir do texto
    text_banco = [
        "Intimação: discussão sobre a 7ª e 8ª horas da categoria bancária e gratificação de função do art. 224 da CLT."
    ]
    subs_banco, obs_banco = orchestrator.infer_subject_and_observation(
        ["Outros Motivos Trabalhistas"], text_banco
    )
    assert any("Bancária" in s for s in subs_banco)

    # 3. Teste a partir da classe PJe (ATSum / ATOrd)
    subs_atsum, obs_atsum = orchestrator.infer_subject_and_observation(
        ["Outros"],
        ["Publicação de notificação genérica."],
        pje_classes=["ATSum - Ação Trabalhista - Rito Sumaríssimo"]
    )
    assert any("Sumaríssimo" in s for s in subs_atsum)

    # 4. Geração do ranking com múltiplos motivos identificados
    predictor = BayesianRiskPredictor()
    simulated_processes = [
        {"primary_subject": subs_horas[0], "sentence_outcome": "condenacoes", "primary_subject_code": 864},
        {"primary_subject": subs_horas[0], "sentence_outcome": "condenacoes", "primary_subject_code": 864},
        {"primary_subject": subs_banco[0], "sentence_outcome": "condenacoes", "primary_subject_code": 864},
        {"primary_subject": subs_atsum[0], "sentence_outcome": "improcedentes", "primary_subject_code": None},
    ]
    metrics = predictor.generate_dashboard_metrics("BANCO TESTE S.A.", simulated_processes)

    # Não deve ter 1 único motivo: deve ter os motivos específicos identificados
    assert len(metrics["ranking_motivos"]) >= 3
    # Ranking de condenação deve ter 2 motivos (Horas Extras com 2, Bancários com 1)
    assert len(metrics["ranking_motivos_condenacao"]) == 2
    assert metrics["ranking_motivos_condenacao"][0]["motivo"] == "Falta de Pagamento de Horas Extras e Reflexos"
    assert metrics["ranking_motivos_condenacao"][0]["quantidade_condenacoes"] == 2.0




def test_extract_brand_search_term_wide_branch_discovery():
    """
    Valida a extração do Nome Raiz / Marca para que a busca no Comunica PJe
    capture todas as filiais e empresas do grupo sem afunilamento societário.
    """
    from app.utils.sanitizer import extract_brand_search_term

    assert extract_brand_search_term("PicPay") == "PICPAY"
    assert extract_brand_search_term("picpay") == "PICPAY"
    assert extract_brand_search_term("Stone") == "STONE"
    assert extract_brand_search_term("TOTVS") == "TOTVS"
    assert extract_brand_search_term("PICPAY INSTITUICAO DE PAGAMENTO S.A.") == "PICPAY"
    assert extract_brand_search_term("PICPAY SERVICOS S.A.") == "PICPAY"
    assert extract_brand_search_term("STONE PAGAMENTOS S.A.") == "STONE"
    assert extract_brand_search_term("BANCO BRADESCO S.A.") == "BRADESCO"
    assert extract_brand_search_term("MAGAZINE LUIZA S/A") in ["MAGAZINE LUIZA", "MAGALU"]


def test_aggregate_group_capital_and_format_brl():
    """
    Valida a consolidação de capital social de todas as filiais e formatação em R$.
    """
    from app.services.economic_service import format_brl, aggregate_group_capital

    assert format_brl(0.0) == "R$ 0,00"
    assert format_brl(843500000.0) == "R$ 843.500.000,00"
    assert format_brl(12500.50) == "R$ 12.500,50"

    def mock_brasilapi(cnpj):
        if cnpj == "22896431000110":
            return {"capital_social": 843500000.0}
        if cnpj == "22896431000200":
            return {"capital_social": 50000000.0}
        return {"capital_social": 0.0}

    res = aggregate_group_capital(
        ["22896431000110", "22896431000200"],
        brasil_api_fetcher=mock_brasilapi
    )
    assert res["total_capital_social"] == 893500000.0
    assert res["capital_social_formatado"] == "R$ 893.500.000,00"
    assert res["filiais_count"] == 2


def test_resolve_company_logo_smart_cascade_never_generic_globe():
    """
    Valida que resolve_company_logo utiliza a primeira palavra da marca e cascata inteligente
    (Google S2 Favicons HD -> Unavatar -> Iniciais) sem nunca usar o globo cinza 16x16.
    """
    from app.services.economic_service import resolve_company_logo

    picpay_logo = resolve_company_logo("PICPAY INSTITUICAO DE PAGAMENTO S.A.")
    assert picpay_logo["domain"] == "picpay.com"
    assert "google.com/s2/favicons" in picpay_logo["logo_primary_url"]
    assert "unavatar.io/picpay.com" in picpay_logo["logo_secondary_url"]
    assert "google.com/s2/favicons" in picpay_logo["logo_favicon_url"]
    assert picpay_logo["initials"] in ["PI", "PIC", "PIP"]

    bradesco_logo = resolve_company_logo("BANCO BRADESCO S.A.")
    assert bradesco_logo["domain"] == "bradesco.com.br"
    assert bradesco_logo["initials"] == "BB"
