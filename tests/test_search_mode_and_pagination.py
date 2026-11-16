import re
import pytest
from unittest.mock import MagicMock, patch

from app.utils.sanitizer import (
    classify_search_mode,
    resolve_search_strategy,
    CORPORATE_SUFFIX_REGEX,
    sanitize_company_name,
)
from app.services.cnj_searcher import CNJProcessSearcher


def test_classify_search_mode():
    # Caso A: CNPJ puro ou formatado
    assert classify_search_mode("22896431000110") == "STRICT_CNPJ"
    assert classify_search_mode("22.896.431/0001-10") == "STRICT_CNPJ"

    # Caso B: Razão social estrita com sufixos jurídicos
    assert classify_search_mode("PICPAY INSTITUICAO DE PAGAMENTO S.A.") == "STRICT_RAZAO"
    assert classify_search_mode("BANCO BRADESCO S/A") == "STRICT_RAZAO"
    assert classify_search_mode("EMPRESA XYZ LTDA") == "STRICT_RAZAO"
    assert classify_search_mode("STONE PAGAMENTOS S.A.") == "STRICT_RAZAO"
    assert classify_search_mode("COMPANHIA BRASILEIRA DE DISTRIBUICAO COMERCIO") == "STRICT_RAZAO"

    # Caso C: Nome Fantasia / Marca comum (busca ampla de todas as filiais)
    assert classify_search_mode("Picpay") == "BROAD_BRAND"
    assert classify_search_mode("Nubank") == "BROAD_BRAND"
    assert classify_search_mode("Stone") == "BROAD_BRAND"
    assert classify_search_mode("Totvs") == "BROAD_BRAND"
    assert classify_search_mode("Carrefour") == "BROAD_BRAND"


def test_resolve_search_strategy_broad_brand():
    # BROAD_BRAND: NUNCA substituir pela Razão Social longa antes de consultar o Comunica PJe!
    dummy_brasil_api = MagicMock(return_value="PICPAY INSTITUICAO DE PAGAMENTO S.A.")
    
    strat = resolve_search_strategy("Picpay", brasil_api_fetcher=dummy_brasil_api)
    
    assert strat["mode"] == "BROAD_BRAND"
    assert strat["comunica_query_term"] == "PICPAY"
    assert "PICPAY" in strat["display_title"]
    assert "GRUPO CONSOLIDADO" in strat["display_title"]
    assert strat["is_broad_brand"] is True
    assert strat["target_cnpj"] is None
    # A BrasilAPI NÃO deve ter sido chamada para substituir o termo de busca no modo BROAD_BRAND
    dummy_brasil_api.assert_not_called()


def test_resolve_search_strategy_strict_cnpj():
    # STRICT_CNPJ: Triangula com a BrasilAPI
    dummy_brasil_api = MagicMock(return_value="PICPAY INSTITUICAO DE PAGAMENTO S.A.")
    
    strat = resolve_search_strategy("22.896.431/0001-10", brasil_api_fetcher=dummy_brasil_api)
    
    assert strat["mode"] == "STRICT_CNPJ"
    assert strat["target_cnpj"] == "22896431000110"
    assert strat["is_broad_brand"] is False
    dummy_brasil_api.assert_called_once_with("22896431000110")


def test_resolve_search_strategy_strict_razao():
    # STRICT_RAZAO: Mantém o termo informado pelo usuário
    strat = resolve_search_strategy("PICPAY INSTITUICAO DE PAGAMENTO S.A.")
    
    assert strat["mode"] == "STRICT_RAZAO"
    assert strat["is_broad_brand"] is False
    assert "PICPAY INSTITUICAO DE PAGAMENTO" in strat["comunica_query_term"]


def test_paginate_comunica_pje_stopping_criterion():
    """
    Testa se a paginação NÃO para quando uma página tem menos de 50 processos desduplicados
    (ex: 50 itens brutos que viram 42 processos únicos). Só encerra quando len(raw_items) < 50.
    """
    searcher = CNJProcessSearcher()
    
    # Simula página 1 com 50 itens brutos (alguns processos repetidos -> 42 únicos)
    page1_items = []
    for i in range(42):
        cnj = f"00000{i:02d}0020245020001"
        page1_items.append({"numero_processo": cnj, "siglaTribunal": "trt2", "texto": "horas extras"})
    # Completa até 50 com duplicatas
    for i in range(8):
        cnj = f"00000{i:02d}0020245020001"
        page1_items.append({"numero_processo": cnj, "siglaTribunal": "trt2", "texto": "duplicata"})

    assert len(page1_items) == 50

    # Simula página 2 com 30 itens brutos (última página)
    page2_items = []
    for i in range(42, 72):
        cnj = f"00000{i:02d}0020245020001"
        page2_items.append({"numero_processo": cnj, "siglaTribunal": "trt2", "texto": "insalubridade"})

    assert len(page2_items) == 30

    def mock_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        p_num = params.get("pagina", 1)
        if p_num == 1:
            resp.json.return_value = {"items": page1_items}
        elif p_num == 2:
            resp.json.return_value = {"items": page2_items}
        else:
            resp.json.return_value = {"items": []}
        return resp

    searcher.session.get = MagicMock(side_effect=mock_get)

    updates = []
    def on_page_cb(page, total_procs, procs_dict):
        updates.append((page, total_procs))

    strat = {"comunica_query_term": "PICPAY", "mode": "BROAD_BRAND", "is_broad_brand": True}
    res = searcher.paginate_comunica_pje_standard(
        search_strategy=strat,
        start_page=1,
        max_pages=5,
        on_page_callback=on_page_cb,
    )

    # Verificações cruciais:
    # 1. Leu exatamente 2 páginas (não abortou na página 1 mesmo tendo 42 processos únicos desduplicados!)
    assert res["pages_read"] == 2
    # 2. Total de processos únicos encontrados foi 72 (42 da página 1 + 30 da página 2)
    assert res["total_processes"] == 72
    # 3. O callback progressivo foi disparado para as 2 páginas
    assert len(updates) == 2
    assert updates[0] == (1, 42)
    assert updates[1] == (2, 72)


def test_api_search_company_broad_brand_mode(client):
    """
    Testa se a API responde ao modo BROAD_BRAND para 'Picpay',
    garantindo is_broad_brand=True e título consolidado.
    """
    with patch("app.routes.api.orchestrator.analyze_company_by_name") as mock_analyze:
        mock_analyze.return_value = {
            "empresa": "PICPAY (GRUPO CONSOLIDADO / FILIAIS)",
            "total_processos": 42,
            "processos": [],
            "niveis_sigilo": {},
            "taxa_sigilo_pct": 0.0,
            "desfechos_sentencas": {},
            "ranking_motivos": [],
            "ranking_motivos_condenacao": [],
            "plus_previsao_condenacao": [],
            "score_risco_terceirizacao": 25.0,
            "probabilidade_condenacao_geral_pct": 25.0,
            "ai_analysis": {},
            "scan_status": "COMPLETED",
            "movements_hash": "hash_picpay",
        }
        res = client.post("/api/v1/search-company", json={"company_name": "Picpay", "force_refresh": True})
        assert res.status_code == 200
        data = res.get_json()
        assert data["is_broad_brand"] is True
        assert "PICPAY" in data["empresa"]
        assert "GRUPO CONSOLIDADO" in data["empresa"]

        # Verifica que o orchestrator recebeu a search_strategy com comunica_query_term puro
        call_kwargs = mock_analyze.call_args[1]
        assert call_kwargs["search_strategy"]["comunica_query_term"] == "PICPAY"
        assert call_kwargs["search_strategy"]["mode"] == "BROAD_BRAND"

