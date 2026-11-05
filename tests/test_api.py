import json
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


def test_search_company_mocked_pipeline(client):
    mock_metrics = {
        "empresa": "Alpha Facilities Ltda",
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
        response = client.post("/api/v1/search-company", json={"company_name": "Alpha Facilities Ltda"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["empresa"] == "Alpha Facilities Ltda"
        assert data["total_processos"] == 5
        assert len(data["ranking_motivos"]) == 1
        assert "plus_previsao_condenacao" in data

