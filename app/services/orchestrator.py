import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

import requests

from app.utils.sanitizer import sanitize_company_name, is_cnpj, fetch_cnpj_brasilapi
from app.utils.crypto import generate_movements_hash
from app.utils.rate_limiter import RateLimiter
from app.services.ai_analyzer import LegalAIAnalyzer
from app.services.prediction_engine import BayesianRiskPredictor, safe_int
from app.services.tpu_constants import (
    TPU_OUTCOME_CODES,
    TPU_EXPERT_REPORT_CODES,
)

logger = logging.getLogger(__name__)

# Padrões semânticos expandidos para inferência de causa da ação a partir do texto das publicações PJe
# quando os metadados do DataJud/CNJ retornarem apenas "Outros Assuntos" ou categorias genéricas.
SUBJECT_INFERENCE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # 1. Categoria Bancária / Finanças (Ex: Bradesco, Itaú, Santander, BB)
    (re.compile(r"\b(7[ªa]\s*(e|a)\s*8[ªa]\s*horas?|s[ée]tima\s*e\s*oitava\s*horas?|divisor\s*180|divisor\s*200|banc[áa]ri[oa]|cargo\s*de\s*confian[çc]a\s*banc[áa]ri[oa]|art(\.|igo)?\s*224|enquadramento\s*sindical\s*banc[áa]ri[oa]|quebra\s*de\s*caixa|gratifica[çc][ãa]o\s*de\s*fun[çc][ãa]o\s*banc[áa]ria)\b", re.IGNORECASE), "Direitos da Categoria Bancária (7ª/8ª Horas / Enquadramento)"),

    # 2. Horas Extras e Jornada de Trabalho
    (re.compile(r"\b(horas?\s+extras?|banco\s+de\s+horas?|intervalo\s+intra(jornada)?|intervalo\s+interjornada|jornada\s+de\s+trabalho|cart[ãa]o(ões)?\s+de\s+ponto|registro\s+de\s+ponto|controle\s+de\s+ponto|minutos\s+residuais|tempo\s+[àa]\s+disposi[çc][ãa]o|jornada\s+12x36|sobreaviso|prontid[ãa]o)\b", re.IGNORECASE), "Horas Extras"),

    # 3. Adicional de Insalubridade
    (re.compile(r"\b(adicional\s+de\s+insalubridade|insalubre|grau\s+(m[ée]dio|m[áa]ximo|m[íi]nimo))\b", re.IGNORECASE), "Adicional de Insalubridade"),

    # 4. Adicional de Periculosidade
    (re.compile(r"\b(adicional\s+de\s+periculosidade|periculoso|inflam[áa]veis|eletricidade|vigilante|transporte\s+de\s+valores)\b", re.IGNORECASE), "Adicional de Periculosidade"),

    # 5. Adicional Noturno
    (re.compile(r"\b(adicional\s+noturno|trabalho\s+noturno|hora\s+noturna)\b", re.IGNORECASE), "Adicional Noturno"),

    # 6. Verbas Rescisórias e Término Contratual
    (re.compile(r"\b(verbas?\s+rescis[óo]rias?|aviso\s+pr[ée]vio|saldo\s+de\s+sal[áa]rio|f[ée]rias(\s*(\+|\s*e)\s*1/3)?|d[ée]cimo\s+terceiro|13[º°]\s+sal[áa]rio|rescis[ãa]o\s+indireta|dispensa\s+imotivada|justa\s+causa)\b", re.IGNORECASE), "Verbas Rescisórias"),

    # 7. Multas do Art. 477 e 467 da CLT
    (re.compile(r"\b(art(\.|igo)?\s*477|art(\.|igo)?\s*467|multa\s+do\s+artigo\s+477|multa\s+do\s+artigo\s+467)\b", re.IGNORECASE), "Multas dos Artigos 477 e 467 da CLT"),

    # 8. FGTS e Multa de 40%
    (re.compile(r"\b(fgts|fundo\s+de\s+garantia|dep[óo]sitos?\s+do\s+fgts|multa\s+(de\s+)?40%?|multa\s+rescis[óo]ria)\b", re.IGNORECASE), "FGTS"),

    # 9. Dano Moral, Assédio e Metas Abusivas
    (re.compile(r"\b(danos?\s+morais?|ass[ée]dio\s+moral|ass[ée]dio\s+sexual|indeniza[çc][ãa]o\s+por\s+dano\s+moral|cobran[çc]a\s+de\s+metas?|dano\s+existencial|revista\s+[íi]ntima)\b", re.IGNORECASE), "Indenização por Dano Moral"),

    # 10. Equiparação Salarial e Desvio de Função
    (re.compile(r"\b(equipara[çc][ãa]o\s+salarial|paradigma|desvio\s+de\s+fun[çc][ãa]o|ac[úu]mulo\s+de\s+fun[çc][ãa]o)\b", re.IGNORECASE), "Equiparação Salarial"),

    # 11. Diferenças Salariais e Remuneração
    (re.compile(r"\b(diferen[çc]as?\s+salaria(l|is)|comiss[õo]es|estorno\s+de\s+comiss[õo]es|plr|participa[çc][ãa]o\s+nos\s+lucros|vale[ -]refei[çc][ãa]o|vale[ -]alimenta[çc][ãa]o|aux[íi]lio[ -]alimenta[çc][ãa]o)\b", re.IGNORECASE), "Diferenças Salariais e Remuneração"),

    # 12. Terceirização e Responsabilidade Subsidiária
    (re.compile(r"\b(terceiriza[çc][ãa]o|responsabilidade\s+subsidi[áa]ria|s[úu]mula\s+331|tomador(a)?\s+de\s+servi[çc]os?|grupo\s+econ[ôo]mico|sucess[ãa]o\s+empresarial|sucess[ãa]o\s+de\s+empregadores)\b", re.IGNORECASE), "Terceirização / Resp. Subsidiária"),

    # 13. Reconhecimento de Vínculo de Emprego
    (re.compile(r"\b(v[íi]nculo\s+(de\s+emprego|empregat[íi]cio)|pejotiza[çc][ãa]o|anota[çc][ãa]o\s+(na|da)\s+ctps|baixa\s+na\s+ctps|contrato\s+de\s+est[áa]gio\s+nulo)\b", re.IGNORECASE), "Reconhecimento de Vínculo"),

    # 14. Acidente de Trabalho e Doença Ocupacional
    (re.compile(r"\b(acidente\s+de\s+trabalho|doen[çc]a\s+(ocupacional|profissional)|ler/dort|les[ãa]o\s+por\s+esfor[çc]o\s+repetitivo|estabilidade\s+acident[áa]ria|pens[ãa]o\s+vital[íi]cia)\b", re.IGNORECASE), "Acidente de Trabalho / Doença"),

    # 15. Estabilidade Provisória (Gestante / CIPA / Sindical)
    (re.compile(r"\b(gestante|estabilidade\s+da\s+gestante|licen[çc]a\s+maternidade|estabilidade\s+sindical|cipeiro|estabilidade\s+cipa)\b", re.IGNORECASE), "Estabilidade da Gestante"),

    # 16. Execução Trabalhista e Fase de Liquidação
    (re.compile(r"\b(sisbajud|renajud|infojud|bloqueio\s+de\s+ativos|penhora|mandado\s+de\s+penhora|c[áa]lculos\s+de\s+liquida[çc][ãa]o|impugna[çc][ãa]o\s+aos\s+c[áa]lculos|execu[çc][ãa]o\s+de\s+t[íi]tulo|cumprimento\s+de\s+senten[çc]a)\b", re.IGNORECASE), "Execução Trabalhista e Liquidação"),

    # 17. Acordo Homologado e Quitação
    (re.compile(r"\b(acordo\s+homologado|transa[çc][ãa]o\s+homologada|termo\s+de\s+concilia[çc][ãa]o|quita[çc][ãa]o\s+geral)\b", re.IGNORECASE), "Homologação de Acordo e Conciliação"),
]

CLASS_INFERENCE_MAP: List[Tuple[str, str]] = [
    ("RECURSO ORDINÁRIO", "Recurso Ordinário Trabalhista"),
    ("AGRAVO DE PETIÇÃO", "Agravo de Petição em Execução"),
    ("SUMARÍSSIMO", "Reclamatória Trabalhista (Rito Sumaríssimo)"),
    ("ORDINÁRIO", "Reclamatória Trabalhista (Rito Ordinário)"),
    ("EXECUÇÃO", "Execução Trabalhista"),
    ("CUMPRIMENTO", "Cumprimento de Sentença Trabalhista"),
    ("MONITÓRIA", "Ação Monitória Trabalhista"),
    ("CONSIGNAÇÃO", "Consignação em Pagamento"),
    ("INQUÉRITO", "Inquérito para Apuração de Falta Grave"),
    ("TUTELA", "Tutela Cautelar / Urgência Trabalhista"),
    ("MANDADO DE SEGURANÇA", "Mandado de Segurança Trabalhista"),
]


class CNJMetadataOrchestrator:
    """
    Orquestrador de Inteligência Jurídica e Integração de APIs do CNJ:
    - Triangulação reversa de CNPJ e sanitização de Razão Social.
    - Execução Fast-Path (< 3s) e Deep Scan assíncrono em background.
    - Rate Limiting e Exponential Backoff com jitter.
    - Análise semântica de publicações PJe para inferir assuntos quando rotulados como 'Outros Assuntos'.
    - Motor Preditivo Bayesiano (Dirichlet-Multinomial / Beta-Binomial com IC 90% e Shrinkage Empírico).
    - Geração de hash SHA-256 para curto-circuito de chamadas à IA.
    """

    # NLP Leve de Sentenças e Despachos do PJe
    REGEX_PROCEDENCIA_PARTE = re.compile(
        r"\b(julgo\s+parcialmente\s+procedente|acolho\s+em\s+parte|proced[êe]ncia\s+em\s+parte|condena-\se\s+em\s+parte|procedentes\s+em\s+parte)\b",
        re.IGNORECASE,
    )
    REGEX_PROCEDENCIA = re.compile(
        r"\b(julgo\s+procedente|acolho\s+os\s+pedidos|declarar\s+a\s+proced[êe]ncia|condenar\s+a\s+reclamada)\b",
        re.IGNORECASE,
    )
    REGEX_IMPROCEDENCIA = re.compile(
        r"\b(julgo\s+improcedente|rejeito\s+os\s+pedidos|declarar\s+a\s+improced[êe]ncia|absolvo\s+a\s+reclamada|totalmente\s+improcedentes)\b",
        re.IGNORECASE,
    )
    REGEX_ACORDO = re.compile(
        r"\b(homolog(o|ar|a-se)\s+(o\s+)?acordo|concilia[çc][ãa]o\s+homologada|transa[çc][ãa]o\s+homologada)\b",
        re.IGNORECASE,
    )
    REGEX_EXTINCAO_SEM_MERITO = re.compile(
        r"\b(extin(go|to)\s+o\s+processo\s+sem\s+resolu[çc][ãa]o\s+do\s+m[ée]rito|homologo\s+a\s+desist[êe]ncia|arquive-se\s+definitivamente)\b",
        re.IGNORECASE,
    )
    REGEX_LAUDO_PERICIAL = re.compile(
        r"\b(laudo\s+pericial|per[íi]cia\s+t[ée]cnica|perito\s+(judicial|nomeado)|quesitos\s+periciais|conclus[ãa]o\s+do\s+laudo)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        self.comunica_url = os.getenv("COMUNICA_PJE_URL", "https://comunicaapi.pje.jus.br/api/v1/comunicacao")
        self.datajud_base_url = os.getenv("DATAJUD_BASE_URL", "https://api-publica.datajud.cnj.jus.br")
        self.datajud_key = os.getenv("DATAJUD_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) RadarJus/2.0 (Compliance Monitor)"
        })
        self._cache = {}
        self.ai_analyzer = LegalAIAnalyzer()
        self.predictor = BayesianRiskPredictor()

    def classify_text_decision_detailed(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Classifica o desfecho processual e a subcategoria (diferenciando procedência total,
        procedência em parte, acordo, improcedência e extinção sem mérito).
        """
        if not text:
            return None, None
        if self.REGEX_ACORDO.search(text):
            return "acordos", "acordo_homologado"
        if self.REGEX_PROCEDENCIA_PARTE.search(text):
            return "condenacoes", "procedencia_parcial"
        if self.REGEX_PROCEDENCIA.search(text):
            return "condenacoes", "procedencia_total"
        if self.REGEX_IMPROCEDENCIA.search(text):
            return "improcedentes", "improcedencia"
        if self.REGEX_EXTINCAO_SEM_MERITO.search(text):
            return "extintos_sem_merito", "extinto_sem_merito"
        return None, None

    def classify_text_decision(self, text: str) -> Optional[str]:
        """Classifica o desfecho processual principal a partir do texto publicado no Comunica PJe."""
        outcome, _ = self.classify_text_decision_detailed(text)
        return outcome

    def detect_expert_report_in_text(self, text: str) -> bool:
        """Verifica se o texto menciona perícia técnica ou laudo pericial."""
        if not text:
            return False
        return bool(self.REGEX_LAUDO_PERICIAL.search(text))

    def parse_datajud_movements(self, movements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analisa a árvore de movimentos do DataJud utilizando códigos da TPU/CNJ."""
        outcome = "em_aberto"
        sub_outcome = None
        has_expert_report = False

        for mov in movements:
            if not isinstance(mov, dict):
                continue
            code = safe_int(mov.get("codigo"))
            if code is None:
                continue

            if code in TPU_EXPERT_REPORT_CODES:
                has_expert_report = True

            if code in TPU_OUTCOME_CODES and outcome == "em_aberto":
                tpu_info = TPU_OUTCOME_CODES[code]
                category = tpu_info["categoria"]
                if category in ("condenacoes", "improcedentes", "acordos", "extintos_sem_merito"):
                    outcome = category
                    sub_outcome = tpu_info.get("subcategoria")

        return {
            "outcome": outcome,
            "sub_outcome": sub_outcome,
            "has_expert_report": has_expert_report,
        }

    def infer_subject_and_observation(
        self,
        current_subjects: List[str],
        pje_texts: List[str],
        pje_classes: Optional[List[str]] = None,
    ) -> Tuple[List[str], Optional[str]]:
        """
        Caso os assuntos oficiais do DataJud contenham apenas 'Outros Assuntos' ou categorias genéricas,
        inspeciona o conteúdo das publicações e classes processuais do PJe para inferir o motivo real do processo
        e adicionar uma observação detalhada para auditoria jurídica.
        """
        combined_text = " ".join(pje_texts).lower()
        is_generic = (
            not current_subjects or
            all(
                any(g in s.lower() for g in ["outros", "não especificado", "generico", "motivos trabalhistas"])
                for s in current_subjects
            )
        )

        if not is_generic:
            return current_subjects, None

        # 1. Procura nos textos por assuntos trabalhistas conhecidos (coleta todos os temas identificados)
        matched_labels = []
        for pattern, label in SUBJECT_INFERENCE_PATTERNS:
            if pattern.search(combined_text):
                if label not in matched_labels:
                    matched_labels.append(label)

        if matched_labels:
            primary_label = matched_labels[0]
            observation = (
                f"Assunto identificado via publicação PJe: '{primary_label}'. "
                f"(Metadados oficiais DataJud indicavam apenas 'Outros Assuntos')."
            )
            inferred_list = [f"{label} (Inferido via PJe)" for label in matched_labels]
            return inferred_list, observation

        # 2. Se os padrões não bateram mas temos classes processuais PJe, infere a partir da classe
        if pje_classes:
            combined_classes = " ".join(pje_classes).upper()
            for key, class_label in CLASS_INFERENCE_MAP:
                if key in combined_classes:
                    obs = f"Causa inferida a partir da classe processual PJe: '{class_label}'."
                    return [f"{class_label} (Inferido via PJe)"], obs

        # 3. Análise semântica complementar dos termos da publicação para não agrupar cegamente
        if re.search(r"\b(audi[êe]ncia|instru[çc][ãa]o|testemunha|per[íi]cia)\b", combined_text):
            label = "Instrução Probatória e Perícias"
            return [f"{label} (Inferido via PJe)"], f"Causa em fase instrutória identificada no texto PJe: '{label}'."

        if re.search(r"\b(execu[çc][ãa]o|penhora|bloqueio|c[áa]lculos|alvar[áa])\b", combined_text):
            label = "Execução Trabalhista e Liquidação"
            return [f"{label} (Inferido via PJe)"], f"Fase executória/satisfativa identificada no texto PJe: '{label}'."

        if re.search(r"\b(recurso|ac[óo]rd[ãa]o|turma|tribunal)\b", combined_text):
            label = "Fase Recursal Trabalhista"
            return [f"{label} (Inferido via PJe)"], f"Fase recursal identificada no texto PJe: '{label}'."

        if re.search(r"\b(despacho|notifica[çc][ãa]o|intima[çc][ãa]o|cita[çc][ãa]o)\b", combined_text):
            label = "Obrigações Trabalhistas em Tramitação"
            return [f"{label} (Inferido via PJe)"], f"Processo trabalhista ativo em fase de intimação/despacho: '{label}'."

        return current_subjects or ["Outros Motivos Trabalhistas"], None

    @staticmethod
    def normalize_court(sigla: str, num_proc: str = "") -> str:
        """
        Normaliza a sigla do tribunal para o formato exato dos endpoints do DataJud (ex: 'trt2', 'trt15').
        Evita chamadas com zeros à esquerda ('trt02') que geram 404 e travam a aplicação com timeouts.
        """
        clean = re.sub(r"[^a-zA-Z0-9]", "", str(sigla or "").lower())
        m = re.match(r"^trt0?(\d+)$", clean)
        if m:
            return f"trt{int(m.group(1))}"
        if clean in ("tst", "stj", "stf"):
            return clean

        clean_digits = re.sub(r"\D", "", str(num_proc or ""))
        if len(clean_digits) == 20:
            justice_segment = clean_digits[13]
            court_region = clean_digits[14:16]
            if justice_segment == "5":
                return "tst" if court_region == "00" else f"trt{int(court_region)}"
            elif justice_segment == "8":
                return f"tjr{int(court_region)}" if court_region.isdigit() else f"tj{court_region}"

        return clean if clean else "trt2"

    def _fetch_comunica_pje(
        self,
        company_query: str,
        max_pages: int = 3,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        Coleta publicações no Comunica PJe com controle de taxa e suporte a CNPJ.
        Utiliza itensPorPagina=100 (teto oficial da API) para maximizar a captura
        de processos reais em pouquíssimas requisições HTTP.
        """
        # Triangulação de CNPJ via BrasilAPI
        if is_cnpj(company_query):
            official_name = fetch_cnpj_brasilapi(company_query)
            if official_name:
                company_query = official_name

        sanitized_name = sanitize_company_name(company_query)
        search_term = sanitized_name if sanitized_name else company_query

        processes_map = {}
        has_more = False

        for page in range(1, max_pages + 1):
            RateLimiter.comunica_pje.acquire(tokens=1.0, block=False)

            try:
                resp = self.session.get(
                    self.comunica_url,
                    params={
                        "nomeParte": search_term,
                        "itensPorPagina": page_size,
                        "pagina": page,
                    },
                    timeout=4.5,
                )
                if not resp.ok:
                    break
                items = resp.json().get("items", [])
                if not items:
                    has_more = False
                    break
            except Exception as exc:
                logger.warning("Falha ao consultar Comunica PJe para '%s': %s", search_term, exc)
                break

            for item in items:
                num_raw = item.get("numero_processo") or item.get("numeroProcesso") or ""
                num_proc = "".join(filter(str.isdigit, str(num_raw)))
                tribunal = (item.get("siglaTribunal") or "").lower().strip()
                texto = item.get("texto") or ""
                nome_classe = item.get("nomeClasse") or ""
                tipo_doc = item.get("tipoDocumento") or ""
                data_pub = item.get("dataDisponibilizacao") or item.get("dataPublicacao") or ""

                if len(num_proc) != 20 or not tribunal:
                    continue

                if num_proc not in processes_map:
                    processes_map[num_proc] = {
                        "numero_processo": num_proc,
                        "tribunal": tribunal,
                        "textos_pje": [texto] if texto else [],
                        "classes_pje": [nome_classe] if nome_classe else [],
                        "tipos_doc_pje": [tipo_doc] if tipo_doc else [],
                        "data_publicacao": data_pub,
                    }
                else:
                    if texto and texto not in processes_map[num_proc]["textos_pje"]:
                        processes_map[num_proc]["textos_pje"].append(texto)
                    if nome_classe and nome_classe not in processes_map[num_proc]["classes_pje"]:
                        processes_map[num_proc]["classes_pje"].append(nome_classe)
                    if tipo_doc and tipo_doc not in processes_map[num_proc]["tipos_doc_pje"]:
                        processes_map[num_proc]["tipos_doc_pje"].append(tipo_doc)

            if len(items) < page_size:
                has_more = False
                break
            else:
                has_more = True

        self._last_fetch_has_more = has_more
        return processes_map

    def _query_datajud_tribunal(
        self,
        tribunal: str,
        proc_numbers: List[str],
        headers: Dict[str, str],
        since_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Executa consulta em lote para um tribunal no Elasticsearch do DataJud."""
        endpoint = f"{self.datajud_base_url}/api_publica_{tribunal}/_search"
        must_clauses = [
            {"terms": {"numeroProcesso": proc_numbers[:500]}}
        ]
        if since_date:
            must_clauses.append({
                "range": {
                    "dataHoraUltimaAtualizacao": {"gt": since_date}
                }
            })

        payload = {
            "size": len(proc_numbers),
            "_source": [
                "numeroProcesso",
                "assuntos",
                "nivelSigilo",
                "movimentos",
                "dataHoraUltimaAtualizacao",
                "grau"
            ],
            "query": {"bool": {"must": must_clauses}} if since_date else {"terms": {"numeroProcesso": proc_numbers[:500]}}
        }

        RateLimiter.datajud.acquire(tokens=1.0, block=False)

        try:
            resp = self.session.post(endpoint, json=payload, headers=headers, timeout=(1.5, 3.0))
            if resp.ok:
                return resp.json().get("hits", {}).get("hits", [])
        except Exception as exc:
            logger.debug("Consulta ao tribunal '%s' no DataJud rápida sem bloqueio: %s", tribunal, exc)

        return []

    def _enrich_with_datajud(
        self,
        processes_map: Dict[str, Any],
        since_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Enriquece os processos em paralelo com o DataJud utilizando ThreadPoolExecutor
        com tribunais normalizados para tempo de resposta < 1.5s.
        """
        by_tribunal = defaultdict(list)
        for num_proc, info in processes_map.items():
            norm_court = self.normalize_court(info.get("tribunal"), num_proc)
            by_tribunal[norm_court].append(num_proc)

        enriched_by_number = {}
        for num_proc, info in processes_map.items():
            pje_texts = info.get("textos_pje", [])
            pje_classes = info.get("classes_pje", [])
            combined_pje = " ".join(pje_texts)
            decision_from_text, sub_decision_from_text = self.classify_text_decision_detailed(combined_pje)
            has_expert_text = self.detect_expert_report_in_text(combined_pje)
            norm_court = self.normalize_court(info.get("tribunal"), num_proc)

            inferred_subs, obs = self.infer_subject_and_observation([], pje_texts, pje_classes)
            primary_sub = inferred_subs[0] if inferred_subs else "Outros Motivos Trabalhistas"

            enriched_by_number[num_proc] = {
                "numero_processo": num_proc,
                "tribunal": norm_court,
                "grau": "G1",
                "subjects": inferred_subs,
                "primary_subject": primary_sub,
                "primary_subject_code": None,
                "nivelSigilo": 0,
                "nivel_sigilo": 0,
                "sentence_outcome": decision_from_text or "em_aberto",
                "sentence_sub_outcome": sub_decision_from_text,
                "has_expert_report": has_expert_text,
                "observation": obs,
                "dataHoraUltimaAtualizacao": info.get("data_publicacao", ""),
                "movimentos": [],
                "raw_metadata": None,
            }

        headers = {
            "Authorization": f"APIKey {self.datajud_key}",
            "Content-Type": "application/json",
        }

        # Paraleliza a consulta aos tribunais no DataJud para tempo de resposta < 3 segundos
        max_workers = min(16, max(1, len(by_tribunal)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_tribunal = {
                executor.submit(
                    self._query_datajud_tribunal, tribunal, proc_numbers, headers, since_date
                ): tribunal
                for tribunal, proc_numbers in by_tribunal.items()
            }

            for future in as_completed(future_to_tribunal):
                tribunal = future_to_tribunal[future]
                try:
                    hits = future.result()
                except Exception as exc:
                    logger.debug("Falha na consulta paralela do tribunal %s: %s", tribunal, exc)
                    hits = []

                for hit in hits:
                    src = hit.get("_source", {})
                    num = src.get("numeroProcesso")
                    if num not in enriched_by_number:
                        continue

                    entry = enriched_by_number[num]
                    raw_assuntos = src.get("assuntos") or []
                    subjects = [
                        a.get("nome") for a in raw_assuntos if isinstance(a, dict) and a.get("nome")
                    ]
                    subject_codes = [
                        safe_int(a.get("codigo"))
                        for a in raw_assuntos
                        if isinstance(a, dict) and safe_int(a.get("codigo")) is not None
                    ]

                    # Inspeciona texto PJe se os assuntos oficiais forem genéricos
                    pje_texts = processes_map.get(num, {}).get("textos_pje", [])
                    pje_classes = processes_map.get(num, {}).get("classes_pje", [])
                    subjects, observation = self.infer_subject_and_observation(subjects, pje_texts, pje_classes)

                    # Análise de movimentos TPU
                    movimentos = src.get("movimentos") or []
                    mov_analysis = self.parse_datajud_movements(movimentos)

                    outcome = mov_analysis["outcome"]
                    sub_outcome = mov_analysis.get("sub_outcome")
                    if outcome == "em_aberto" and entry["sentence_outcome"] != "em_aberto":
                        outcome = entry["sentence_outcome"]
                        sub_outcome = entry.get("sentence_sub_outcome")

                    has_expert = mov_analysis["has_expert_report"] or entry["has_expert_report"]
                    sigilo_val = safe_int(src.get("nivelSigilo"), default=0) or 0

                    entry.update({
                        "subjects": subjects,
                        "primary_subject": subjects[0] if subjects else entry["primary_subject"],
                        "primary_subject_code": subject_codes[0] if subject_codes else None,
                        "nivelSigilo": sigilo_val,
                        "nivel_sigilo": sigilo_val,
                        "sentence_outcome": outcome,
                        "sentence_sub_outcome": sub_outcome,
                        "has_expert_report": has_expert,
                        "observation": observation or entry["observation"],
                        "grau": src.get("grau", "G1"),
                        "dataHoraUltimaAtualizacao": src.get("dataHoraUltimaAtualizacao") or entry["dataHoraUltimaAtualizacao"],
                        "movimentos": movimentos,
                        "raw_metadata": src,
                    })

        return list(enriched_by_number.values())

    def _calculate_metrics(
        self,
        company_name: str,
        processes: List[Dict[str, Any]],
        scan_status: str = "COMPLETED",
    ) -> Dict[str, Any]:
        """
        Delega o cálculo estatístico ao BayesianRiskPredictor (Dirichlet-Multinomial / Beta-Binomial
        com Intervalo de Credibilidade de 90%, ajuste de laudo em escala logit e Shrinkage Empirical Bayes)
        e anexa o hash de movimentos e os processos enriquecidos.
        """
        metrics = self.predictor.generate_dashboard_metrics(company_name, processes)
        metrics["scan_status"] = scan_status
        metrics["movements_hash"] = generate_movements_hash(processes)
        metrics["processos"] = processes
        return metrics

    def analyze_company_by_name(
        self,
        company_name: str,
        fast_path: bool = False,
        limit_processes: int = 100,
    ) -> Dict[str, Any]:
        """
        Executa a análise da empresa com dados reais completos.
        - Utiliza page_size=100 para trazer todos os processos em lote rápido.
        - Se fast_path=True: busca até 2 páginas (200 publicações) de forma rápida.
          Se a empresa tiver menos de 100 publicações, o resultado vem 100% completo na 1ª requisição!
        - Se fast_path=False: varredura completa até 6 páginas (600 publicações).
        """
        cache_key = company_name.strip().lower()
        if not fast_path and cache_key in self._cache:
            return self._cache[cache_key]

        max_pages = 2 if fast_path else 6
        page_size = 100

        # 1. Coleta processos no Comunica PJe
        processes_map = self._fetch_comunica_pje(company_name, max_pages=max_pages, page_size=page_size)

        if not processes_map:
            empty_res = self._calculate_metrics(company_name, [], scan_status="COMPLETED")
            empty_res["ai_analysis"] = self.ai_analyzer.analyze_company(
                {"empresa": company_name, "total_processos": 0}
            )
            if not fast_path:
                self._cache[cache_key] = empty_res
            return empty_res

        # 2. Enriquecimento no DataJud (em paralelo por tribunal)
        processes = self._enrich_with_datajud(processes_map)

        # 3. Cálculo de métricas via BayesianRiskPredictor
        has_more = getattr(self, "_last_fetch_has_more", False)
        status = "PARTIAL_FAST_PATH" if (fast_path and has_more) else "COMPLETED"
        metrics = self._calculate_metrics(company_name, processes, scan_status=status)

        # 4. Geração de parecer de IA (radarjusAgent ADK)
        ai_result = self.ai_analyzer.analyze_company(metrics)
        metrics["ai_analysis"] = ai_result

        if not fast_path:
            self._cache[cache_key] = metrics

        return metrics
