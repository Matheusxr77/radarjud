import math
import logging
import unicodedata
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional, Tuple

from app.services.tpu_constants import (
    TPU_SUBJECT_BASELINES,
    SUBJECT_KEYWORD_BASELINES,
    TRT_REGIONAL_LOGIT_MODIFIERS,
    DEFAULT_TRT_BASE_PROBABILITY,
    DEFAULT_TRT_ACORDO_PROBABILITY,
    DEFAULT_TRT_IMP_PROBABILITY,
    BAYESIAN_ALPHA_PRIOR,
)

logger = logging.getLogger(__name__)

try:
    from scipy.stats import beta as _scipy_beta  # type: ignore
except ImportError:
    _scipy_beta = None


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Converte valor para int com segurança, tratando None, booleanos e strings não numéricas."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def normalize_subject_text(text: str) -> str:
    """Normaliza acentos (NFKD), caixa e espaços para matching determinístico."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def _tilt_simplex(
    p_cond: float,
    p_acordo: float,
    p_imp: float,
    delta_logit: float,
) -> Tuple[float, float, float]:
    """
    Aplica deslocamento em escala logit (delta_logit) sobre p_cond na simplex 3-dimensional (softmax tilting).
    Preserva estritamente:
    1) p'_cond + p'_acordo + p'_imp == 1.0
    2) logit(p'_cond) == logit(p_cond) + delta_logit
    3) p'_acordo / p'_imp == p_acordo / p_imp (independência de alternativas irrelevantes)
    """
    s = max(1e-9, p_cond + p_acordo + p_imp)
    pc, pa, pi = p_cond / s, p_acordo / s, p_imp / s
    if delta_logit == 0.0:
        return pc, pa, pi
    mult = math.exp(max(-10.0, min(10.0, delta_logit)))
    c_tilted = pc * mult
    z = c_tilted + pa + pi
    return c_tilted / z, pa / z, pi / z


def _betacf(a: float, b: float, x: float) -> float:
    """Fração continuada de Lentz para a função Beta Incompleta Regularizada I_x(a, b)."""
    max_iter = 200
    eps = 3.0e-9
    fpmin = 1.0e-30

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d

    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break

    return h


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Calcula a CDF da distribuição Beta(a, b) no ponto x in [0, 1]."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    bt = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """
    Calcula o quantil q in (0, 1) da distribuição Beta(a, b).
    Usa scipy.stats.beta.ppf se disponível, ou bissecção sobre I_x(a, b) com precisão 1e-9.
    """
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    if _scipy_beta is not None:
        return float(_scipy_beta.ppf(q, a, b))

    lo, hi = 0.0, 1.0
    for _ in range(45):
        mid = 0.5 * (lo + hi)
        if regularized_incomplete_beta(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _round_simplex_pct(theta_cond: float, theta_acordo: float, theta_imp: float) -> Tuple[float, float, float]:
    """
    Arredonda as 3 probabilidades da simplex para 1 casa decimal garantindo soma exata de 100.0%,
    calculando cada componente diretamente de sua própria massa posterior e ajustando apenas o
    resíduo de arredondamento (±0.1%) no maior componente.
    """
    c_pct = round(theta_cond * 100.0, 1)
    a_pct = round(theta_acordo * 100.0, 1)
    i_pct = round(theta_imp * 100.0, 1)
    diff = round(100.0 - (c_pct + a_pct + i_pct), 1)
    if diff != 0.0:
        if c_pct >= a_pct and c_pct >= i_pct:
            c_pct = round(c_pct + diff, 1)
        elif a_pct >= c_pct and a_pct >= i_pct:
            a_pct = round(a_pct + diff, 1)
        else:
            i_pct = round(i_pct + diff, 1)
    return c_pct, a_pct, i_pct


class BayesianRiskPredictor:
    """
    Motor Bayesiano Empírico (Dirichlet-Multinomial / Beta-Binomial) de Previsão de Risco:
    1. Separa os 3 desfechos reais (Condenação, Acordo, Improcedência) via conjugação Dirichlet-Multinomial.
    2. Conserva estritamente a massa amostral: cada processo julgado soma exatamente 1.0 nos contadores posteriores.
    3. Calcula o Intervalo de Credibilidade de 90% (Beta PPF 5% e 95%) com desconto monotônico de censura à direita.
    4. Aplica o efeito da proporção de laudos periciais e do tribunal via softmax tilting em escala logit.
    5. Estima o prior hierárquico Leave-One-Subject-Out (Empirical Bayes shrinkage) sem dupla contagem dos dados.
    6. Calcula o Score de Risco Súmula 331 TST em fórmula única convexa (65% adverso + 20% rescisórias + 15% Súmula 331/Execução).
    """

    def __init__(self, alpha_prior: float = BAYESIAN_ALPHA_PRIOR):
        self.alpha_prior = float(alpha_prior)

    def get_subject_baseline_prior(
        self,
        subject_name: str,
        subject_code: Any = None,
        tribunal: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retorna os parâmetros basais (pbase_cond, pbase_acordo, pbase_imp, logit_laudo_beta)
        para o assunto TPU e ajusta pelo modificador regional do Tribunal (TRT) via softmax tilting.
        """
        code_int = safe_int(subject_code)
        base_entry = None
        if code_int is not None and code_int in TPU_SUBJECT_BASELINES:
            base_entry = dict(TPU_SUBJECT_BASELINES[code_int])
        else:
            norm_name = normalize_subject_text(subject_name)
            for keywords, cfg in SUBJECT_KEYWORD_BASELINES:
                if any(k in norm_name for k in keywords):
                    base_entry = dict(cfg)
                    break

        if base_entry is None:
            base_entry = {
                "pbase": DEFAULT_TRT_BASE_PROBABILITY,
                "pbase_acordo": DEFAULT_TRT_ACORDO_PROBABILITY,
                "pbase_imp": DEFAULT_TRT_IMP_PROBABILITY,
                "logit_laudo_beta": 0.10,
                "sumula_331_core": False,
            }

        p_cond = float(base_entry["pbase"])
        p_acordo = float(base_entry.get("pbase_acordo", DEFAULT_TRT_ACORDO_PROBABILITY))
        p_imp = float(base_entry.get("pbase_imp", max(0.05, 1.0 - p_cond - p_acordo)))

        mod = 0.0
        if tribunal:
            norm_trib = normalize_subject_text(tribunal).replace(" ", "")
            mod = TRT_REGIONAL_LOGIT_MODIFIERS.get(norm_trib, 0.0)

        p_cond, p_acordo, p_imp = _tilt_simplex(p_cond, p_acordo, p_imp, mod)

        return {
            "pbase_cond": p_cond,
            "pbase_acordo": p_acordo,
            "pbase_imp": p_imp,
            "logit_laudo_beta": float(base_entry.get("logit_laudo_beta", 0.10)),
            "sumula_331_core": bool(base_entry.get("sumula_331_core", False)),
        }

    def get_subject_baseline_probability(
        self,
        subject_name: str,
        subject_code: Any = None,
        tribunal: Optional[str] = None,
    ) -> float:
        """Compatibilidade direta com chamadas que esperam apenas pbase_cond."""
        prior = self.get_subject_baseline_prior(subject_name, subject_code, tribunal)
        return round(prior["pbase_cond"], 4)

    def calculate_prediction_for_subject(
        self,
        subject_name: str,
        subject_code: Any,
        cond: float,
        acordo: float,
        imp: float,
        open_count: int,
        laudo_ratio: float = 0.0,
        has_expert_report: bool = False,
        tribunal: Optional[str] = None,
        empirical_company_prior: Optional[Dict[str, float]] = None,
        secrecy_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Modelo Conjugado Dirichlet-Multinomial com Shrinkage Empirical Bayes e Intervalo de Credibilidade Beta (90%):
        - Separa Condenação (theta_cond), Acordo (theta_acordo) e Vitória/Improcedência (theta_imp).
        - Aplica ajuste de proporção de laudos periciais via softmax tilting em escala logit.
        - Calcula o IC 90% [lo, hi] via distribuição Beta marginal com desconto monotônico de censura.
        """
        prior_info = self.get_subject_baseline_prior(subject_name, subject_code, tribunal)
        p0_cond = prior_info["pbase_cond"]
        p0_acordo = prior_info["pbase_acordo"]
        p0_imp = prior_info["pbase_imp"]

        # Empirical Bayes (Leave-One-Subject-Out): combina o prior do assunto/TRT com a distribuição
        # empírica dos DEMAIS assuntos da empresa (evitando dupla contagem dos dados deste assunto)
        if empirical_company_prior and empirical_company_prior.get("weight", 0.0) > 0.0:
            w = empirical_company_prior["weight"]
            p0_cond = (1.0 - w) * p0_cond + w * empirical_company_prior["p_cond"]
            p0_acordo = (1.0 - w) * p0_acordo + w * empirical_company_prior["p_acordo"]
            p0_imp = (1.0 - w) * p0_imp + w * empirical_company_prior["p_imp"]

        # Efeito da proporção de laudos periciais em escala logit (softmax tilting)
        effective_laudo_ratio = float(laudo_ratio) if laudo_ratio > 0 else (1.0 if has_expert_report else 0.0)
        beta_laudo = prior_info["logit_laudo_beta"]
        if effective_laudo_ratio > 0.0 and beta_laudo != 0.0:
            p0_cond, p0_acordo, p0_imp = _tilt_simplex(
                p0_cond, p0_acordo, p0_imp, beta_laudo * effective_laudo_ratio
            )

        julgados = float(cond) + float(acordo) + float(imp)
        total_motivo = julgados + float(open_count)
        censoring_rate = (float(open_count) / total_motivo) if total_motivo > 0 else 0.0

        # Parâmetros posteriores da Dirichlet(alpha_cond, alpha_acordo, alpha_imp)
        alpha_cond = self.alpha_prior * p0_cond + float(cond)
        alpha_acordo = self.alpha_prior * p0_acordo + float(acordo)
        alpha_imp = self.alpha_prior * p0_imp + float(imp)
        alpha_sum = alpha_cond + alpha_acordo + alpha_imp

        theta_cond = alpha_cond / alpha_sum
        theta_acordo = alpha_acordo / alpha_sum
        theta_imp = alpha_imp / alpha_sum

        prob_cond_pct, prob_acordo_pct, prob_vencer_pct = _round_simplex_pct(
            theta_cond, theta_acordo, theta_imp
        )

        # Desconto monotônico de censura à direita:
        # Apenas o tamanho amostral observado (julgados) sofre desconto de até 25% conforme a fração de abertos,
        # garantindo que (a_eff + b_eff) = alpha_prior + n_eff >= alpha_prior seja estritamente crescente em julgados.
        n_eff = julgados * (1.0 - 0.25 * censoring_rate)
        concentration_eff = self.alpha_prior + n_eff
        a_eff = max(0.25, theta_cond * concentration_eff)
        b_eff = max(0.25, (1.0 - theta_cond) * concentration_eff)

        lo_90 = beta_ppf(0.05, a_eff, b_eff)
        hi_90 = beta_ppf(0.95, a_eff, b_eff)
        ic_width_pp = round((hi_90 - lo_90) * 100.0, 1)

        # Precisão derivada diretamente da largura do IC 90% (1 - largura_intervalo)
        confidence_pct = round(max(5.0, min(98.0, (1.0 - (hi_90 - lo_90)) * 100.0)), 1)

        if ic_width_pp <= 22.0 and julgados >= 25:
            confidence_level = "Alta"
        elif ic_width_pp <= 40.0 and julgados >= 6:
            confidence_level = "Média"
        else:
            confidence_level = "Baixa (Amostra Inicial)"

        # Diagnóstico baseado na distribuição tripartida (Vitória vs Acordo vs Condenação)
        if julgados == 0:
            diagnostico_vitoria = "Prior Basal do TRT (Sem julgados anteriores na empresa)"
        elif prob_vencer_pct >= 50.0:
            diagnostico_vitoria = "Alta probabilidade de improcedência (êxito)"
        elif prob_cond_pct >= 55.0:
            diagnostico_vitoria = "Risco elevado de condenação"
        elif prob_acordo_pct >= 40.0:
            diagnostico_vitoria = "Forte tendência conciliatória (acordo)"
        else:
            diagnostico_vitoria = "Equilíbrio / Risco moderado"

        expected_open_cond = round(open_count * theta_cond, 2)
        expected_open_acordos = round(open_count * theta_acordo, 2)

        return {
            "motivo": subject_name,
            "codigo_tpu": safe_int(subject_code),
            "processos_abertos": int(open_count),
            "processos_julgados": int(round(julgados)),
            "probabilidade_condenacao_pct": prob_cond_pct,
            "probabilidade_acordo_pct": prob_acordo_pct,
            "probabilidade_vencer_pct": prob_vencer_pct,
            "ic_90_inferior_pct": round(lo_90 * 100.0, 1),
            "ic_90_superior_pct": round(hi_90 * 100.0, 1),
            "largura_ic_90_pp": ic_width_pp,
            "condenacoes_esperadas_abertos": expected_open_cond,
            "acordos_esperados_abertos": expected_open_acordos,
            "taxa_censura_abertos_pct": round(censoring_rate * 100.0, 1),
            "proporcao_laudo_pericial_pct": round(effective_laudo_ratio * 100.0, 1),
            "diagnostico_chance_vitoria": diagnostico_vitoria,
            "confianca_previsao_pct": confidence_pct,
            "nivel_confianca": confidence_level,
            "possui_laudo_pericial": effective_laudo_ratio > 0.0,
            "pbase_tribunal_pct": round(prior_info["pbase_cond"] * 100.0, 1),
        }

    def evaluate_temporal_backtest(
        self,
        processes: List[Dict[str, Any]],
        split_ratio: float = 0.7,
    ) -> Optional[Dict[str, Any]]:
        """
        Executa backtest temporal (out-of-time validation) sobre os processos julgados:
        - Ordena processos julgados por data (dataHoraUltimaAtualizacao / data_publicacao).
        - Estima as probabilidades posteriores no conjunto de treino (até t_split).
        - Avalia o Brier Score multiclasse (Condenação, Acordo, Improcedência) e binário no conjunto de teste.
        """
        judged = [
            p for p in processes
            if p.get("sentence_outcome") in ("condenacoes", "acordos", "improcedentes")
        ]
        if len(judged) < 6:
            return None

        judged_sorted = sorted(
            judged,
            key=lambda p: str(p.get("dataHoraUltimaAtualizacao") or p.get("data_publicacao") or ""),
        )
        split_idx = max(3, int(len(judged_sorted) * split_ratio))
        if split_idx >= len(judged_sorted):
            return None

        train_procs = judged_sorted[:split_idx]
        test_procs = judged_sorted[split_idx:]

        train_metrics = self.generate_dashboard_metrics("Backtest_Train", train_procs, _run_backtest=False)
        preds_by_subject = {
            normalize_subject_text(item["motivo"]): item
            for item in train_metrics.get("plus_previsao_condenacao", [])
        }

        brier_binary_sum = 0.0
        brier_multiclass_sum = 0.0
        for p in test_procs:
            sub = p.get("primary_subject") or (p.get("subjects") or ["Outros"])[0]
            norm_sub = normalize_subject_text(sub)
            pred = preds_by_subject.get(norm_sub)
            if pred:
                p_cond = pred["probabilidade_condenacao_pct"] / 100.0
                p_acordo = pred["probabilidade_acordo_pct"] / 100.0
                p_imp = pred["probabilidade_vencer_pct"] / 100.0
            else:
                prior = self.get_subject_baseline_prior(sub, p.get("primary_subject_code"), p.get("tribunal"))
                p_cond, p_acordo, p_imp = prior["pbase_cond"], prior["pbase_acordo"], prior["pbase_imp"]

            outcome = p.get("sentence_outcome")
            y_cond = 1.0 if outcome == "condenacoes" else 0.0
            y_acordo = 1.0 if outcome == "acordos" else 0.0
            y_imp = 1.0 if outcome == "improcedentes" else 0.0

            brier_binary_sum += (p_cond - y_cond) ** 2
            brier_multiclass_sum += ((p_cond - y_cond) ** 2 + (p_acordo - y_acordo) ** 2 + (p_imp - y_imp) ** 2) / 3.0

        n_test = len(test_procs)
        brier_score = round(brier_binary_sum / n_test, 4)
        brier_multi = round(brier_multiclass_sum / n_test, 4)
        return {
            "amostra_treino": len(train_procs),
            "amostra_teste": n_test,
            "brier_score_condenacao": brier_score,
            "brier_score_multiclasse": brier_multi,
            "calibracao": "Adequada" if brier_score < 0.25 else "Requer mais dados",
        }

    def generate_dashboard_metrics(
        self,
        company_name: str,
        processes: List[Dict[str, Any]],
        _run_backtest: bool = True,
    ) -> Dict[str, Any]:
        """
        Gera o conjunto completo de métricas executivas com consistência matemática:
        - Conservação estrita de massa amostral por motivo (julgados = cond + acordo + imp = inteiro exato).
        - Em processos de procedência parcial com múltiplos assuntos (k > 1), reparte (0.85 cond, 0.15 imp)
          para preservar a soma 1.0 por processo julgado sem perder massa amostral.
        - Shrinkage Empirical Bayes Leave-One-Subject-Out (sem dupla contagem).
        - Score de Risco de Terceirização (Súmula 331 TST) em fórmula única convexa (65% adverso + 20% rescisórias + 15% Súmula 331/Execução).
        """
        total = len(processes)
        outcomes = {
            "condenacoes": 0,
            "acordos": 0,
            "improcedentes": 0,
            "em_aberto": 0,
        }
        detailed_outcomes = {
            "procedencia_total": 0,
            "procedencia_parcial": 0,
            "acordos": 0,
            "improcedentes": 0,
            "extintos_sem_merito": 0,
            "em_aberto": 0,
        }

        if total == 0:
            return {
                "empresa": company_name,
                "total_processos": 0,
                "ranking_motivos": [],
                "desfechos_sentencas": outcomes,
                "desfechos_detalhados": detailed_outcomes,
                "niveis_sigilo": {},
                "taxa_sigilo_pct": 0.0,
                "score_risco_terceirizacao": 0.0,
                "probabilidade_condenacao_geral_pct": None,
                "taxa_historica_condenacao_pct": None,
                "plus_previsao_condenacao": [],
                "alerta_sumula_331_tst": False,
            }

        reasons_counter: Counter = Counter()
        canonical_display_name: Dict[str, str] = {}
        secrecy_levels: Counter = Counter()
        tribunal_counter: Counter = Counter()

        history_by_reason: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "cond": 0.0,
            "acordo": 0.0,
            "imp": 0.0,
            "laudo_count": 0,
            "total_count": 0,
            "open_count": 0,
            "code": None,
            "tribunals": Counter(),
        })

        sumula_331_direct_count = 0
        rescisorias_fgts_count = 0

        for p in processes:
            raw_sigilo = p.get("nivel_sigilo") if p.get("nivel_sigilo") is not None else p.get("nivelSigilo")
            sigilo = safe_int(raw_sigilo, default=0) or 0
            secrecy_levels[sigilo] += 1

            trib = str(p.get("tribunal") or "").strip().lower()
            if trib:
                tribunal_counter[trib] += 1

            raw_status = str(p.get("sentence_outcome") or "em_aberto").strip().lower()
            sub_outcome = str(p.get("sentence_sub_outcome") or "").strip().lower()

            if raw_status == "condenacoes":
                outcomes["condenacoes"] += 1
                if sub_outcome == "procedencia_parcial":
                    detailed_outcomes["procedencia_parcial"] += 1
                else:
                    detailed_outcomes["procedencia_total"] += 1
                eff_status = "condenacoes"
            elif raw_status == "acordos":
                outcomes["acordos"] += 1
                detailed_outcomes["acordos"] += 1
                eff_status = "acordos"
            elif raw_status == "improcedentes":
                outcomes["improcedentes"] += 1
                detailed_outcomes["improcedentes"] += 1
                eff_status = "improcedentes"
            elif raw_status in ("extintos_sem_merito", "baixa_arquivado", "arquivado", "desistencia"):
                detailed_outcomes["extintos_sem_merito"] += 1
                eff_status = "extintos_sem_merito"
            elif raw_status == "em_aberto":
                outcomes["em_aberto"] += 1
                detailed_outcomes["em_aberto"] += 1
                eff_status = "em_aberto"
            else:
                logger.warning(
                    "Desfecho processual desconhecido '%s' no processo %s; classificado como em_aberto.",
                    raw_status,
                    p.get("numero_processo"),
                )
                outcomes["em_aberto"] += 1
                detailed_outcomes["em_aberto"] += 1
                eff_status = "em_aberto"

            raw_subjects = p.get("subjects")
            if isinstance(raw_subjects, list) and raw_subjects:
                subj_list = [str(s).strip() for s in raw_subjects if str(s).strip()]
            else:
                primary = str(p.get("primary_subject") or "Outros Motivos Trabalhistas").strip()
                subj_list = [primary]

            if not subj_list:
                subj_list = ["Outros Motivos Trabalhistas"]

            # Remove duplicatas no mesmo processo preservando a ordem
            unique_subjects = list(dict.fromkeys(subj_list))
            is_multi_subject = len(unique_subjects) > 1
            reason_code = safe_int(p.get("primary_subject_code"))

            has_sumula_331_in_proc = False
            has_rescisoria_in_proc = False

            for idx, reason_label in enumerate(unique_subjects):
                code_for_sub = reason_code if idx == 0 else None
                norm_key = f"tpu:{code_for_sub}" if code_for_sub is not None else normalize_subject_text(reason_label)
                if norm_key not in canonical_display_name:
                    canonical_display_name[norm_key] = reason_label

                reasons_counter[norm_key] += 1
                r_stat = history_by_reason[norm_key]
                if code_for_sub is not None and r_stat["code"] is None:
                    r_stat["code"] = code_for_sub
                r_stat["total_count"] += 1
                if trib:
                    r_stat["tribunals"][trib] += 1

                # Cada processo julgado contribui com massa total exatamente 1.0 no assunto:
                # Se for procedência parcial em processo com múltiplos pedidos (k > 1),
                # reparte (0.85 cond, 0.15 imp) para refletir que nem todos os pedidos foram acolhidos,
                # mantendo cond + acordo + imp == 1.0 por processo julgado.
                if eff_status == "condenacoes":
                    if sub_outcome == "procedencia_parcial" and is_multi_subject:
                        r_stat["cond"] += 0.85
                        r_stat["imp"] += 0.15
                    else:
                        r_stat["cond"] += 1.0
                elif eff_status == "acordos":
                    r_stat["acordo"] += 1.0
                elif eff_status == "improcedentes":
                    r_stat["imp"] += 1.0
                elif eff_status == "em_aberto":
                    r_stat["open_count"] += 1

                if p.get("has_expert_report"):
                    r_stat["laudo_count"] += 1

                norm_text = normalize_subject_text(reason_label)
                if any(k in norm_text for k in ("terceiriz", "subsidiar", "331", "execuc", "sisbajud", "penhora", "bloqueio")):
                    has_sumula_331_in_proc = True
                if any(k in norm_text for k in ("rescis", "fgts", "477", "467", "salari", "aviso")):
                    has_rescisoria_in_proc = True

            if has_sumula_331_in_proc:
                sumula_331_direct_count += 1
            if has_rescisoria_in_proc:
                rescisorias_fgts_count += 1

        ranking = [
            {
                "motivo": canonical_display_name[norm_key],
                "codigo_tpu": history_by_reason[norm_key]["code"],
                "quantidade": qtd,
                "porcentagem": round((qtd / total) * 100.0, 1),
            }
            for norm_key, qtd in reasons_counter.most_common()
        ]

        secret_processes = sum(qtd for lvl, qtd in secrecy_levels.items() if int(lvl) > 0)
        secrecy_rate = round(secret_processes / total, 4)

        julgados_totais = outcomes["condenacoes"] + outcomes["acordos"] + outcomes["improcedentes"]

        # Previsões Bayesianas por motivo com Empirical Bayes Leave-One-Subject-Out
        predictions = []
        for norm_key, stats in history_by_reason.items():
            dominant_trib = stats["tribunals"].most_common(1)[0][0] if stats["tribunals"] else None
            laudo_ratio = stats["laudo_count"] / max(stats["total_count"], 1)

            subj_julgados = stats["cond"] + stats["acordo"] + stats["imp"]
            other_julgados = max(0.0, float(julgados_totais) - subj_julgados)
            eb_prior = None
            if other_julgados >= 1.0:
                other_cond = max(0.0, float(outcomes["condenacoes"]) - stats["cond"])
                other_acordo = max(0.0, float(outcomes["acordos"]) - stats["acordo"])
                other_imp = max(0.0, float(outcomes["improcedentes"]) - stats["imp"])
                other_sum = other_cond + other_acordo + other_imp
                if other_sum > 0:
                    eb_prior = {
                        "weight": other_sum / (other_sum + 10.0),
                        "p_cond": other_cond / other_sum,
                        "p_acordo": other_acordo / other_sum,
                        "p_imp": other_imp / other_sum,
                    }

            pred = self.calculate_prediction_for_subject(
                subject_name=canonical_display_name[norm_key],
                subject_code=stats["code"],
                cond=stats["cond"],
                acordo=stats["acordo"],
                imp=stats["imp"],
                open_count=stats["open_count"],
                laudo_ratio=laudo_ratio,
                has_expert_report=stats["laudo_count"] > 0,
                tribunal=dominant_trib,
                empirical_company_prior=eb_prior,
                secrecy_rate=secrecy_rate,
            )
            predictions.append(pred)

        predictions.sort(key=lambda x: (x["processos_abertos"], x["probabilidade_condenacao_pct"]), reverse=True)

        # Taxa histórica empírica de condenação (para exibição do histórico factual)
        taxa_condenacao_empirica_pct = (
            round((outcomes["condenacoes"] / julgados_totais) * 100.0, 1)
            if julgados_totais > 0
            else None
        )

        # Posterior global de condenação e de desfecho adverso da empresa (com shrinkage Bayesiano)
        posterior_cond_rate = (
            outcomes["condenacoes"] + self.alpha_prior * DEFAULT_TRT_BASE_PROBABILITY
        ) / (julgados_totais + self.alpha_prior)

        posterior_adverse_rate = (
            outcomes["condenacoes"]
            + 0.50 * outcomes["acordos"]
            + self.alpha_prior * (DEFAULT_TRT_BASE_PROBABILITY + 0.50 * DEFAULT_TRT_ACORDO_PROBABILITY)
        ) / (julgados_totais + self.alpha_prior)

        # Fórmula Única Convexa para o Score de Risco de Terceirização (Súmula 331 TST) [0 a 100]:
        # - 65% Taxa posterior de desfecho adverso (condenação + 0.5 * acordo com shrinkage)
        # - 20% Proporção de inadimplência rescisória / FGTS / Art. 477 CLT
        # - 15% Incidência direta de Terceirização (Súmula 331) / Execução / Bloqueio Judicial
        taxa_rescisorias = rescisorias_fgts_count / total
        taxa_sumula_331_exec = sumula_331_direct_count / total

        overall_risk_score = round(
            min(
                100.0,
                max(
                    5.0,
                    (
                        posterior_adverse_rate * 65.0
                        + taxa_rescisorias * 20.0
                        + taxa_sumula_331_exec * 15.0
                    ),
                ),
            ),
            1,
        )

        # Probabilidade geral de condenação reportada com shrinkage para amostras pequenas
        probabilidade_condenacao_geral_pct = (
            round(posterior_cond_rate * 100.0, 1)
            if julgados_totais > 0
            else overall_risk_score
        )

        backtest = self.evaluate_temporal_backtest(processes) if _run_backtest else None

        return {
            "empresa": company_name,
            "total_processos": total,
            "ranking_motivos": ranking,
            "desfechos_sentencas": outcomes,
            "desfechos_detalhados": detailed_outcomes,
            "niveis_sigilo": dict(secrecy_levels),
            "taxa_sigilo_pct": round(secrecy_rate * 100.0, 1),
            "score_risco_terceirizacao": overall_risk_score,
            "probabilidade_condenacao_geral_pct": probabilidade_condenacao_geral_pct,
            "taxa_historica_condenacao_pct": taxa_condenacao_empirica_pct,
            "plus_previsao_condenacao": predictions,
            "alerta_sumula_331_tst": overall_risk_score >= 60.0 or taxa_rescisorias >= 0.35,
            "backtest_temporal": backtest,
        }
