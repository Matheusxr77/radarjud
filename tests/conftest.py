import pytest
from app import create_app
from app.extensions import db


@pytest.fixture
def app():
    """Cria uma instância da aplicação configurada para testes (SQLite in-memory)."""
    app = create_app("testing")

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Cliente de teste Flask para disparar requisições HTTP."""
    return app.test_client()

