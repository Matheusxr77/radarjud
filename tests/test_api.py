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
    "banco bradesco",        # Apenas letras minúsculas (falha no caps lock/formatação)
    "EMPRESA@!!!",           # Caracteres especiais/apenas símbolos
    "123",                   # Número que não é CNPJ
    "   ",                   # String vazia com espaços
    "Nome Incompleto S/A"    # Formatação mista não normalizada
])
def test_search_company_invalid_parse_and_caps_lock(client, invalid_input):
    """
    Garante que entradas mal formatadas, sem Caps Lock ou que não passem no parse
    de CNPJ/Razão Social sejam rejeitadas com a mensagem de erro específica.
    """
    response = client.post("/api/v1/search-company", json={"company_name": invalid_input})
    
    assert response.status_code in [400, 422]
    data = response.get_json()
    
    assert "error" in data
    assert data["error"] == "empresas so são pesquisadas por razão social ou CNPJ"


def test_search_company_accepts_only_cnpj_and_razao_social(client):
    """
    Garante que a API aceita um CNPJ válido (com ou sem pontuação)
    e uma Razão Social válida (tudo em Caps Lock).
    """
    mock_metrics = {
        "empresa": "EMPRESA VALIDA LTDA",
        "total_processos": 1,
        "ranking_motivos": [],
        "desfechos_sentencas": {},
        "niveis_sigilo": {},
        "plus_previsao_condenacao": []
    }

    with patch("app.routes.api.orchestrator.analyze_company_by_name", return_value=mock_metrics):
        # Teste 1: CNPJ Válido
        resp_cnpj = client.post("/api/v1/search-company", json={"company_name": "12.345.678/0001-90"})
        assert resp_cnpj.status_code == 200

        # Teste 2: Razão Social Válida (Caps Lock)
        resp_razao = client.post("/api/v1/search-company", json={"company_name": "EMPRESA VALIDA LTDA"})
        assert resp_razao.status_code == 200


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