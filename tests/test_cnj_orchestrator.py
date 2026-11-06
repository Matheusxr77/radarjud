import pytest
from unittest.mock import patch, MagicMock
from app.services.cnj_orchestrator import CNJMetadataOrchestrator


def test_orchestrator_cache():
    """Valida o funcionamento do cache local em memória."""
    orchestrator = CNJMetadataOrchestrator()
    orchestrator._cache["empresa teste"] = {"cached": True, "empresa": "empresa teste"}

    res = orchestrator.analyze_company_by_name("empresa teste")
    assert res.get("cached") is True


def test_orchestrator_multi_subject_and_no_keyerror():
    """
    Valida:
    1. Agrupamento multi-assunto N:N (todos os assuntos no ranking)
    2. Ausência de KeyError no history_by_reason
    3. Cálculo de delta_laudo e previsão bayesiana
    """
    orchestrator = CNJMetadataOrchestrator()

    mock_processes = [
        {
            "numero_processo": "00001234520235020001",
            "subjects": ["Horas Extras", "Adicional de Insalubridade", "FGTS"],
            "nivelSigilo": 0,
            "sentence_outcome": "condenacoes",
            "has_expert_report": True
        },
        {
            "numero_processo": "00004567820235020001",
            "subjects": ["Horas Extras", "Aviso Prévio"],
            "nivelSigilo": 1,
            "sentence_outcome": "acordos",
            "has_expert_report": False
        },
        {
            "numero_processo": "00007891020235020001",
            "subjects": ["Horas Extras"],
            "nivelSigilo": 0,
            "sentence_outcome": "em_aberto",
            "has_expert_report": False
        }
    ]

    with patch.object(orchestrator, "_fetch_comunica_pje", return_value={"mock": True}):
        with patch.object(orchestrator, "_enrich_with_datajud", return_value=mock_processes):
            result = orchestrator.analyze_company_by_name("Empresa Multi Assunto")

            assert result["total_processos"] == 3
            assert result["desfechos_sentencas"]["condenacoes"] == 1
            assert result["desfechos_sentencas"]["acordos"] == 1
            assert result["desfechos_sentencas"]["em_aberto"] == 1
            assert result["niveis_sigilo"][0] == 2
            assert result["niveis_sigilo"][1] == 1

            # Multi-assunto: Horas Extras apareceu 3 vezes
            motivos = {r["motivo"]: r["quantidade"] for r in result["ranking_motivos"]}
            assert motivos["Horas Extras"] == 3
            assert motivos["Adicional de Insalubridade"] == 1
            assert motivos["FGTS"] == 1
            assert motivos["Aviso Prévio"] == 1

            # Módulo Plus preditivo gerado sem KeyError
            preds = {p["motivo"]: p for p in result["plus_previsao_condenacao"]}
            assert "Horas Extras" in preds
            assert "Adicional de Insalubridade" in preds
            assert preds["Horas Extras"]["processos_abertos"] == 1
            assert preds["Horas Extras"]["probabilidade_condenacao_pct"] > 0
            assert preds["Horas Extras"]["confianca_previsao_pct"] > 0


def test_datajud_failure_keeps_processes_found_by_comunica():
    orchestrator = CNJMetadataOrchestrator()
    response = MagicMock(ok=False, status_code=429)
    processes = {
        "00001234520235020001": {
            "numero_processo": "00001234520235020001",
            "tribunal": "trt2",
            "textos_pje": [],
        }
    }

    with patch.object(orchestrator.session, "post", return_value=response):
        result = orchestrator._enrich_with_datajud(processes)

    assert len(result) == 1
    assert result[0]["numero_processo"] == "00001234520235020001"
    assert result[0]["sentence_outcome"] == "em_aberto"
