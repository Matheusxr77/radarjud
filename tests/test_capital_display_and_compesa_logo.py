"""
Testes unitários para:
1. Descoberta dinâmica de CNPJ via destinatários, textos de processos e busca online.
2. build_capital_social_display SEMPRE retornando o valor numérico em R$ formatado
   e a observação de compatibilidade como campo separado para badge.
3. Fallback concorrente (BrasilAPI + publica.cnpj.ws + receitaws) para leitura do Capital Social.
4. Resolução da logo de empresas com extração de sigla '- COMPESA', domínio compesa.com.br e Google Favicon HD.
"""

import pytest
from app.services.economic_service import (
    extract_smart_brand_slug,
    resolve_company_logo,
    discover_company_cnpjs,
    build_capital_social_display,
    fetch_cnpj_capital_with_fallback,
    fetch_accurate_cnpj_data,
    evaluate_economic_health_and_capital,
)


def test_compesa_slug_and_logo_resolution():
    """
    Testa a extração correta de COMPESA a partir da razão social completa
    'COMPANHIA PERNAMBUCANA DE SANEAMENTO - COMPESA' e a resolução dos links
    incluindo o Google Favicon HD (sz=128).
    """
    razao = "COMPANHIA PERNAMBUCANA DE SANEAMENTO - COMPESA"
    slug = extract_smart_brand_slug(user_query=razao, razao_social=razao)
    assert slug == "COMPESA"

    logo = resolve_company_logo(user_query=razao, razao_social=razao)
    assert logo["brand"] == "COMPESA"
    assert logo["domain"] == "compesa.com.br"
    assert "google.com/s2/favicons" in logo["logo_primary_url"]
    assert "compesa.com.br" in logo["logo_primary_url"]
    assert logo["logo_secondary_url"] == "https://unavatar.io/compesa.com.br?fallback=false"
    assert logo["logo_favicon_url"] == logo["logo_primary_url"]


def test_discover_company_cnpjs_from_online_search(monkeypatch):
    """
    Quando as publicações não trazem CNPJ no texto, discover_company_cnpjs
    encontra o CNPJ matriz via busca online dinâmica.
    """
    monkeypatch.setattr(
        "app.services.economic_service.resolve_cnpjs_by_company_name_online",
        lambda name, legals=None: ["09769035000164"]
    )
    cnpjs = discover_company_cnpjs(user_query="COMPESA", brand_term="COMPESA", all_processes={})
    assert "09769035000164" in cnpjs


def test_discover_company_cnpjs_from_process_texts():
    """
    Testa extração de CNPJ citado no teor das publicações do PJe.
    """
    mock_processes = {
        "proc1": {
            "publicacoes_texto": [
                "Intime-se a executada inscrita no CNPJ 09.769.035/0001-64 para manifestação."
            ]
        }
    }
    cnpjs = discover_company_cnpjs(user_query="Empresa X", brand_term="EMPRESA X", all_processes=mock_processes)
    assert "09769035000164" in cnpjs


def test_build_capital_social_display_never_replaces_money_value(monkeypatch):
    """
    Garante que valor_formatado SEMPRE contém a quantia em dinheiro (R$ X.XXX,XX)
    e NUNCA é substituído pela string 'Compatível com o porte'.
    """
    def mock_fetch(cnpj, timeout=4.0):
        return {
            "cnpj": cnpj,
            "porte": "DEMAIS",
            "capital_social": 1850000000.0,
            "razao_social": "EMPRESA S.A.",
        }

    monkeypatch.setattr(
        "app.services.economic_service.fetch_accurate_cnpj_data",
        mock_fetch
    )

    res = build_capital_social_display(["09769035000164"], total_processes=120)
    assert res["valor_numerico"] == 1850000000.0
    assert "R$ 1.850.000.000,00" in res["valor_formatado"]
    assert "Compatível com o porte" in res["observacao_porte"]
    assert "Alta Solidez" in res["observacao_porte"] or "Alto lastro" in res["observacao_porte"]
    assert "Compatível com o porte" not in res["valor_formatado"]


def test_build_capital_social_display_small_capital(monkeypatch):
    """
    Garante que mesmo capital social pequeno (R$ 10.000,00) seja exibido em dinheiro,
    com observação de alerta adequada se houver muitos processos.
    """
    def mock_fetch(cnpj, timeout=4.0):
        return {
            "cnpj": cnpj,
            "porte": "ME",
            "capital_social": 10000.0,
            "razao_social": "PEQUENA EMPRESA ME",
        }

    monkeypatch.setattr(
        "app.services.economic_service.fetch_accurate_cnpj_data",
        mock_fetch
    )

    res = build_capital_social_display(["12345678000199"], total_processes=50)
    assert res["valor_numerico"] == 10000.0
    assert res["valor_formatado"] == "R$ 10.000,00"
    assert "Atenção: Capital baixo" in res["observacao_porte"]


def test_fetch_cnpj_capital_with_fallback_secondary(monkeypatch):
    """
    Testa busca em publica.cnpj.ws quando a BrasilAPI falha.
    """
    import requests

    class MockRespFail:
        status_code = 500

    class MockRespSuccess:
        status_code = 200
        def json(self):
            return {
                "razao_social": "COMPANHIA PERNAMBUCANA DE SANEAMENTO",
                "capital_social": 2500000000.0,
                "porte": {"descricao": "DEMAIS"},
                "estabelecimento": {"situacao_cadastral": "ATIVA", "nome_fantasia": "COMPESA"}
            }

    def mock_get(url, *args, **kwargs):
        if "brasilapi.com.br" in url or "receitaws.com.br" in url:
            return MockRespFail()
        return MockRespSuccess()

    monkeypatch.setattr(requests, "get", mock_get)

    data = fetch_cnpj_capital_with_fallback("09769035000164")
    assert data["capital_social"] == 2500000000.0
    assert data["razao_social"] == "COMPANHIA PERNAMBUCANA DE SANEAMENTO"
    assert data["porte"] == "DEMAIS"


def test_evaluate_economic_health_and_capital_picpay(monkeypatch):
    """
    Testa o caso do PicPay:
    Descobre as matrizes do conglomerado (22896431 e 09516419),
    soma o capital bilionário (> R$ 2 bilhões), mantém situação cadastral ATIVA,
    insolvência baixíssima (<= 5.0%), risco de calote mitigado e classificar como CENARIO_A.
    """
    monkeypatch.setattr(
        "app.services.economic_service.resolve_cnpjs_by_company_name_online",
        lambda name, legals=None: ["22896431000110", "09516419000175"]
    )

    def mock_fetch(c, timeout=4.0):
        if "22896431" in c:
            return {
                "cnpj": "22896431000110",
                "raiz": "22896431",
                "razao_social": "PICPAY INSTITUICAO DE PAGAMENTO S.A.",
                "nome_fantasia": "PICPAY",
                "capital_social": 2150000000.0,
                "porte": "DEMAIS",
                "situacao": "ATIVA",
            }
        return {
            "cnpj": "09516419000175",
            "raiz": "09516419",
            "razao_social": "PICPAY BANK - BANCO MULTIPLO S.A.",
            "nome_fantasia": "PICPAY BANK",
            "capital_social": 400000000.0,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
        }

    monkeypatch.setattr("app.services.economic_service.fetch_accurate_cnpj_data", mock_fetch)

    mock_processes = {
        "proc_001": {
            "publicacoes_texto": ["Intimação genérica sem menção expressa de CNPJ."],
            "destinatarios_nomes": ["PICPAY INSTITUICAO DE PAGAMENTO S.A."],
        }
    }

    res = evaluate_economic_health_and_capital(
        user_query="Picpay",
        brand_term="PICPAY",
        all_processes=mock_processes,
        conviction_prob_pct=30.0,
    )

    assert res["situacao_cadastral"] == "ATIVA"
    assert res["capital_social_num"] >= 2000000000.0
    assert "R$" in res["capital_social_formatado"]
    assert res["probabilidade_insolvencia"] <= 5.0
    assert res["cenario_tipo"] == "CENARIO_A"
    assert "Cenário A" in res["cenario_badge"]
    assert "Alta Solidez" in res["badge_compatibilidade_porte"] or "Compatível" in res["badge_compatibilidade_porte"]


def test_evaluate_economic_health_and_capital_small_business_me_epp(monkeypatch):
    """
    Testa empresa de pequeno porte (ME/EPP) com capital de R$ 30.000,00:
    - Exibe o valor real em dinheiro R$ 30.000,00.
    - Exibe o badge contendo 'porte da empresa'.
    - Situação ATIVA.
    - Mantém insolvência baixa e Cenário A se não tiver excesso de processos.
    """
    mock_processes = {
        "proc_1": {"publicacoes_texto": ["Intimação de teste"], "cnpjs_encontrados": ["99888777000166"]}
    }

    monkeypatch.setattr("app.services.economic_service.fetch_accurate_cnpj_data", lambda c, timeout=4.0: {
        "cnpj": "99888777000166",
        "raiz": "99888777",
        "razao_social": "PADARIA CENTRAL EPP",
        "nome_fantasia": "PADARIA CENTRAL",
        "porte": "EPP",
        "situacao": "ATIVA",
        "capital_social": 30000.0,
    })

    res = evaluate_economic_health_and_capital(
        user_query="99888777000166",
        brand_term="PADARIA CENTRAL",
        all_processes=mock_processes,
        conviction_prob_pct=20.0,
    )

    assert res["capital_social_num"] == 30000.0
    assert res["capital_social_formatado"] == "R$ 30.000,00"
    assert res["situacao_cadastral"] == "ATIVA"
    assert res["cenario_tipo"] == "CENARIO_A"
    assert "porte da empresa" in res["badge_compatibilidade_porte"].lower() or "compatível com o porte" in res["badge_compatibilidade_porte"].lower()
    assert res["probabilidade_insolvencia"] <= 12.0
