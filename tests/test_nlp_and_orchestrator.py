from app.services.orchestrator import CNJMetadataOrchestrator


def test_classify_text_decision():
    orchestrator = CNJMetadataOrchestrator()

    text_proc = "Diante do exposto, acolho os pedidos da petição inicial e condeno a reclamada..."
    assert orchestrator.classify_text_decision(text_proc) == "condenacoes"

    text_parc = "Ante o exposto, julgo parcialmente procedente a presente reclamação trabalhista..."
    assert orchestrator.classify_text_decision(text_parc) == "condenacoes"

    text_imp = "Isto posto, julgo improcedente a ação e absolvo a reclamada..."
    assert orchestrator.classify_text_decision(text_imp) == "improcedentes"

    text_acordo = "Homologo o acordo firmado entre as partes para que produza seus efeitos legais..."
    assert orchestrator.classify_text_decision(text_acordo) == "acordos"


def test_detect_expert_report():
    orchestrator = CNJMetadataOrchestrator()

    text_laudo = "Apresentação de quesitos periciais e determinação de perícia técnica pelo juízo."
    assert orchestrator.detect_expert_report_in_text(text_laudo) is True

    text_normal = "Designada audiência de instrução e julgamento para o dia 10."
    assert orchestrator.detect_expert_report_in_text(text_normal) is False


def test_parse_tpu_movements():
    orchestrator = CNJMetadataOrchestrator()

    movements = [
        {"codigo": 219, "nome": "Procedência", "dataHora": "2023-01-01T10:00:00"},
        {"codigo": 83, "nome": "Designação de Perícia", "dataHora": "2022-05-01T10:00:00"}
    ]

    analysis = orchestrator.parse_datajud_movements(movements)
    assert analysis["outcome"] == "condenacoes"
    assert analysis["has_expert_report"] is True

