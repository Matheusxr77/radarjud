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
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # Registro de Blueprints
    app.register_blueprint(api_bp)

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
        except Exception as exc:
            app.logger.warning("Não foi possível conectar imediatamente ao PostgreSQL para db.create_all(): %s", exc)

    return app

