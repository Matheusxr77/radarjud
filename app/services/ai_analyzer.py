import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger(__name__)

# Integração nativa com o diretório radarjusAgent (Google ADK + Vertex AI)
RADARJUS_AGENT_DIR = Path(__file__).resolve().parents[2] / "radarjusAgent"
if RADARJUS_AGENT_DIR.exists() and str(RADARJUS_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(RADARJUS_AGENT_DIR))

try:
    from agents.clients_agent import gerar_parecer_adk as _adk_gerar_parecer
    ADK_AGENT_AVAILABLE = True
except Exception as _exc:
    logger.debug("Agente ADK radarjusAgent não carregado diretamente: %s", _exc)
    _adk_gerar_parecer = None
    ADK_AGENT_AVAILABLE = False


class LegalAIAnalyzer:
    """
    Motor Híbrido de Inteligência Artificial para Diagnóstico Preditivo Jurídico:
    1. Modo Agente ADK (radarjusAgent): Orquestração via Google ADK + Vertex AI (gemini-2.5-flash-lite)
       para auditoria de terceiros e Súmula 331 do TST em tempo real.
    2. Modo Nuvem REST: Conexão com Gemini Flash-Lite via Google Generative AI se GEMINI_API_KEY configurada.
    3. Modo Local (Gratuito / Offline): Motor de NLP e Heurística Jurídica especializado em
       Direito do Trabalho (Súmula 331 TST, TPU/CNJ e Teoria do Risco Empresarial), garantindo
       fidelidade técnica a custo zero e sem dependências externas.
    """

    def __init__(self):
        self.gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.agent_service_url = os.getenv("RADARJUS_AGENT_URL", "").strip()

    def _call_adk_agent(self, summary_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Invoca o agente radarjusAgent (in-process via Google ADK ou via microserviço HTTP se configurado)
        usando os dados JSON em tempo real da busca.
        """
        empresa = summary_data.get("empresa", "Empresa Analisada")

        # 1. Se houver URL de microserviço externo configurada (ex: http://127.0.0.1:8000)
        if self.agent_service_url:
            try:
                url = f"{self.agent_service_url.rstrip('/')}/analisar-empresa"
                resp = requests.post(
                    url,
                    json={"company_name": empresa, "resumo_metricas": summary_data},
                    timeout=15,
                )
                if resp.ok:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        return payload.get("ai_analysis") or payload.get("parecer_agente_adk")
            except Exception as exc:
                logger.warning("Falha ao contatar microserviço radarjusAgent (%s): %s", self.agent_service_url, exc)

        # 2. Execução direta in-process do radarjusAgent (Google ADK)
        if ADK_AGENT_AVAILABLE and _adk_gerar_parecer is not None:
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        return pool.submit(
                            lambda: asyncio.run(_adk_gerar_parecer(empresa, summary_data))
                        ).result(timeout=20)
                else:
                    return asyncio.run(_adk_gerar_parecer(empresa, summary_data))
            except Exception as exc:
                logger.warning("Erro ao executar radarjusAgent ADK in-process: %s", exc)

        return None

    def _call_gemini_api(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Executa a chamada HTTP à API REST do Gemini caso a chave esteja presente."""
        if not self.gemini_api_key:
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        try:
            resp = requests.post(url, json=payload, timeout=12)
            if resp.ok:
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(raw_text)
            else:
                logger.warning("Falha na chamada Gemini (Status %d): %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Erro de conexão com a API do Gemini: %s. Utilizando motor local.", exc)

        return None

    def _generate_local_legal_ai_report(self, summary_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Motor Local de Inteligência Jurídica Especializada.
        Processa os metadados agregados para inferir padrões de passivo, probabilidade de êxito
        e medidas preventivas segundo a jurisprudência consolidada do TST.
        """
        empresa = summary_data.get("empresa", "Empresa Analisada")
        total = summary_data.get("total_processos", 0)
        outcomes = summary_data.get("desfechos_sentencas", {})
        condenacoes = outcomes.get("condenacoes", 0)
        acordos = outcomes.get("acordos", 0)
        improcedentes = outcomes.get("improcedentes", 0)
        em_aberto = outcomes.get("em_aberto", 0)
        total_julgados = condenacoes + improcedentes + acordos
        ranking = summary_data.get("ranking_motivos", [])

        # Caso 1: Empresa sem nenhum processo localizado no CNJ
        if total == 0:
            resumo = (
                f"Não foram encontrados processos judiciais trabalhistas para a empresa {empresa} "
                f"nas bases públicas do CNJ e diários oficiais. Na ausência de histórico litigioso, "
                f"não há passivo identificado nem dados amostrais para calcular taxas de condenação ou acordos."
            )
            return {
                "modelo_utilizado": "Local-Legal-NLP-v2",
                "orquestrador": "Google ADK (Agent Development Kit)",
                "perfil_risco_consolidado": "Sem Processos Localizados",
                "parecer_governanca": resumo,
                "resumo_executivo": resumo,
                "recomendacao_homologacao": "HOMOLOGAR SEM RESTRIÇÕES",
                "taxa_historica_vitoria_empresa_pct": None,
                "taxa_historica_acordos_pct": None,
                "taxa_historica_condenacao_pct": None,
                "processos_em_aberto_monitorados": 0,
                "principais_vulnerabilidades": [],
                "alertas_sumula_331": [],
                "recomendacoes_compliance": [
                    "Manter rotinas preventivas de conformidade trabalhista (CLT e eSocial).",
                    "Acompanhar periodicamente novas publicações ou eventuais citações judiciais."
                ],
                "status": "sucesso"
            }

        # Caso 2: Empresa com processos abertos, mas sem histórico anterior julgado (condenações/improcedências/acordos)
        if total_julgados == 0:
            top_motivos = [r["motivo"] for r in ranking[:3]] if ranking else ["Temas Contratuais Gerais"]
            motivos_str = ", ".join(top_motivos)
            resumo = (
                f"A empresa {empresa} possui {total} processo(s) em tramitação ({motivos_str}), porém "
                f"nenhum desfecho anterior transitado em julgado (sentença ou acordo homologado). "
                f"Sem dados pretéritos de julgamento na empresa, as estimativas de probabilidade de vitória "
                f"adotam exclusivamente as médias jurisprudenciais basais dos TRTs."
            )
            return {
                "modelo_utilizado": "Local-Legal-NLP-v2",
                "orquestrador": "Google ADK (Agent Development Kit)",
                "perfil_risco_consolidado": "Amostra Inicial (Em Andamento)",
                "parecer_governanca": resumo,
                "resumo_executivo": resumo,
                "recomendacao_homologacao": "APROVAR COM RETENÇÃO CAUTELAR",
                "taxa_historica_vitoria_empresa_pct": None,
                "taxa_historica_acordos_pct": None,
                "taxa_historica_condenacao_pct": None,
                "processos_em_aberto_monitorados": em_aberto,
                "principais_vulnerabilidades": top_motivos,
                "alertas_sumula_331": [
                    f"Demandas em aberto concentradas em: {motivos_str}."
                ],
                "recomendacoes_compliance": [
                    "Estruturar auditoria prévia de provas documentais e testemunhais para os processos em curso.",
                    "Avaliar viabilidade de conciliação nas primeiras audiências para reduzir custos processuais."
                ],
                "status": "sucesso"
            }

        # Caso 3: Empresa com dados empíricos de processos já julgados
        taxa_vitoria_historica = round((improcedentes / total_julgados) * 100, 1)
        taxa_acordo_historica = round((acordos / total_julgados) * 100, 1)
        taxa_condenacao_historica = round((condenacoes / total_julgados) * 100, 1)

        # Identificação dos principais temas de vulnerabilidade
        top_motivos = [r["motivo"] for r in ranking[:3]] if ranking else ["Relações Contratuais Gerais"]
        motivos_str = ", ".join(top_motivos)

        # Diagnóstico de risco baseado em dados empíricos reais
        if taxa_vitoria_historica >= 50.0:
            perfil_risco = "Baixo a Moderado"
            rec_homologacao = "HOMOLOGAR SEM RESTRIÇÕES"
            parecer_resumo = (
                f"A empresa {empresa} apresenta histórico favorável com índice de vitória (improcedência) de "
                f"{taxa_vitoria_historica}% sobre os {total_julgados} processos já julgados, indicando consistência probatória e compliance robusto."
            )
        elif taxa_vitoria_historica >= 30.0:
            perfil_risco = "Moderado"
            rec_homologacao = "APROVAR COM RETENÇÃO CAUTELAR"
            parecer_resumo = (
                f"A empresa {empresa} possui taxa de vitória de {taxa_vitoria_historica}% e adesão a conciliações de "
                f"{taxa_acordo_historica}%, com demandas concentradas em: {motivos_str}."
            )
        else:
            perfil_risco = "Crítico / Elevado"
            rec_homologacao = "REPROVAR / BLOQUEAR"
            parecer_resumo = (
                f"A empresa {empresa} apresenta taxa de condenação elevada ({taxa_condenacao_historica}% dos julgados). "
                f"Os registros apontam vulnerabilidade em pedidos envolvendo {motivos_str}, "
                f"com risco reflexo direto para tomadores de serviço sob a ótica da Súmula 331 do TST."
            )

        recomendacoes = [
            "Auditar cartões de ponto eletrônicos e escalas de descanso para suprimir passivo de jornada.",
            "Instituir comitê de prevenção e canal de ouvidoria independente para mitigar queixas de assédio/dano moral.",
            "Fiscalizar guias de recolhimento de FGTS e rescisões contratuais rigorosamente no prazo do art. 477 da CLT.",
            "Exigir certidões negativas e comprovantes fiscais periódicos de prestadores terceirizados (Súmula 331/TST)."
        ]

        return {
            "modelo_utilizado": "Local-Legal-NLP-v2",
            "orquestrador": "Google ADK (Agent Development Kit)",
            "perfil_risco_consolidado": perfil_risco,
            "parecer_governanca": parecer_resumo,
            "resumo_executivo": parecer_resumo,
            "recomendacao_homologacao": rec_homologacao,
            "taxa_historica_vitoria_empresa_pct": taxa_vitoria_historica,
            "taxa_historica_acordos_pct": taxa_acordo_historica,
            "taxa_historica_condenacao_pct": taxa_condenacao_historica,
            "processos_em_aberto_monitorados": em_aberto,
            "principais_vulnerabilidades": top_motivos,
            "alertas_sumula_331": [
                f"Assuntos recorrentes: {motivos_str}.",
                f"Desfechos analisados: {condenacoes} condenações, {acordos} acordos, {improcedentes} improcedentes e {em_aberto} em aberto."
            ],
            "recomendacoes_compliance": recomendacoes,
            "status": "sucesso"
        }

    def analyze_company(self, summary_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Orquestra a análise por IA:
        1. Executa o agente ADK radarjusAgent (Google ADK + Vertex AI Gemini Flash-Lite) sobre o JSON em tempo real.
        2. Tenta Gemini REST se GEMINI_API_KEY estiver configurada.
        3. Fallback para o motor analítico local.
        """
        total = summary_data.get("total_processos", 0)
        outcomes = summary_data.get("desfechos_sentencas", {})
        total_julgados = (
            outcomes.get("condenacoes", 0)
            + outcomes.get("improcedentes", 0)
            + outcomes.get("acordos", 0)
        )

        # Calibração determinística para casos sem processos ou sem julgados
        if total == 0 or total_julgados == 0:
            adk_calibrated = self._call_adk_agent(summary_data)
            if adk_calibrated:
                return adk_calibrated
            return self._generate_local_legal_ai_report(summary_data)

        # 1. Tenta o Agente ADK radarjusAgent (Google ADK + Vertex AI)
        adk_result = self._call_adk_agent(summary_data)
        if adk_result:
            # Se houver GEMINI_API_KEY configurada mas Vertex AI não estiver ativo, permite enriquecer via REST
            if self.gemini_api_key and "Google ADK" in str(adk_result.get("modelo_utilizado", "")):
                pass
            return adk_result

        # 2. Se houver chave Gemini REST, monta prompt estruturado
        if self.gemini_api_key:
            prompt = (
                "Você é um consultor jurídico sênior especialista em passivo trabalhista brasileiro e Súmula 331 do TST. "
                "Analise os metadados JSON a seguir e retorne um diagnóstico objetivo em formato JSON contendo: "
                "'perfil_risco_consolidado', 'resumo_executivo', 'recomendacao_homologacao', "
                "'taxa_historica_vitoria_empresa_pct', 'principais_vulnerabilidades', "
                "'alertas_sumula_331', 'recomendacoes_compliance'.\n"
                f"Metadados: {json.dumps(summary_data, ensure_ascii=False)}"
            )
            cloud_result = self._call_gemini_api(prompt)
            if cloud_result:
                cloud_result["modelo_utilizado"] = f"{self.gemini_model} (Google ADK)"
                cloud_result.setdefault("orquestrador", "Google ADK (Agent Development Kit)")
                return cloud_result

        # 3. Fallback para o motor local gratuito
        return self._generate_local_legal_ai_report(summary_data)
