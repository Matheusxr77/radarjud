import os
import logging
from flask import Flask, jsonify, render_template
from app.config import config_by_name
from app.extensions import db, cors
from app.routes import api_bp


def create_app(config_name: str = None) -> Flask:
    """
    Application Factory para o LexGuard AI.
    Configura banco de dados PostgreSQL, CORS, logs e rotas da API.
    """
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development").lower()

    app = Flask(__name__)
    config_obj = config_by_name.get(config_name, config_by_name["default"])
    app.config.from_object(config_obj)

    # Configuração de Logs
    logging.basicConfig(
        level=logging.DEBUG if app.config.get("DEBUG") else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Inicialização de Extensões
    db.init_app(app)
    cors.init_app(app, resources={r"/api/.*": {"origins": "*"}, r"/api/*": {"origins": "*"}})

    # Registro de Blueprints
    app.register_blueprint(api_bp)

    # Aliases de exportação PDF sob /api/... (além de /api/v1/...)
    from app.routes.api import handle_pdf_export_request

    @app.route("/api/report/pdf", methods=["GET", "POST"])
    @app.route("/api/export/pdf", methods=["GET", "POST"])
    def _api_pdf_query_or_post():
        return handle_pdf_export_request()

    @app.route("/api/report/pdf/<path:company_name>", methods=["GET"])
    @app.route("/api/export/pdf/<path:company_name>", methods=["GET"])
    def _api_pdf_by_path(company_name: str):
        return handle_pdf_export_request(company_name=company_name)

    @app.route("/api/history/<int:search_id>/summary.pdf", methods=["GET"])
    def _api_pdf_by_history_id(search_id: int):
        return handle_pdf_export_request(search_id=search_id)

    # Rota raiz informativa
    @app.route("/", methods=["GET"])
    def index():
        try:
            return render_template("index.html")
        except Exception:
            return jsonify({
                "service": "LexGuard AI - Orquestrador de Risco de Terceiros",
                "version": "1.0.0",
                "description": "Diagnóstico de Passivo Trabalhista/Cível via Metadados DataJud e Comunica PJe",
                "endpoints": {
                    "health": "/api/v1/health",
                    "search_company": "/api/v1/search-company",
                    "company_dashboard": "/api/v1/company-dashboard",
                    "history": "/api/v1/history"
                }
            }), 200

    # Cria tabelas no banco de dados se não existirem
    with app.app_context():
        try:
            db.create_all()
            if db.engine.dialect.name == "postgresql":
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE company_searches ADD COLUMN IF NOT EXISTS movements_hash VARCHAR(64);"))
                    conn.execute(db.text("ALTER TABLE company_searches ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;"))
                    conn.execute(db.text("ALTER TABLE company_searches ADD COLUMN IF NOT EXISTS scan_status VARCHAR(20) DEFAULT 'COMPLETED';"))
                    conn.execute(db.text("ALTER TABLE company_searches ADD COLUMN IF NOT EXISTS ai_analysis JSON;"))
                    conn.execute(db.text("ALTER TABLE company_searches ADD COLUMN IF NOT EXISTS economic_standing JSON;"))
                    conn.execute(db.text("ALTER TABLE company_searches ADD COLUMN IF NOT EXISTS ranking_motivos_condenacao JSON;"))
                    conn.execute(db.text("ALTER TABLE process_records ADD COLUMN IF NOT EXISTS observation VARCHAR(500);"))
                    conn.execute(db.text("ALTER TABLE process_records ALTER COLUMN grau TYPE VARCHAR(50);"))
                    conn.execute(db.text("ALTER TABLE process_records ALTER COLUMN tribunal TYPE VARCHAR(50);"))
                    conn.commit()
        except Exception as exc:
            app.logger.warning("Não foi possível conectar imediatamente ao PostgreSQL para db.create_all(): %s", exc)

    return app

