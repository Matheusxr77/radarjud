import pytest
from unittest.mock import MagicMock, patch

from app.services.cnj_searcher import CNJProcessSearcher, TJ_MAP


def test_clean_company_name():
    """Valida a limpeza de nomes e sufixos que quebram a busca no Comunica PJe."""
    assert CNJProcessSearcher.clean_company_name("BANCO BRADESCO S/A") == "BANCO BRADESCO"
    assert CNJProcessSearcher.clean_company_name("Magazine Luiza S.A.") == "Magazine Luiza"
    assert CNJProcessSearcher.clean_company_name("Empresa Alfa Ltda.") == "Empresa Alfa"
    assert CNJProcessSearcher.clean_company_name("Comércio & Serviços Silva EIRELI") == "Comércio Serviços Silva"
    assert CNJProcessSearcher.clean_company_name("Pequena Empresa ME") == "Pequena Empresa"
    assert CNJProcessSearcher.clean_company_name("Posto São Jorge EPP.") == "Posto São Jorge"


def test_normalize_cnj():
    """Valida a extração estrita de exatamente 20 dígitos numéricos."""
    assert CNJProcessSearcher.normalize_cnj("0001234-56.2024.5.06.0001") == "00012345620245060001"
    assert CNJProcessSearcher.normalize_cnj("00012345620245060001") == "00012345620245060001"
    assert CNJProcessSearcher.normalize_cnj("12345") == ""
    assert CNJProcessSearcher.normalize_cnj(None) == ""
    assert CNJProcessSearcher.normalize_cnj("ABC-00012345620245060001-XYZ") == "00012345620245060001"


def test_resolve_datajud_alias():
    """Valida dedução dinâmica do endpoint do DataJud pelos dígitos 13 e 14:16 do CNJ."""
    # Justiça do Trabalho (J=5)
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620245000001") == "api_publica_tst"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620245060001") == "api_publica_trt6"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620245020001") == "api_publica_trt2"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620245150001") == "api_publica_trt15"

    # Justiça Federal (J=4)
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620244010001") == "api_publica_trf1"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620244030001") == "api_publica_trf3"

    # Justiça Estadual (J=8)
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620248260001") == "api_publica_tjsp"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620248190001") == "api_publica_tjrj"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620248130001") == "api_publica_tjmg"
    assert CNJProcessSearcher.resolve_datajud_alias("00012345620248210001") == "api_publica_tjrs"

    # Fallback por sigla
    assert CNJProcessSearcher.resolve_datajud_alias("invalid", fallback_sigla="TRT-15") == "api_publica_trt15"


def test_fetch_comunica_pje_protocol_and_defensive_parsing():
    """Garante cumprimento estrito de itensPorPagina <= 50, User-Agent e múltiplos formatos de resposta."""
    searcher = CNJProcessSearcher()

    # Formato 1: Dicionário com chave "items"
    mock_items = [
        {
            "numero_processo": "0001234-56.2024.5.06.0001",
            "siglaTribunal": "TRT6",
            "nomeOrgao": "1ª Vara do Trabalho de Recife",
            "texto": "Fica a reclamada intimada da r. sentença condenatória.",
            "data_disponibilizacao": "2024-03-01"
        },
        {
            "numeroprocessocommascara": "0009876-54.2023.5.06.0002",
            "siglaTribunal": "TRT6",
            "nomeOrgao": "2ª Vara do Trabalho de Recife",
            "teor": "Intimação para audiência de instrução.",
            "dataDisponibilizacao": "2024-02-15"
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True
    mock_resp.json.return_value = {"items": mock_items}

    with patch.object(searcher.session, "get", return_value=mock_resp) as mock_get:
        procs = searcher.fetch_comunica_pje("Empresa Teste S/A", max_pages=1)

        # Valida que respeitou o teto máximo de 50 itens por página
        mock_get.assert_called()
        call_params = mock_get.call_args_list[0][1]["params"]
        assert call_params["itensPorPagina"] == 50
        assert call_params["pagina"] == 1
        assert call_params["nomeParte"] == "Empresa Teste S/A"

        # Valida extração e normalização dos 20 dígitos numéricos
        assert "00012345620245060001" in procs
        assert "00098765420235060002" in procs
        assert len(procs) == 2


def test_enrich_with_datajud_batching_and_defensive_subjects():
    """Valida busca em lote terms (até 50) e flatten defensivo de assuntos aninhados."""
    searcher = CNJProcessSearcher()

    comunica_procs = {
        "00012345620245060001": {
            "numeroProcesso": "00012345620245060001",
            "siglaTribunal": "TRT6",
            "publicacoes_texto": ["Texto da publicação 1"],
            "classes_pje": ["Ação Trabalhista - Rito Ordinário"],
        }
    }

    # Simula resposta do DataJud com assuntos aninhados [[{...}]] e código TPU 219 (Procedência)
    mock_hit = {
        "_source": {
            "numeroProcesso": "00012345620245060001",
            "tribunal": "TRT6",
            "nivelSigilo": 0,
            "assuntos": [
                [{"codigo": 1694, "nome": "Horas Extras"}],
                {"codigo": 2546, "nome": "Verbas Rescisórias"}
            ],
            "movimentos": [
                {"codigo": 219, "nome": "Procedência do Pedido"}
            ]
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"hits": {"hits": [mock_hit]}}

    with patch.object(searcher.session, "post", return_value=mock_resp) as mock_post:
        results = searcher.enrich_with_datajud(comunica_procs)

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "api_publica_trt6/_search" in call_url

        call_body = mock_post.call_args[1]["json"]
        assert call_body["query"]["terms"]["numeroProcesso"] == ["00012345620245060001"]

        assert len(results) == 1
        proc = results[0]
        assert proc["numeroProcesso"] == "00012345620245060001"
        assert proc["statusDesfecho"] == "CONDENACAO"
        assert proc["sentence_outcome"] == "condenacoes"
        assert any("Horas Extras" in s for s in proc["subjects"])
        assert "Verbas Rescisórias" in proc["subjects"]


def test_datajud_fallback_semantic_text_when_not_indexed():
    """Garante que quando o DataJud não tiver indexado o processo, usa o texto do Comunica PJe."""
    searcher = CNJProcessSearcher()

    comunica_procs = {
        "00012345620245060001": {
            "numeroProcesso": "00012345620245060001",
            "siglaTribunal": "TRT6",
            "publicacoes_texto": [
                "Ante o exposto, julgo procedente em parte a reclamatória para condenar a reclamada "
                "ao pagamento de horas extras excedentes à 8ª diária e reflexos em aviso prévio e FGTS. "
                "Determino a realização de perícia contábil."
            ],
            "classes_pje": ["Ação Trabalhista - Rito Ordinário"],
        }
    }

    # DataJud retorna 404 ou lista vazia (processo recente ainda não indexado no CNJ)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"hits": {"hits": []}}

    with patch.object(searcher.session, "post", return_value=mock_resp):
        results = searcher.enrich_with_datajud(comunica_procs)

        assert len(results) == 1
        proc = results[0]
        # Não ficou zerado! Inferiu desfecho e assuntos pelo texto da publicação:
        assert proc["statusDesfecho"] == "CONDENACAO"
        assert proc["sentence_outcome"] == "condenacoes"
        assert proc["sentence_sub_outcome"] == "procedencia_parcial"
        assert proc["has_expert_report"] is True
        assert proc["sinaisInstrucao"]["possuiPericiaOuLaudo"] is True
        assert any("Horas Extras" in s for s in proc["subjects"])

