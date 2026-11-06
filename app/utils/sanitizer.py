import re
import unicodedata
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# Padrões de tipos e sufixos societários comuns no Brasil
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
