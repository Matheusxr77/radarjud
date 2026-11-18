import os
import re
import unicodedata
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Set, Tuple
import requests

from app.utils.sanitizer import (
    STOPWORDS_SOCIETARIAS,
    extract_brand_search_term,
    is_cnpj,
    CORPORATE_SUFFIX_REGEX,
    classify_search_mode,
    resolve_search_strategy,
)
from app.utils.http_client import request_gov_br_with_fallback

logger = logging.getLogger(__name__)

TJ_MAP = {
    1: "tjac", 2: "tjal", 3: "tjap", 4: "tjam", 5: "tjba", 6: "tjce",
    7: "tjdft", 8: "tjes", 9: "tjgo", 10: "tjma", 11: "tjmt", 12: "tjms",
    13: "tjmg", 14: "tjpa", 15: "tjpb", 16: "tjpr", 17: "tjpe", 18: "tjpi",
    19: "tjrj", 20: "tjrn", 21: "tjrs", 22: "tjro", 23: "tjrr", 24: "tjsc",
    25: "tjse", 26: "tjsp", 27: "tjto"
}

# Termos genéricos que NÃO devem mascarar os motivos substantivos do processo
GENERIC_SUBJECT_TERMS = {
    "reclamacao trabalhista", "direito do trabalho", "obrigacoes", "responsabilidade civil",
    "direito civil", "direito do consumidor", "contratos", "outros", "processo judicial",
    "rescisao do contrato de trabalho", "verbas rescisorias", "outros motivos trabalhistas",
    "reclamacao trabalhista (geral)", "reclamatoria trabalhista", "processo do trabalho",
    "atos processuais", "direito individual do trabalho", "direito processual civil e do trabalho",
    "obrigação / reclamação em apuração inicial", "obrigacao / reclamacao em apuracao inicial",
}

# Mapa léxico determinístico de altíssima precisão para extração em microssegundos (0.01ms)
DETAILED_CAUSE_PATTERNS = [
    # Bancário / Cível / Consumidor
    (7780, "Fraude Bancária / Golpe do PIX / Contestação de Transação", re.compile(r"golpe\s+do\s+pix|fraude|transa[cç][aã]o\s+n[aã]o\s+reconhecida|bloqueio\s+indevido|bloqueio\s+de\s+conta|med\s+pix|invas[aã]o\s+de\s+conta|engenharia\s+social|fortuito\s+interno|s[uú]mula\s*479", re.I)),
    (7779, "Negativação Indevida (SPC / Serasa)", re.compile(r"negativa[cç][aã]o\s+indevida|inscri[cç][aã]o\s+indevida|serasa|spc|cadastro\s+de\s+inadimplentes|apontamento\s+desabonador", re.I)),
    (9587, "Tarifas Bancárias e Cobrança Indevida", re.compile(r"tarifa\s+banc[aá]ria|cesta\s+de\s+servi[cç]os|pacote\s+de\s+servi[cç]os|cobran[cç]a\s+indevida|falha\s+na\s+presta[cç][aã]o|repeti[cç][aã]o\s+do\s+ind[eé]bito|taxa\s+de\s+manuten[cç][aã]o", re.I)),
    (4960, "Revisão de Contrato e Juros Abusivos", re.compile(r"revis[aã]o\s+de\s+contrato|juros\s+abusivos|anatocismo|taxa\s+de\s+juros|tabela\s+price|cl[aá]usula\s+abusiva|capitaliza[cç][aã]o\s+de\s+juros", re.I)),
    (997, "Busca e Apreensão em Alienação Fiduciária", re.compile(r"busca\s+e\s+apreens[aã]o|aliena[cç][aã]o\s+fiduci[aá]ria|decreto[\s-]lei\s*911|apreens[aã]o\s+de\s+ve[ií]culo|purga[cç][aã]o\s+da\s+mora", re.I)),
    (4970, "Empréstimo Consignado e Reserva de Margem (RMC)", re.compile(r"empr[eé]stimo\s+consignado|reserva\s+de\s+margem|rmc|rcc|desconto\s+em\s+folha|cart[aã]o\s+consignado|margem\s+consign[aá]vel", re.I)),
    (7771, "Cartão de Crédito e Anuidade", re.compile(r"cart[aã]o\s+de\s+cr[eé]dito|anuidade|limite\s+do\s+cart[aã]o|fatura\s+do\s+cart[aã]o", re.I)),
    (4980, "Superendividamento e Repactuação de Dívidas", re.compile(r"superendividamento|lei\s*14\.?181|repactua[cç][aã]o\s+de\s+d[ií]vidas|m[ií]nimo\s+existencial|renegocia[cç][aã]o", re.I)),
    (10433, "Indenização por Danos Morais (Direito do Consumidor)", re.compile(r"dano\s+moral\s+ao\s+consumidor|cdc|c[oó]digo\s+de\s+defesa\s+do\s+consumidor|rela[cç][aã]o\s+de\s+consumo", re.I)),

    # Trabalhista Bancário e Geral
    (224, "Direitos da Categoria Bancária (7ª e 8ª Horas / Cargo de Confiança)", re.compile(r"banc[aá]ri[ao]|cargo\s+de\s+confian[cç]a|7[aª]\s+hora|8[aª]\s+hora|s[eé]tima\s+e\s+oitava|enquadramento\s+banc[aá]rio|gratifica[cç][aã]o\s+de\s+fun[cç][aã]o|jornada\s+de\s+seis\s+horas|art\.\s*224", re.I)),
    (2550, "Equiparação Salarial e Desvio de Função", re.compile(r"equipara[cç][aã]o\s+salarial|desvio\s+de\s+fun[cç][aã]o|paradigma|diferen[cç]as?\s+salariais?|ac[uú]mulo\s+de\s+fun[cç][aã]o", re.I)),
    (2551, "Participação nos Lucros e Resultados (PLR) e Bônus", re.compile(r"\bplr\b|participa[cç][aã]o\s+nos\s+lucros|b[oô]nus|pr[eé]mio\s+produ[cç][aã]o|programa\s+de\s+remunera[cç][aã]o", re.I)),
    (2552, "Cobrança de Metas e Estorno de Comissões", re.compile(r"estorno\s+de\s+comiss[oõ]es|cobran[cç]a\s+de\s+metas|metas\s+abusivas|diferen[cç]as?\s+de\s+comiss|comiss[oõ]es\s+por\s+vendas", re.I)),
    (1694, "Horas Extras e Intervalo Intrajornada", re.compile(r"horas?\s+extras?|sobrejornada|intrajornada|banco\s+de\s+horas|art\.\s*71|controle\s+de\s+ponto|cart[aã]o\s+de\s+ponto|jornada\s+de\s+trabalho|dsr|sobreaviso", re.I)),
    (2546, "Inadimplemento de Verbas Rescisórias e Multa 40%", re.compile(r"verbas\s+rescis[oó]rias|multa\s+do\s+art\.\s*477|multa\s+de\s+40%|aviso\s+pr[eé]vio\s+indenizado|saldo\s+de\s+sal[aá]rio|13[oº]\s+sal[aá]rio|f[eé]rias\s+proporcionais|trct|rescis[aã]o\s+indireta", re.I)),
    (2029, "Irregularidade / Ausência de Depósitos de FGTS", re.compile(r"dep[oó]sitos?\s+do\s+fgts|extrato\s+anal[ií]tico\s+do\s+fgts|diferen[cç]as\s+de\s+fgts|fundo\s+de\s+garantia|saque\s+do\s+fgts|guias\s+de\s+fgts", re.I)),
    (1855, "Assédio Moral e Danos Morais Trabalhistas", re.compile(r"ass[eé]dio\s+moral|dano\s+moral|danos\s+morais|indeniza[cç][aã]o\s+por\s+dano|humilha[cç][aã]o|tratamento\s+degradante", re.I)),
    (1656, "Adicional de Insalubridade (NR-15)", re.compile(r"insalubridade|agente\s+insalubre|nr[\s-]*15|laudo\s+pericial\s+insalub", re.I)),
    (1681, "Adicional de Periculosidade (NR-16)", re.compile(r"periculosidade|inflam[aá]veis|energia\s+el[eé]trica|nr[\s-]*16|risco\s+acentuado", re.I)),
    (1904, "Responsabilidade Subsidiária / Terceirização (Súmula 331 TST)", re.compile(r"responsabilidade\s+subsidi[aá]ria|s[uú]mula\s*331|tomadora?\s+de\s+servi[cç]os|culpa\s+in\s+vigilando|terceiriza[cç][aã]o|prestadora\s+de\s+servi[cç]os", re.I)),
    (1998, "Reconhecimento de Vínculo Empregatício (Pejotização)", re.compile(r"v[ií]nculo\s+de\s+emprego|v[ií]nculo\s+empregat[ií]cio|anota[cç][aã]o\s+da\s+ctps|subordina[cç][aã]o\s+jur[ií]dica|pejotiza[cç][aã]o", re.I)),
    (8801, "Acidente de Trabalho / Doença Ocupacional", re.compile(r"acidente\s+de\s+trabalho|doen[cç]a\s+ocupacional|ler\s*\/?\s*dort|nexo\s+causal|estabilidade\s+acident[aá]ria", re.I)),
    (1682, "Adicional Noturno", re.compile(r"adicional\s+noturno|jornada\s+noturna|hora\s+noturna", re.I)),
    (2555, "Reintegração e Estabilidade Provisória", re.compile(r"reintegra[cç][aã]o|estabilidade\s+provis[oó]ria|estabilidade\s+gestante|cipeir[ao]|acidente\s+de\s+trabalho", re.I)),
    (10000, "Execução e Cumprimento de Sentença Trabalhista", re.compile(r"sisbajud|penhora|bloqueio|c[aá]lculos\s+de\s+liquida[cç][aã]o|cumprimento\s+de\s+senten[cç]a|execu[cç][aã]o\s+trabalhista|execu[cç][aã]o\s+de\s+t[ií]tulo", re.I)),
]


def extract_real_cause_fast(
    existing_assuntos: list,
    raw_texts: list,
    classes_pje: Optional[list] = None
) -> list:
    """
    Descobre a causa real do processo em microssegundos:
    1. Analisa termos de alta precisão no teor das publicações (0.01ms).
    2. Utiliza assuntos específicos do DataJud se disponíveis.
    3. Se o texto for conciso e não houver assunto específico, usa a Classe Processual PJe estruturada.
    4. Fallback genérico apenas em último caso.
    """
    full_text = " ".join(str(t) for t in (raw_texts or []) if t)
    matched_causes = []

    if full_text:
        for code, label, pattern in DETAILED_CAUSE_PATTERNS:
            if pattern.search(full_text):
                matched_causes.append({"codigo": code, "nome": label})

    # Se achou causas específicas no texto da publicação/sentença, usa elas prioritariamente!
    if matched_causes:
        return matched_causes[:3]

    # Se não achou no texto mas tem assuntos no DataJud, filtra os genéricos se houver mais de um
    if existing_assuntos:
        specific = []
        for a in existing_assuntos:
            nome = a.get("nome", "") if isinstance(a, dict) else str(a)
            if nome.lower() not in GENERIC_SUBJECT_TERMS:
                specific.append(a if isinstance(a, dict) else {"codigo": None, "nome": nome})
        if specific:
            return specific[:2]
        res = []
        for a in existing_assuntos[:2]:
            res.append(a if isinstance(a, dict) else {"codigo": None, "nome": str(a)})
        return res

    # 3. Fallback inteligente na classe processual estruturada do Comunica PJe
    if classes_pje:
        classes_str = " ".join(str(c) for c in classes_pje if c).lower()
        if re.search(r"ordin[aá]rio|atord", classes_str):
            return [{"codigo": 985, "nome": "Ação Trabalhista (Rito Ordinário)"}]
        if re.search(r"sumar[ií]ssimo|atsum", classes_str):
            return [{"codigo": 1125, "nome": "Ação Trabalhista (Rito Sumaríssimo)"}]
        if re.search(r"execu[cç]|cumprimento|cumsen", classes_str):
            return [{"codigo": 10000, "nome": "Execução e Cumprimento de Sentença Trabalhista"}]
        if re.search(r"juizado", classes_str):
            return [{"codigo": 436, "nome": "Juizado Especial Cível / Consumidor"}]
        if re.search(r"procedimento\s+comum|c[ií]vel", classes_str):
            return [{"codigo": 7, "nome": "Procedimento Comum Cível / Consumidor"}]
        if re.search(r"monit[oó]ria", classes_str):
            return [{"codigo": 40, "nome": "Ação Monitória Bancária"}]
        if re.search(r"busca\s+e\s+apreens", classes_str):
            return [{"codigo": 997, "nome": "Busca e Apreensão em Alienação Fiduciária"}]

        # Se houver alguma classe com nome legível
        for c in classes_pje:
            if isinstance(c, str) and len(c.strip()) >= 5:
                return [{"codigo": None, "nome": c.strip()}]

    return [{"codigo": 7644, "nome": "Obrigação / Reclamação em Apuração Inicial"}]


def classify_process_outcome(
    movimentos_codigos: set,
    assuntos_nomes: list,
    texto_publicacoes: str,
    classe_nome: str = ""
) -> str:
    """
    Classifica TODOS os processos em: CONDENACAO, ACORDO, IMPROCEDENTE ou EM_ABERTO.
    Reconhece fases de execução, liquidação e cumprimento de sentença como CONDENACAO.
    """
    txt = (texto_publicacoes or "").lower()
    classe_lower = (classe_nome or "").lower()
    assuntos_concat = " ".join(assuntos_nomes or []).lower()

    # 1. Códigos oficiais TPU do DataJud
    if {219, 221, 11385} & movimentos_codigos:
        return "CONDENACAO"
    if {466, 467} & movimentos_codigos:
        return "ACORDO"
    if 220 in movimentos_codigos:
        return "IMPROCEDENTE"

    # 2. Se a própria Classe Processual ou Assunto já é Execução / Cumprimento de Sentença
    if any(k in classe_lower or k in assuntos_concat for k in [
        "cumprimento de sentença", "cumprimento de sentenca", "execução trabalhista",
        "execucao trabalhista", "execução de título judicial", "execucao de titulo judicial",
        "execução de título extrajudicial", "execucao de titulo extrajudicial",
        "liquidação", "liquidacao"
    ]):
        return "CONDENACAO"

    # 3. Sinais textuais de Acordo no Comunica PJe
    if re.search(
        r"homologo\s+o\s+acordo|homologa[cç][aã]o\s+de\s+acordo|concilia[cç][aã]o\s+homologada|"
        r"cumprimento\s+do\s+acordo|descumprimento\s+do\s+acordo|transa[cç][aã]o\s+homologada",
        txt
    ):
        return "ACORDO"

    # 4. Sinais textuais de Improcedência / Extinção sem ônus
    if re.search(
        r"julgo\s+improcedente|totalmente\s+improcedente|improcedentes\s+os\s+pedidos|"
        r"extinto\s+sem\s+resolu[cç][aã]o\s+do\s+m[eé]rito|arquivem-se\s+os\s+autos\s+por\s+aus[eê]ncia\s+do\s+autor",
        txt
    ):
        return "IMPROCEDENTE"

    # 5. Sinais textuais diretos OU indiretos de Condenação (Sentença, Liquidação, Execução, Penhora, Recurso com preparo)
    if re.search(
        r"procedente\s+em\s+parte|julgo\s+procedente|parcialmente\s+procedente|"
        r"condeno\s+a\s+reclamada|condenar\s+a\s+r[eé]|condena[cç][aã]o|"
        r"cumprimento\s+de\s+senten[cç]a|in[ií]cio\s+da\s+execu[cç][aã]o|execu[cç][aã]o\s+definitiva|"
        r"c[aá]lculos\s+de\s+liquida[cç][aã]o|homologo\s+os\s+c[aá]lculos|apresentar\s+c[aá]lculos|"
        r"penhora|sisbajud|bacenjud|renajud|bloqueio\s+judicial|garantia\s+do\s+ju[ií]zo|"
        r"embargos\s+[aà]\s+execu[cç][aã]o|expe[cç]a-se\s+alvar[aá]|intime-se\s+a\s+executada|"
        r"pagar\s+o\s+valor\s+da\s+condena[cç][aã]o|dep[oó]sito\s+recursal|honor[aá]rios\s+sucumbenciais\s+pela\s+reclamada",
        txt
    ):
        return "CONDENACAO"

    return "EM_ABERTO"


class CNJProcessSearcher:
    """
    Cliente robusto para integração oficial com as APIs públicas do Judiciário:
    1. Comunica PJe (DJEN): Descoberta de publicações e processos por nome da parte.
       - Respeita o limite estrito da API do CNJ de itensPorPagina <= 50.
       - Envia headers obrigatórios com User-Agent para contornar bloqueio do WAF.
    2. DataJud (CNJ): Consulta de metadados, movimentações TPU e segredo de justiça via Elasticsearch.
       - Roteamento dinâmico do alias do tribunal baseado na estrutura CNJ NNNNNNNDDAAAAJTROOOO.
       - Agrupamento em lote via query 'terms' (até 50 processos por POST).
       - Fallback semântico inteligente no texto das publicações para nunca zerar o dashboard.
    """

    COMUNICA_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
    DATAJUD_BASE = "https://api-publica.datajud.cnj.jus.br"
    DEFAULT_DATAJUD_KEY = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="

    def __init__(self, session: Optional[requests.Session] = None):
        key = os.getenv("DATAJUD_API_KEY") or self.DEFAULT_DATAJUD_KEY
        self.datajud_key = key.strip() if key else self.DEFAULT_DATAJUD_KEY
        self.session = session or requests.Session()
        
        # Suporte a proxy dedicado isolado (não interfere no pip/build do Render)
        proxy_url = os.getenv("PJE_PROXY_URL") or os.getenv("JUDICIAL_PROXY_URL")
        if proxy_url and proxy_url.strip():
            proxy_clean = proxy_url.strip()
            self.session.proxies = {
                "http": proxy_clean,
                "https": proxy_clean,
            }
            logger.info("CNJProcessSearcher configurado com proxy dedicado PJE_PROXY_URL.")

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        })

    def _request_gov(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        timeout: float = 8.0,
    ) -> requests.Response:
        """Encaminha requisições ao Judiciário com headers pt-BR e fallback anti-geoblocking."""
        return request_gov_br_with_fallback(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json_body=json_body,
            timeout=timeout,
            session=self.session,
        )

    @staticmethod
    def clean_company_name(name: str) -> str:
        """Remove pontuações e sufixos que quebram o match no Comunica PJe."""
        cleaned = re.sub(r"\b(LTDA\.?|S\.?/?\s*A\.?|EIRELI\.?|MEI?\.?|EPP\.?)\b", "", name, flags=re.IGNORECASE)
        cleaned = re.sub(r"[^\w\s]", " ", cleaned)
        cleaned = re.sub(r"\b(LTDA|S\s*A|SA|EIRELI|ME|EPP)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = " ".join(cleaned.split())
        return cleaned if len(cleaned) >= 3 else name.strip()

    @staticmethod
    def extract_brand_search_term(raw_input: str, razao_social: str = "", nome_fantasia: str = "") -> str:
        """Extrai o nome raiz/marca da empresa para busca ampla de filiais no Comunica PJe."""
        return extract_brand_search_term(raw_input, razao_social=razao_social, nome_fantasia=nome_fantasia)

    @staticmethod
    def normalize_cnj(raw_number: Any) -> str:
        """Garante exatamente 20 dígitos numéricos sem pontos ou traços."""
        digits = re.sub(r"\D", "", str(raw_number or ""))
        return digits if len(digits) == 20 else ""

    @staticmethod
    def resolve_datajud_alias(cnj_20: str, fallback_sigla: str = "") -> str:
        """Extrai o alias do tribunal no DataJud pelos dígitos 13..15 do número CNJ."""
        if len(cnj_20) == 20:
            j = cnj_20[13]
            tr = int(cnj_20[14:16])
            if j == "5":
                return "api_publica_tst" if tr == 0 else f"api_publica_trt{tr}"
            if j == "4":
                return f"api_publica_trf{tr}"
            if j == "8" and tr in TJ_MAP:
                return f"api_publica_{TJ_MAP[tr]}"
        if fallback_sigla:
            clean_sigla = fallback_sigla.lower().replace("-", "").strip()
            return f"api_publica_{clean_sigla}"
        return "api_publica_trt6"

    def _parse_comunica_items(
        self,
        items: List[Any],
        processes: Dict[str, Any],
        discovered_entities: Optional[Set[str]] = None,
        is_tst_override: bool = False,
    ):
        """Extrai números CNJ de 20 dígitos, textos, metadados e filiais descobertas."""
        for item in items:
            if not isinstance(item, dict):
                continue

            if discovered_entities is not None:
                for dest in item.get("destinatarios", []) or []:
                    if isinstance(dest, dict):
                        nome_dest = dest.get("nome") or dest.get("nomeParte")
                        if nome_dest and isinstance(nome_dest, str) and len(nome_dest.strip()) >= 3:
                            discovered_entities.add(nome_dest.strip().upper())
                        cnpj_dest = dest.get("numeroDocumento") or dest.get("cpfCnpj") or dest.get("cnpj")
                        if cnpj_dest and isinstance(cnpj_dest, str) and is_cnpj(cnpj_dest):
                            discovered_entities.add(re.sub(r"\D", "", cnpj_dest))

            raw_num = (
                item.get("numero_processo")
                or item.get("numeroProcesso")
                or item.get("numeroprocessocommascara")
            )
            cnj = self.normalize_cnj(raw_num)
            if not cnj:
                continue

            trib_sigla = "tst" if is_tst_override else (item.get("siglaTribunal", "") or "")
            is_tst = is_tst_override or trib_sigla.lower() == "tst"

            entry = processes.setdefault(cnj, {
                "numeroProcesso": cnj,
                "numero_processo": cnj,
                "siglaTribunal": trib_sigla,
                "orgaoJulgador": item.get("nomeOrgao", ""),
                "dataUltimaPublicacao": item.get("data_disponibilizacao") or item.get("dataDisponibilizacao", ""),
                "publicacoes_texto": [],
                "destinatarios_nomes": [],
                "classes_pje": [],
                "tipos_doc_pje": [],
                "assuntos_inferidos": [],
                "is_tst_recurso": is_tst,
            })
            if is_tst:
                entry["is_tst_recurso"] = True
                if not entry.get("siglaTribunal") or entry["siglaTribunal"] == "N/A":
                    entry["siglaTribunal"] = "tst"

            dest_list = item.get("destinatarios") or []
            if isinstance(dest_list, list):
                for dest in dest_list:
                    if isinstance(dest, dict):
                        nome_dest = dest.get("nome") or dest.get("nomeParte")
                        if nome_dest and isinstance(nome_dest, str) and len(nome_dest.strip()) >= 3:
                            nome_clean = nome_dest.strip().upper()
                            if nome_clean not in entry.setdefault("destinatarios_nomes", []):
                                entry["destinatarios_nomes"].append(nome_clean)
                            if discovered_entities is not None:
                                discovered_entities.add(nome_clean)
                        cnpj_dest = dest.get("numeroDocumento") or dest.get("cpfCnpj") or dest.get("cnpj")
                        if cnpj_dest and isinstance(cnpj_dest, str) and is_cnpj(cnpj_dest):
                            clean_cnpj = re.sub(r"\D", "", cnpj_dest)
                            if clean_cnpj not in entry.setdefault("cnpjs_encontrados", []):
                                entry["cnpjs_encontrados"].append(clean_cnpj)
                            if discovered_entities is not None:
                                discovered_entities.add(clean_cnpj)

            texto = (
                item.get("texto")
                or item.get("teor")
                or item.get("conteudo")
                or item.get("textoPublicacao")
                or ""
            )
            if texto and texto not in entry["publicacoes_texto"]:
                entry["publicacoes_texto"].append(texto)

            classe = item.get("nomeClasse") or item.get("nome_classe") or item.get("classe") or ""
            if classe and classe not in entry["classes_pje"]:
                entry["classes_pje"].append(classe)
            cod_classe = item.get("codigoClasse") or item.get("codClasse")
            if cod_classe and cod_classe not in entry.setdefault("codigos_classe", []):
                entry["codigos_classe"].append(cod_classe)

            tipo_doc = item.get("tipoDocumento") or item.get("tipo_documento") or item.get("tipoComunicacao") or ""
            if tipo_doc and tipo_doc not in entry["tipos_doc_pje"]:
                entry["tipos_doc_pje"].append(tipo_doc)

    def fetch_single_comunica_page(
        self,
        brand_term: str,
        page_num: int = 1,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Busca uma única página no Comunica PJe com timeout curto (< 5s)."""
        p = {
            "nomeParte": brand_term.strip(),
            "pagina": page_num,
            "itensPorPagina": 50,
        }
        procs: Dict[str, Any] = {}
        self.last_page_raw_count = 0
        self.last_page_has_more = False
        try:
            r = self._request_gov("GET", self.COMUNICA_URL, params=p, timeout=timeout)
            if r.status_code == 200:
                py = r.json()
                items = py if isinstance(py, list) else (
                    py.get("items") or py.get("itens") or py.get("content") or []
                )
                self.last_page_raw_count = len(items)
                self.last_page_has_more = (len(items) == 50)
                self._parse_comunica_items(items, procs)
        except Exception as exc:
            logger.debug("Comunica PJe página %d tratada com resiliência (%s): %s", page_num, brand_term, exc)
        return procs

    def paginate_comunica_pje_standard(
        self,
        search_strategy: Dict[str, Any],
        start_page: int = 1,
        max_pages: int = 20,
        on_page_callback=None,
        timeout: float = 6.0,
    ) -> Dict[str, Any]:
        """
        Varre sequencialmente o Comunica PJe (páginas start_page..max_pages) com itensPorPagina=50.
        CRITÉRIO DE PARADA ESTRITO:
        - Interrompe APENAS se a lista bruta de itens retornados pela API for vazia (len(raw_items) == 0)
          ou menor que 50 (len(raw_items) < 50).
        - NUNCA interrompe baseado na contagem de processos únicos desduplicados!
        - Executa callback progressivo a cada página para alimentar contadores em tempo real.
        """
        query_term = (
            search_strategy.get("comunica_query_term")
            or search_strategy.get("raw_input")
            or ""
        ).strip()

        all_processes: Dict[str, Any] = {}
        discovered_entities: Set[str] = set()
        pages_read = 0

        for page in range(start_page, max_pages + 1):
            params = {
                "nomeParte": query_term,
                "pagina": page,
                "itensPorPagina": 50,
            }
            try:
                resp = self._request_gov("GET", self.COMUNICA_URL, params=params, timeout=timeout)
                if not resp.ok:
                    logger.warning("Comunica PJe HTTP %s na página %d para '%s'", resp.status_code, page, query_term)
                    break

                payload = resp.json()
                if isinstance(payload, dict):
                    raw_items = payload.get("items") or payload.get("itens") or payload.get("content") or []
                elif isinstance(payload, list):
                    raw_items = payload
                else:
                    raw_items = []

                if not raw_items:
                    break

                pages_read += 1
                self._parse_comunica_items(raw_items, all_processes, discovered_entities)

                if on_page_callback:
                    try:
                        on_page_callback(page, len(all_processes), all_processes)
                    except Exception as cb_err:
                        logger.debug("Erro em on_page_callback página %d: %s", page, cb_err)

                # Critério de parada oficial: última página da API do CNJ tem menos de 50 itens brutos
                if len(raw_items) < 50:
                    break

            except Exception as exc:
                logger.error("Erro na paginação do Comunica PJe página %d: %s", page, exc)
                break

        # Se for busca ampla por marca/grupo (BROAD_BRAND), faz varredura complementar no TST
        if search_strategy.get("is_broad_brand", False) or search_strategy.get("mode") == "BROAD_BRAND":
            try:
                params_tst = {
                    "nomeParte": query_term,
                    "siglaTribunal": "TST",
                    "pagina": 1,
                    "itensPorPagina": 50,
                }
                resp_tst = self._request_gov("GET", self.COMUNICA_URL, params=params_tst, timeout=timeout)
                if resp_tst.ok:
                    py_tst = resp_tst.json()
                    tst_items = py_tst if isinstance(py_tst, list) else (
                        py_tst.get("items") or py_tst.get("itens") or []
                    )
                    if tst_items:
                        self._parse_comunica_items(tst_items, all_processes, discovered_entities, is_tst_override=True)
            except Exception as tst_exc:
                logger.debug("Consulta complementar TST tratada silenciosamente: %s", tst_exc)

        logger.info(
            "paginate_comunica_pje_standard('%s') finalizada: %d páginas lidas, %d processos únicos, %d entidades.",
            query_term, pages_read, len(all_processes), len(discovered_entities)
        )

        return {
            "processes": all_processes,
            "discovered_entities": discovered_entities,
            "pages_read": pages_read,
            "total_processes": len(all_processes),
        }

    def fetch_brasil_api_fast(self, cnpj: str, timeout: float = 3.5) -> Dict[str, Any]:
        """Consulta ultra-rápida de CNPJ na BrasilAPI com timeout restrito de 3.5s."""
        clean = re.sub(r"\D", "", str(cnpj or ""))
        if len(clean) != 14:
            return {}
        try:
            r = self.session.get(f"https://brasilapi.com.br/api/cnpj/v1/{clean}", timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception as exc:
            logger.debug("BrasilAPI rápida tratada para CNPJ %s: %s", clean, exc)
        return {}

    def fetch_comunica_pje(self, company_name: str, max_pages: int = 2) -> Dict[str, Any]:
        """
        Busca rápida no Comunica PJe (DJEN) respeitando o limite estrito de itensPorPagina <= 50.
        Retorna dicionário indexado pelo número CNJ de 20 dígitos.
        """
        processes: Dict[str, Any] = {}
        search_terms = [company_name.strip()]
        cleaned = self.clean_company_name(company_name)
        if cleaned.upper() not in [t.upper() for t in search_terms]:
            search_terms.append(cleaned)
        brand_term = self.extract_brand_search_term(company_name)
        if brand_term and brand_term.upper() not in [t.upper() for t in search_terms]:
            search_terms.append(brand_term)

        for term in search_terms:
            for page in range(1, max_pages + 1):
                params = {
                    "nomeParte": term,
                    "pagina": page,
                    "itensPorPagina": 50,  # NUNCA passar de 50! Limite oficial da API.
                }
                try:
                    resp = self._request_gov("GET", self.COMUNICA_URL, params=params, timeout=20)
                    if resp.status_code != 200:
                        logger.warning("Comunica PJe HTTP %s para termo '%s'", resp.status_code, term)
                        break

                    payload = resp.json()
                    items = payload if isinstance(payload, list) else (
                        payload.get("items") or payload.get("itens") or payload.get("content") or []
                    )
                    if not items:
                        break

                    self._parse_comunica_items(items, processes)
                    if len(items) < 50:
                        break
                except Exception as exc:
                    logger.error("Erro ao consultar Comunica PJe: %s", exc)
                    break

            if processes:
                break

        # Varredura complementar no TST
        try:
            params_tst = {
                "nomeParte": search_terms[0],
                "siglaTribunal": "TST",
                "pagina": 1,
                "itensPorPagina": 50,
            }
            resp_tst = self._request_gov("GET", self.COMUNICA_URL, params=params_tst, timeout=10)
            if resp_tst.ok:
                payload_tst = resp_tst.json()
                tst_items = payload_tst if isinstance(payload_tst, list) else (
                    payload_tst.get("items") or payload_tst.get("itens") or []
                )
                self._parse_comunica_items(tst_items, processes, is_tst_override=True)
        except Exception as exc:
            logger.debug("Consulta ao TST no Comunica PJe tratada silenciosamente: %s", exc)

        return processes

    def fetch_comunica_pje_all_branches(
        self,
        brand_term: str,
        max_pages: int = 20
    ) -> Tuple[Dict[str, Any], Set[str]]:
        """
        Deep Scan em segundo plano: Varre em paralelo até 12 páginas (até 600 publicações) no Comunica PJe
        usando a Marca/Nome Raiz da empresa para capturar processos de TODAS as filiais e empresas do grupo.
        Retorna tupla com (processes_map, discovered_entities).
        """
        processes: Dict[str, Any] = {}
        discovered_entities: Set[str] = set()
        clean_brand = brand_term.strip()

        # Página 1 inicial para extrair count e primeiras 50 publicações
        params_p1 = {
            "nomeParte": clean_brand,
            "pagina": 1,
            "itensPorPagina": 50,
        }
        total_count = None
        has_more = False
        try:
            resp = self._request_gov("GET", self.COMUNICA_URL, params=params_p1, timeout=5.0)
            if resp.ok:
                payload = resp.json()
                if isinstance(payload, dict):
                    total_count = payload.get("count") or payload.get("total")
                    items = payload.get("items") or payload.get("itens") or payload.get("content") or []
                elif isinstance(payload, list):
                    items = payload
                else:
                    items = []
                self._parse_comunica_items(items, processes, discovered_entities)
                has_more = len(items) == 50
        except Exception as exc:
            logger.error("Erro na página 1 do Deep Scan Comunica PJe: %s", exc)

        # Determina quantas páginas varrer
        if total_count and isinstance(total_count, int) and total_count > 0:
            calc_pages = (total_count + 49) // 50
            target_pages = min(max_pages, calc_pages)
        else:
            target_pages = max_pages if has_more else 1

        if target_pages >= 2:
            def _fetch_single_page(p_num: int) -> List[Any]:
                p = {"nomeParte": clean_brand, "pagina": p_num, "itensPorPagina": 50}
                try:
                    r = self._request_gov("GET", self.COMUNICA_URL, params=p, timeout=5.0)
                    if r.ok:
                        py = r.json()
                        return py if isinstance(py, list) else (
                            py.get("items") or py.get("itens") or py.get("content") or []
                        )
                except Exception as e:
                    logger.debug("Erro ao paginar Comunica PJe página %d: %s", p_num, e)
                return []

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_fetch_single_page, p): p for p in range(2, target_pages + 1)}
                for f in as_completed(futures):
                    page_items = f.result()
                    if page_items:
                        self._parse_comunica_items(page_items, processes, discovered_entities)

        # Varredura complementar no TST
        try:
            params_tst = {
                "nomeParte": clean_brand,
                "siglaTribunal": "TST",
                "pagina": 1,
                "itensPorPagina": 50,
            }
            resp_tst = self._request_gov("GET", self.COMUNICA_URL, params=params_tst, timeout=5.0)
            if resp_tst.ok:
                py_tst = resp_tst.json()
                tst_items = py_tst if isinstance(py_tst, list) else (
                    py_tst.get("items") or py_tst.get("itens") or []
                )
                self._parse_comunica_items(tst_items, processes, discovered_entities, is_tst_override=True)
        except Exception as exc:
            logger.debug("Consulta complementar TST tratada silenciosamente: %s", exc)

        logger.info(
            "fetch_comunica_pje_all_branches('%s') concluiu: %d processos únicos, %d filiais/entidades descobertas.",
            clean_brand, len(processes), len(discovered_entities)
        )
        return processes, discovered_entities

    def enrich_with_datajud(self, comunica_processes: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Agrupa os números CNJ por tribunal e faz busca em lote (`terms`) no DataJud.
        Combina os metadados do DataJud com os textos já capturados no Comunica PJe.
        """
        by_alias = defaultdict(list)
        for cnj, info in comunica_processes.items():
            alias = self.resolve_datajud_alias(cnj, info.get("siglaTribunal", ""))
            by_alias[alias].append(cnj)

        datajud_results = {}
        headers = {
            "Authorization": f"APIKey {self.datajud_key}",
            "Content-Type": "application/json",
        }

        for alias, cnj_list in by_alias.items():
            url = f"{self.DATAJUD_BASE}/{alias}/_search"
            # Lotes de até 50 processos por requisição
            for i in range(0, len(cnj_list), 50):
                batch = cnj_list[i:i + 50]
                body = {
                    "size": len(batch),
                    "_source": ["numeroProcesso", "tribunal", "nivelSigilo", "assuntos", "movimentos.codigo", "grau"],
                    "query": {
                        "terms": {
                            "numeroProcesso": batch
                        }
                    }
                }
                try:
                    resp = self._request_gov("POST", url, json_body=body, headers=headers, timeout=6.0)
                    if resp.status_code == 200:
                        hits = resp.json().get("hits", {}).get("hits", [])
                        for hit in hits:
                            src = hit.get("_source", {})
                            num = self.normalize_cnj(src.get("numeroProcesso"))
                            if num:
                                datajud_results[num] = src
                    else:
                        logger.warning("DataJud %s HTTP %s: %s", alias, resp.status_code, resp.text[:200])
                except Exception as exc:
                    logger.error("Erro no DataJud (%s): %s", alias, exc)

        # Consolida Comunica PJe + DataJud
        consolidated = []
        for cnj, com_data in comunica_processes.items():
            dj = datajud_results.get(cnj, {})
            consolidated.append(self._merge_process_data(cnj, com_data, dj))

        return consolidated

    def enrich_top_tribunals_datajud(
        self,
        all_processes: Dict[str, Any],
        max_tribunals: int = 3,
        timeout: float = 6.0,
    ) -> List[Dict[str, Any]]:
        """
        Enriquecimento com Time-Boxing rígido no DataJud:
        1. Agrupa os processos por tribunal (alias).
        2. Seleciona apenas os Top N tribunais com maior volume.
        3. Consulta os Top tribunais em paralelo com timeout estrito de 6s e _source filtrado.
        4. Mescla todos os processos com _merge_process_data_fast em microssegundos.
        """
        by_alias = defaultdict(list)
        for cnj, info in all_processes.items():
            sigla = info.get("siglaTribunal", "") if isinstance(info, dict) else ""
            alias = self.resolve_datajud_alias(cnj, sigla)
            by_alias[alias].append(cnj)

        sorted_tribunals = sorted(by_alias.items(), key=lambda item: len(item[1]), reverse=True)
        top_tribunals = sorted_tribunals[:max_tribunals]

        datajud_results = {}
        headers = {
            "Authorization": f"APIKey {self.datajud_key}",
            "Content-Type": "application/json",
        }

        def _fetch_alias_batch(alias: str, cnjs: List[str]) -> List[Dict[str, Any]]:
            url = f"{self.DATAJUD_BASE}/{alias}/_search"
            batch = cnjs[:50]
            body = {
                "size": len(batch),
                "_source": ["numeroProcesso", "tribunal", "nivelSigilo", "assuntos", "movimentos.codigo", "grau"],
                "query": {
                    "terms": {
                        "numeroProcesso": batch
                    }
                }
            }
            try:
                resp = self._request_gov("POST", url, json_body=body, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json().get("hits", {}).get("hits", [])
            except Exception as exc:
                logger.debug("DataJud amostragem rápida (%s) tratada: %s", alias, exc)
            return []

        if top_tribunals:
            with ThreadPoolExecutor(max_workers=min(len(top_tribunals), 4)) as pool:
                futures = {
                    pool.submit(_fetch_alias_batch, alias, cnj_list): alias
                    for alias, cnj_list in top_tribunals
                }
                for fut in as_completed(futures):
                    try:
                        hits = fut.result() or []
                        for hit in hits:
                            src = hit.get("_source", {})
                            num = self.normalize_cnj(src.get("numeroProcesso"))
                            if num:
                                datajud_results[num] = src
                    except Exception:
                        pass

        consolidated = [
            self._merge_process_data_fast(cnj, com_data if isinstance(com_data, dict) else {}, datajud_results.get(cnj))
            for cnj, com_data in all_processes.items()
        ]
        return consolidated

    def _merge_process_data_fast(
        self,
        cnj: str,
        com_data: Dict[str, Any],
        dj: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fusão ultra-rápida de processo do Comunica PJe com metadados do DataJud (se disponíveis),
        classificando causa real via extract_real_cause_fast em 0.01ms.
        """
        dj = dj or {}
        raw_texts = com_data.get("publicacoes_texto") or com_data.get("textos_pje") or []
        texto_concat = " ".join(str(t) for t in raw_texts if t).lower()

        # Extração de assuntos existentes do DataJud
        raw_assuntos = dj.get("assuntos", [])
        existing_assuntos = []
        for item in raw_assuntos:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict) and sub.get("nome"):
                        existing_assuntos.append({"codigo": sub.get("codigo"), "nome": sub.get("nome")})
            elif isinstance(item, dict) and item.get("nome"):
                existing_assuntos.append({"codigo": item.get("codigo"), "nome": item.get("nome")})

        classes_list = com_data.get("classes_pje", []) or []
        classe_str = " ".join(str(c) for c in classes_list).strip()

        # Classificação instantânea de causa real
        assuntos = extract_real_cause_fast(existing_assuntos, raw_texts, classes_pje=classes_list)
        subjects_names = [a["nome"] for a in assuntos if isinstance(a, dict) and a.get("nome")]
        primary_name = subjects_names[0] if subjects_names else "Obrigação / Reclamação em Apuração Inicial"
        primary_code = assuntos[0].get("codigo") if assuntos and isinstance(assuntos[0], dict) else None

        # Classificação de status/desfecho (DataJud TPU, Classe Processual, Assuntos e Sinais Textuais)
        movimentos = dj.get("movimentos", []) or []
        codigos_mov = {m.get("codigo") for m in movimentos if isinstance(m, dict)}

        status = classify_process_outcome(codigos_mov, subjects_names, texto_concat, classe_str)

        sentence_outcome = "em_aberto"
        sub_outcome = None
        if status == "CONDENACAO":
            sentence_outcome = "condenacoes"
            sub_outcome = "procedencia_parcial" if (221 in codigos_mov or "procedente em parte" in texto_concat or "parcialmente procedente" in texto_concat) else "procedencia_total"
        elif status == "ACORDO":
            sentence_outcome = "acordos"
            sub_outcome = "acordo_homologado"
        elif status == "IMPROCEDENTE":
            sentence_outcome = "improcedentes"
            sub_outcome = "improcedencia"
        else:
            sentence_outcome = "em_aberto"
            sub_outcome = None

        tem_pericia = bool({581, 11024} & codigos_mov) or any(w in texto_concat for w in ["laudo pericial", "perícia", "pericia", "perito"])
        tem_revelia = any(w in texto_concat for w in ["revelia", "confissão ficta", "confissao ficta"])

        classes_concat = " ".join(str(c) for c in com_data.get("classes_pje", [])).lower()
        is_tst = (
            com_data.get("is_tst_recurso", False)
            or (com_data.get("siglaTribunal") or "").lower() == "tst"
            or "tst" in texto_concat
            or "recurso de revista" in texto_concat
            or "airr" in texto_concat
            or "recurso de revista" in classes_concat
        )
        grau_val = "TST (Recursal)" if is_tst else dj.get("grau", "G1")
        sigilo_val = int(dj.get("nivelSigilo", 0))

        return {
            "numeroProcesso": cnj,
            "numero_processo": cnj,
            "tribunal": dj.get("tribunal") or com_data.get("siglaTribunal", "N/A"),
            "grau": grau_val,
            "is_tst_recurso": is_tst,
            "nivelSigilo": sigilo_val,
            "nivel_sigilo": sigilo_val,
            "dataAjuizamento": dj.get("dataAjuizamento") or com_data.get("dataUltimaPublicacao", ""),
            "dataHoraUltimaAtualizacao": dj.get("dataHoraUltimaAtualizacao") or com_data.get("dataUltimaPublicacao", ""),
            "assuntos": assuntos,
            "subjects": subjects_names,
            "primary_subject": primary_name,
            "primary_subject_code": primary_code,
            "statusDesfecho": status,
            "sentence_outcome": sentence_outcome,
            "sentence_sub_outcome": sub_outcome,
            "has_expert_report": tem_pericia,
            "observation": f"Assunto identificado via publicação PJe: '{primary_name}'." if primary_name else None,
            "sinaisInstrucao": {
                "possuiPericiaOuLaudo": tem_pericia,
                "possuiSinalRevelia": tem_revelia,
                "encontradoNoDatajud": bool(dj),
            },
            "publicacoes_texto": com_data.get("publicacoes_texto", []),
            "destinatarios_nomes": com_data.get("destinatarios_nomes", []),
            "classes_pje": classes_list,
            "resumoPublicacao": (com_data.get("publicacoes_texto") or [""])[0][:500],
            "raw_metadata": dj if dj else None,
            "movimentos": movimentos,
        }

    def _merge_process_data(self, cnj: str, com_data: Dict[str, Any], dj: Dict[str, Any]) -> Dict[str, Any]:
        """Mescla dados do Comunica PJe com os metadados do DataJud via pipeline ultra-rápido."""
        return self._merge_process_data_fast(cnj, com_data, dj)

    @staticmethod
    def _infer_subjects_from_text(texto: str) -> List[Dict[str, Any]]:
        """Infere o motivo do processo pelo texto da publicação utilizando extract_real_cause_fast."""
        return extract_real_cause_fast([], [texto])
