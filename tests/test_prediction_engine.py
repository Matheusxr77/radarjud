import pytest
from app.services.prediction_engine import BayesianRiskPredictor


def test_prediction_formula_basic():
    predictor = BayesianRiskPredictor(alpha_prior=3.0)

    # Caso com 10 condenações, 2 acordos, 3 improcedentes (total 15 julgados)
    res = predictor.calculate_prediction_for_subject(
        subject_name="Horas Extras",
        subject_code=1855,
        cond=10,
        acordo=2,
        imp=3,
        open_count=5,
        has_expert_report=False,
        meta_completeness=0.95,
        secrecy_rate=0.0,
    )

    assert "probabilidade_condenacao_pct" in res
    assert "confianca_previsao_pct" in res
    assert res["processos_abertos"] == 5
    assert res["processos_julgados"] == 15
    # Probabilidade deve estar entre 60% e 90%
    assert 60.0 <= res["probabilidade_condenacao_pct"] <= 90.0


def test_confidence_scale_as_per_specification():
    """
    Valida a especificação na página 5:
    - Se a empresa tem 18 processos já julgados com sigilo 0 e metadados íntegros, a confiança atinge ~89% a 94%.
    - Se for um motivo novo com apenas 1 processo anterior julgado, o sistema alerta confiança baixa (~48%).
    """
    predictor = BayesianRiskPredictor()

    # 1 processo julgado (n = 1)
    res_1 = predictor.calculate_prediction_for_subject(
        subject_name="Assunto Novo",
        subject_code=None,
        cond=1,
        acordo=0,
        imp=0,
        open_count=2,
        has_expert_report=False,
        meta_completeness=0.95,
        secrecy_rate=0.0,
    )

    # 18 processos julgados (n = 18)
    res_18 = predictor.calculate_prediction_for_subject(
        subject_name="Horas Extras",
        subject_code=1855,
        cond=14,
        acordo=2,
        imp=2,
        open_count=4,
        has_expert_report=False,
        meta_completeness=0.95,
        secrecy_rate=0.0,
    )

    # Confiança com 1 processo em torno de 45% - 52%
    assert 40.0 <= res_1["confianca_previsao_pct"] <= 55.0
    assert res_1["nivel_confianca"] == "Baixa (Amostra Inicial)"

    # Confiança com 18 processos entre 85% e 96%
    assert 85.0 <= res_18["confianca_previsao_pct"] <= 96.0
    assert res_18["nivel_confianca"] == "Alta"


def test_expert_report_risk_adjustment():
    """Laudo pericial adiciona ajuste técnico no risco de condenação."""
    predictor = BayesianRiskPredictor()

    res_without = predictor.calculate_prediction_for_subject(
        subject_name="Adicional de Insalubridade",
        subject_code=2029,
        cond=2,
        acordo=1,
        imp=2,
        open_count=3,
        has_expert_report=False,
    )

    res_with = predictor.calculate_prediction_for_subject(
        subject_name="Adicional de Insalubridade",
        subject_code=2029,
        cond=2,
        acordo=1,
        imp=2,
        open_count=3,
        has_expert_report=True,
    )

    assert res_with["probabilidade_condenacao_pct"] > res_without["probabilidade_condenacao_pct"]
    assert res_with["possui_laudo_pericial"] is True

