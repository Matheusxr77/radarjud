import math
import logging
from typing import Dict, List, Any
from app.services.tpu_constants import (
    TPU_SUBJECT_BASELINES,
    DEFAULT_TRT_BASE_PROBABILITY,
    BAYESIAN_ALPHA_PRIOR,
    EXPERT_REPORT_RISK_ADJUSTMENT,
)

logger = logging.getLogger(__name__)


class BayesianRiskPredictor:
    """
    Motor Bayesiano Empírico de Previsão de Risco e Confiança Estatística (Módulo PLUS).
    Calcula:
    1. Probabilidade Prevista de Condenação Pcond(m) por Motivo TPU
    2. Grau de Confiança Estatística Conf(%)
    3. Score Geral de Risco de Terceirização (Súmula 331 TST)
    """

    def __init__(self, alpha_prior: float = BAYESIAN_ALPHA_PRIOR):
        self.alpha_prior = alpha_prior

    def get_subject_baseline_probability(self, subject_name: str, subject_code: Any = None) -> float:
        """
        Retorna a taxa basal nacional/regional do tribunal Pbase_TRT(m)
        para o assunto TPU especificado.
        """
        if subject_code and int(subject_code) in TPU_SUBJECT_BASELINES:
            return TPU_SUBJECT_BASELINES[int(subject_code)]["pbase"]

        # Busca por aproximação no nome do assunto se o código não for encontrado diretamente
        norm_name = subject_name.lower()
        if "rescis" in norm_name or "fgts" in norm_name or "477" in norm_name or "467" in norm_name:
            return 0.79
        if "hora" in norm_name or "jornada" in norm_name or "intervalo" in norm_name:
            return 0.68
        if "insalubr" in norm_name or "periculos" in norm_name:
            return 0.60
        if "moral" in norm_name or "assédio" in norm_name:
            return 0.42
        if "terceiriz" in norm_name or "subsidiár" in norm_name:
            return 0.76

        return DEFAULT_TRT_BASE_PROBABILITY

    def calculate_prediction_for_subject(
        self,
        subject_name: str,
        subject_code: Any,
        cond: int,
        acordo: int,
        imp: int,
        open_count: int,
        has_expert_report: bool,
        meta_completeness: float = 0.95,
        secrecy_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Implementa a Fórmula 2 e Fórmula 3 da especificação:
        Pcond(m) = [ (Ncond + 0.65 * Nacordo + alpha * Pbase) / (Njulgados + alpha) ] + Delta_laudo
        Conf(%) = min(98%, [ (1 - 1 / sqrt(1 + n)) * 0.70 + Cmeta * 0.20 + (1 - Taxa_Sigilo) * 0.10 ] * 100)
        """
        julgados = cond + acordo + imp
        pbase = self.get_subject_baseline_probability(subject_name, subject_code)

        # Ajuste de laudo pericial (Delta_laudo)
        delta_laudo = EXPERT_REPORT_RISK_ADJUSTMENT if has_expert_report else 0.0

        # Cálculo da probabilidade prevista Pcond(m)
        numerador = cond + (0.65 * acordo) + (self.alpha_prior * pbase)
        denominador = julgados + self.alpha_prior
        raw_prob = (numerador / denominador) + delta_laudo

        # Limites operacionais de probabilidade: entre 5% e 98%
        prob_pct = round(max(5.0, min(98.0, raw_prob * 100)), 1)

        # Cálculo da confiança estatística Conf(%)
        # n = Njulgados
        n = julgados
        term_volume = 1.0 - (1.0 / math.sqrt(1.0 + n))
        term_meta = meta_completeness
        term_visibility = 1.0 - min(1.0, max(0.0, secrecy_rate))

        conf_factor = (term_volume * 0.70) + (term_meta * 0.20) + (term_visibility * 0.10)
        confidence_pct = round(min(98.0, max(15.0, conf_factor * 100)), 1)

        # Classificação qualitativa da confiança
        if confidence_pct >= 85.0:
            confidence_level = "Alta"
        elif confidence_pct >= 60.0:
            confidence_level = "Média"
        else:
            confidence_level = "Baixa (Amostra Inicial)"

        return {
            "motivo": subject_name,
            "codigo_tpu": subject_code,
            "processos_abertos": open_count,
            "processos_julgados": julgados,
            "probabilidade_condenacao_pct": prob_pct,
            "confianca_previsao_pct": confidence_pct,
            "nivel_confianca": confidence_level,
            "possui_laudo_pericial": has_expert_report,
            "pbase_tribunal_pct": round(pbase * 100, 1),
        }

    def generate_dashboard_metrics(
        self,
        company_name: str,
        processes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Gera o conjunto completo de métricas executivas:
        - Totais e contadores
        - Ranking percentual por motivo TPU
        - Desfechos de sentenças (Condenações, Acordos, Improcedentes, Em Aberto)
        - Distribuição de níveis de sigilo
        - Módulo PLUS: Previsões de condenação e confiança por motivo
        - Score consolidado de risco para terceirização
        """
        total = len(processes)
        from collections import Counter, defaultdict

        reasons_counter = Counter()
        reasons_code_map = {}
        outcomes = {
            "condenacoes": 0,
            "acordos": 0,
            "improcedentes": 0,
            "em_aberto": 0,
        }
        secrecy_levels = Counter()
        history_by_reason = defaultdict(lambda: {
            "cond": 0,
            "acordo": 0,
            "imp": 0,
            "laudo": False,
            "open": 0,
            "code": None
        })

        for p in processes:
            reason = p.get("primary_subject", "Não Informado")
            reason_code = p.get("primary_subject_code")
            reasons_counter[reason] += 1
            if reason_code and reason not in reasons_code_map:
                reasons_code_map[reason] = reason_code

            sigilo = int(p.get("nivelSigilo", 0))
            secrecy_levels[sigilo] += 1

            status = p.get("sentence_outcome", "em_aberto")
            if status not in outcomes:
                status = "em_aberto"
            outcomes[status] += 1

            # Atualiza histórico por motivo
            reason_stat = history_by_reason[reason]
            reason_stat["code"] = reason_code or reasons_code_map.get(reason)

            if status == "condenacoes":
                reason_stat["cond"] += 1
            elif status == "acordos":
                reason_stat["acordo"] += 1
            elif status == "improcedentes":
                reason_stat["imp"] += 1
            else:
                reason_stat["open"] += 1

            if p.get("has_expert_report"):
                reason_stat["laudo"] = True

        # Ranking percentual dos motivos mais frequentes
        ranking = [
            {
                "motivo": mot,
                "codigo_tpu": reasons_code_map.get(mot),
                "quantidade": qtd,
                "porcentagem": round((qtd / max(total, 1)) * 100, 1),
            }
            for mot, qtd in reasons_counter.most_common()
        ]

        # Taxa de sigilo global
        secret_processes = sum(qtd for lvl, qtd in secrecy_levels.items() if lvl > 0)
        secrecy_rate = (secret_processes / max(total, 1))

        # Módulo PLUS: Previsões de condenação por motivo
        predictions = []
        for mot, stats in history_by_reason.items():
            pred = self.calculate_prediction_for_subject(
                subject_name=mot,
                subject_code=stats["code"],
                cond=stats["cond"],
                acordo=stats["acordo"],
                imp=stats["imp"],
                open_count=stats["open"],
                has_expert_report=stats["laudo"],
                meta_completeness=0.95,
                secrecy_rate=secrecy_rate,
            )
            predictions.append(pred)

        # Ordena previsões por quantidade de processos abertos (prioridade de ação)
        predictions.sort(key=lambda x: (x["processos_abertos"], x["probabilidade_condenacao_pct"]), reverse=True)

        # Cálculo do Score Geral de Risco (0 a 100)
        # Pondera: Taxa histórica de condenação (50%), Verbas rescisórias no ranking (30%), Sigilo (20%)
        julgados_totais = outcomes["condenacoes"] + outcomes["acordos"] + outcomes["improcedentes"]
        taxa_condenacao_historica = (
            (outcomes["condenacoes"] + 0.65 * outcomes["acordos"]) / max(julgados_totais, 1)
        ) if julgados_totais > 0 else 0.60

        # Percentual de processos rescisórios / FGTS
        rescisorias_qtd = sum(
            qtd for mot, qtd in reasons_counter.items()
            if any(term in mot.lower() for term in ["rescis", "fgts", "477", "salário", "aviso"])
        )
        taxa_rescisorias = (rescisorias_qtd / max(total, 1))

        overall_risk_score = round(
            min(100.0, max(0.0, (
                (taxa_condenacao_historica * 50) +
                (taxa_rescisorias * 30) +
                (secrecy_rate * 20)
            ) * 100 / 100)),
            1
        )

        return {
            "empresa": company_name,
            "total_processos": total,
            "ranking_motivos": ranking,
            "desfechos_sentencas": outcomes,
            "niveis_sigilo": dict(secrecy_levels),
            "taxa_sigilo_pct": round(secrecy_rate * 100, 1),
            "score_risco_terceirizacao": overall_risk_score,
            "plus_previsao_condenacao": predictions,
            "alerta_sumula_331_tst": overall_risk_score > 65.0,
        }

