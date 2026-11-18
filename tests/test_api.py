import json
import pytest
from unittest.mock import patch


def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "healthy"
    assert "database" in data


def test_search_company_validation(client):
    response = client.post("/api/v1/search-company", json={})
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data


@pytest.mark.parametrize("invalid_input", [
    "EMPRESA@!!!",           # Caracteres especiais/apenas símbolos
    "123",                   # Número que não é CNPJ
    "   ",                   # String vazia com espaços
    "!",                     # Símbolo único
])
def test_search_company_invalid_parse_and_symbols(client, invalid_input):
    """
    Garante que entradas mal formatadas, sequências numéricas que não são CNPJ
    ou símbolos proibidos sejam rejeitadas com a mensagem de erro específica.
    """
    response = client.post("/api/v1/search-company", json={"company_name": invalid_input})
    
    assert response.status_code in [400, 422]
    data = response.get_json()
    
    assert "error" in data
    assert data["error"] == "empresas so são pesquisadas por razão social ou CNPJ"


def test_search_company_accepts_names_and_cnpj(client):
    """
    Garante que a API aceita busca por nome comum/fantasia (ex: 'Picpay', 'banco bradesco'),
    razão social formal e CNPJ (com ou sem pontuação).
    """
    mock_metrics = {
        "empresa": "EMPRESA TESTE",
        "total_processos": 1,
        "ranking_motivos": [],
        "ranking_motivos_condenacao": [],
        "desfechos_sentencas": {},
        "niveis_sigilo": {},
        "plus_previsao_condenacao": []
    }

    with patch("app.routes.api.orchestrator.analyze_company_by_name", return_value=mock_metrics):
        # Teste 1: CNPJ Válido
        resp_cnpj = client.post("/api/v1/search-company", json={"company_name": "12.345.678/0001-90"})
        assert resp_cnpj.status_code == 200

        # Teste 2: Razão Social Formal
        resp_razao = client.post("/api/v1/search-company", json={"company_name": "EMPRESA VALIDA LTDA"})
        assert resp_razao.status_code == 200

        # Teste 3: Nome Fantasia / Nome Comum com maiúscula inicial (ex: Picpay)
        resp_picpay = client.post("/api/v1/search-company", json={"company_name": "Picpay"})
        assert resp_picpay.status_code == 200

        # Teste 4: Nome em minúsculas (ex: banco bradesco)
        resp_lower = client.post("/api/v1/search-company", json={"company_name": "banco bradesco"})
        assert resp_lower.status_code == 200


def test_search_company_mocked_pipeline(client):
    mock_metrics = {
        "empresa": "ALPHA FACILITIES LTDA",
        "total_processos": 5,
        "ranking_motivos": [{"motivo": "Horas Extras", "quantidade": 3, "porcentagem": 60.0}],
        "desfechos_sentencas": {"condenacoes": 2, "acordos": 1, "improcedentes": 1, "em_aberto": 1},
        "niveis_sigilo": {0: 4, 1: 1},
        "taxa_sigilo_pct": 20.0,
        "score_risco_terceirizacao": 58.5,
        "plus_previsao_condenacao": [
            {
                "motivo": "Horas Extras",
                "processos_abertos": 1,
                "probabilidade_condenacao_pct": 72.4,
                "confianca_previsao_pct": 86.2,
                "nivel_confianca": "Alta"
            }
        ]
    }

    with patch("app.routes.api.orchestrator.analyze_company_by_name", return_value=mock_metrics):
        # Input alterado para Caps Lock para passar na nova validação da API
        response = client.post("/api/v1/search-company", json={"company_name": "ALPHA FACILITIES LTDA"})
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["empresa"] == "ALPHA FACILITIES LTDA"
        assert data["total_processos"] == 5
        assert len(data["ranking_motivos"]) == 1
        assert "plus_previsao_condenacao" in data


def test_clear_search_history_endpoint(client, app):
    """Garante que a rota DELETE /api/v1/history limpa todas as buscas salvas no banco."""
    from app.extensions import db
    from app.models.company_search import CompanySearch
    from app.models.process_record import ProcessRecord

    with app.app_context():
        # Cria registros simulados
        s = CompanySearch(company_name="EMPRESA TESTE DELETE", normalized_name="EMPRESA TESTE DELETE")
        db.session.add(s)
        db.session.commit()

        p = ProcessRecord(company_search_id=s.id, numero_processo="12345678901234567890")
        db.session.add(p)
        db.session.commit()

        assert CompanySearch.query.count() >= 1

        # Deleta via endpoint
        resp = client.delete("/api/v1/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"

        # Confirma que foi limpo
        assert CompanySearch.query.count() == 0
        assert ProcessRecord.query.count() == 0


def test_company_catalog_endpoint_resilience(client):
    """
    Testa resiliência do catálogo de empresas:
    1. Busca por 'Bradesco' retorna 200 com itens e metadados oficiais.
    2. Busca por 'Picpay' retorna 200 com matriz e instituição de pagamento.
    3. Busca por empresa inexistente retorna 200 com lista vazia (sem quebrar com 404).
    """
    # 1. Bradesco
    r = client.get("/api/v1/company-catalog?q=Bradesco&limit=100")
    assert r.status_code == 200
    data = r.get_json()
    assert data["query"] == "Bradesco"
    assert data["count"] >= 5
    cnpjs = [item["cnpj"] for item in data["items"]]
    assert "60746948000112" in cnpjs
    first_item = data["items"][0]
    assert "razao_social" in first_item
    assert "situacao" in first_item
    assert "logo_url" in first_item

    # 2. Picpay
    r_pic = client.get("/api/v1/company-catalog?q=Picpay&limit=100")
    assert r_pic.status_code == 200
    data_pic = r_pic.get_json()
    assert data_pic["count"] >= 1

    # 3. Termo inexistente
    r_none = client.get("/api/v1/company-catalog?q=EmpresaInexistenteXYZ123&limit=100")
    assert r_none.status_code == 200
    data_none = r_none.get_json()
    assert data_none["count"] == 0
    assert data_none["items"] == []
