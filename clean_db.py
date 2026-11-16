import os
import sys
import requests

def clean_database():
    """
    Limpa completamente todas as pesquisas de empresas e processos salvos no banco de dados.
    Suporta execução direta via SQLAlchemy, conexão local na porta do Docker ou chamada à API REST.
    """
    # 1. Tenta via chamada direta à API (caso o backend esteja rodando localmente ou no Docker)
    try:
        resp = requests.delete("http://localhost:5000/api/v1/history", timeout=5)
        if resp.ok:
            data = resp.json()
            print(f"✓ Banco de dados limpo com sucesso via API RadarJus! ({data.get('message', 'Histórico apagado')})")
            return
    except Exception:
        pass

    # 2. Tenta via SQLAlchemy local
    try:
        from app import create_app
        from app.extensions import db
        from app.models.company_search import CompanySearch
        from app.models.process_record import ProcessRecord

        app = create_app()
        with app.app_context():
            num_proc = ProcessRecord.query.delete()
            num_search = CompanySearch.query.delete()
            db.session.commit()
            print(f"✓ Banco de dados limpo com sucesso! ({num_search} pesquisas e {num_proc} processos removidos).")
            return
    except Exception as exc:
        pass

    # 3. Instruções claras caso o banco esteja isolado dentro da rede interna do Docker
    print("Aviso: Conexão direta indisponível (o PostgreSQL está na rede interna do Docker).")
    print("Execute um dos comandos abaixo para limpar o banco de dados:")
    print("  1. No navegador ou no frontend: clique na aba 'Histórico' e use o botão 'Limpar Todo o Histórico'")
    print("  2. Via terminal usando o container backend:")
    print("     docker compose exec backend python3 clean_db.py")
    print("  3. Ou via curl:")
    print("     curl -X DELETE http://localhost:5000/api/v1/history")

if __name__ == "__main__":
    clean_database()
