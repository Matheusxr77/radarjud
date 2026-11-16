import re
import unicodedata
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# Padrões de tipos e sufixos societários comuns no Brasil
STOPWORDS_SOCIETARIAS = {
    "LTDA", "SA", "S/A", "S.A.", "S.A", "EIRELI", "ME", "EPP", "CIA",
    "COMPANHIA", "INSTITUICAO", "PAGAMENTO", "PAGAMENTOS", "SERVICOS",
    "SERVICO", "COMERCIO", "INDUSTRIA", "BRASIL", "DO", "DA", "DE", "DOS",
    "DAS", "E", "EMPRESA", "GRUPO", "HOLDING", "PARTICIPACOES", "SOLUCOES",
    "TECNOLOGIA", "SISTEMAS", "FINANCEIRA", "CREDITO", "MULTIPLO"
}

CORPORATE_SUFFIXES_PATTERN = re.compile(
    r"\b("
    r"L\.?T\.?D\.?A\.?|LIMITADA|"
    r"S\.?A\.?|S\s*/\s*A|S\s+A|SOCIEDADE\s+ANONIMA|"
    r"M\.?E\.?|MICROEMPRESA|"
    r"E\.?P\.?P\.?|EMPRESA\s+DE\s+PEQUENO\s+PORTE|"
    r"E\.?I\.?R\.?E\.?L\.?I\.?|"
    r"SOCIEDADE\s+UNIPESSOAL|"
    r"SOCIEDADE\s+SIMPLES|"
    r"HOLDING|"
    r"PARTICIPACOES(\s+S/?A)?|"
    r"SERVICOS|COMERCIO|INDUSTRIA"
    r")\b",
    re.IGNORECASE,
)


def remove_accents(text: str) -> str:
    """Remove acentuação gráfica convertendo caracteres para formato ASCII simples."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def is_cnpj(value: str) -> bool:
    """
    Verifica se a string fornecida corresponde estruturalmente a um CNPJ
    (14 dígitos numéricos, formatado ou não).
    """
    if not value:
        return False
    digits = re.sub(r"\D", "", str(value))
    return len(digits) == 14


def is_valid_company_search_input(raw: str) -> bool:
    """
    Valida se a entrada de busca é um CNPJ válido (14 dígitos)
    ou um nome de empresa, razão social ou nome fantasia válido
    (ex: 'Picpay', 'banco bradesco', 'Banco Bradesco S.A.', 'Nubank', 'PETROBRAS').
    Permite busca por nome comum, razão social e CNPJ formatado ou numérico.
    Rejeita apenas strings vazias, sequências numéricas inválidas (!= 14 dígitos),
    ou caracteres especiais proibidos.
    """
    if not raw or not isinstance(raw, str):
        return False
    stripped = raw.strip()
    if not stripped:
        return False

    # Caso 1: Entrada estritamente numérica ou formatada como CNPJ
    digits = re.sub(r"\D", "", stripped)
    if digits and (stripped.isdigit() or all(c in "0123456789./- " for c in stripped)):
        return len(digits) == 14

    # Caso 2: Não pode conter caracteres especiais proibidos ou scripts
    if re.search(r"[@!#$%\*\?\_+=<>{}\[\]|\\]", stripped):
        return False

    # Deve conter ao menos 2 caracteres alfanuméricos válidos (letras ou números)
    alphanumeric = re.findall(r"[A-Za-z0-9]", stripped)
    if len(alphanumeric) < 2:
        return False

    return True


def extract_brand_search_term(raw_input: str, razao_social: str = "", nome_fantasia: str = "") -> str:
    """
    Garante que a busca no Comunica PJe capture TODAS as filiais e empresas do mesmo grupo.
    Ex: 'PICPAY INSTITUICAO DE PAGAMENTO S.A.' -> retorna 'PICPAY'
    Preserva a palavra BANCO em nomes compostos como 'BANCO DO BRASIL' ou 'BANCO INTER'.
    """
    raw_clean = re.sub(r"\(.*?\)", "", raw_input or "").strip()
    raw_upper = raw_clean.upper()
    if raw_upper.startswith("BANCO ") and len(raw_upper.split()) >= 2:
        parts = raw_upper.split()
        if len(parts) >= 3 and parts[1] in ("DO", "DA", "DE"):
            return " ".join(parts[:3])
        if parts[1] in ("BRADESCO", "SANTANDER", "ITAU"):
            return parts[1]
        return " ".join(parts[:2])

    candidate = (nome_fantasia.strip() if nome_fantasia and len(nome_fantasia.strip()) >= 3 else razao_social.strip()) or raw_clean
    candidate_upper = re.sub(r"\(.*?\)", "", candidate).strip().upper()
    if candidate_upper.startswith("BANCO ") and len(candidate_upper.split()) >= 2:
        parts = candidate_upper.split()
        if len(parts) >= 3 and parts[1] in ("DO", "DA", "DE"):
            return " ".join(parts[:3])
        if parts[1] in ("BRADESCO", "SANTANDER", "ITAU"):
            return parts[1]
        return " ".join(parts[:2])

    clean_user = re.sub(r"[^\w\s]", "", raw_clean).strip()
    if clean_user and not clean_user.isdigit() and len(clean_user.split()) <= 2:
        return clean_user.upper()

    tokens = [
        t for t in re.sub(r"[^\w\s]", " ", candidate.upper()).split()
        if t not in STOPWORDS_SOCIETARIAS and len(t) > 1
    ]
    if tokens:
        if len(tokens) >= 2 and (len(tokens[0]) <= 3 or tokens[0] in ("MAGAZINE", "CASAS", "REDE", "SUPER", "AUTO", "NOVA", "POSTO", "BANCO")):
            return " ".join(tokens[:2])
        return tokens[0]
    return raw_input.strip().upper()


def sanitize_company_name(name: str) -> str:
    """
    Sanitiza a razão social ou nome empresarial para maximizar correspondências
    no Comunica PJe e DataJud:
    - Remove acentos e caracteres especiais.
    - Remove pontuações e símbolos.
    - Remove sufixos jurídicos societários (LTDA, S/A, ME, EPP, EIRELI, etc.).
    - Preserva o núcleo identificador da empresa.
    """
    if not name:
        return ""

    # 1. Normalização de acentos e maiúsculas
    cleaned = remove_accents(name).upper().strip()

    # 2. Primeira passagem de remoção de sufixos societários (ex: S/A, S.A., LTDA.)
    cleaned = CORPORATE_SUFFIXES_PATTERN.sub(" ", cleaned)

    # 3. Remoção de pontuações e símbolos preservando letras e números
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)

    # 4. Segunda passagem para capturar resquícios (ex: 'S A' gerado por 'S/A')
    cleaned = CORPORATE_SUFFIXES_PATTERN.sub(" ", cleaned)

    # 5. Normalização de espaços múltiplos em branco
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Remove preposições e conjunções isoladas no final após extração de sufixos (ex: 'CARREFOUR E')
    cleaned = re.sub(r"\s+\b(E|DE|DA|DO|DOS|DAS)\b\s*$", "", cleaned, flags=re.IGNORECASE).strip()

    # Se a remoção de sufixos esvaziou a string (ex: input era apenas 'S/A'), retorna o nome original limpo
    if not cleaned:
        cleaned = re.sub(r"[^\w\s]", " ", remove_accents(name).upper()).strip()

    return cleaned


def fetch_cnpj_brasilapi(cnpj: str, timeout: int = 6) -> Optional[str]:
    """
    Consulta a API pública da BrasilAPI para triangulação reversa de CNPJ em Razão Social.
    Retorna a razão social oficial ou None em caso de falha de conexão ou timeout.
    """
    clean_digits = re.sub(r"\D", "", str(cnpj))
    if len(clean_digits) != 14:
        return None

    url = f"https://brasilapi.com.br/api/cnpj/v1/{clean_digits}"
    headers = {"User-Agent": "RadarJus/1.0 (Compliance Monitor)"}

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            razao_social = data.get("razao_social") or data.get("nome_fantasia")
            if razao_social:
                logger.info("BrasilAPI resolveu CNPJ %s para Razão Social: %s", clean_digits, razao_social)
                return razao_social
        else:
            logger.warning("BrasilAPI retornou status %d para o CNPJ %s", response.status_code, clean_digits)
    except requests.RequestException as exc:
        logger.warning("Não foi possível consultar BrasilAPI para o CNPJ %s: %s", clean_digits, exc)

    return None


CORPORATE_SUFFIX_REGEX = re.compile(
    r"\b("
    r"L\.?T\.?D\.?A\.?|LIMITADA|"
    r"S\.?A\.?|S\s*/\s*A|S\s+A|SOCIEDADE\s+ANONIMA|"
    r"M\.?E\.?|MICROEMPRESA|"
    r"E\.?P\.?P\.?|EMPRESA\s+DE\s+PEQUENO\s+PORTE|"
    r"E\.?I\.?R\.?E\.?L\.?I\.?|"
    r"INSTITUICAO\s+DE\s+PAGAMENTO|"
    r"BANCO\s+MULTIPLO|"
    r"SOCIEDADE\s+DE\s+CREDITO|"
    r"HOLDING|"
    r"PARTICIPACOES|"
    r"ME|EPP"
    r")\b",
    re.IGNORECASE,
)


def classify_search_mode(raw_query: str) -> str:
    """
    Classifica a intenção do usuário:
    - 'STRICT_CNPJ': CNPJ com 14 dígitos (busca específica de unidade/filial)
    - 'STRICT_RAZAO': Razão Social específica informada com sufixos jurídicos
    - 'BROAD_BRAND': Nome Fantasia / Marca comum (busca consolidada de grupo/todas as filiais)
    """
    clean_digits = re.sub(r"\D", "", raw_query or "")
    if len(clean_digits) == 14 and (raw_query.strip().isdigit() or all(c in "0123456789./- " for c in raw_query.strip())):
        return "STRICT_CNPJ"

    cleaned_text = (raw_query or "").strip().upper()

    # Se contém sufixos societários explícitos ou termos jurídicos fortes
    if CORPORATE_SUFFIX_REGEX.search(cleaned_text):
        return "STRICT_RAZAO"

    words = [w for w in re.sub(r"[^\w\s]", "", cleaned_text).split() if len(w) > 1]
    # Se tem 4 ou mais palavras estruturadas, é quase certamente uma razão social completa
    if len(words) >= 4:
        return "STRICT_RAZAO"

    # Caso contrário, trata como marca/nome fantasia (busca ampla de todas as filiais)
    return "BROAD_BRAND"


def resolve_search_strategy(user_input: str, brasil_api_fetcher=None) -> dict:
    """
    Resolve a estratégia correta de busca:
    - Se STRICT_CNPJ: triangula CNPJ para Razão Social e busca específico
    - Se STRICT_RAZAO: busca estrita pela Razão Social informada
    - Se BROAD_BRAND (ex: "Picpay", "Stone"): NUNCA substitui pela Razão Social da BrasilAPI!
      Mantém o termo raiz/marca para buscar todas as filiais e empresas vinculadas.
    """
    mode = classify_search_mode(user_input)

    if mode == "STRICT_CNPJ":
        cnpj_clean = re.sub(r"\D", "", user_input)
        razao = brasil_api_fetcher(cnpj_clean) if brasil_api_fetcher else None
        query_term = razao or cnpj_clean
        comunica_term = sanitize_company_name(query_term)
        cache_key = f"STRICT_CNPJ::{comunica_term}::{cnpj_clean}"
        return {
            "mode": "STRICT_CNPJ",
            "comunica_query_term": comunica_term,
            "display_title": razao or f"CNPJ: {user_input.strip()}",
            "is_broad_brand": False,
            "aggregate_all_branches": False,
            "target_cnpj": cnpj_clean,
            "raw_input": user_input,
            "cache_key": cache_key,
        }

    elif mode == "STRICT_RAZAO":
        sanitized = sanitize_company_name(user_input)
        comunica_term = sanitized or user_input.strip().upper()
        cache_key = f"STRICT_RAZAO::{comunica_term}::"
        return {
            "mode": "STRICT_RAZAO",
            "comunica_query_term": comunica_term,
            "display_title": user_input.strip().upper(),
            "is_broad_brand": False,
            "aggregate_all_branches": False,
            "target_cnpj": None,
            "raw_input": user_input,
            "cache_key": cache_key,
        }

    else:  # BROAD_BRAND (ex: "Picpay", "Nubank", "Stone")
        brand_clean = re.sub(r"[^\w\s]", "", user_input).strip().upper()
        cache_key = f"BROAD_BRAND::{brand_clean}::"
        return {
            "mode": "BROAD_BRAND",
            "comunica_query_term": brand_clean,
            "display_title": f"{brand_clean} (GRUPO CONSOLIDADO / FILIAIS)",
            "is_broad_brand": True,
            "aggregate_all_branches": True,
            "target_cnpj": None,
            "raw_input": user_input,
            "cache_key": cache_key,
        }

