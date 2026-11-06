import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import text

from app.extensions import db
from app.models.company_search import CompanySearch, get_utc_now
from app.models.process_record import ProcessRecord
from app.services.orchestrator import CNJMetadataOrchestrator
from app.utils.sanitizer import sanitize_company_name, is_cnpj, fetch_cnpj_brasilapi
from app.utils.crypto import generate_movements_hash

logger = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
orchestrator = CNJMetadataOrchestrator()

# Pool dedicado para tarefas assíncronas em segundo plano (Deep Scan)
background_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="radarjus_bg_worker")


def _deep_scan_background_worker(app, search_id: int, company_name: str):
    """
    Worker assíncrono executado em segundo plano para varredura completa (Deep Scan).
    Pagina todos os processos disponíveis, atualiza estatísticas no banco e marca scan_status='COMPLETED'.
    """
    with app.app_context():
        try:
            logger.info("Iniciando Deep Scan em segundo plano para search_id=%d (%s)...", search_id, company_name)
            search = CompanySearch.query.get(search_id)
            if not search:
                return

            search.scan_status = "SCANNING_BACKGROUND"
            db.session.commit()

            # Executa varredura profunda completa
            deep_results = orchestrator.analyze_company_by_name(company_name, fast_path=False)

            search = CompanySearch.query.get(search_id)
            if not search:
                return

            search.total_processes = deep_results.get("total_processos", search.total_processes)
            search.secrecy_count = sum(
                qtd for lvl, qtd in deep_results.get("niveis_sigilo", {}).items() if int(lvl) > 0
            )
            search.secrecy_rate = round(
                search.secrecy_count / max(search.total_processes, 1), 3
            )
            search.outcomes_summary = deep_results.get("desfechos_sentencas", search.outcomes_summary)
            search.ranking_motivos = deep_results.get("ranking_motivos", search.ranking_motivos)
            search.predictions_summary = deep_results.get("plus_previsao_condenacao", search.predictions_summary)
            search.overall_risk_score = deep_results.get("score_risco_terceirizacao", search.overall_risk_score)
            search.ai_analysis = deep_results.get("ai_analysis", search.ai_analysis)
            search.movements_hash = deep_results.get("movements_hash", search.movements_hash)
            search.scan_status = "COMPLETED"
            search.last_checked_at = get_utc_now()

            # Persiste novos processos sem duplicar
            existing_numbers = {p.numero_processo for p in search.processes}
            for proc in deep_results.get("processos", []):
                num = proc.get("numero_processo")
                if num and num not in existing_numbers:
                    record = ProcessRecord(
                        company_search_id=search.id,
                        numero_processo=num,
                        tribunal=proc.get("tribunal"),
                        grau=proc.get("grau", "G1"),
                        nivel_sigilo=proc.get("nivelSigilo", 0),
                        primary_subject=proc.get("primary_subject"),
                        subjects_json=proc.get("subjects"),
                        observation=proc.get("observation"),
                        sentence_outcome=proc.get("sentence_outcome", "em_aberto"),
                        has_expert_report=proc.get("has_expert_report", False),
                        raw_metadata=proc.get("raw_metadata"),
                    )
                    db.session.add(record)
                    existing_numbers.add(num)

            db.session.commit()
            logger.info("Deep Scan finalizado com sucesso para search_id=%d. Total: %d processos.", search_id, search.total_processes)
        except Exception as exc:
            db.session.rollback()
            logger.exception("Erro no Deep Scan em background (search_id=%d): %s", search_id, exc)
            try:
                search = CompanySearch.query.get(search_id)
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
        "apis_governamentais": {
            "datajud": "configured",
            "comunica_pje": "configured",
            "brasilapi": "active"
        }
    }), 200


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

    if not company_input or not company_input.strip():
        return jsonify({"error": "Parâmetro 'company_name' é obrigatório."}), 400

    raw_query = company_input.strip()

    # Se for CNPJ, consulta BrasilAPI para obter a Razão Social oficial
    resolved_name = raw_query
    if is_cnpj(raw_query):
        official_razao = fetch_cnpj_brasilapi(raw_query)
        if official_razao:
            resolved_name = official_razao

    sanitized = sanitize_company_name(resolved_name)
    normalized_name = sanitized.upper() if sanitized else resolved_name.upper()

    # =========================================================================
    # CURTO-CIRCUITO NÍVEL 1 (Antes do CNJ): Busca instantânea no PostgreSQL
    # =========================================================================
    if not force_refresh:
        try:
            cached_search = (
                CompanySearch.query.filter_by(normalized_name=normalized_name)
                .order_by(CompanySearch.updated_at.desc())
                .first()
            )
            # Só retorna cache se for recente e se a varredura tiver sido COMPLETA
            # (evita congelar a interface em uma busca preliminar incompleta)
            if cached_search and cached_search.is_recent(hours=24) and cached_search.scan_status == "COMPLETED":
                logger.info("Curto-circuito Nível 1 ativado para '%s' (search_id=%d).", normalized_name, cached_search.id)
                res = cached_search.to_dict()
                res["empresa"] = cached_search.company_name
                res["cached"] = True
                res["cache_level"] = "L1_DATABASE"
                res["search_id"] = cached_search.id
                res["processos"] = [p.to_dict() for p in cached_search.processes]
                return jsonify(res), 200
        except Exception as db_err:
            logger.debug("Falha na checagem de cache L1 no banco: %s", db_err)

    if force_refresh:
        orchestrator._cache.pop(raw_query.lower(), None)
        orchestrator._cache.pop(normalized_name.lower(), None)

    try:
        # =====================================================================
        # FAST-PATH OTIMIZADO (< 3 segundos): Busca até 200 publicações reais
        # =====================================================================
        fast_results = orchestrator.analyze_company_by_name(
            company_name=resolved_name,
            fast_path=True,
            limit_processes=100,
        )

        new_hash = fast_results.get("movements_hash")
        initial_status = fast_results.get("scan_status", "COMPLETED")

        # =====================================================================
        # CURTO-CIRCUITO NÍVEL 2 (Antes da IA): Invalidação inteligente por Hash
        # =====================================================================
        cached_search = None
        try:
            cached_search = (
                CompanySearch.query.filter_by(normalized_name=normalized_name)
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

        # Persistência do resultado no PostgreSQL
        search_id = None
        try:
            search_entry = CompanySearch(
                company_name=resolved_name,
                normalized_name=normalized_name,
                total_processes=fast_results.get("total_processos", 0),
                secrecy_count=sum(
                    qtd for lvl, qtd in fast_results.get("niveis_sigilo", {}).items() if int(lvl) > 0
                ),
                secrecy_rate=fast_results.get("taxa_sigilo_pct", 0.0) / 100.0,
                outcomes_summary=fast_results.get("desfechos_sentencas", {}),
                ranking_motivos=fast_results.get("ranking_motivos", []),
                predictions_summary=fast_results.get("plus_previsao_condenacao", []),
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
                    tribunal=proc.get("tribunal"),
                    grau=proc.get("grau", "G1"),
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
                _deep_scan_background_worker, app_ref, search_id, resolved_name
            )

        fast_results["scan_status"] = initial_status
        return jsonify(fast_results), 200

    except Exception as exc:
        logger.exception("Erro ao processar busca de empresa: %s", exc)
        return jsonify({"error": "Erro interno ao consultar APIs do CNJ."}), 500


@api_bp.route("/search_status/<int:search_id>", methods=["GET"])
@api_bp.route("/search-status/<int:search_id>", methods=["GET"])
def get_search_status(search_id: int):
    """
    Endpoint leve para polling pelo front-end.
    Permite verificar se o Deep Scan em segundo plano já completou a paginação.
    """
    try:
        search = CompanySearch.query.get_or_404(search_id)
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
            response["data"] = search.to_dict()
            response["data"]["empresa"] = search.company_name
            response["data"]["processos"] = [p.to_dict() for p in search.processes]

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
        search = CompanySearch.query.get_or_404(search_id)
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


@api_bp.route("/history", methods=["GET"])
def get_search_history():
    """Retorna o histórico de análises de empresas salvas no banco de dados."""
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


@api_bp.route("/history/<int:search_id>", methods=["GET"])
def get_search_detail(search_id: int):
    """Retorna os dados detalhados e a lista de processos de uma análise específica."""
    try:
        search = CompanySearch.query.get_or_404(search_id)
        result = search.to_dict()
        result["empresa"] = search.company_name
        result["processos"] = [p.to_dict() for p in search.processes]
        return jsonify(result), 200
    except Exception as exc:
        logger.error("Erro ao obter detalhes da pesquisa ID %d: %s", search_id, exc)
        return jsonify({"error": "Registro não encontrado."}), 404
