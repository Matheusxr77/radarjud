"""
Módulo de compatibilidade com a especificação original:
Expõe os endpoints da API REST em app/routes.py.
"""
from app.routes.api import api_bp

__all__ = ["api_bp"]

