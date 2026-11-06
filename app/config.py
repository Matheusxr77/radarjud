import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis do .env se existir
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _database_url() -> str:
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        for driver in ("postgresql+psycopg://", "postgresql://"):
            if configured_url.startswith(driver):
                return configured_url.replace(
                    driver, "postgresql+psycopg2://", 1
                )
        return configured_url

    return (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'radarjud')}:"
        f"{os.getenv('POSTGRES_PASSWORD', '')}@"
        f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'radarjud')}"
    )


class Config:
    """Configuração base da aplicação LexGuard AI."""
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-fallback-key")
    DEBUG = False
    TESTING = False

    # Banco de Dados PostgreSQL
    POSTGRES_USER = os.getenv("POSTGRES_USER", "radarjud")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
    POSTGRES_DB = os.getenv("POSTGRES_DB", "radarjud")
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # APIs Públicas Oficiais do Judiciário
    DATAJUD_API_KEY = os.getenv("DATAJUD_API_KEY", "")
    DATAJUD_BASE_URL = os.getenv("DATAJUD_BASE_URL", "https://api-publica.datajud.cnj.jus.br")
    COMUNICA_PJE_URL = os.getenv("COMUNICA_PJE_URL", "https://comunicaapi.pje.jus.br/api/v1/comunicacao")

    # Configurações de Conectividade e Resiliência
    REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    MAX_PROCESSES_PER_QUERY = int(os.getenv("MAX_PROCESSES_PER_QUERY", "100"))
    CACHE_EXPIRATION_HOURS = int(os.getenv("CACHE_EXPIRATION_HOURS", "24"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    # Garante pool robusto em produção
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": 10,
        "max_overflow": 20,
    }


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    # Em testes, usamos sqlite em memória para rapidez e isolamento caso Postgres não esteja rodando
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
