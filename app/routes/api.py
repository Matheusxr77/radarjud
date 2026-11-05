import logging
from flask import Blueprint, request, jsonify
from sqlalchemy import text
from app.extensions import db
from app.services.cnj_orchestrator import CNJMetadataOrchestrator
from app.models.company_search import CompanySearch

logger = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
orchestrator = CNJMetadataOrchestrator()


@api_bp.route("/health", methods=["GET"])
def health_check():
    """Verifica a integridade da aplicação e a conectividade com o PostgreSQL."""
    db_status = "connected"
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"unavailable: {str(exc)}"

    return jsonify({
        "status": "healthy",
        "service": "LexGuard AI Backend",
        "database": db_status,
        "apis_governamentais": {
            "datajud": "configured",
            "comunica_pje": "configured"
        }
    }), 200


@api_bp.route("/company-dashboard", methods=["POST"])
@api_bp.route("/search-company", methods=["POST", "GET"])
def search_company():
    """
    Endpoint principal para pesquisa e diagnóstico de risco de fornecedores/terceiros.
    Aceita requisições POST com JSON ou GET com query param ?name=...
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        company_name = data.get("company_name") or data.get("name")
        limit = int(data.get("limit", 100))
        force_refresh = bool(data.get("force_refresh", False))
    else:
        company_name = request.args.get("name") or request.args.get("company_name")
        limit = int(request.args.get("limit", 100))
        force_refresh = request.args.get("force_refresh", "false").lower() in ("true", "1")

    if not company_name or not company_name.strip():
        return jsonify({
            "error": "Parâmetro 'company_name' é obrigatório."
        }), 400

    if force_refresh:
        cache_key = company_name.strip().lower()
        orchestrator._cache.pop(cache_key, None)

    try:
        results = orchestrator.analyze_company_by_name(
            company_name=company_name.strip()
        )

        # Persistência opcional no banco de dados para histórico e auditoria
        try:
            normalized_name = company_name.strip().upper()
            search_entry = CompanySearch(
                company_name=company_name.strip(),
                normalized_name=normalized_name,
                total_processes=results.get("total_processos", 0),
                secrecy_count=sum(
                    qtd for lvl, qtd in results.get("niveis_sigilo", {}).items() if int(lvl) > 0
                ),
                secrecy_rate=round(
                    sum(qtd for lvl, qtd in results.get("niveis_sigilo", {}).items() if int(lvl) > 0)
                    / max(results.get("total_processos", 1), 1),
                    3
                ),
                outcomes_summary=results.get("desfechos_sentencas", {}),
                ranking_motivos=results.get("ranking_motivos", []),
                predictions_summary=results.get("plus_previsao_condenacao", []),
                overall_risk_score=results.get("score_risco_terceirizacao", 0.0),
            )
            db.session.add(search_entry)
            db.session.commit()
            results["search_id"] = search_entry.id
        except Exception as db_exc:
            db.session.rollback()
            logger.debug("Persistência no DB ignorada ou indisponível: %s", db_exc)

        return jsonify(results), 200
    except Exception as exc:
        logger.exception("Erro ao processar busca de empresa: %s", exc)
        return jsonify({
            "error": "Erro interno ao consultar APIs do CNJ.",
            "details": str(exc)
        }), 500


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
        return jsonify({
            "error": "Não foi possível carregar o histórico.",
            "details": str(exc)
        }), 500


@api_bp.route("/history/<int:search_id>", methods=["GET"])
def get_search_detail(search_id: int):
    """Retorna os dados detalhados e a lista de processos de uma análise específica."""
    try:
        search = CompanySearch.query.get_or_404(search_id)
        result = search.to_dict()
        result["processos"] = [p.to_dict() for p in search.processes]
        return jsonify(result), 200
    except Exception as exc:
        logger.error("Erro ao obter detalhes da pesquisa ID %d: %s", search_id, exc)
        return jsonify({
            "error": "Registro não encontrado.",
            "details": str(exc)
        }), 404

