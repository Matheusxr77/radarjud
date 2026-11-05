import re
import logging
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional

from app.services.cnj_client import CNJClient
from app.services.tpu_constants import (
    TPU_OUTCOME_CODES,
    TPU_EXPERT_REPORT_CODES,
    DEFAULT_TRT_BASE_PROBABILITY,
)

logger = logging.getLogger(__name__)


class CNJMetadataOrchestrator:
    """
    IA Orquestradora:
    Cruza as APIs abertas do Comunica PJe e do DataJud CNJ.
    Executa NLP de desfecho sobre textos de sentenças e agrupamento semântico de códigos TPU.
    """

    # Expressões regulares para classificação jurídica (NLP Leve de Sentenças)
    REGEX_PROCEDENCIA = re.compile(
        r"\b(julgo\s+procedente|acolho\s+os\s+pedidos|declarar\s+a\s+proced[êe]ncia|condenar\s+a\s+reclamada)\b",
        re.IGNORECASE,
    )
    REGEX_PROCEDENCIA_PARTE = re.compile(
        r"\b(julgo\s+parcialmente\s+procedente|acolho\s+em\s+parte|proced[êe]ncia\s+em\s+parte|condena-\se\s+em\s+parte)\b",
        re.IGNORECASE,
    )
    REGEX_IMPROCEDENCIA = re.compile(
        r"\b(julgo\s+improcedente|rejeito\s+os\s+pedidos|declarar\s+a\s+improced[êe]ncia|absolvo\s+a\s+reclamada)\b",
        re.IGNORECASE,
    )
    REGEX_ACORDO = re.compile(
        r"\b(homolog(o|ar|a-se)\s+(o\s+)?acordo|concilia[çc][ãa]o\s+homologada|transa[çc][ãa]o\s+homologada)\b",
        re.IGNORECASE,
    )
    REGEX_LAUDO_PERICIAL = re.compile(
        r"\b(laudo\s+pericial|per[íi]cia\s+t[ée]cnica|perito\s+(judicial|nomeado)|quesitos\s+periciais|conclus[ãa]o\s+do\s+laudo)\b",
        re.IGNORECASE,
    )

    def __init__(self, cnj_client: Optional[CNJClient] = None):
        self.client = cnj_client or CNJClient()

    def classify_text_decision(self, text: str) -> Optional[str]:
        """
        Analisa o texto publicado da sentença/despacho no Comunica PJe para inferir o desfecho.
        Retorna: 'condenacao', 'improcedente', 'acordo' ou None.
        """
        if not text:
            return None

        if self.REGEX_ACORDO.search(text):
            return "acordos"
        if self.REGEX_PROCEDENCIA_PARTE.search(text) or self.REGEX_PROCEDENCIA.search(text):
            return "condenacoes"
        if self.REGEX_IMPROCEDENCIA.search(text):
            return "improcedentes"

        return None

    def detect_expert_report_in_text(self, text: str) -> bool:
        """Verifica se o texto da publicação menciona perícia técnica ou juntada de laudo pericial."""
        if not text:
            return False
        return bool(self.REGEX_LAUDO_PERICIAL.search(text))

    def parse_datajud_movements(self, movements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analisa a árvore de movimentos do DataJud utilizando códigos TPU:
        - Códigos 219, 221 -> Condenações
        - Código 220 -> Improcedência
        - Código 466 -> Acordo Homologado
        - Códigos 83, 11383 -> Prova Pericial Técnica / Laudo
        """
        outcome = "em_aberto"
        has_expert_report = False

        # Itera em ordem cronológica reversa (movimentos mais recentes primeiro se ordenados)
        for mov in movements:
            code = mov.get("codigo")
            if not code:
                continue

            if code in TPU_EXPERT_REPORT_CODES:
                has_expert_report = True

            if code in TPU_OUTCOME_CODES and outcome == "em_aberto":
                category = TPU_OUTCOME_CODES[code]["categoria"]
                if category in ("condenacoes", "improcedentes", "acordos"):
                    outcome = category

        return {
            "outcome": outcome,
            "has_expert_report": has_expert_report,
        }

    def enrich_processes(self, communications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Cruza publicações únicas do Comunica PJe com o Elasticsearch do DataJud.
        Gera uma lista padronizada de metadados processuais.
        """
        # Agrupa publicações por número de processo para agregar textos
        proc_map: Dict[str, List[str]] = defaultdict(list)
        for item in communications:
            proc_num = item.get("numero_processo") or item.get("numeroProcesso")
            if proc_num:
                proc_map[proc_num].append(item.get("texto", "") or "")

        enriched_list = []
        for proc_num, texts in proc_map.items():
            combined_text = "\n".join(texts)
            text_decision = self.classify_text_decision(combined_text)
            text_has_expert = self.detect_expert_report_in_text(combined_text)

            # Consulta o DataJud para metadados oficiais
            datajud_data = self.client.fetch_datajud_process_metadata(proc_num)

            primary_subject = "Outros / Não Especificado"
            primary_subject_code = None
            subjects = []
            secrecy_level = 0
            court = self.client.extract_court_from_process_number(proc_num)
            grau = "G1"
            movements = []

            if datajud_data:
                secrecy_level = int(datajud_data.get("nivelSigilo", 0))
                subjects = datajud_data.get("assuntos", [])
                movements = datajud_data.get("movimentos", [])
                court = datajud_data.get("_court", court)
                grau = datajud_data.get("grau", "G1")

                if subjects and len(subjects) > 0:
                    primary_subject = subjects[0].get("nome", primary_subject)
                    primary_subject_code = subjects[0].get("codigo")

            # Parse de movimentos TPU
            movement_analysis = self.parse_datajud_movements(movements)
            outcome = movement_analysis["outcome"]
            has_expert = movement_analysis["has_expert_report"] or text_has_expert

            # Se os movimentos do DataJud ainda não refletem desfecho, usa NLP da publicação do PJe
            if outcome == "em_aberto" and text_decision:
                outcome = text_decision

            enriched_list.append({
                "numero_processo": proc_num,
                "tribunal": court,
                "grau": grau,
                "nivelSigilo": secrecy_level,
                "primary_subject": primary_subject,
                "primary_subject_code": primary_subject_code,
                "assuntos": subjects,
                "sentence_outcome": outcome,
                "has_expert_report": has_expert,
                "raw_metadata": datajud_data,
            })

        return enriched_list

