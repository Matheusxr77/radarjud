import time
from urllib.parse import quote

from app.extensions import db
from app.models.company_search import CompanySearch, get_utc_now
from app.routes.api import SEARCH_CACHE
from app.services.pdf_report import (
    _generate_pdf_pure_python,
    generate_company_report_pdf,
    normalize_report_payload,
)


def _sample_bradesco_payload():
    return {
        "empresa": "BRADESCO (GRUPO CONSOLIDADO / FILIAIS)",
        "total_processos": 942,
        "indice_exposicao_financeira": 15.9,
        "score_risco_terceirizacao": 15.9,
        "probabilidade_condenacao_geral_pct": 88.2,
        "desfechos_sentencas": {
            "condenacoes": 410,
            "acordos": 180,
            "improcedentes": 55,
            "em_aberto": 297,
        },
        "ranking_motivos": [
            {"motivo": "Horas Extras e Intervalo Intrajornada", "quantidade": 210, "percentual": 22.3},
            {"motivo": "Direitos da Categoria Bancária (7ª e 8ª Horas)", "quantidade": 165, "percentual": 17.5},
        ],
        "ranking_motivos_condenacao": [
            {"motivo": "Horas Extras e Intervalo Intrajornada", "quantidade_condenacoes": 145, "percentual": 35.4},
            {"motivo": "Equiparação Salarial e Desvio de Função", "quantidade_condenacoes": 92, "percentual": 22.4},
        ],
        "plus_previsao_condenacao": [
            {
                "assunto": "Horas Extras e Intervalo Intrajornada",
                "probabilidade_prevista_pct": 78.4,
                "confiabilidade": "Alta",
                "amostra_julgada": 645,
            }
        ],
        "idoneidade_economica": {
            "cenario": "CENARIO_A",
            "cenario_tipo": "CENARIO_A",
            "cenario_badge": "Cenário A · Solvência Comprovada",
            "cenario_titulo": "Cenário A: Idoneidade Econômica Robusta • Risco de Calote Subsidiário Mitigado",
            "cenario_descricao": "A empresa apresenta situação cadastral ATIVA e Capital Social consolidado de R$ 115.187.056.914,72.",
            "capital_social_num": 115187056914.72,
            "capital_social_formatado": "R$ 115.187.056.914,72",
            "badge_compatibilidade_porte": "Alta Solidez Patrimonial • Compatível com o porte (DEMAIS)",
            "subtitulo_unidades": "Capital Social consolidado cobrindo 51 unidades/filiais mapeadas.",
            "situacao_cadastral": "ATIVA",
            "probabilidade_insolvencia": 0.8,
            "risco_calote_subsidiario": 0.7,
            "possui_sancoes_cgu": False,
            "possui_divida_ativa": False,
            "cnpj_utilizado": "60746948000112",
            "cnpjs_consolidados": ["60746948000112", "33055146000193"],
        },
        "ai_analysis": {
            "perfil_risco_consolidado": "Baixo / Solvente",
            "recomendacao_homologacao": "HOMOLOGAR COM MONITORAMENTO",
            "resumo_executivo": "Conglomerado financeiro com solvência comprovada sob a Súmula 331 do TST.",
            "recomendacoes_compliance": [
                "Exigir certidões negativas periódicas e comprovantes de FGTS/INSS."
            ],
        },
        "scan_status": "COMPLETED",
    }


def test_pdf_export_by_history_id_returns_200_and_valid_pdf(client, app):
    """Garante que GET /api/v1/history/<id>/summary.pdf retorna 200 e um PDF válido."""
    payload = _sample_bradesco_payload()
    with app.app_context():
        search = CompanySearch(
            company_name=payload["empresa"],
            normalized_name="BRADESCO",
            total_processes=payload["total_processos"],
            outcomes_summary=payload["desfechos_sentencas"],
            ranking_motivos=payload["ranking_motivos"],
            ranking_motivos_condenacao=payload["ranking_motivos_condenacao"],
            predictions_summary=payload["plus_previsao_condenacao"],
            economic_standing=payload["idoneidade_economica"],
            overall_risk_score=payload["score_risco_terceirizacao"],
            ai_analysis=payload["ai_analysis"],
            scan_status="COMPLETED",
            last_checked_at=get_utc_now(),
        )
        db.session.add(search)
        db.session.commit()
        sid = search.id

    resp = client.get(f"/api/v1/history/{sid}/summary.pdf")
    assert resp.status_code == 200
    assert "application/pdf" in resp.content_type
    assert resp.data.startswith(b"%PDF-")
    assert len(resp.data) > 500


def test_pdf_export_with_slashes_in_company_name_and_cnpj_never_404(client, app):
    """
    Valida que barras '/' no nome ('BRADESCO (GRUPO CONSOLIDADO / FILIAIS)') e no CNPJ
    ('60.746.948/0001-12') NUNCA retornam 404, tanto via path quanto via query string.
    """
    payload = _sample_bradesco_payload()
    SEARCH_CACHE["BROAD_BRAND::BRADESCO::"] = (time.time(), payload)

    try:
        # 1. Path com barra no nome consolidado (/api/v1 e /api)
        resp_name_v1 = client.get("/api/v1/report/pdf/BRADESCO (GRUPO CONSOLIDADO / FILIAIS)")
        assert resp_name_v1.status_code == 200
        assert "application/pdf" in resp_name_v1.content_type
        assert resp_name_v1.data.startswith(b"%PDF-")

        resp_name_alias = client.get("/api/report/pdf/BRADESCO (GRUPO CONSOLIDADO / FILIAIS)")
        assert resp_name_alias.status_code == 200
        assert "application/pdf" in resp_name_alias.content_type
        assert resp_name_alias.data.startswith(b"%PDF-")

        # 2. Path com barra no CNPJ formatado
        resp_cnpj_v1 = client.get("/api/v1/report/pdf/60.746.948/0001-12")
        assert resp_cnpj_v1.status_code == 200
        assert "application/pdf" in resp_cnpj_v1.content_type
        assert resp_cnpj_v1.data.startswith(b"%PDF-")

        resp_cnpj_export = client.get("/api/export/pdf/60.746.948/0001-12")
        assert resp_cnpj_export.status_code == 200
        assert "application/pdf" in resp_cnpj_export.content_type
        assert resp_cnpj_export.data.startswith(b"%PDF-")

        # 3. Query string (?empresa=... e ?q=...)
        q_name = quote("BRADESCO (GRUPO CONSOLIDADO / FILIAIS)")
        resp_qs_name = client.get(f"/api/report/pdf?empresa={q_name}")
        assert resp_qs_name.status_code == 200
        assert "application/pdf" in resp_qs_name.content_type
        assert resp_qs_name.data.startswith(b"%PDF-")

        q_cnpj = quote("60.746.948/0001-12")
        resp_qs_cnpj = client.get(f"/api/export/pdf?q={q_cnpj}")
        assert resp_qs_cnpj.status_code == 200
        assert "application/pdf" in resp_qs_cnpj.content_type
        assert resp_qs_cnpj.data.startswith(b"%PDF-")
    finally:
        SEARCH_CACHE.pop("BROAD_BRAND::BRADESCO::", None)


def test_pdf_export_via_post_with_session_payload_works_without_db_record(client):
    """
    Garante que enviar o objeto já carregado na tela via POST /api/v1/report/pdf
    sempre gera o PDF com 200 OK, mesmo sem registro prévio no banco ou cache.
    """
    payload = _sample_bradesco_payload()
    resp = client.post("/api/v1/report/pdf", json=payload)
    assert resp.status_code == 200
    assert "application/pdf" in resp.content_type
    assert resp.data.startswith(b"%PDF-")
    assert "radarjus-bradesco.pdf" in resp.headers.get("Content-Disposition", "")


def test_pdf_report_contains_all_v2_risktech_and_solvency_fields():
    """
    Verifica que o normalizador e o gerador de PDF incluem todos os campos obrigatórios da v2.0:
    Nome/Grupo, CNPJ Matriz, Exposição Financeira, Eventos, Confiança Bayesiana,
    Capital Social Consolidado, Situação RFB, Insolvência, Risco Subsidiário, Cenário A/B,
    Ranking de Condenações e Parecer Súmula 331 TST.
    """
    payload = _sample_bradesco_payload()
    norm = normalize_report_payload(payload)

    assert norm["company_name"] == "BRADESCO (GRUPO CONSOLIDADO / FILIAIS)"
    assert norm["cnpj_formatted"] == "60.746.948/0001-12"
    assert norm["exposure_score"] == 15.9
    assert norm["total_processes"] == 942
    assert norm["conf_label"] == "Alta"
    assert norm["economic"]["capital_fmt"] == "R$ 115.187.056.914,72"
    assert norm["economic"]["situacao_rfb"] == "ATIVA"
    assert norm["economic"]["prob_insolv"] == 0.8
    assert norm["economic"]["risco_sub"] == 0.7
    assert norm["economic"]["cenario_tipo"] == "CENARIO_A"
    assert len(norm["ranking_condenacoes"]) == 2
    assert "HOMOLOGAR" in norm["compliance"]["recomendacao"]

    # Valida geração via ReportLab (ou fallback) e também diretamente no fallback puro
    pdf_bytes = generate_company_report_pdf(payload)
    assert pdf_bytes.startswith(b"%PDF-")
    assert b"%%EOF" in pdf_bytes

    pure_pdf = _generate_pdf_pure_python(norm)
    assert pure_pdf.startswith(b"%PDF-1.4")
    assert b"BRADESCO \\(GRUPO CONSOLIDADO / FILIAIS\\)" in pure_pdf
    assert b"60.746.948/0001-12" in pure_pdf
    assert b"R$ 115.187.056.914,72" in pure_pdf
    assert b"HOMOLOGAR COM MONITORAMENTO" in pure_pdf
    assert b"Horas Extras e Intervalo Intrajornada" in pure_pdf
