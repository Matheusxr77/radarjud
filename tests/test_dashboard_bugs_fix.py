"""
Testes unitários rigorosos para a correção dos 4 bugs do dashboard (Itaú / CNJ / PJe):
1. BUG 1: A soma dos Desfechos DEVE ser 100% igual ao Total de Registros Identificados (631 == 631).
2. BUG 2: Processos em Execução / Cumprimento de Sentença / Liquidação / Sisbajud são classificados como CONDENAÇÃO.
3. BUG 3: Eliminação de "Obrigação / Reclamação em Apuração Inicial" via padrões bancários/trabalhistas e nomeClasse do PJe.
4. BUG 4: Desduplicação estrita no ranking de motivos ("Assédio Moral" colapsa em uma única barra consolidada).
"""

import pytest
from app.services.cnj_searcher import (
    CNJProcessSearcher,
    classify_process_outcome,
    extract_real_cause_fast,
    DETAILED_CAUSE_PATTERNS,
)
from app.services.prediction_engine import (
    BayesianRiskPredictor,
    normalize_subject_text,
)


def test_bug1_outcome_sum_strictly_equals_total_processes():
    """
    BUG 1:
    Garante que para qualquer volume de processos (ex: 631), a soma dos
    desfechos (condenações + acordos + improcedências + em aberto) seja
    matematicamente idêntica a total_processos (631 == 631).
    """
    predictor = BayesianRiskPredictor()

    # Cria 631 processos simulados (mistura de condenações, acordos, improcedências, execuções e abertos)
    sample_processes = []
    for i in range(631):
        if i < 15:
            p = {
                "numero_processo": f"0000{i:03d}0020245020001",
                "sentence_outcome": "condenacoes",
                "subjects": ["Horas Extras e Intervalo Intrajornada"],
            }
        elif i < 25:
            p = {
                "numero_processo": f"0000{i:03d}0020245020001",
                "sentence_outcome": "acordos",
                "subjects": ["Horas Extras e Intervalo Intrajornada"],
            }
        elif i < 40:
            p = {
                "numero_processo": f"0000{i:03d}0020245020001",
                "sentence_outcome": "improcedentes",
                "subjects": ["Horas Extras e Intervalo Intrajornada"],
            }
        elif i < 60:
            # Em execução -> deve migrar para condenação
            p = {
                "numero_processo": f"0000{i:03d}0020245020001",
                "sentence_outcome": "em_aberto",
                "subjects": ["Execução e Cumprimento de Sentença Trabalhista"],
            }
        else:
            p = {
                "numero_processo": f"0000{i:03d}0020245020001",
                "sentence_outcome": "em_aberto",
                "subjects": ["Direitos da Categoria Bancária (7ª e 8ª Horas / Cargo de Confiança)"],
            }
        sample_processes.append(p)

    metrics = predictor.build_dashboard_metrics("Itaú Unibanco S.A.", sample_processes)

    total = metrics["total_processos"]
    outcomes = metrics["desfechos_sentencas"]
    soma_desfechos = sum(outcomes.values())

    assert total == 631
    assert soma_desfechos == 631
    assert outcomes["condenacoes"] == 15 + 20  # 15 prévias + 20 processos em execução convertidos
    assert outcomes["acordos"] == 10
    assert outcomes["improcedentes"] == 15
    assert outcomes["em_aberto"] == 631 - (35 + 10 + 15)


def test_bug1_enrich_top_tribunals_preserves_all_processes(monkeypatch):
    """
    BUG 1:
    enrich_top_tribunals_datajud DEVE iterar sobre TODOS os processos de all_processes
    e retornar a lista completa (ex: 631 itens), sem descartar processos não consultados no DataJud.
    """
    searcher = CNJProcessSearcher()

    # Mock DataJud HTTP call para retornar vazio ou simular amostragem parcial
    def mock_post(*args, **kwargs):
        class MockResp:
            status_code = 200
            def json(self):
                return {"hits": {"hits": []}}
        return MockResp()

    monkeypatch.setattr(searcher.session, "post", mock_post)

    all_processes = {}
    for i in range(631):
        cnj = f"0000{i:03d}0020245020001"
        all_processes[cnj] = {
            "numeroProcesso": cnj,
            "siglaTribunal": "TRT2" if i < 150 else ("TRT1" if i < 300 else "TRT15"),
            "publicacoes_texto": ["Intimação referente a despacho judicial."],
            "classes_pje": ["Ação Trabalhista - Rito Ordinário"],
        }

    enriched = searcher.enrich_top_tribunals_datajud(all_processes, max_tribunals=3, timeout=1.0)

    assert len(enriched) == 631
    # Verifica que todos os números CNJ estão preservados
    returned_cnjs = {p["numeroProcesso"] for p in enriched}
    assert len(returned_cnjs) == 631


def test_bug2_execution_and_sentence_compliance_classified_as_condenacao():
    """
    BUG 2:
    Processos com classe, assunto ou termos de Execução, Cumprimento de Sentença,
    Liquidação, Sisbajud, Penhora e Bloqueio DEVEM ser classificados como CONDENACAO.
    """
    # 1. Por Classe Processual
    res1 = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=[],
        texto_publicacoes="Fica a parte intimada para manifestação.",
        classe_nome="Cumprimento de Sentença Trabalhista"
    )
    assert res1 == "CONDENACAO"

    # 2. Por Assunto
    res2 = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=["Execução e Cumprimento de Sentença Trabalhista"],
        texto_publicacoes="Despacho de mero expediente.",
        classe_nome=""
    )
    assert res2 == "CONDENACAO"

    # 3. Por Texto de Sisbajud / Penhora / Pagamento de condenação
    res3 = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=[],
        texto_publicacoes="Proceda-se à ordem de penhora online via SISBAJUD nas contas da executada.",
        classe_nome=""
    )
    assert res3 == "CONDENACAO"

    # 4. Por Homologação de Cálculos de Liquidação
    res4 = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=[],
        texto_publicacoes="Homologo os cálculos de liquidação apresentados pelo reclamante.",
        classe_nome=""
    )
    assert res4 == "CONDENACAO"

    # 5. Por Alvará de Levantamento
    res5 = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=[],
        texto_publicacoes="Expeça-se alvará em favor do autor e intime-se a executada para pagamento do saldo remanescente.",
        classe_nome=""
    )
    assert res5 == "CONDENACAO"


def test_bug2_acordo_and_improcedencia_classification():
    """BUG 2: Acordos e Improcedências devem ser mantidos corretamente."""
    res_acordo = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=[],
        texto_publicacoes="Homologo o acordo celebrado entre as partes para que surta seus jurídicos efeitos.",
        classe_nome=""
    )
    assert res_acordo == "ACORDO"

    res_imp = classify_process_outcome(
        movimentos_codigos=set(),
        assuntos_nomes=[],
        texto_publicacoes="Ante o exposto, julgo totalmente improcedentes os pedidos da exordial.",
        classe_nome=""
    )
    assert res_imp == "IMPROCEDENTE"


def test_bug3_banking_and_labor_patterns_expansion():
    """
    BUG 3:
    Verifica que termos bancários e trabalhistas bancários capturam a causa real
    sem cair no fallback genérico 'Obrigação / Reclamação em Apuração Inicial'.
    """
    # 1. Bancário / Cível
    t1 = "Ação revisional referente a juros abusivos e capitalização de juros em contrato de financiamento."
    c1 = extract_real_cause_fast([], [t1])
    assert c1[0]["nome"] == "Revisão de Contrato e Juros Abusivos"

    t2 = "Fraude bancária com transação PIX não reconhecida pelo correntista via golpe do pix."
    c2 = extract_real_cause_fast([], [t2])
    assert c2[0]["nome"] == "Fraude Bancária / Golpe do PIX / Contestação de Transação"

    t3 = "Ação indenizatória por negativação indevida perante órgãos de proteção ao crédito Serasa e SPC."
    c3 = extract_real_cause_fast([], [t3])
    assert c3[0]["nome"] == "Negativação Indevida (SPC / Serasa)"

    t4 = "Contrato de empréstimo consignado com reserva de margem consignável (RMC / RCC)."
    c4 = extract_real_cause_fast([], [t4])
    assert c4[0]["nome"] == "Empréstimo Consignado e Reserva de Margem (RMC)"

    t5 = "Ação de busca e apreensão de veículo alienado fiduciariamente sob Decreto-Lei 911."
    c5 = extract_real_cause_fast([], [t5])
    assert c5[0]["nome"] == "Busca e Apreensão em Alienação Fiduciária"

    # 2. Trabalhista Bancário
    t6 = "Enquadramento na categoria de bancário com pedido de 7ª e 8ª horas por inexistência de cargo de confiança (art. 224 da CLT)."
    c6 = extract_real_cause_fast([], [t6])
    assert c6[0]["nome"] == "Direitos da Categoria Bancária (7ª e 8ª Horas / Cargo de Confiança)"

    t7 = "Diferenças salariais decorrentes de equiparação salarial com o paradigma apontado na petição inicial."
    c7 = extract_real_cause_fast([], [t7])
    assert c7[0]["nome"] == "Equiparação Salarial e Desvio de Função"

    t8 = "Pagamento de participação nos lucros e resultados (PLR) proporcional ao ano trabalhado."
    c8 = extract_real_cause_fast([], [t8])
    assert c8[0]["nome"] == "Participação nos Lucros e Resultados (PLR) e Bônus"


def test_bug3_structured_pje_class_prevents_generic_fallback():
    """
    BUG 3:
    Quando a publicação for curta e sem termos específicos, a função DEVE usar
    a classe estruturada do PJe (ex: 'Ação Trabalhista - Rito Ordinário') em vez de
    'Obrigação / Reclamação em Apuração Inicial'.
    """
    short_text = ["Fica a reclamada intimada para comparecer à audiência designada."]
    classes = ["Ação Trabalhista - Rito Ordinário"]

    causes = extract_real_cause_fast([], short_text, classes_pje=classes)
    assert causes[0]["nome"] == "Ação Trabalhista (Rito Ordinário)"
    assert causes[0]["nome"] != "Obrigação / Reclamação em Apuração Inicial"

    # Teste para Rito Sumaríssimo
    classes_sum = ["Ação Trabalhista - Rito Sumaríssimo"]
    causes_sum = extract_real_cause_fast([], short_text, classes_pje=classes_sum)
    assert causes_sum[0]["nome"] == "Ação Trabalhista (Rito Sumaríssimo)"

    # Teste para Juizado Especial
    classes_jdc = ["Procedimento do Juizado Especial Cível"]
    causes_jdc = extract_real_cause_fast([], short_text, classes_pje=classes_jdc)
    assert causes_jdc[0]["nome"] == "Juizado Especial Cível / Consumidor"


def test_bug4_deduplication_of_motivos_by_normalized_name():
    """
    BUG 4:
    'Assédio Moral e Danos Morais Trabalhistas' que ocorra com código TPU ou sem código TPU,
    ou com pequenas variações de espaços, DEVE colapsar em uma única entrada no ranking
    de motivos, somando as contagens (ex: 3 + 1 = 4).
    """
    predictor = BayesianRiskPredictor()

    processes = [
        # 3 processos com código TPU 1855
        {
            "numero_processo": "00000010020245020001",
            "primary_subject": "Assédio Moral e Danos Morais Trabalhistas",
            "primary_subject_code": 1855,
            "subjects": ["Assédio Moral e Danos Morais Trabalhistas"],
            "sentence_outcome": "condenacoes",
        },
        {
            "numero_processo": "00000020020245020001",
            "primary_subject": "Assédio Moral e Danos Morais Trabalhistas",
            "primary_subject_code": 1855,
            "subjects": ["Assédio Moral e Danos Morais Trabalhistas"],
            "sentence_outcome": "em_aberto",
        },
        {
            "numero_processo": "00000030020245020001",
            "primary_subject": "Assédio Moral e Danos Morais Trabalhistas",
            "primary_subject_code": 1855,
            "subjects": ["Assédio Moral e Danos Morais Trabalhistas"],
            "sentence_outcome": "em_aberto",
        },
        # 1 processo sem código TPU (ex: inferido por texto ou outra fonte)
        {
            "numero_processo": "00000040020245020001",
            "primary_subject": "Assédio Moral e Danos Morais Trabalhistas",
            "primary_subject_code": None,
            "subjects": ["Assédio Moral e Danos Morais Trabalhistas "],
            "sentence_outcome": "em_aberto",
        },
    ]

    metrics = predictor.generate_dashboard_metrics("Itaú Unibanco S.A.", processes)

    ranking = metrics["ranking_motivos"]
    assedio_entries = [r for r in ranking if "Assédio Moral" in r["motivo"]]

    # DEVE existir exatamente 1 entrada no ranking, e não 2!
    assert len(assedio_entries) == 1
    assert assedio_entries[0]["quantidade"] == 4
    assert assedio_entries[0]["porcentagem"] == 100.0
