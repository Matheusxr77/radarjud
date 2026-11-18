import json
from unittest.mock import MagicMock
import pytest
import requests

from app import create_app, db
from app.utils.http_client import request_gov_br_with_fallback, DEFAULT_BROWSER_HEADERS


@pytest.fixture
def app_instance():
    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app_instance):
    return app_instance.test_client()


def test_request_gov_br_sends_pt_br_headers(monkeypatch):
    """Verifica se request_gov_br_with_fallback envia headers com pt-BR e User-Agent brasileiro."""
    captured_kwargs = {}

    def mock_request(self, method, url, **kwargs):
        captured_kwargs["method"] = method
        captured_kwargs["url"] = url
        captured_kwargs["headers"] = kwargs.get("headers", {})
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.ok = True
        return resp

    monkeypatch.setattr(requests.Session, "request", mock_request)

    resp = request_gov_br_with_fallback("GET", "https://comunicaapi.pje.jus.br/api/v1/comunicacao")
    assert resp.status_code == 200
    headers = captured_kwargs["headers"]
    assert "pt-BR" in headers.get("Accept-Language", "")
    assert "Mozilla/5.0" in headers.get("User-Agent", "")


def test_request_gov_br_fallback_on_403(monkeypatch):
    """
    Simula bloqueio por IP (HTTP 403 Forbidden nos EUA) na chamada direta
    e valida acionamento automático de fallback para CNJ_PROXY_URL (gru1).
    """
    monkeypatch.setenv("CNJ_PROXY_URL", "https://seu-front.vercel.app/api/cnj-proxy")
    monkeypatch.delenv("CNJ_FORCE_PROXY", raising=False)

    call_history = []

    def mock_request(self, method, url, **kwargs):
        call_history.append((url, kwargs))
        resp = MagicMock(spec=requests.Response)
        if "vercel.app" in url:
            resp.status_code = 200
            resp.ok = True
            resp.text = '{"items": [{"numeroProcesso": "0000123-45.2023.5.02.0001"}]}'
            resp.json.return_value = {"items": [{"numeroProcesso": "0000123-45.2023.5.02.0001"}]}
            return resp
        # Chamada direta simula 403 Forbidden do WAF do PJe
        resp.status_code = 403
        resp.ok = False
        return resp

    monkeypatch.setattr(requests.Session, "request", mock_request)

    target_url = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
    resp = request_gov_br_with_fallback(
        "GET",
        target_url,
        params={"nomeParte": "EMPRESA TESTE", "pagina": 1},
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 200
    assert len(call_history) == 2
    # 1ª chamada foi direta para comunicaapi
    assert "comunicaapi.pje.jus.br" in call_history[0][0]
    # 2ª chamada foi via proxy vercel
    assert "seu-front.vercel.app/api/cnj-proxy" in call_history[1][0]
    assert "target" in call_history[1][1].get("params", {})
    assert "comunicaapi.pje.jus.br" in call_history[1][1]["params"]["target"]


def test_request_gov_br_force_proxy_setting(monkeypatch):
    """Quando CNJ_FORCE_PROXY=true, dispara diretamente pelo proxy sem tentar chamada direta."""
    monkeypatch.setenv("CNJ_PROXY_URL", "https://seu-front.vercel.app/api/cnj-proxy")
    monkeypatch.setenv("CNJ_FORCE_PROXY", "true")

    call_history = []

    def mock_request(self, method, url, **kwargs):
        call_history.append((url, kwargs))
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.ok = True
        return resp

    monkeypatch.setattr(requests.Session, "request", mock_request)

    resp = request_gov_br_with_fallback(
        "POST",
        "https://api-publica.datajud.cnj.jus.br/api_publica_trt2/_search",
        json_body={"query": {"match_all": {}}},
        headers={"Authorization": "APIKey test-key"},
    )

    assert resp.status_code == 200
    assert len(call_history) == 1
    assert "seu-front.vercel.app/api/cnj-proxy" in call_history[0][0]
    assert call_history[0][1]["headers"].get("Authorization") == "APIKey test-key"


def test_search_endpoint_with_client_prefetched_items(client, monkeypatch):
    """
    Testa se o backend consome client_prefetched_items quando a coleta direta
    do servidor falha ou retorna vazia, calculando desfechos e predições com os dados do cliente.
    """
    sample_prefetched = [
        {
            "numero_processo": "0000111-22.2023.5.02.0001",
            "tribunal": "TRT2",
            "nomeClasse": "Ação Trabalhista",
            "textoPublicacao": "JULGO PROCEDENTE EM PARTE o pedido para condenar a reclamada ao pagamento de horas extras.",
            "destinatarios": [
                {"nome": "EMPRESA PROXIED S/A", "numeroDocumento": "11222333000181"}
            ],
        },
        {
            "numero_processo": "0000222-33.2023.5.02.0002",
            "tribunal": "TRT2",
            "nomeClasse": "Ação Trabalhista",
            "textoPublicacao": "HOMOLOGO O ACORDO celebrado entre as partes para quitar verbas rescisórias.",
            "destinatarios": [
                {"nome": "EMPRESA PROXIED S/A", "numeroDocumento": "11222333000181"}
            ],
        },
    ]

    # Força a busca direta no orquestrador a retornar vazio (simulando bloqueio geográfico)
    from app.services.cnj_searcher import CNJProcessSearcher

    def mock_fetch_single_page(self, brand_term, page_num=1, timeout=5.0):
        return {}

    monkeypatch.setattr(CNJProcessSearcher, "fetch_single_comunica_page", mock_fetch_single_page)

    payload = {
        "company_name": "EMPRESA PROXIED S/A",
        "client_prefetched_items": sample_prefetched,
    }

    # Testa tanto na rota /search-company quanto no alias /search
    resp = client.post("/api/v1/search", json=payload)
    assert resp.status_code == 200
    data = resp.get_json()

    assert data.get("total_processos") == 2
    assert "desfechos_sentencas" in data
    assert data["desfechos_sentencas"].get("condenacoes") == 1
    assert data["desfechos_sentencas"].get("acordos") == 1
