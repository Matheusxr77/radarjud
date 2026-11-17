import re
import sys
import time
import uuid
import asyncio
import logging
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import text

from app.extensions import db
from app.models.company_search import CompanySearch, get_utc_now
from app.models.process_record import ProcessRecord
from app.services.orchestrator import CNJMetadataOrchestrator
from app.services.economic_service import (
    EconomicStandingService,
    aggregate_group_capital,
    resolve_company_logo,
    discover_company_cnpjs,
    build_capital_social_display,
)
from app.utils.sanitizer import (
    sanitize_company_name,
    extract_brand_search_term,
    is_cnpj,
    fetch_cnpj_brasilapi,
    is_valid_company_search_input,
    classify_search_mode,
    resolve_search_strategy,
    CORPORATE_SUFFIX_REGEX,
)
from app.utils.crypto import generate_movements_hash

logger = logging.getLogger(__name__)

# REGRA 4 e 5: Cache em Memória com TTL de 15 minutos e Regex instantâneo de CNPJ
CNPJ_REGEX = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
SEARCH_CACHE = {}  # {brand_term.upper(): (timestamp, dict_result)}
CACHE_TTL = 900    # 15 minutos

ACTIVE_SEARCH_LOCK = threading.Lock()
ACTIVE_SEARCH_JOB_ID = None
CANCELLED_JOB_IDS = set()


def fast_extract_cnpjs_from_texts(processes_dict: dict) -> list:
    """Extrai CNPJs únicos por raiz (8 primeiros dígitos) diretamente dos textos do Comunica PJe em 1ms."""
    seen_roots = {}
    for proc in processes_dict.values():
        for txt in proc.get("publicacoes_texto", []):
            for match in CNPJ_REGEX.findall(str(txt)):
                digits = re.sub(r"\D", "", match)
                if len(digits) == 14:
                    root = digits[:8]
                    if root not in seen_roots:
                        seen_roots[root] = digits
                        if len(seen_roots) >= 3:
                            return list(seen_roots.values())
    return list(seen_roots.values())

# Integração com radarjusAgent (Google ADK + Vertex AI)
RADARJUS_AGENT_DIR = Path(__file__).resolve().parents[2] / "radarjusAgent"
if RADARJUS_AGENT_DIR.exists() and str(RADARJUS_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(RADARJUS_AGENT_DIR))

try:
    from main import (
        executar_pipeline_radarjus as _executar_pipeline_radarjus,
        _extract_processes_from_realtime_json as _extract_agent_procs,
    )
except Exception as _agent_exc:
    logger.debug("Pipeline completo radarjusAgent indisponível: %s", _agent_exc)
    _executar_pipeline_radarjus = None
    _extract_agent_procs = None

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
orchestrator = CNJMetadataOrchestrator()
economic_service = EconomicStandingService()

# Pool dedicado para tarefas assíncronas em segundo plano (Deep Scan)
background_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="radarjus_bg_worker")


def _deep_scan_background_worker(
    app,
    search_id: int,
    company_name: str,
    search_strategy: dict = None,
    job_id: str = "",
    page1_processes: list = None,
):
    """
    Worker assíncrono executado em segundo plano para varredura completa (Deep Scan):
    1. Verifica cancelamento de jobs antigos para não congestionar a fila.
    2. Pagina sequencialmente até 15 páginas no Comunica PJe (limite oficial de 50 por página).
       Critério de parada: len(raw_items) < 50.
       Callback atualiza s.total_processes no banco progressivamente para o polling no frontend.
    3. Enriquece os Top 3 Tribunais no DataJud com timeout estrito de 6s.
    4. Consolida o Capital Social somado de todas as unidades descobertas do grupo.
    5. Atualiza banco de dados, gera parecer de IA e salva em SEARCH_CACHE (TTL 15 min).
    """
    with app.app_context():
        try:
            if job_id:
                with ACTIVE_SEARCH_LOCK:
                    if job_id in CANCELLED_JOB_IDS:
                        logger.info("Deep Scan abortado: job_id=%s foi cancelado.", job_id)
                        return

            logger.info("Iniciando Deep Scan em segundo plano para search_id=%d (%s)...", search_id, company_name)
            search = db.session.get(CompanySearch, search_id)
            if not search:
                return

            search.scan_status = "SCANNING_BACKGROUND"
            db.session.commit()

            start_ts = time.time()
            strat = search_strategy or {
                "comunica_query_term": extract_brand_search_term(company_name, razao_social=company_name),
                "display_title": company_name,
                "is_broad_brand": True,
                "mode": "BROAD_BRAND",
            }
            clean_brand = strat.get("comunica_query_term", "")

            all_processes_map = {}
            if page1_processes:
                for p in page1_processes:
                    num = p.get("numero_processo") or p.get("numeroProcesso")
                    if num:
                        all_processes_map[num] = {
                            "numeroProcesso": num,
                            "numero_processo": num,
                            "siglaTribunal": p.get("tribunal", ""),
                            "publicacoes_texto": p.get("publicacoes_texto", []),
                            "classes_pje": p.get("classes_pje", []),
                            "is_tst_recurso": p.get("is_tst_recurso", False),
                        }

            # Callback progressivo para polling no frontend (42 -> 90 -> 180 -> 350 -> 500+)
            def _on_page_update(page_num: int, accumulated_count: int, current_procs: dict):
                if job_id:
                    with ACTIVE_SEARCH_LOCK:
                        if job_id in CANCELLED_JOB_IDS:
                            return
                try:
                    for cnj, data in (current_procs or {}).items():
                        if cnj not in all_processes_map:
                            all_processes_map[cnj] = data
                        else:
                            for t in data.get("publicacoes_texto", []):
                                if t not in all_processes_map[cnj]["publicacoes_texto"]:
                                    all_processes_map[cnj]["publicacoes_texto"].append(t)
                            for c in data.get("classes_pje", []):
                                if c not in all_processes_map[cnj].get("classes_pje", []):
                                    all_processes_map[cnj].setdefault("classes_pje", []).append(c)

                    s = db.session.get(CompanySearch, search_id)
                    if s:
                        s.total_processes = len(all_processes_map)
                        s.last_checked_at = get_utc_now()
                        db.session.commit()
                except Exception as db_e:
                    db.session.rollback()
                    logger.debug("Erro ao atualizar progresso da página %d: %s", page_num, db_e)

            # 1. Paginação padrão até 20 páginas no Comunica PJe (começando da página 2)
            scan_res = orchestrator.searcher.paginate_comunica_pje_standard(
                search_strategy=strat,
                start_page=2,
                max_pages=20,
                on_page_callback=_on_page_update,
                timeout=6.0,
            )

            # Mescla todos os processos retornados em all_processes_map
            for cnj, data in scan_res.get("processes", {}).items():
                if cnj not in all_processes_map:
                    all_processes_map[cnj] = data
                else:
                    for t in data.get("publicacoes_texto", []):
                        if t not in all_processes_map[cnj]["publicacoes_texto"]:
                            all_processes_map[cnj]["publicacoes_texto"].append(t)
                    for c in data.get("classes_pje", []):
                        if c not in all_processes_map[cnj].get("classes_pje", []):
                            all_processes_map[cnj].setdefault("classes_pje", []).append(c)

            if job_id:
                with ACTIVE_SEARCH_LOCK:
                    if job_id in CANCELLED_JOB_IDS:
                        logger.info("Deep Scan cancelado antes da consolidação de CNPJ/DataJud: %s", job_id)
                        return

            # 2. Consolidação de Capital Social de todas as filiais e unidades vinculadas
            brand_term_clean = strat.get("comunica_query_term") or clean_brand
            cnpjs_found = set(discover_company_cnpjs(company_name, brand_term_clean, all_processes_map, search_strategy=strat))
            for ent in scan_res.get("discovered_entities", []):
                if is_cnpj(ent):
                    cnpjs_found.add(re.sub(r"\D", "", ent))

            cap_display = build_capital_social_display(list(cnpjs_found), total_processes=len(all_processes_map))
            capital_info = {
                "capital_ativo_formatado": cap_display.get("valor_formatado", "R$ 0,00"),
                "unidades_consolidadas": cap_display.get("unidades_consolidadas", 1),
                "observacao_porte": cap_display.get("observacao_porte", ""),
                "capital_social_display": cap_display,
            }

            # 3. Enriquecimento no DataJud APENAS para os Top 3 Tribunais com timeout de 6s
            enriched_list = orchestrator.searcher.enrich_top_tribunals_datajud(
                all_processes_map, max_tribunals=3, timeout=6.0
            )

            # 4. Métricas e parecer de IA
            deep_results = orchestrator.predictor.build_dashboard_metrics(
                company_name=company_name,
                processes=enriched_list,
                capital_info=capital_info,
                scan_status="COMPLETED",
            )
            deep_results["empresa"] = company_name
            deep_results["is_broad_brand"] = strat.get("is_broad_brand", False)
            deep_results["movements_hash"] = generate_movements_hash(enriched_list)
            deep_results["processos"] = enriched_list

            deep_results["idoneidade_economica"] = economic_service.evaluate_economic_standing(
                company_name=company_name,
                company_processes=enriched_list,
                p_condenacao_trabalhista=deep_results.get("probabilidade_condenacao_geral_pct") or 0.0,
                discovered_entities=list(cnpjs_found),
                search_strategy=strat,
            )
            econ = deep_results["idoneidade_economica"]
            deep_results["logo"] = econ.get("logo", {})
            if econ.get("indice_exposicao_financeira") is not None:
                deep_results["indice_exposicao_financeira"] = econ["indice_exposicao_financeira"]
                deep_results["score_risco_terceirizacao"] = econ["indice_exposicao_financeira"]
                deep_results["overall_risk_score"] = econ["indice_exposicao_financeira"]
            deep_results["capital_social_consolidado"] = econ.get("valor_formatado") or econ.get("capital_social_formatado") or cap_display.get("valor_formatado")
            deep_results["unidades_consolidadas"] = econ.get("unidades_consolidadas") or cap_display.get("unidades_consolidadas", 1)
            deep_results["observacao_porte"] = econ.get("observacao_porte") or cap_display.get("observacao_porte", "")
            deep_results["capital_social_display"] = econ.get("capital_social_display") or cap_display

            deep_results["ai_analysis"] = orchestrator.ai_analyzer.analyze_company(deep_results)
            deep_results["parecer_agente_adk"] = deep_results["ai_analysis"]

            # 5. Atualização e persistência no banco (Garante métricas completas com prioridade)
            search = db.session.get(CompanySearch, search_id)
            if not search:
                return

            search.total_processes = deep_results.get("total_processos", len(enriched_list))
            search.secrecy_count = sum(
                qtd for lvl, qtd in deep_results.get("niveis_sigilo", {}).items() if int(lvl) > 0
            )
            search.secrecy_rate = round(search.secrecy_count / max(search.total_processes, 1), 3)
            search.outcomes_summary = deep_results.get("desfechos_sentencas", {})
            search.ranking_motivos = deep_results.get("ranking_motivos", [])
            search.ranking_motivos_condenacao = deep_results.get("ranking_motivos_condenacao", [])
            search.predictions_summary = deep_results.get("plus_previsao_condenacao", [])
            search.overall_risk_score = deep_results.get("score_risco_terceirizacao", 0.0)
            search.ai_analysis = deep_results.get("ai_analysis", {})
            search.movements_hash = deep_results.get("movements_hash")
            search.economic_standing = deep_results["idoneidade_economica"]
            search.scan_status = "COMPLETED"
            search.last_checked_at = get_utc_now()
            db.session.commit()

            # Persiste os registros de processos individuais em lote protegido
            try:
                existing_numbers = {p.numero_processo for p in search.processes}
                new_records = []
                for proc in enriched_list:
                    num = proc.get("numero_processo")
                    if num and num not in existing_numbers:
                        record = ProcessRecord(
                            company_search_id=search.id,
                            numero_processo=num,
                            tribunal=str(proc.get("tribunal") or "")[:50],
                            grau=str(proc.get("grau") or "G1")[:50],
                            nivel_sigilo=proc.get("nivelSigilo", 0),
                            primary_subject=proc.get("primary_subject"),
                            subjects_json=proc.get("subjects"),
                            observation=proc.get("observation"),
                            sentence_outcome=proc.get("sentence_outcome", "em_aberto"),
                            has_expert_report=proc.get("has_expert_report", False),
                            raw_metadata=proc.get("raw_metadata"),
                        )
                        new_records.append(record)
                        existing_numbers.add(num)

                if new_records:
                    for i in range(0, len(new_records), 50):
                        try:
                            db.session.bulk_save_objects(new_records[i:i + 50])
                            db.session.commit()
                        except Exception as batch_err:
                            db.session.rollback()
                            logger.debug("Lote de processos %d tratado defensivamente: %s", i, batch_err)
            except Exception as rec_err:
                logger.debug("Persistência detalhada de processos ignorada: %s", rec_err)

            # REGRA 4: Salva no Cache em Memória com TTL de 15 minutos
            deep_results["search_id"] = search.id
            bg_cache_key = search_strategy.get("cache_key") or f"{search_strategy.get('mode', 'BROAD_BRAND')}::{search_strategy.get('comunica_query_term', clean_brand)}::{search_strategy.get('target_cnpj') or ''}"
            SEARCH_CACHE[bg_cache_key] = (time.time(), deep_results)
            logger.info("Deep Scan finalizado com sucesso em %.1fs para search_id=%d. Total: %d processos.",
                        time.time() - start_ts, search_id, search.total_processes)
        except Exception as exc:
            db.session.rollback()
            logger.exception("Erro no Deep Scan em background (search_id=%d): %s", search_id, exc)
            try:
                search = db.session.get(CompanySearch, search_id)
                if search:
                    search.scan_status = "COMPLETED"
                    db.session.commit()
            except Exception:
                pass


@api_bp.route("/health", methods=["GET"])
def health_check():
    """Verifica a integridade da aplicação e a conectividade com o PostgreSQL."""
    db_status = "connected"
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        db_status = "unavailable"

    return jsonify({
        "status": "healthy",
        "service": "RadarJus Backend",
        "database": db_status,
        "agente_adk": "integrated (radarjusAgent)" if _executar_pipeline_radarjus else "local_fallback",
        "apis_governamentais": {
            "datajud": "configured",
            "comunica_pje": "configured",
            "brasilapi": "active"
        }
    }), 200


@api_bp.route("/processar", methods=["POST"])
@api_bp.route("/analisar-empresa", methods=["POST"])
def run_agent_pipeline():
    """
    Executa diretamente o pipeline do agente ADK (radarjusAgent) sobre o JSON em tempo real
    ou nome/CNPJ da empresa, gerando CSV de auditoria e parecer Súmula 331 do TST.
    """
    data = request.get_json(silent=True) or {}
    company_input = (
        data.get("company_name")
        or data.get("empresa")
        or data.get("name")
        or ""
    ).strip() if isinstance(data, dict) else ""
    cnpj = str(data.get("cnpj") or "").strip() if isinstance(data, dict) else ""
    processos_in = (
        _extract_agent_procs(data, company_input)
        if _extract_agent_procs is not None
        else None
    )
    metricas_pre = data if isinstance(data, dict) and "desfechos_sentencas" in data else None

    if not company_input and not processos_in:
        return jsonify({"error": "Informe 'company_name' ou a lista 'processos' em JSON."}), 400

    if _executar_pipeline_radarjus is None:
        return jsonify({"error": "Módulo radarjusAgent não encontrado."}), 500

    try:
        resultado = asyncio.run(
            _executar_pipeline_radarjus(
                empresa=company_input,
                cnpj=cnpj,
                processos_in=processos_in,
                metricas_pre_agregadas=metricas_pre,
            )
        )
        return jsonify(resultado), 200
    except Exception as exc:
        logger.exception("Erro ao executar pipeline radarjusAgent: %s", exc)
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/company-dashboard", methods=["POST"])
@api_bp.route("/search-company", methods=["POST", "GET"])
def search_company():
    """
    Endpoint principal para pesquisa e diagnóstico de risco de fornecedores/terceiros.
    Implementa:
    - Curto-Circuito Nível 1: Retorno instantâneo do PostgreSQL se consultado recentemente (< 24h).
    - Triangulação de CNPJ via BrasilAPI e sanitização de Razão Social.
    - Fast-Path síncrono (< 3s na primeira página).
    - Deep Scan disparado em background para complementar o histórico.
    - Curto-Circuito Nível 2: Se o hash de movimentos não mudou, preserva o parecer da IA.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        company_input = data.get("company_name") or data.get("name")
        force_refresh = bool(data.get("force_refresh", False))
    else:
        company_input = request.args.get("name") or request.args.get("company_name")
        force_refresh = request.args.get("force_refresh", "false").lower() in ("true", "1")

    if company_input is None:
        return jsonify({"error": "Parâmetro 'company_name' é obrigatório."}), 400

    raw_query = str(company_input).strip()
    if not raw_query or not is_valid_company_search_input(raw_query):
        return jsonify({"error": "empresas so são pesquisadas por razão social ou CNPJ"}), 400

    # 1. Resolução inteligente de estratégia de busca (BROAD_BRAND vs STRICT_RAZAO vs STRICT_CNPJ)
    search_strategy = resolve_search_strategy(raw_query, brasil_api_fetcher=fetch_cnpj_brasilapi)
    query_term = search_strategy["comunica_query_term"]
    display_title = search_strategy["display_title"]
    is_broad_brand = search_strategy["is_broad_brand"]
    cnpj_used = search_strategy["target_cnpj"]

    sanitized = sanitize_company_name(query_term)
    normalized_name = sanitized.upper() if sanitized else query_term.upper()

    brand_term = extract_brand_search_term(
        raw_input=raw_query,
        razao_social=display_title,
        nome_fantasia=query_term if not is_cnpj(raw_query) else "",
    )

    cache_key = search_strategy.get("cache_key") or f"{search_strategy['mode']}::{search_strategy['comunica_query_term']}::{search_strategy.get('target_cnpj') or ''}"

    # =========================================================================
    # REGRA 4: Cache em Memória com TTL de 15 minutos (0.05s de resposta)
    # =========================================================================
    if not force_refresh:
        cached_entry = SEARCH_CACHE.get(cache_key)
        if cached_entry:
            ts, cached_data = cached_entry
            econ_entry = cached_data.get("idoneidade_economica") or {}
            # Se o cache em memória tiver homônimo INAPTO antigo para empresa ativa, invalida o cache
            if econ_entry.get("situacao_cadastral") == "INAPTA" and (cached_data.get("total_processos") or 0) >= 25:
                SEARCH_CACHE.pop(cache_key, None)
            elif time.time() - ts < CACHE_TTL and (cached_data.get("total_processos") or 0) > 0:
                logger.info("Cache em memória (TTL 15 min) ativado para '%s'. Resposta instantânea em 0.05s.", cache_key)
                res_copy = dict(cached_data)
                res_copy["cached"] = True
                res_copy["cache_level"] = "L0_MEMORY_CACHE"
                return jsonify(res_copy), 200

    # =========================================================================
    # CURTO-CIRCUITO NÍVEL 1 (Antes do CNJ): Busca instantânea no PostgreSQL
    # =========================================================================
    if not force_refresh:
        try:
            cached_search = (
                CompanySearch.query.filter(
                    (CompanySearch.normalized_name == normalized_name) |
                    (CompanySearch.company_name == display_title)
                )
                .order_by(CompanySearch.updated_at.desc())
                .first()
            )
            # Só retorna cache se houver processos (> 0), for recente e varredura COMPLETA.
            # Evita que uma busca vazia anterior ou preliminar congele o sistema por 24 horas.
            if (
                cached_search
                and (cached_search.total_processes or 0) > 0
                and cached_search.is_recent(hours=24)
                and cached_search.scan_status == "COMPLETED"
            ):
                logger.info("Curto-circuito Nível 1 ativado para '%s' (search_id=%d).", normalized_name, cached_search.id)
                proc_dicts = [p.to_dict() for p in cached_search.processes]
                res = cached_search.to_dict()
                res["empresa"] = cached_search.company_name
                res["is_broad_brand"] = is_broad_brand
                res["search_strategy"] = search_strategy
                res["total_processos"] = cached_search.total_processes
                res["desfechos_sentencas"] = cached_search.outcomes_summary or {}
                res["ranking_motivos"] = cached_search.ranking_motivos or []
                res["ranking_motivos_condenacao"] = cached_search.ranking_motivos_condenacao or []
                res["plus_previsao_condenacao"] = cached_search.predictions_summary or []
                res["score_risco_terceirizacao"] = cached_search.overall_risk_score or 0.0
                res["overall_risk_score"] = cached_search.overall_risk_score or 0.0

                if proc_dicts and len(proc_dicts) >= (cached_search.total_processes or 0):
                    fresh_metrics = orchestrator.predictor.generate_dashboard_metrics(
                        cached_search.company_name, proc_dicts
                    )
                    res["total_processos"] = fresh_metrics.get("total_processos", cached_search.total_processes)
                    res["desfechos_sentencas"] = fresh_metrics.get("desfechos_sentencas", cached_search.outcomes_summary)
                    res["plus_previsao_condenacao"] = fresh_metrics["plus_previsao_condenacao"]
                    res["score_risco_terceirizacao"] = fresh_metrics["score_risco_terceirizacao"]
                    res["overall_risk_score"] = fresh_metrics["score_risco_terceirizacao"]
                    res["ranking_motivos"] = fresh_metrics.get("ranking_motivos", cached_search.ranking_motivos or [])
                    res["ranking_motivos_condenacao"] = fresh_metrics.get("ranking_motivos_condenacao", cached_search.ranking_motivos_condenacao or [])
                    res["probabilidade_condenacao_geral_pct"] = fresh_metrics.get("probabilidade_condenacao_geral_pct")
                    res["taxa_historica_condenacao_pct"] = fresh_metrics.get("taxa_historica_condenacao_pct")
                    cached_search.ranking_motivos = fresh_metrics.get("ranking_motivos", cached_search.ranking_motivos or [])
                    cached_search.ranking_motivos_condenacao = fresh_metrics.get("ranking_motivos_condenacao", cached_search.ranking_motivos_condenacao or [])
                    cached_search.predictions_summary = fresh_metrics["plus_previsao_condenacao"]
                    cached_search.overall_risk_score = fresh_metrics["score_risco_terceirizacao"]
                # Análise de Idoneidade Econômica & Risco de Calote Subsidiário (Súmula 331 TST)
                econ_data = economic_service.evaluate_economic_standing(
                    company_name=cached_search.company_name,
                    cnpj=cnpj_used,
                    company_processes=proc_dicts,
                    p_condenacao_trabalhista=res.get("probabilidade_condenacao_geral_pct") or 0.0,
                    search_strategy=search_strategy,
                )
                res["idoneidade_economica"] = econ_data
                res["logo"] = econ_data.get("logo", {})
                res["capital_social_consolidado"] = econ_data.get("valor_formatado") or econ_data.get("capital_social_formatado")
                res["observacao_porte"] = econ_data.get("observacao_porte")
                res["capital_social_display"] = econ_data.get("capital_social_display")
                if econ_data.get("indice_exposicao_financeira") is not None:
                    res["indice_exposicao_financeira"] = econ_data["indice_exposicao_financeira"]
                    res["score_risco_terceirizacao"] = econ_data["indice_exposicao_financeira"]
                    res["overall_risk_score"] = econ_data["indice_exposicao_financeira"]

                # Re-executa o Agente ADK sobre os metadados (incluindo idoneidade econômica) para garantir parecer sempre atualizado
                fresh_ai = orchestrator.ai_analyzer.analyze_company(res)
                if fresh_ai:
                    res["ai_analysis"] = fresh_ai
                    cached_search.ai_analysis = fresh_ai
                res["cached"] = True
                res["cache_level"] = "L1_DATABASE"
                res["search_id"] = cached_search.id
                res["processos"] = proc_dicts
                res["parecer_agente_adk"] = res.get("ai_analysis", {})

                # Atualiza os dados de idoneidade no banco de dados para eliminar dados defasados
                try:
                    cached_search.economic_standing = econ_data
                    db.session.commit()
                except Exception:
                    pass

                return jsonify(res), 200
        except Exception as db_err:
            logger.debug("Falha na checagem de cache L1 no banco: %s", db_err)

    if force_refresh:
        orchestrator._cache.pop(raw_query.lower(), None)
        orchestrator._cache.pop(query_term.lower(), None)
        orchestrator._cache.pop(normalized_name.lower(), None)

    current_job_id = str(uuid.uuid4())
    with ACTIVE_SEARCH_LOCK:
        global ACTIVE_SEARCH_JOB_ID
        if ACTIVE_SEARCH_JOB_ID:
            CANCELLED_JOB_IDS.add(ACTIVE_SEARCH_JOB_ID)
            if len(CANCELLED_JOB_IDS) > 100:
                CANCELLED_JOB_IDS.clear()
        ACTIVE_SEARCH_JOB_ID = current_job_id

    try:
        # =====================================================================
        # FAST-PATH OTIMIZADO (< 2 segundos): Busca Página 1 no Comunica PJe
        # =====================================================================
        fast_results = orchestrator.analyze_company_by_name(
            company_name=display_title,
            search_strategy=search_strategy,
            fast_path=True,
            limit_processes=50,
        )

        fast_results["empresa"] = display_title
        fast_results["is_broad_brand"] = is_broad_brand
        fast_results["search_strategy"] = search_strategy

        new_hash = fast_results.get("movements_hash")
        initial_status = fast_results.get("scan_status", "COMPLETED")

        # =====================================================================
        # CURTO-CIRCUITO NÍVEL 2 (Antes da IA): Invalidação inteligente por Hash
        # =====================================================================
        cached_search = None
        try:
            cached_search = (
                CompanySearch.query.filter(
                    (CompanySearch.normalized_name == normalized_name) |
                    (CompanySearch.company_name == display_title)
                )
                .order_by(CompanySearch.updated_at.desc())
                .first()
            )
        except Exception:
            pass

        # Se o hash de movimentos for idêntico ao anterior, preserva parecer de IA
        if cached_search and cached_search.movements_hash and cached_search.movements_hash == new_hash:
            logger.info("Curto-circuito Nível 2 (Hash idêntico) para '%s'. Mantendo IA anterior.", normalized_name)
            fast_results["ai_analysis"] = cached_search.ai_analysis
            fast_results["cached_ai"] = True
            fast_results["cache_level"] = "L2_HASH_MATCH"

        fast_results["parecer_agente_adk"] = fast_results.get("ai_analysis", {})

        # Análise de Idoneidade Econômica & Risco de Calote Subsidiário (Súmula 331 TST)
        econ_data = economic_service.evaluate_economic_standing(
            company_name=display_title,
            cnpj=cnpj_used,
            company_processes=fast_results.get("processos", []),
            p_condenacao_trabalhista=fast_results.get("probabilidade_condenacao_geral_pct") or 0.0,
            discovered_entities=fast_results.get("discovered_entities", []),
            search_strategy=search_strategy,
        )
        fast_results["idoneidade_economica"] = econ_data
        fast_results["logo"] = econ_data.get("logo", {})
        fast_results["capital_social_consolidado"] = econ_data.get("valor_formatado") or econ_data.get("capital_social_formatado")
        fast_results["observacao_porte"] = econ_data.get("observacao_porte")
        fast_results["capital_social_display"] = econ_data.get("capital_social_display")
        if econ_data.get("indice_exposicao_financeira") is not None:
            fast_results["indice_exposicao_financeira"] = econ_data["indice_exposicao_financeira"]
            fast_results["score_risco_terceirizacao"] = econ_data["indice_exposicao_financeira"]
            fast_results["overall_risk_score"] = econ_data["indice_exposicao_financeira"]

        # Sincroniza o parecer do Agente ADK com a idoneidade econômica calculada
        fresh_ai = orchestrator.ai_analyzer.analyze_company(fast_results)
        if fresh_ai:
            fast_results["ai_analysis"] = fresh_ai
            fast_results["parecer_agente_adk"] = fresh_ai

        # Persistência do resultado no PostgreSQL
        search_id = None
        try:
            search_entry = CompanySearch(
                company_name=display_title,
                normalized_name=normalized_name,
                total_processes=fast_results.get("total_processos", 0),
                secrecy_count=sum(
                    qtd for lvl, qtd in fast_results.get("niveis_sigilo", {}).items() if int(lvl) > 0
                ),
                secrecy_rate=fast_results.get("taxa_sigilo_pct", 0.0) / 100.0,
                outcomes_summary=fast_results.get("desfechos_sentencas", {}),
                ranking_motivos=fast_results.get("ranking_motivos", []),
                ranking_motivos_condenacao=fast_results.get("ranking_motivos_condenacao", []),
                predictions_summary=fast_results.get("plus_previsao_condenacao", []),
                economic_standing=econ_data,
                overall_risk_score=fast_results.get("score_risco_terceirizacao", 0.0),
                ai_analysis=fast_results.get("ai_analysis", {}),
                movements_hash=new_hash,
                scan_status=initial_status,
                last_checked_at=get_utc_now(),
            )
            db.session.add(search_entry)
            db.session.commit()
            search_id = search_entry.id
            fast_results["search_id"] = search_id

            # Persiste os processos identificados
            for proc in fast_results.get("processos", []):
                record = ProcessRecord(
                    company_search_id=search_entry.id,
                    numero_processo=proc.get("numero_processo"),
                    tribunal=str(proc.get("tribunal") or "")[:50],
                    grau=str(proc.get("grau") or "G1")[:50],
                    nivel_sigilo=proc.get("nivelSigilo", 0),
                    primary_subject=proc.get("primary_subject"),
                    subjects_json=proc.get("subjects"),
                    observation=proc.get("observation"),
                    sentence_outcome=proc.get("sentence_outcome", "em_aberto"),
                    has_expert_report=proc.get("has_expert_report", False),
                    raw_metadata=proc.get("raw_metadata"),
                )
                db.session.add(record)
            db.session.commit()
        except Exception as db_exc:
            db.session.rollback()
            logger.debug("Persistência no DB ignorada ou indisponível: %s", db_exc)

        # =====================================================================
        # DEEP SCAN (Background): Dispara apenas se ainda existirem mais páginas
        # =====================================================================
        if search_id and initial_status == "PARTIAL_FAST_PATH" and not current_app.config.get("TESTING"):
            app_ref = current_app._get_current_object()
            background_executor.submit(
                _deep_scan_background_worker,
                app_ref,
                search_id,
                display_title,
                search_strategy,
                current_job_id,
                fast_results.get("processos", []),
            )

        fast_results["scan_status"] = initial_status
        SEARCH_CACHE[cache_key] = (time.time(), fast_results)
        return jsonify(fast_results), 200

    except Exception as exc:
        logger.exception("Erro ao processar busca de empresa: %s", exc)
        return jsonify({"error": "Erro interno ao consultar APIs do CNJ."}), 500


# =============================================================================
# CATÁLOGO DE EMPRESAS  ->  GET /api/v1/company-catalog?q=sauter&limit=100[&debug=1]
#   (alias: /api/v1/companies/search)
#
# COLE ESTE BLOCO no mesmo arquivo do `api_bp` (o que contém search_company),
# logo ANTES de:   @api_bp.route("/search_status/<int:search_id>", ...)
#
# Não precisa de blueprint novo nem de register_blueprint: a rota nasce em api_bp.
# Reaproveita o que o arquivo já importa: orchestrator, discover_company_cnpjs,
# resolve_company_logo, fetch_cnpj_brasilapi, resolve_search_strategy,
# is_valid_company_search_input, is_cnpj, CNPJ_REGEX, ThreadPoolExecutor, current_app.
# =============================================================================
import inspect
import unicodedata

import requests

CATALOG_CACHE = {}          # {(termo_normalizado,): (timestamp, payload)}
CATALOG_TTL = 3600          # 1h
CATALOG_PAGES = 3           # páginas do Comunica PJe varridas na descoberta (50 itens/página)
CATALOG_MAX_ROOTS = 30      # teto de empresas (raízes de CNPJ) por busca
CATALOG_BRANCHES_PER_ROOT = 3   # filiais (vistas nos processos) listadas por empresa
CATALOG_MAX_BRANCH_JOBS = 20    # teto global de consultas extras de filiais à BrasilAPI
_FREE_MAIL = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br", "uol.com.br",
    "bol.com.br", "terra.com.br", "ig.com.br", "icloud.com", "live.com", "msn.com",
}


def _cat_norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9 ]", " ", text.upper()).strip()


def _cat_dv(base12: str) -> str:
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    r1 = sum(int(d) * w for d, w in zip(base12, w1)) % 11
    d1 = "0" if r1 < 2 else str(11 - r1)
    r2 = sum(int(d) * w for d, w in zip(base12 + d1, w2)) % 11
    d2 = "0" if r2 < 2 else str(11 - r2)
    return d1 + d2


def _cat_valid(cnpj: str) -> bool:
    return len(cnpj) == 14 and len(set(cnpj)) > 1 and cnpj[12:] == _cat_dv(cnpj[:12])


def _cat_matrix(root8: str) -> str:
    base = root8 + "0001"
    return base + _cat_dv(base)


def _cat_brasilapi(cnpj: str):
    """Usa o fetcher do projeto; se ele devolver outro formato, cai na BrasilAPI direta."""
    try:
        data = fetch_cnpj_brasilapi(cnpj)
        if isinstance(data, dict) and data.get("razao_social") and "descricao_situacao_cadastral" in data:
            return data
    except Exception as exc:
        logger.debug("Catálogo: fetch_cnpj_brasilapi falhou (%s): %s", cnpj, exc)
    try:
        r = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}", timeout=12)
        return r.json() if r.ok else None
    except Exception as exc:
        logger.debug("Catálogo: BrasilAPI direta falhou (%s): %s", cnpj, exc)
        return None


def _cat_logo(data: dict, cnpj: str):
    """Chama resolve_company_logo do projeto adaptando-se à assinatura real (inspect).
    Retorna (logo_dict, origem) — origem aparece no ?debug=1."""
    razao = data.get("razao_social") or ""
    fantasia = data.get("nome_fantasia") or ""
    email = (data.get("email") or "").lower()
    values = {
        "company_name": razao, "razao_social": razao, "name": razao, "nome": razao,
        "nome_fantasia": fantasia, "cnpj": cnpj, "email": email,
        "brasil_data": data, "brasilapi_data": data, "cnpj_data": data, "data": data,
        "dados_cnpj": data, "company_data": data,
    }
    try:
        sig = inspect.signature(resolve_company_logo)
        kwargs, missing = {}, []
        for pname, p in sig.parameters.items():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            if pname in values:
                kwargs[pname] = values[pname]
            elif p.default is inspect.Parameter.empty:
                missing.append(pname)
        if missing:
            raise TypeError(f"parâmetros obrigatórios não mapeados: {missing}")
        logo = resolve_company_logo(**kwargs)
        if isinstance(logo, str):
            logo = {"logo_url": logo}
        if isinstance(logo, dict) and logo:
            return logo, "resolve_company_logo"
    except Exception as exc:
        origem = f"fallback favicon ({type(exc).__name__}: {exc})"
    else:
        origem = "fallback favicon (resolve_company_logo devolveu vazio)"

    domain = email.split("@")[-1] if "@" in email else ""
    initials = "".join(re.findall(r"[A-Za-z0-9]", razao))[:3].upper() or "EMP"
    logo = {"fallback_initials": initials}
    if domain and domain not in _FREE_MAIL:
        logo["google_favicon_url"] = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    return logo, origem


def _cat_enrich(args):
    app, cnpj, fallback, with_logo = args
    with app.app_context():
        used = cnpj
        data = _cat_brasilapi(cnpj)
        if not data and fallback and fallback != cnpj:
            used, data = fallback, _cat_brasilapi(fallback)
        if not data:
            return None
        if with_logo:
            logo, origem = _cat_logo(data, used)
        else:
            logo, origem = {}, "filial"
        return used, data, logo, origem


@api_bp.route("/company-catalog", methods=["GET"])
@api_bp.route("/companies/search", methods=["GET"])
def search_companies_catalog():
    """Catálogo: todas as empresas cujo nome contém o termo, agrupáveis por raiz de CNPJ
    (matriz + filiais vistas nos processos), cada uma com logo, razão social e CNPJ.
    O frontend envia o CNPJ escolhido a POST /search-company (modo STRICT_CNPJ)."""
    q = (request.args.get("q") or "").strip()
    debug = request.args.get("debug") == "1"
    include_branches = request.args.get("include_branches", "1") != "0"
    try:
        limit = max(1, min(int(request.args.get("limit", CATALOG_MAX_ROOTS)), CATALOG_MAX_ROOTS))
    except ValueError:
        limit = CATALOG_MAX_ROOTS
    if len(q) < 3:
        return jsonify({"error": "Informe ao menos 3 caracteres.", "items": []}), 400
    if not is_valid_company_search_input(q):
        return jsonify({"error": "empresas so são pesquisadas por razão social ou CNPJ", "items": []}), 400

    key = (_cat_norm(q), include_branches, limit)
    if not debug:
        hit = CATALOG_CACHE.get(key)
        if hit and time.time() - hit[0] < CATALOG_TTL:
            return jsonify(hit[1]), 200

    dbg = {"consulta": q}
    app = current_app._get_current_object()
    counts, seen = {}, {}   # raiz -> nº de processos que a citam / CNPJs completos vistos

    try:
        digits_q = re.sub(r"\D", "", q)
        if is_cnpj(q) and _cat_valid(digits_q):
            counts[digits_q[:8]] = 0
            seen[digits_q[:8]] = {digits_q}
            dbg["descoberta"] = "CNPJ informado"
        else:
            strat = resolve_search_strategy(q, brasil_api_fetcher=fetch_cnpj_brasilapi)
            brand = strat.get("comunica_query_term") or q
            dbg["termo_pje"] = brand

            processes_map = {}
            try:
                processes_map = orchestrator._fetch_comunica_pje(
                    brand, max_pages=CATALOG_PAGES, page_size=50
                ) or {}
            except Exception as exc:
                dbg["pje_erro"] = f"{type(exc).__name__}: {exc}"
                logger.warning("Catálogo: Comunica PJe falhou para '%s': %s", brand, exc)
            dbg["pje_processos"] = len(processes_map)

            # 1) CNPJs nos textos das publicações (contados 1x por processo e por raiz)
            for proc in processes_map.values():
                here = {}
                for txt in proc.get("publicacoes_texto", []) or []:
                    for m in CNPJ_REGEX.findall(str(txt)):
                        d = re.sub(r"\D", "", m)
                        if _cat_valid(d):
                            here.setdefault(d[:8], set()).add(d)
                for root, cnpjs in here.items():
                    counts[root] = counts.get(root, 0) + 1
                    seen.setdefault(root, set()).update(cnpjs)
            dbg["cnpjs_nos_textos"] = len(counts)

            # 2) Descoberta já existente no projeto (grupo/marca)
            try:
                for ent in discover_company_cnpjs(q, brand, processes_map, search_strategy=strat) or []:
                    d = re.sub(r"\D", "", str(ent))
                    if _cat_valid(d):
                        counts.setdefault(d[:8], 0)
                        seen.setdefault(d[:8], set()).add(d)
            except Exception as exc:
                dbg["discover_erro"] = f"{type(exc).__name__}: {exc}"
            dbg["raizes_candidatas"] = len(counts)

        roots = sorted(counts, key=lambda r: -counts[r])[:limit]
        jobs = [(app, _cat_matrix(r), sorted(seen[r])[0], True) for r in roots]
        n_matrizes = len(jobs)
        if include_branches:
            budget = CATALOG_MAX_BRANCH_JOBS
            for r in roots:
                mat = _cat_matrix(r)
                for c in [c for c in sorted(seen[r]) if c != mat][:CATALOG_BRANCHES_PER_ROOT]:
                    if budget <= 0:
                        break
                    jobs.append((app, c, None, False))
                    budget -= 1

        by_cnpj = is_cnpj(q)          # busca por CNPJ não passa pelo filtro de nome
        tokens = [] if by_cnpj else _cat_norm(q).split()
        items, emitted = [], set()
        descartados = sem_resposta = 0
        logos = {"resolve_company_logo": 0, "fallback": 0}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for job, res in zip(jobs, pool.map(_cat_enrich, jobs)):
                if not res:
                    sem_resposta += 1
                    continue
                cnpj, data, logo, origem = res
                if cnpj in emitted:
                    continue
                haystack = _cat_norm(f"{data.get('razao_social', '')} {data.get('nome_fantasia') or ''}")
                if not all(t in haystack for t in tokens):
                    descartados += 1
                    continue
                emitted.add(cnpj)
                if origem != "filial":
                    logos["resolve_company_logo" if origem == "resolve_company_logo" else "fallback"] += 1
                    if origem != "resolve_company_logo":
                        dbg.setdefault("logo_motivo_fallback", origem)
                items.append({
                    "cnpj": cnpj,
                    "cnpj_root": cnpj[:8],
                    "razao_social": data.get("razao_social"),
                    "nome_fantasia": data.get("nome_fantasia") or None,
                    "situacao": data.get("descricao_situacao_cadastral"),
                    "capital_social": data.get("capital_social"),
                    "municipio": data.get("municipio"),
                    "uf": data.get("uf"),
                    "matriz": cnpj[8:12] == "0001",
                    "processos_encontrados": counts.get(cnpj[:8], 0),
                    "logo": logo,
                    "logo_url": (logo.get("logo_url") or logo.get("google_favicon_url")
                                 or logo.get("logo_primary_url")),
                })
        dbg["brasilapi"] = {"consultas": len(jobs), "matrizes": n_matrizes,
                            "sem_resposta": sem_resposta, "descartados_por_nome": descartados}
        dbg["logos"] = logos

        items.sort(key=lambda c: (c["situacao"] != "ATIVA", -c["processos_encontrados"],
                                  _cat_norm(c["razao_social"]), c["cnpj_root"],
                                  not c["matriz"], c["cnpj"]))
        payload = {"query": q, "count": len(items), "items": items}
        if debug:
            payload["debug"] = dbg
        if not items:
            logger.info("Catálogo sem resultados para '%s': %s", q, dbg)
            return jsonify({**payload, "error": "NOT_FOUND"}), 404
        CATALOG_CACHE[key] = (time.time(), {k: v for k, v in payload.items() if k != "debug"})
        return jsonify(payload), 200
    except Exception as exc:
        logger.exception("Erro no catálogo de empresas ('%s'): %s", q, exc)
        body = {"error": "Erro interno ao montar o catálogo.", "items": []}
        if debug:
            body["debug"] = {**dbg, "excecao": f"{type(exc).__name__}: {exc}"}
        return jsonify(body), 500


@api_bp.route("/search_status/<int:search_id>", methods=["GET"])
@api_bp.route("/search-status/<int:search_id>", methods=["GET"])
def get_search_status(search_id: int):
    """
    Endpoint leve para polling pelo front-end.
    Permite verificar se o Deep Scan em segundo plano já completou a paginação.
    """
    try:
        search = db.session.get(CompanySearch, search_id)
        if not search:
            return jsonify({"error": "Registro não encontrado."}), 404
        is_completed = search.scan_status == "COMPLETED"
        response = {
            "search_id": search.id,
            "company_name": search.company_name,
            "scan_status": search.scan_status,
            "is_completed": is_completed,
            "total_processes": search.total_processes,
            "last_checked_at": search.last_checked_at.isoformat() if search.last_checked_at else None,
            "updated_at": search.updated_at.isoformat() if search.updated_at else None,
        }
        # Quando completado, retorna os dados atualizados para atualizar o dashboard sem reload
        if is_completed:
            proc_dicts = [p.to_dict() for p in search.processes]
            response["data"] = search.to_dict()
            response["data"]["empresa"] = search.company_name
            response["data"]["processos"] = proc_dicts
            response["data"]["is_broad_brand"] = "GRUPO CONSOLIDADO" in search.company_name.upper()

            # Prioriza estritamente os totais, desfechos e motivos consolidados do search
            # (calculados pelo background worker sobre TODOS os processos da varredura completa)
            response["data"]["total_processos"] = search.total_processes or len(proc_dicts)
            response["data"]["desfechos_sentencas"] = search.outcomes_summary or {}
            response["data"]["ranking_motivos"] = search.ranking_motivos or []
            response["data"]["ranking_motivos_condenacao"] = search.ranking_motivos_condenacao or []
            response["data"]["plus_previsao_condenacao"] = search.predictions_summary or []
            response["data"]["score_risco_terceirizacao"] = search.overall_risk_score or 0.0
            response["data"]["overall_risk_score"] = search.overall_risk_score or 0.0

            # Se proc_dicts contiver o volume completo ou superior, sincroniza métricas
            if proc_dicts and len(proc_dicts) >= (search.total_processes or 0):
                fresh_metrics = orchestrator.predictor.generate_dashboard_metrics(
                    search.company_name, proc_dicts
                )
                response["data"]["total_processos"] = fresh_metrics.get("total_processos", search.total_processes)
                response["data"]["desfechos_sentencas"] = fresh_metrics.get("desfechos_sentencas", search.outcomes_summary)
                response["data"]["ranking_motivos"] = fresh_metrics.get("ranking_motivos", search.ranking_motivos)
                response["data"]["ranking_motivos_condenacao"] = fresh_metrics.get("ranking_motivos_condenacao", search.ranking_motivos_condenacao)
                response["data"]["plus_previsao_condenacao"] = fresh_metrics.get("plus_previsao_condenacao", search.predictions_summary)
                response["data"]["score_risco_terceirizacao"] = fresh_metrics.get("score_risco_terceirizacao", search.overall_risk_score)
                response["data"]["overall_risk_score"] = fresh_metrics.get("score_risco_terceirizacao", search.overall_risk_score)

            response["data"]["parecer_agente_adk"] = response["data"].get("ai_analysis", {})
            if isinstance(search.economic_standing, dict):
                response["data"]["idoneidade_economica"] = search.economic_standing
                response["data"]["logo"] = search.economic_standing.get("logo", {})
                if search.economic_standing.get("indice_exposicao_financeira") is not None:
                    response["data"]["indice_exposicao_financeira"] = search.economic_standing["indice_exposicao_financeira"]
                    response["data"]["score_risco_terceirizacao"] = search.economic_standing["indice_exposicao_financeira"]
                    response["data"]["overall_risk_score"] = search.economic_standing["indice_exposicao_financeira"]

        return jsonify(response), 200
    except Exception as exc:
        logger.error("Erro ao verificar status do search_id %d: %s", search_id, exc)
        return jsonify({"error": "Registro não encontrado."}), 404


@api_bp.route("/sync-incremental/<int:search_id>", methods=["POST"])
def incremental_sync(search_id: int):
    """
    Executa varredura incremental por delta temporal:
    Consulta no DataJud/Comunica PJe apenas processos ou movimentações com
    dataHoraUltimaAtualizacao > cached_search.last_checked_at.
    """
    try:
        search = db.session.get(CompanySearch, search_id)
        if not search:
            return jsonify({"error": "Registro não encontrado."}), 404
        since_date = search.last_checked_at.isoformat() if search.last_checked_at else None

        processes_map = orchestrator._fetch_comunica_pje(search.company_name, max_pages=2, page_size=50)
        if not processes_map:
            search.last_checked_at = get_utc_now()
            db.session.commit()
            return jsonify({
                "status": "sem_novos_dados",
                "search_id": search_id,
                "updated": False,
                "message": "Nenhuma alteração recente encontrada no CNJ."
            }), 200

        delta_processes = orchestrator._enrich_with_datajud(processes_map, since_date=since_date)
        if not delta_processes:
            search.last_checked_at = get_utc_now()
            db.session.commit()
            return jsonify({
                "status": "sem_novos_dados",
                "search_id": search_id,
                "updated": False,
                "message": "Movimentações processuais inalteradas."
            }), 200

        new_hash = generate_movements_hash(delta_processes)
        if new_hash == search.movements_hash:
            search.last_checked_at = get_utc_now()
            db.session.commit()
            return jsonify({
                "status": "hash_inalterado",
                "search_id": search_id,
                "updated": False,
                "message": "Metadados idênticos. Não houve necessidade de reprocessar com IA."
            }), 200

        # Novos andamentos detectados: recalcula e roda IA
        metrics = orchestrator._calculate_metrics(search.company_name, delta_processes, scan_status="COMPLETED")
        ai_res = orchestrator.ai_analyzer.analyze_company(metrics)

        search.total_processes = metrics.get("total_processos", search.total_processes)
        search.outcomes_summary = metrics.get("desfechos_sentencas", search.outcomes_summary)
        search.ranking_motivos = metrics.get("ranking_motivos", search.ranking_motivos)
        search.predictions_summary = metrics.get("plus_previsao_condenacao", search.predictions_summary)
        search.overall_risk_score = metrics.get("score_risco_terceirizacao", search.overall_risk_score)
        search.ai_analysis = ai_res
        search.movements_hash = new_hash
        search.last_checked_at = datetime.utcnow()

        db.session.commit()
        return jsonify({
            "status": "atualizado_com_sucesso",
            "search_id": search_id,
            "updated": True,
            "data": search.to_dict()
        }), 200
    except Exception as exc:
        db.session.rollback()
        logger.exception("Erro na varredura incremental do search_id %d: %s", search_id, exc)
        return jsonify({"error": "Falha na sincronização incremental."}), 500


@api_bp.route("/history", methods=["GET", "DELETE"])
def get_search_history():
    """
    GET: Retorna o histórico de análises de empresas salvas no banco de dados.
    DELETE: Remove todo o histórico de buscas e processos vinculados do banco de dados.
    """
    if request.method == "DELETE":
        try:
            ProcessRecord.query.delete()
            CompanySearch.query.delete()
            db.session.commit()
            SEARCH_CACHE.clear()
            logger.info("Todo o histórico de pesquisas e processos foi limpo pelo usuário.")
            return jsonify({
                "status": "success",
                "message": "Histórico de pesquisas e processos limpo com sucesso."
            }), 200
        except Exception as exc:
            db.session.rollback()
            logger.error("Erro ao limpar histórico de pesquisas: %s", exc)
            return jsonify({"error": "Falha ao limpar histórico."}), 500

    limit = int(request.args.get("limit", 20))
    try:
        searches = (
            CompanySearch.query.order_by(CompanySearch.updated_at.desc())
            .limit(limit)
            .all()
        )
        return jsonify({
            "total": len(searches),
            "items": [s.to_dict() for s in searches]
        }), 200
    except Exception as exc:
        logger.error("Erro ao listar histórico de pesquisas: %s", exc)
        return jsonify({"error": "Não foi possível carregar o histórico."}), 500


@api_bp.route("/history/<int:search_id>", methods=["GET", "DELETE"])
def get_search_detail(search_id: int):
    """
    GET: Retorna os dados detalhados e a lista de processos de uma análise específica.
    DELETE: Remove um registro específico do histórico.
    """
    if request.method == "DELETE":
        try:
            search = db.session.get(CompanySearch, search_id)
            if not search:
                return jsonify({"error": "Registro não encontrado."}), 404
            db.session.delete(search)
            db.session.commit()
            logger.info("Registro search_id=%d removido pelo usuário.", search_id)
            return jsonify({
                "status": "success",
                "message": f"Registro {search_id} removido com sucesso."
            }), 200
        except Exception as exc:
            db.session.rollback()
            logger.error("Erro ao deletar search_id %d: %s", search_id, exc)
            return jsonify({"error": "Falha ao remover o registro."}), 500
    try:
        search = db.session.get(CompanySearch, search_id)
        if not search:
            return jsonify({"error": "Registro não encontrado."}), 404
        proc_dicts = [p.to_dict() for p in search.processes]
        result = search.to_dict()
        result["empresa"] = search.company_name
        result["processos"] = proc_dicts
        if proc_dicts:
            fresh_metrics = orchestrator.predictor.generate_dashboard_metrics(
                search.company_name, proc_dicts
            )
            result["plus_previsao_condenacao"] = fresh_metrics["plus_previsao_condenacao"]
            result["score_risco_terceirizacao"] = fresh_metrics["score_risco_terceirizacao"]
            result["ranking_motivos_condenacao"] = fresh_metrics.get("ranking_motivos_condenacao", [])
            result["previsao_bloqueada"] = fresh_metrics.get("previsao_bloqueada", False)
            result["motivo_bloqueio_previsao"] = fresh_metrics.get("motivo_bloqueio_previsao")
        result["parecer_agente_adk"] = result.get("ai_analysis", {})
        return jsonify(result), 200
    except Exception as exc:
        logger.error("Erro ao obter detalhes da pesquisa ID %d: %s", search_id, exc)
        return jsonify({"error": "Registro não encontrado."}), 404