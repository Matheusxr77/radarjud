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


def test_infer_subject_and_observation_classes_and_text():
    """Garante que a inferência semântica extrai causas específicas mesmo com grafias variadas."""
    orchestrator = CNJMetadataOrchestrator()

    # 1. Extração por texto com horas extras e dano moral
    pje_text = "Sentença: julgo procedente o pedido de horas extras excedentes à 8a diária e reflexos em DSR e indenização por danos morais."
    subs, obs = orchestrator.infer_subject_and_observation([], [pje_text])
    assert "Falta de Pagamento de Horas Extras e Reflexos" in subs
    assert "Indenização por Danos Morais e Assédio no Trabalho" in subs
    assert obs is not None

    # 2. Extração por classe processual em caixa alta sem acento
    subs_classe, obs_classe = orchestrator.infer_subject_and_observation([], [], pje_classes=["ACAO TRABALHISTA - RITO ORDINARIO"])
    assert subs_classe == ["Reclamatória Trabalhista (Rito Ordinário)"]

    # 3. Extração por classe processual TST / Recurso de Revista
    subs_rr, obs_rr = orchestrator.infer_subject_and_observation([], [], pje_classes=["Recurso de Revista Trabalhista"])
    assert "Recurso de Revista Trabalhista (TST)" in subs_rr[0]


def test_tst_recurso_recognition():
    """Garante que processos que tramitaram no TST sejam identificados com grau TST (Recursal)."""
    orchestrator = CNJMetadataOrchestrator()
    processes_map = {
        "00001002220235020001": {
            "tribunal": "trt2",
            "textos_pje": ["Remessa dos autos à instância superior - Tribunal Superior do Trabalho para julgamento do AIRR."],
            "classes_pje": ["Recurso de Revista"],
            "tipos_doc_pje": ["Acórdão"],
            "data_publicacao": "2023-08-01",
            "is_tst_recurso": False,
        }
    }
    enriched = orchestrator._enrich_with_datajud(processes_map)
    assert len(enriched) == 1
    assert enriched[0]["is_tst_recurso"] is True
    assert enriched[0]["grau"] == "TST (Recursal)"


