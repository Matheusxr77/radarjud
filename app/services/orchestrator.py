import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

import requests

from app.utils.sanitizer import sanitize_company_name, is_cnpj, fetch_cnpj_brasilapi, remove_accents, extract_brand_search_term
from app.utils.crypto import generate_movements_hash
from app.utils.rate_limiter import RateLimiter
from app.services.ai_analyzer import LegalAIAnalyzer
from app.services.prediction_engine import BayesianRiskPredictor, safe_int
from app.services.tpu_constants import (
    TPU_OUTCOME_CODES,
    TPU_EXPERT_REPORT_CODES,
)

import html

logger = logging.getLogger(__name__)


def clean_text_for_inference(text: str) -> str:
    """
    Remove entidades HTML, substitui tags HTML por espaços e normaliza espaços em branco
    para evitar junção acidental de palavras e permitir busca semântica precisa via regex.
    """
    if not text:
        return ""
    unescaped = html.unescape(str(text))
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", without_tags).strip()


# Padrões semânticos expandidos para inferência de causa da ação a partir do texto das publicações PJe,
# classes processuais e movimentações quando os metadados do DataJud/CNJ retornarem apenas "Outros Assuntos"
# ou códigos genéricos de topo de árvore (como TPU 864 - Direito do Trabalho).
SUBJECT_INFERENCE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # 1. Categoria Bancária / Finanças (Ex: Bradesco, Itaú, Santander, BB, Fintechs)
    (
        re.compile(
            r"\b(7[ªa]\s*(e|a)\s*8[ªa]\s*horas?|s[ée]tima\s*e\s*oitava\s*horas?|divisor\s*(180|200)|"
            r"banc[áa]ri[oa]|cargo\s*de\s*confian[çc]a\s*banc[áa]ri[oa]|art(\.|igo)?\s*224|"
            r"enquadramento\s*sindical\s*banc[áa]ri[oa]|quebra\s*de\s*caixa|gratifica[çc][ãa]o\s*de\s*fun[çc][ãa]o\s*banc[áa]ria|"
            r"plano\s*de\s*cargos\s*e\s*sal[áa]rios\s*banc[áa]rio)\b",
            re.IGNORECASE,
        ),
        "Direitos da Categoria Bancária (7ª e 8ª Horas / Enquadramento)",
    ),

    # 2. Horas Extras e Jornada de Trabalho
    (
        re.compile(
            r"\b(horas?\s+(extras?|suplementares?|extraordin[áa]rias?)|sobrejornada|banco\s+de\s+horas?|"
            r"intervalo\s+intra(jornada)?|intervalo\s+interjornada|jornada\s+de\s+trabalho|"
            r"cart[ãa]o(ões)?\s+de\s+ponto|registro\s+de\s+ponto|controle\s+de\s+ponto|folha\s+de\s+ponto|"
            r"minutos\s+residuais|tempo\s+[àa]\s+disposi[çc][ãa]o|jornada\s+12x36|sobreaviso|prontid[ãa]o|"
            r"repouso\s+semanal\s+remunerado|reflexos?\s+(em|no)\s+dsr)\b",
            re.IGNORECASE,
        ),
        "Falta de Pagamento de Horas Extras e Reflexos",
    ),

    # 3. FGTS e Multa Rescisória de 40%
    (
        re.compile(
            r"\b(fgts|fundo\s+de\s+garantia|dep[óo]sitos?\s+(do\s+)?fgts|multa\s+(rescis[óo]ria\s+)?(de\s+)?40%?|"
            r"40%\s+do\s+fgts|saque\s+do\s+fgts|diferen[çc]as?\s+de\s+fgts|recolhimento\s+(do\s+)?fgts|"
            r"extrato\s+anal[íi]tico\s+do\s+fgts|guias\s+de\s+fgts)\b",
            re.IGNORECASE,
        ),
        "Não Recolhimento de FGTS e Multa Rescisória de 40%",
    ),

    # 4. Verbas Rescisórias e Término Contratual
    (
        re.compile(
            r"\b(verbas?\s+rescis[óo]rias?|aviso\s+pr[ée]vio(\s+indenizado)?|saldo\s+de\s+sal[áa]rio|"
            r"f[ée]rias(\s*(\+|\s*e)\s*1/3|\s+proporcionais|\s+vencidas)?|d[ée]cimo\s+terceiro(\s+proporcional)?|"
            r"13[º°]\s+sal[áa]rio|rescis[ãa]o\s+indireta|dispensa\s+imotivada|justa\s+causa|"
            r"termo\s+de\s+rescis[ãa]o|trct|baixa\s+na\s+ctps|entrega\s+de\s+guias)\b",
            re.IGNORECASE,
        ),
        "Inadimplemento de Verbas Rescisórias e Aviso Prévio",
    ),

    # 5. Multas dos Artigos 477 e 467 da CLT
    (
        re.compile(
            r"\b(art(\.|igo)?\s*477|art(\.|igo)?\s*467|multa\s+(do\s+art(\.|igo)?\s*)?477|"
            r"multa\s+(do\s+art(\.|igo)?\s*)?467)\b",
            re.IGNORECASE,
        ),
        "Multas dos Artigos 477 e 467 da CLT",
    ),

    # 6. Dano Moral, Assédio e Metas Abusivas
    (
        re.compile(
            r"\b(danos?\s+morais?|ass[ée]dio\s+moral|ass[ée]dio\s+sexual|indeniza[çc][ãa]o\s+por\s+dano\s+moral|"
            r"cobran[çc]a\s+(excessiva\s+)?de\s+metas?|metas\s+abusivas|dano\s+existencial|"
            r"ofensa\s+[àa]\s+honra|revista\s+[íi]ntima|humilha[çc][ãa]o)\b",
            re.IGNORECASE,
        ),
        "Indenização por Danos Morais e Assédio no Trabalho",
    ),

    # 7. Adicional de Insalubridade e Periculosidade
    (
        re.compile(
            r"\b(adicional\s+de\s+insalubridade|insalubre|grau\s+(m[ée]dio|m[áa]ximo|m[íi]nimo)|agente\s+nocivo|"
            r"adicional\s+de\s+periculosidade|periculoso|inflam[áa]veis|eletricidade|explosivos|"
            r"vigilante|transporte\s+de\s+valores|motociclista)\b",
            re.IGNORECASE,
        ),
        "Adicional de Insalubridade e Periculosidade",
    ),

    # 8. Adicional Noturno
    (
        re.compile(
            r"\b(adicional\s+noturno|trabalho\s+noturno|hora\s+noturna|jornada\s+noturna)\b",
            re.IGNORECASE,
        ),
        "Adicional Noturno e Hora Noturna Reduzida",
    ),

    # 9. Diferenças Salariais, Comissões e Desvio de Função
    (
        re.compile(
            r"\b(diferen[çc]as?\s+salaria(l|is)|comiss[õo]es|estorno\s+de\s+comiss[õo]es|premia[çc][ãa]o|pr[êe]mios|"
            r"equipara[çc][ãa]o\s+salarial|paradigma|desvio\s+de\s+fun[çc][ãa]o|ac[úu]mulo\s+de\s+fun[çc][ãa]o|"
            r"sal[áa]rio\s+por\s+fora|sal[áa]rio\s+utilidade|plr|participa[çc][ãa]o\s+nos\s+lucros|"
            r"vale[ -]refei[çc][ãa]o|vale[ -]alimenta[çc][ãa]o|aux[íi]lio[ -]alimenta[çc][ãa]o|"
            r"reajuste\s+salarial|piso\s+salarial|conven[çc][ãa]o\s+coletiva)\b",
            re.IGNORECASE,
        ),
        "Diferenças Salariais, Comissões e Desvio de Função",
    ),

    # 10. Terceirização e Responsabilidade Subsidiária (Súmula 331 TST)
    (
        re.compile(
            r"\b(terceiriza[çc][ãa]o|responsabilidade\s+subsidi[áa]ria|s[úu]mula\s+331|tomador(a)?\s+de\s+servi[çc]os?|"
            r"grupo\s+econ[ôo]mico|sucess[ãa]o\s+empresarial|sucess[ãa]o\s+de\s+empregadores|empresa\s+interposta)\b",
            re.IGNORECASE,
        ),
        "Responsabilidade Subsidiária e Terceirização (Súmula 331 TST)",
    ),

    # 11. Reconhecimento de Vínculo de Emprego / Pejotização
    (
        re.compile(
            r"\b(v[íi]nculo\s+(de\s+emprego|empregat[íi]cio)|pejotiza[çc][ãa]o|anota[çc][ãa]o\s+(na|da)\s+ctps|"
            r"baixa\s+na\s+ctps|carteira\s+de\s+trabalho|contrato\s+de\s+est[áa]gio\s+nulo|fraude\s+trabalhista)\b",
            re.IGNORECASE,
        ),
        "Reconhecimento de Vínculo de Emprego / Pejotização",
    ),

    # 12. Acidente de Trabalho e Doença Ocupacional
    (
        re.compile(
            r"\b(acidente\s+de\s+trabalho|doen[çc]a\s+(ocupacional|profissional)|ler/dort|les[ãa]o\s+por\s+esfor[çc]o\s+repetitivo|"
            r"estabilidade\s+acident[áa]ria|pens[ãa]o\s+vital[íi]cia|pens[ãa]o\s+mensal|cat\b)\b",
            re.IGNORECASE,
        ),
        "Acidente de Trabalho e Doença Ocupacional",
    ),

    # 13. Estabilidade Provisória (Gestante / CIPA / Sindical)
    (
        re.compile(
            r"\b(gestante|estabilidade\s+da\s+gestante|licen[çc]a\s+maternidade|estabilidade\s+sindical|cipeiro|"
            r"estabilidade\s+cipa|reintegra[çc][ãa]o)\b",
            re.IGNORECASE,
        ),
        "Estabilidade Provisória e Garantia de Emprego (Gestante/CIPA)",
    ),

    # 14. Execução Trabalhista e Fase de Liquidação
    (
        re.compile(
            r"\b(sisbajud|renajud|infojud|bloqueio\s+de\s+(ativos|valores|contas)|penhora|mandado\s+de\s+penhora|"
            r"c[áa]lculos\s+de\s+liquida[çc][ãa]o|impugna[çc][ãa]o\s+aos\s+c[áa]lculos|execu[çc][ãa]o\s+de\s+t[íi]tulo|"
            r"cumprimento\s+de\s+senten[çc]a|homologa[çc][ãa]o\s+de\s+c[áa]lculos)\b",
            re.IGNORECASE,
        ),
        "Execução Trabalhista e Cumprimento de Sentença",
    ),

    # 15. Acordo Homologado e Quitação
    (
        re.compile(
            r"\b(acordo\s+homologado|transa[çc][ãa]o\s+homologada|termo\s+de\s+concilia[çc][ãa]o|quita[çc][ãa]o\s+geral|"
            r"homologa[çc][ãa]o\s+de\s+acordo)\b",
            re.IGNORECASE,
        ),
        "Homologação de Acordo e Conciliação Trabalhista",
    ),
]

CLASS_INFERENCE_MAP: List[Tuple[str, str]] = [
    # Siglas e termos de classes processuais PJe da Justiça do Trabalho (normalizados em ASCII)
    ("ATORD", "Reclamatória Trabalhista (Rito Ordinário)"),
    ("ATSUM", "Reclamatória Trabalhista (Rito Sumaríssimo)"),
    ("ACUM", "Ação de Cumprimento de Cláusula Coletiva"),
    ("CUMSEN", "Cumprimento de Sentença Trabalhista"),
    ("EXPROVAS", "Execução Provisória em Autos Suplementares"),
    ("EXFIS", "Execução Fiscal Trabalhista"),
    ("ROT", "Recurso Ordinário Trabalhista"),
    ("AP", "Agravo de Petição em Execução"),
    ("AIRR", "Agravo de Instrumento em Recurso de Revista (TST)"),
    ("RR", "Recurso de Revista Trabalhista (TST)"),
    ("HTE", "Homologação de Transação Extrajudicial"),
    ("CONPAG", "Consignação em Pagamento"),
    ("CARTPREC", "Carta Precatória Trabalhista"),
    ("RECURSO ORDINARIO", "Recurso Ordinário Trabalhista"),
    ("AGRAVO DE PETICAO", "Agravo de Petição em Execução"),
    ("AGRAVO DE INSTRUMENTO", "Agravo de Instrumento Trabalhista"),
    ("RECURSO DE REVISTA", "Recurso de Revista Trabalhista (TST)"),
    ("SUMARISSIMO", "Reclamatória Trabalhista (Rito Sumaríssimo)"),
    ("ORDINARIO", "Reclamatória Trabalhista (Rito Ordinário)"),
    ("EXECUCAO", "Execução Trabalhista e Cumprimento de Sentença"),
    ("CUMPRIMENTO", "Cumprimento de Sentença Trabalhista"),
    ("MONITORIA", "Ação Monitória Trabalhista"),
    ("CONSIGNACAO", "Consignação em Pagamento"),
    ("INQUERITO", "Inquérito para Apuração de Falta Grave"),
    ("TUTELA", "Tutela Cautelar / Urgência Trabalhista"),
    ("MANDADO DE SEGURANCA", "Mandado de Segurança Trabalhista"),
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
        default_key = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="
        raw_key = os.getenv("DATAJUD_API_KEY")
        self.datajud_key = raw_key.strip() if (raw_key and raw_key.strip()) else default_key
        self.session = requests.Session()

        # Suporte a proxy dedicado isolado (não interfere no pip/build do Render)
        proxy_url = os.getenv("PJE_PROXY_URL") or os.getenv("JUDICIAL_PROXY_URL")
        if proxy_url and proxy_url.strip():
            proxy_clean = proxy_url.strip()
            self.session.proxies = {
                "http": proxy_clean,
                "https": proxy_clean,
            }
            logger.info("CNJMetadataOrchestrator configurado com proxy dedicado PJE_PROXY_URL.")

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })
        self._cache = {}
        self.ai_analyzer = LegalAIAnalyzer()
        self.predictor = BayesianRiskPredictor()
        from app.services.cnj_searcher import CNJProcessSearcher
        self.searcher = CNJProcessSearcher(session=self.session)

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
        tipos_doc: Optional[List[str]] = None,
    ) -> Tuple[List[str], Optional[str]]:
        """
        Inspeciona o conteúdo das publicações, classes processuais e movimentações para inferir
        o motivo/causa de pedir real do processo quando os metadados forem genéricos ou ausentes.
        """
        generic_tokens = [
            "outros", "não especificado", "nao especificado", "generico",
            "motivos trabalhistas", "direito do trabalho", "direito processual",
            "processo do trabalho", "atos processuais"
        ]

        # 1. Filtra assuntos oficiais que já sejam específicos e informativos
        valid_specific_subjects = [
            s for s in (current_subjects or [])
            if not any(token in s.lower() for token in generic_tokens)
        ]
        if valid_specific_subjects:
            return valid_specific_subjects, None

        # 2. Prepara texto concatenado e limpo (sem tags HTML e com entidades decodificadas)
        raw_items = list(pje_texts or []) + list(tipos_doc or [])
        cleaned_pieces = [clean_text_for_inference(t) for t in raw_items if t]
        combined_text = " ".join(cleaned_pieces)
        unaccented_text = remove_accents(combined_text)
        search_corpus = f"{combined_text} {unaccented_text}"

        # 3. Procura por causas de pedir substantivas nos textos de publicações e andamentos
        matched_labels = []
        for pattern, label in SUBJECT_INFERENCE_PATTERNS:
            if pattern.search(search_corpus):
                if label not in matched_labels:
                    matched_labels.append(label)

        if matched_labels:
            primary_label = matched_labels[0]
            observation = (
                f"Assunto identificado via publicação PJe: '{primary_label}'. "
                f"(Metadados oficiais indicavam apenas assuntos genéricos)."
            )
            return matched_labels, observation

        # 4. Se os textos forem concisos, infere a partir da classe processual PJe (ex: ATSum, ATOrd, CumSen, ROT)
        if pje_classes:
            combined_classes = " ".join(pje_classes).upper()
            cleaned_classes = re.sub(r"[^A-Z0-9\s]", " ", remove_accents(combined_classes))
            for key, class_label in CLASS_INFERENCE_MAP:
                norm_key = remove_accents(key.upper())
                if re.search(r"\b" + re.escape(norm_key) + r"\b", cleaned_classes) or norm_key in cleaned_classes:
                    obs = f"Causa inferida a partir da classe processual PJe: '{class_label}'."
                    return [class_label], obs

        # 5. Análise semântica complementar dos termos da publicação para não agrupar cegamente
        if re.search(r"\b(laudo|perici(a|al)|quesitos|perito)\b", search_corpus, re.IGNORECASE):
            label = "Perícia Técnica Trabalhista"
            return [label], f"Fase pericial identificada no texto PJe: '{label}'."

        if re.search(r"\b(audiencia|instrucao|testemunha)\b", search_corpus, re.IGNORECASE):
            label = "Instrução Probatória e Audiências"
            return [label], f"Causa em fase instrutória identificada no texto PJe: '{label}'."

        if re.search(r"\b(execucao|penhora|bloqueio|calculos|alvara|liquidacao)\b", search_corpus, re.IGNORECASE):
            label = "Execução Trabalhista e Cumprimento de Sentença"
            return [label], f"Fase executória identificada no texto PJe: '{label}'."

        if re.search(r"\b(recurso|acordao|turma|tribunal|agravo)\b", search_corpus, re.IGNORECASE):
            label = "Fase Recursal Trabalhista"
            return [label], f"Fase recursal identificada no texto PJe: '{label}'."

        if re.search(r"\b(despacho|notificacao|intimacao|citacao)\b", search_corpus, re.IGNORECASE):
            label = "Obrigações Trabalhistas em Tramitação"
            return [label], f"Processo ativo em fase de intimação/despacho: '{label}'."

        return ["Outros Motivos Trabalhistas"], None

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
        max_pages: int = 2,
        page_size: int = 50,
        fast_path: bool = False,
    ) -> Any:
        """
        Coleta publicações no Comunica PJe com controle de taxa e suporte a CNPJ.
        Utiliza itensPorPagina <= 50 (limite máximo e estrito da API do CNJ).
        """
        official_name = ""
        if is_cnpj(company_query):
            official_name = fetch_cnpj_brasilapi(company_query) or ""
        else:
            from app.services.economic_service import find_company_cnpj_by_name
            reg = find_company_cnpj_by_name(company_query)
            if reg and reg.get("razao_social"):
                official_name = reg["razao_social"]

        brand_term = extract_brand_search_term(
            raw_input=company_query,
            razao_social=official_name,
            nome_fantasia=company_query if not is_cnpj(company_query) else "",
        )

        self.searcher.session = self.session
        self.searcher.datajud_key = self.datajud_key
        if fast_path:
            return self.searcher.fetch_single_comunica_page(brand_term, page_num=1, timeout=5.0)
        procs, disc = self.searcher.fetch_comunica_pje_all_branches(brand_term, max_pages=max_pages if max_pages > 2 else 10)
        self.last_discovered_entities = list(disc)
        return procs

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
            {"terms": {"numeroProcesso": proc_numbers[:50]}}
        ]
        if since_date:
            must_clauses.append({
                "range": {
                    "dataHoraUltimaAtualizacao": {"gt": since_date}
                }
            })

        payload = {
            "size": len(proc_numbers[:50]),
            "query": {"bool": {"must": must_clauses}} if since_date else {"terms": {"numeroProcesso": proc_numbers[:50]}}
        }

        try:
            resp = self.session.post(endpoint, json=payload, headers=headers, timeout=25)
            if resp.ok:
                return resp.json().get("hits", {}).get("hits", [])
        except Exception as exc:
            logger.debug("Consulta ao tribunal '%s' no DataJud rápida: %s", tribunal, exc)

        return []

    def _enrich_with_datajud(
        self,
        processes_map: Dict[str, Any],
        since_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Enriquece os processos com o DataJud utilizando busca otimizada
        agrupada nos Top Tribunais (timeout 6.0s) e fallback semântico do Comunica PJe.
        """
        self.searcher.session = self.session
        self.searcher.datajud_key = self.datajud_key
        return self.searcher.enrich_top_tribunals_datajud(processes_map, max_tribunals=3, timeout=6.0)

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
        search_strategy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executa a análise da empresa com dados reais completos.
        - Se fast_path=True: busca rápida (< 3s na primeira página).
        - Se fast_path=False: Deep Scan em segundo plano varrendo em paralelo até 12 páginas (até 600 publicações)
          para capturar todas as filiais e empresas vinculadas do grupo.
        """
        cache_key = company_name.strip().lower()
        if not fast_path and cache_key in self._cache:
            return self._cache[cache_key]

        max_pages = 1 if fast_path else 12
        discovered_entities: List[str] = []

        query_term = None
        if search_strategy and search_strategy.get("comunica_query_term"):
            query_term = search_strategy["comunica_query_term"]

        if fast_path and query_term:
            processes_map = self.searcher.fetch_single_comunica_page(query_term, page_num=1, timeout=5.0) or {}
        else:
            search_input = query_term or company_name
            processes_map = self._fetch_comunica_pje(search_input, max_pages=max_pages, fast_path=fast_path) or {}
        
        discovered_entities = getattr(self, "last_discovered_entities", [])

        if not processes_map:
            empty_res = self._calculate_metrics(company_name, [], scan_status="COMPLETED")
            empty_res["discovered_entities"] = discovered_entities
            empty_res["ai_analysis"] = self.ai_analyzer.analyze_company(
                {"empresa": company_name, "total_processos": 0}
            )
            if not fast_path:
                self._cache[cache_key] = empty_res
            return empty_res

        # 2. Enriquecimento:
        if fast_path:
            # REGRA 2: Fast Path Ultra-Rápido (< 2s)
            # NÃO chama DataJud! Processa os itens da página 1 via _merge_process_data_fast em ~1ms
            processes = [
                self.searcher._merge_process_data_fast(cnj, com_data if isinstance(com_data, dict) else {}, None)
                for cnj, com_data in processes_map.items()
            ]
        else:
            # REGRA 3: Deep Scan com Time-Boxing Rígido (Top 3 tribunais, timeout 6.0s)
            processes = self._enrich_with_datajud(processes_map)

        # 3. Cálculo de métricas via BayesianRiskPredictor
        # CRITÉRIO DE PARADA CORRETO: verifica se a página bruta tinha 50 itens
        has_more = getattr(self.searcher, "last_page_has_more", False) or len(processes_map) >= 50
        status = "PARTIAL_FAST_PATH" if (fast_path and has_more) else "COMPLETED"
        metrics = self._calculate_metrics(company_name, processes, scan_status=status)
        metrics["discovered_entities"] = discovered_entities

        # 4. Geração de parecer de IA (radarjusAgent ADK)
        ai_result = self.ai_analyzer.analyze_company(metrics)
        metrics["ai_analysis"] = ai_result

        if not fast_path:
            self._cache[cache_key] = metrics

        return metrics

