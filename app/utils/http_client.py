import os
import inspect
import logging
import urllib.parse
from typing import Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)

DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

STATUS_TO_RETRY_PROXY = {403, 405, 429, 502, 503}


def _build_full_url(url: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Monta a URL completa com query parameters concatenados."""
    if not params:
        return url
    parsed = urllib.parse.urlparse(url)
    existing_query = urllib.parse.parse_qsl(parsed.query)
    for k, v in params.items():
        if v is not None:
            existing_query.append((k, str(v)))
    new_query = urllib.parse.urlencode(existing_query)
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def _call_client(
    client: Any,
    method: str,
    url: str,
    params: Optional[Dict[str, Any]],
    json_body: Optional[Any],
    headers: Dict[str, str],
    timeout: float,
) -> requests.Response:
    """Invoca o método específico (get/post) ou genérico (request) preservando mocks."""
    m = method.upper()
    caller = getattr(client, m.lower(), None) if hasattr(client, m.lower()) else None
    if caller is not None:
        kwargs: Dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if params is not None:
            kwargs["params"] = params
        if json_body is not None and m == "POST":
            kwargs["json"] = json_body

        try:
            sig = inspect.signature(caller)
            accepts_headers = "headers" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        except (ValueError, TypeError):
            accepts_headers = True

        if accepts_headers:
            kwargs["headers"] = headers

        try:
            return caller(url, **kwargs)
        except TypeError:
            kwargs.pop("headers", None)
            return caller(url, **kwargs)

    return client.request(
        method=method,
        url=url,
        params=params,
        json=json_body,
        headers=headers,
        timeout=timeout,
    )


def request_gov_br_with_fallback(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    timeout: float = 8.0,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """
    Executa requisições para domínios governamentais brasileiros (PJe, DataJud, CGU)
    com headers de navegador pt-BR e arquitetura de fallback automático via Edge Proxy (gru1).
    """
    client = session or requests

    merged_headers = dict(DEFAULT_BROWSER_HEADERS)
    if headers:
        merged_headers.update(headers)

    proxy_url = (os.getenv("CNJ_PROXY_URL") or os.getenv("PJE_PROXY_URL") or "").strip()
    force_proxy = os.getenv("CNJ_FORCE_PROXY", "").strip().lower() in ("true", "1")

    full_target_url = _build_full_url(url, params)

    # 1. Se configurado para forçar proxy, envia diretamente pelo proxy
    if force_proxy and proxy_url:
        logger.info("Roteamento forçado via CNJ_PROXY_URL para: %s", full_target_url)
        return _dispatch_via_proxy(
            client=client,
            proxy_url=proxy_url,
            target_url=full_target_url,
            method=method,
            headers=merged_headers,
            json_body=json_body,
            timeout=timeout,
        )

    # 2. Tentativa direta com headers brasileiros
    try:
        resp = _call_client(
            client=client,
            method=method,
            url=url,
            params=params,
            json_body=json_body,
            headers=merged_headers,
            timeout=timeout,
        )
        if resp.status_code in STATUS_TO_RETRY_PROXY and proxy_url:
            logger.warning(
                "Chamada direta a %s retornou status %d. Ativando fallback automático via CNJ_PROXY_URL.",
                url,
                resp.status_code,
            )
            return _dispatch_via_proxy(
                client=client,
                proxy_url=proxy_url,
                target_url=full_target_url,
                method=method,
                headers=merged_headers,
                json_body=json_body,
                timeout=timeout,
            )
        return resp
    except (requests.RequestException, TimeoutError) as exc:
        if proxy_url:
            logger.warning(
                "Falha de conexão direta a %s (%s). Ativando fallback automático via CNJ_PROXY_URL.",
                url,
                exc,
            )
            return _dispatch_via_proxy(
                client=client,
                proxy_url=proxy_url,
                target_url=full_target_url,
                method=method,
                headers=merged_headers,
                json_body=json_body,
                timeout=timeout,
            )
        raise exc


def _dispatch_via_proxy(
    client: Any,
    proxy_url: str,
    target_url: str,
    method: str,
    headers: Dict[str, str],
    json_body: Optional[Any],
    timeout: float,
) -> requests.Response:
    """Dispara a requisição através da Serverless Edge Function na Vercel (gru1 - SP)."""
    proxy_params = {"target": target_url}
    proxy_headers = dict(headers)
    proxy_headers["X-Target-Url"] = target_url

    return _call_client(
        client=client,
        method=method,
        url=proxy_url,
        params=proxy_params,
        json_body=json_body,
        headers=proxy_headers,
        timeout=timeout,
    )
