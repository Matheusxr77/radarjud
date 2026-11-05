import os
import re
import logging
from typing import List, Dict, Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class CNJClient:
    """
    Cliente de integração com as APIs Públicas Oficiais do Poder Judiciário:
    1. Comunica PJe (Diário de Justiça Eletrônico / Descoberta de Números CNJ por Nome de Parte)
    2. DataJud CNJ (Elasticsearch dos Metadados: Assuntos TPU, Movimentos e Nível de Sigilo)
    """

    def __init__(
        self,
        comunica_url: Optional[str] = None,
        datajud_base_url: Optional[str] = None,
        datajud_api_key: Optional[str] = None,
        timeout: int = 15,
    ):
        self.comunica_url = comunica_url or os.getenv(
            "COMUNICA_PJE_URL", "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
        )
        self.datajud_base_url = datajud_base_url or os.getenv(
            "DATAJUD_BASE_URL", "https://api-publica.datajud.cnj.jus.br"
        )
        self.datajud_api_key = datajud_api_key or os.getenv(
            "DATAJUD_API_KEY",
            "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==",
        )
        self.timeout = timeout

        # Configuração de sessão com retentativas automáticas e resiliência
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_comunica_pje_by_name(
        self, company_name: str, max_items: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Passo 1: Consulta a API Comunica PJe pelo nome da empresa (nomeParte).
        Coleta publicações, intimações e a lista de números de processos.
        """
        params = {
            "nomeParte": company_name,
            "itensPorPagina": min(max_items, 100),
        }
        try:
            logger.info("Consultando Comunica PJe para: %s", company_name)
            resp = self.session.get(
                self.comunica_url,
                params=params,
                timeout=self.timeout,
                headers={"Accept": "application/json", "User-Agent": "LexGuardAI/1.0"},
            )
            if resp.ok:
                data = resp.json()
                items = data.get("items", [])
                logger.info("Comunica PJe retornou %d publicações para '%s'", len(items), company_name)
                return items
            else:
                logger.warning(
                    "Comunica PJe retornou status %s para '%s': %s",
                    resp.status_code,
                    company_name,
                    resp.text[:200],
                )
                return []
        except Exception as exc:
            logger.error("Erro de conexão ao Comunica PJe para '%s': %s", company_name, exc)
            return []

    def extract_court_from_process_number(self, cnj_number: str) -> str:
        """
        Deduz o endpoint do tribunal no DataJud pelo número único do processo CNJ.
        Formato CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO
        Exemplo: 0000123-45.2023.5.02.0001 -> J=5 (Trabalho), TR=02 -> trt2
                 0000456-78.2022.5.15.0001 -> TR=15 -> trt15
                 0000789-10.2021.5.00.0000 -> TR=00 -> tst
        """
        clean_num = re.sub(r"\D", "", cnj_number)
        if len(clean_num) == 20:
            justice_segment = clean_num[13]
            court_region = clean_num[14:16]

            if justice_segment == "5":  # Justiça do Trabalho
                if court_region == "00":
                    return "tst"
                court_int = int(court_region)
                return f"trt{court_int}"
            elif justice_segment == "8":  # Justiça Estadual
                return f"tjr{court_region}"

        return "trt2"  # Default para tribunal com maior volume trabalhista

    def fetch_datajud_process_metadata(
        self, process_number: str, court: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Passo 2: Consulta a API Pública do DataJud CNJ via Elasticsearch.
        Extrai: assuntos (TPU), nivelSigilo (0-5) e árvore de movimentos.
        """
        clean_num = re.sub(r"\D", "", process_number)
        target_court = court or self.extract_court_from_process_number(process_number)
        endpoint = f"{self.datajud_base_url}/api_publica_{target_court}/_search"

        payload = {
            "query": {
                "match": {
                    "numeroProcesso": clean_num
                }
            }
        }
        headers = {
            "Authorization": f"APIKey {self.datajud_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "LexGuardAI/1.0",
        }

        try:
            resp = self.session.post(
                endpoint, json=payload, headers=headers, timeout=self.timeout
            )
            if resp.ok:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                if hits:
                    source = hits[0].get("_source", {})
                    source["_court"] = target_court
                    return source
            else:
                logger.debug(
                    "DataJud retornou status %d para o processo %s em %s",
                    resp.status_code,
                    process_number,
                    target_court,
                )
        except Exception as exc:
            logger.debug(
                "Falha ao consultar DataJud para processo %s: %s",
                process_number,
                exc,
            )

        return None

