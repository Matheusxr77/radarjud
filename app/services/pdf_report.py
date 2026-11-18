import io
import re
import html
import logging
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _format_cnpj(raw: Optional[str]) -> str:
    """Formata um CNPJ de 14 dígitos como XX.XXX.XXX/XXXX-XX ou retorna fallback legível."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return "Não informado / Grupo Consolidado"


def _format_pct(val: Any, fallback: str = "0,0%") -> str:
    if val is None or val == "":
        return fallback
    try:
        num = float(val)
        return f"{num:.1f}%".replace(".", ",")
    except (TypeError, ValueError):
        return str(val)


def _format_brl(val: Any) -> str:
    if isinstance(val, str) and "R$" in val:
        return val
    try:
        num = float(val or 0.0)
        return f"R$ {num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "R$ 0,00"


def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    text = str(val).strip()
    return text if text else default


def _xml_escape(text: Any) -> str:
    return html.escape(_safe_str(text))


def _ascii_latin1(text: str) -> str:
    """Normaliza caracteres especiais para codificação WinAnsi (Latin-1) no gerador PDF fallback."""
    replacements = {
        "•": "-",
        "·": "-",
        "—": "-",
        "–": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        normalized = unicodedata.normalize("NFKD", text)
        return normalized.encode("latin-1", "ignore").decode("latin-1")


def _extract_bayesian_confidence(payload: Dict[str, Any], outcomes: Dict[str, Any]) -> Tuple[str, str]:
    """Determina o Grau de Confiança Bayesiano e detalhes da amostra."""
    preds = (
        payload.get("plus_previsao_condenacao")
        or payload.get("predictions_summary")
        or payload.get("predictions")
        or []
    )
    if isinstance(preds, list) and preds:
        first = preds[0] if isinstance(preds[0], dict) else {}
        conf = (
            first.get("confiabilidade")
            or first.get("nivel_confianca")
            or first.get("grau_confianca")
            or first.get("confidence")
        )
        if conf:
            sample = first.get("amostra_julgada") or first.get("n_julgados")
            detail = f"{conf} ({sample} julgados)" if sample else str(conf)
            return str(conf), detail

    julgados = (
        int(outcomes.get("condenacoes") or 0)
        + int(outcomes.get("acordos") or 0)
        + int(outcomes.get("improcedentes") or 0)
    )
    if julgados >= 30:
        return "Alta", f"Alta ({julgados} julgados)"
    if julgados >= 10:
        return "Moderada", f"Moderada ({julgados} julgados)"
    if julgados >= 1:
        return "Baixa / Inicial", f"Baixa ({julgados} julgados)"
    return "Inicial (Sem Julgados)", "Sem sentenças julgadas mapeadas"


def normalize_report_payload(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza qualquer payload (do banco CompanySearch, do SEARCH_CACHE, do orchestrator
    ou enviado via POST pelo frontend React) para o formato unificado de relatório PDF v2.0.
    """
    data = raw_data if isinstance(raw_data, dict) else {}

    company_name = (
        data.get("empresa")
        or data.get("company_name")
        or data.get("name")
        or "Empresa Consultada"
    )

    econ = (
        data.get("idoneidade_economica")
        or data.get("idoneidadeEconomica")
        or data.get("economic_standing")
        or {}
    )
    if not isinstance(econ, dict):
        econ = {}

    ai = (
        data.get("ai_analysis")
        or data.get("aiAnalysis")
        or data.get("parecer_agente_adk")
        or {}
    )
    if not isinstance(ai, dict):
        ai = {}

    outcomes = (
        data.get("desfechos_sentencas")
        or data.get("outcomes_summary")
        or data.get("outcomes")
        or {}
    )
    if not isinstance(outcomes, dict):
        outcomes = {}

    strategy = data.get("search_strategy") or {}
    if not isinstance(strategy, dict):
        strategy = {}

    # CNPJ Matriz
    raw_cnpj = (
        econ.get("cnpj_utilizado")
        or econ.get("cnpj_triangulado")
        or (econ.get("cnpjs_consolidados")[0] if isinstance(econ.get("cnpjs_consolidados"), list) and econ.get("cnpjs_consolidados") else None)
        or strategy.get("target_cnpj")
        or data.get("cnpj")
    )
    cnpj_formatted = _format_cnpj(raw_cnpj)

    # Eventos e Score de Exposição
    total_processes = int(
        data.get("total_processos")
        if data.get("total_processos") is not None
        else (data.get("total_processes") if data.get("total_processes") is not None else (data.get("total") or 0))
    )

    exposure_score = (
        data.get("indice_exposicao_financeira")
        if data.get("indice_exposicao_financeira") is not None
        else (
            econ.get("indice_exposicao_financeira")
            if econ.get("indice_exposicao_financeira") is not None
            else (
                data.get("riskScore")
                if data.get("riskScore") is not None
                else (
                    data.get("score_risco_terceirizacao")
                    if data.get("score_risco_terceirizacao") is not None
                    else (data.get("overall_risk_score") if data.get("overall_risk_score") is not None else 0.0)
                )
            )
        )
    )

    prob_condenacao = (
        data.get("probabilidade_condenacao_geral_pct")
        if data.get("probabilidade_condenacao_geral_pct") is not None
        else econ.get("probabilidade_condenacao_bruta")
    )

    conf_label, conf_detail = _extract_bayesian_confidence(data, outcomes)

    # Idoneidade Econômica
    capital_display = econ.get("capital_social_display") or data.get("capital_social_display") or {}
    if not isinstance(capital_display, dict):
        capital_display = {}

    capital_fmt = (
        econ.get("capital_social_formatado")
        or econ.get("valor_formatado")
        or capital_display.get("valor_formatado")
        or data.get("capital_social_consolidado")
        or _format_brl(econ.get("capital_social_num"))
    )
    badge_porte = (
        econ.get("badge_compatibilidade_porte")
        or econ.get("observacao_porte")
        or capital_display.get("observacao_porte")
        or data.get("observacao_porte")
        or "Compatível com o porte"
    )
    subtitulo_unidades = (
        econ.get("subtitulo_unidades")
        or econ.get("detalhe_filiais")
        or "Capital Social registrado perante a Receita Federal do Brasil."
    )
    situacao_rfb = _safe_str(econ.get("situacao_cadastral"), "ATIVA").upper()
    prob_insolv = econ.get("probabilidade_insolvencia")
    if prob_insolv is None:
        prob_insolv = econ.get("probabilidade_insolvencia_pct")
    risco_sub = econ.get("risco_calote_subsidiario")
    if risco_sub is None:
        risco_sub = econ.get("risco_calote_subsidiario_pct")

    cenario_tipo = _safe_str(econ.get("cenario_tipo") or econ.get("cenario"), "CENARIO_A").upper()
    is_cenario_a = (cenario_tipo == "CENARIO_A") or (econ.get("cenario_class") == "seguro")
    cenario_badge = _safe_str(
        econ.get("cenario_badge"),
        "Cenário A · Solvência Comprovada" if is_cenario_a else "Cenário B · Risco Crítico",
    )
    cenario_titulo = _safe_str(
        econ.get("cenario_titulo"),
        "Cenário A: Idoneidade Econômica Robusta • Risco de Calote Subsidiário Mitigado"
        if is_cenario_a
        else "Cenário B: Risco Crítico de Calote Subsidiário (Alerta Máximo Súmula 331 TST)",
    )
    cenario_descricao = _safe_str(
        econ.get("cenario_descricao") or econ.get("parecer_sumula_331"),
        "Auditoria de solvência baseada nos dados oficiais da Receita Federal, CGU, PGFN e CNJ.",
    )

    sancoes_cgu = bool(econ.get("possui_sancoes_cgu"))
    divida_pgfn = bool(econ.get("possui_divida_ativa"))
    sancoes_texto = (
        "Restrições apontadas em bases federais (CGU/PGFN)"
        if (sancoes_cgu or divida_pgfn)
        else "Nada Consta (CEIS/CNEP/PGFN)"
    )

    # Rankings
    raw_cond_ranking = (
        data.get("ranking_motivos_condenacao")
        or data.get("rankingCondenacoes")
        or []
    )
    raw_motivos_ranking = (
        data.get("ranking_motivos")
        or data.get("reasons")
        or []
    )

    def _normalize_ranking(items: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not isinstance(items, list):
            return normalized
        for it in items[:10]:
            if isinstance(it, dict):
                nome = _safe_str(
                    it.get("motivo") or it.get("assunto") or it.get("nome") or it.get("subject"),
                    "Motivo não especificado",
                )
                qtd = int(
                    it.get("quantidade_condenacoes")
                    or it.get("quantidade")
                    or it.get("total")
                    or it.get("count")
                    or 0
                )
                pct = it.get("percentual") or it.get("pct") or it.get("taxa_condenacao_pct")
                normalized.append({"motivo": nome, "quantidade": qtd, "percentual": pct})
            elif isinstance(it, str):
                normalized.append({"motivo": it, "quantidade": 0, "percentual": None})
        return normalized

    ranking_condenacoes = _normalize_ranking(raw_cond_ranking)
    ranking_motivos = _normalize_ranking(raw_motivos_ranking)

    # Parecer Executivo de Compliance (Súmula 331 TST)
    recomendacao = _safe_str(
        ai.get("recomendacao_homologacao") or ai.get("recomendacao") or ai.get("recomendacao_compliance"),
        "HOMOLOGAR COM MONITORAMENTO" if is_cenario_a else "REPROVAR / BLOQUEAR",
    )
    perfil_risco = _safe_str(
        ai.get("perfil_risco_consolidado") or ai.get("nivel_risco"),
        "Baixo / Solvente" if is_cenario_a else "Crítico / Elevado",
    )
    resumo_executivo = _safe_str(
        ai.get("resumo_executivo") or ai.get("parecer_governanca") or ai.get("analise_executiva"),
        cenario_descricao,
    )
    alertas_331 = ai.get("alertas_sumula_331") or []
    if not isinstance(alertas_331, list):
        alertas_331 = [str(alertas_331)]
    recomendacoes_comp = ai.get("recomendacoes_compliance") or ai.get("recomendacoes_praticas") or []
    if not isinstance(recomendacoes_comp, list):
        recomendacoes_comp = [str(recomendacoes_comp)]

    scan_status = _safe_str(data.get("scan_status") or data.get("scanStatus"), "COMPLETED")

    return {
        "company_name": _safe_str(company_name, "Empresa Consultada"),
        "cnpj_formatted": cnpj_formatted,
        "scan_status": scan_status,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_processes": total_processes,
        "exposure_score": exposure_score,
        "prob_condenacao": prob_condenacao,
        "conf_label": conf_label,
        "conf_detail": conf_detail,
        "outcomes": {
            "condenacoes": int(outcomes.get("condenacoes") or 0),
            "acordos": int(outcomes.get("acordos") or 0),
            "improcedentes": int(outcomes.get("improcedentes") or 0),
            "em_aberto": int(outcomes.get("em_aberto") or 0),
        },
        "economic": {
            "is_cenario_a": is_cenario_a,
            "cenario_tipo": cenario_tipo,
            "cenario_badge": cenario_badge,
            "cenario_titulo": cenario_titulo,
            "cenario_descricao": cenario_descricao,
            "capital_fmt": capital_fmt,
            "badge_porte": badge_porte,
            "subtitulo_unidades": subtitulo_unidades,
            "situacao_rfb": situacao_rfb,
            "prob_insolv": prob_insolv,
            "risco_sub": risco_sub,
            "sancoes_texto": sancoes_texto,
        },
        "ranking_condenacoes": ranking_condenacoes,
        "ranking_motivos": ranking_motivos,
        "compliance": {
            "recomendacao": recomendacao,
            "perfil_risco": perfil_risco,
            "resumo_executivo": resumo_executivo,
            "alertas_331": [str(a) for a in alertas_331 if a][:5],
            "recomendacoes": [str(r) for r in recomendacoes_comp if r][:5],
        },
    }


def _generate_pdf_reportlab(report: Dict[str, Any]) -> bytes:
    """Gera o relatório executivo estilizado em PDF usando ReportLab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"RadarJus - {report['company_name']}",
        author="RadarJus v2.0 - Inteligência Preditiva RiskTech",
    )

    navy = colors.HexColor("#17283a")
    blue = colors.HexColor("#245a81")
    slate = colors.HexColor("#526477")
    border_col = colors.HexColor("#d6e0ea")
    bg_soft = colors.HexColor("#f4f7fa")
    green = colors.HexColor("#25865a")
    green_bg = colors.HexColor("#eaf7f0")
    red = colors.HexColor("#c34a4a")
    red_bg = colors.HexColor("#fdf0f0")
    amber = colors.HexColor("#b67b23")

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "RJTitle", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=15, leading=19, textColor=colors.white, spaceAfter=2,
    )
    style_sub = ParagraphStyle(
        "RJSub", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.5, leading=12, textColor=colors.HexColor("#d7e4f0"),
    )
    style_h2 = ParagraphStyle(
        "RJH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=11, leading=14, textColor=navy, spaceBefore=10, spaceAfter=5,
    )
    style_body = ParagraphStyle(
        "RJBody", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9, leading=13, textColor=colors.HexColor("#243447"),
    )
    style_small = ParagraphStyle(
        "RJSmall", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8, leading=11, textColor=slate,
    )
    style_kpi_label = ParagraphStyle(
        "RJKpiLabel", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=7.2, leading=9.5, textColor=slate,
    )
    style_kpi_val = ParagraphStyle(
        "RJKpiVal", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=13, leading=16, textColor=navy,
    )

    story: List[Any] = []
    page_w = A4[0] - 32 * mm
    econ = report["economic"]
    comp = report["compliance"]
    out = report["outcomes"]

    # 1. Cabeçalho Corporativo
    status_label = "Varredura Completa" if report["scan_status"] == "COMPLETED" else "Varredura em Andamento"
    header_table = Table(
        [[
            Paragraph(
                f"RADARJUS v2.0 · INTELIGÊNCIA PREDITIVA &amp; COMPLIANCE<br/>"
                f"<font size='14'><b>{_xml_escape(report['company_name'])}</b></font>",
                style_title,
            ),
            Paragraph(
                f"<b>CNPJ Matriz:</b> {_xml_escape(report['cnpj_formatted'])}<br/>"
                f"<b>Emissão:</b> {_xml_escape(report['generated_at'])}<br/>"
                f"<b>Status:</b> {_xml_escape(status_label)}",
                style_sub,
            ),
        ]],
        colWidths=[page_w * 0.63, page_w * 0.37],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), navy),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # 2. KPIs Principais (Exposição Financeira, Eventos, Confiança Bayesiana, Desfechos)
    exp_num = float(report["exposure_score"] or 0.0)
    exp_color = "#c34a4a" if exp_num >= 60 else ("#b67b23" if exp_num >= 35 else "#25865a")
    kpi_table = Table(
        [[
            Paragraph(
                f"ÍNDICE DE EXPOSIÇÃO FINANCEIRA<br/>"
                f"<font size='14' color='{exp_color}'><b>{_format_pct(report['exposure_score'])}</b></font><br/>"
                f"<font size='7.5' color='#526477'>Score geral de impacto subsidiário</font>",
                style_kpi_label,
            ),
            Paragraph(
                f"EVENTOS IDENTIFICADOS<br/>"
                f"<font size='14' color='#17283a'><b>{report['total_processes']}</b></font><br/>"
                f"<font size='7.5' color='#526477'>Registros em bases oficiais CNJ</font>",
                style_kpi_label,
            ),
            Paragraph(
                f"CONFIANÇA BAYESIANA<br/>"
                f"<font size='11' color='#245a81'><b>{_xml_escape(report['conf_label'])}</b></font><br/>"
                f"<font size='7.5' color='#526477'>{_xml_escape(report['conf_detail'])}</font>",
                style_kpi_label,
            ),
            Paragraph(
                f"DESFECHOS MAPEADOS<br/>"
                f"<font size='9' color='#17283a'><b>Cond: {out['condenacoes']} · Acord: {out['acordos']}</b></font><br/>"
                f"<font size='7.5' color='#526477'>Improc: {out['improcedentes']} · Abertos: {out['em_aberto']}</font>",
                style_kpi_label,
            ),
        ]],
        colWidths=[page_w / 4.0] * 4,
    )
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_soft),
        ("BOX", (0, 0), (-1, -1), 0.6, border_col),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, border_col),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 8))

    # 3. Idoneidade Econômica & Risco de Exposição Subsidiária (Súmula 331 TST)
    story.append(Paragraph("Idoneidade Econômica &amp; Risco de Exposição Subsidiária (Súmula 331 TST)", style_h2))

    banner_bg = green_bg if econ["is_cenario_a"] else red_bg
    banner_border = green if econ["is_cenario_a"] else red
    banner_text_hex = "#1b6945" if econ["is_cenario_a"] else "#a33333"

    scenario_box = Table(
        [[
            Paragraph(
                f"<font color='{banner_text_hex}'><b>{_xml_escape(econ['cenario_badge'])} — {_xml_escape(econ['cenario_titulo'])}</b></font><br/>"
                f"<font size='8.5' color='#243447'>{_xml_escape(econ['cenario_descricao'])}</font>",
                style_body,
            )
        ]],
        colWidths=[page_w],
    )
    scenario_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), banner_bg),
        ("BOX", (0, 0), (-1, -1), 0.9, banner_border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(scenario_box)
    story.append(Spacer(1, 6))

    econ_grid = Table(
        [
            [
                Paragraph(
                    f"<font size='7.5' color='#245a81'><b>CAPITAL SOCIAL CONSOLIDADO</b></font><br/>"
                    f"<font size='11' color='#17283a'><b>{_xml_escape(econ['capital_fmt'])}</b></font><br/>"
                    f"<font size='7.5' color='#526477'>{_xml_escape(econ['badge_porte'])}</font><br/>"
                    f"<font size='7' color='#68798a'>{_xml_escape(econ['subtitulo_unidades'])}</font>",
                    style_body,
                ),
                Paragraph(
                    f"<font size='7.5' color='#245a81'><b>SITUAÇÃO CADASTRAL RFB</b></font><br/>"
                    f"<font size='11' color='{'#25865a' if econ['situacao_rfb'] == 'ATIVA' else '#c34a4a'}'><b>{_xml_escape(econ['situacao_rfb'])}</b></font><br/>"
                    f"<font size='7.5' color='#526477'>Sanções/Débitos: {_xml_escape(econ['sancoes_texto'])}</font>",
                    style_body,
                ),
            ],
            [
                Paragraph(
                    f"<font size='7.5' color='#245a81'><b>PROBABILIDADE DE INSOLVÊNCIA</b></font><br/>"
                    f"<font size='11' color='#17283a'><b>{_format_pct(econ['prob_insolv'])}</b></font><br/>"
                    f"<font size='7.5' color='#526477'>Modelo atuarial de solvência patrimonial</font>",
                    style_body,
                ),
                Paragraph(
                    f"<font size='7.5' color='#245a81'><b>RISCO DE EXPOSIÇÃO SUBSIDIÁRIA</b></font><br/>"
                    f"<font size='11' color='#17283a'><b>{_format_pct(econ['risco_sub'])}</b></font><br/>"
                    f"<font size='7.5' color='#526477'>Probabilidade combinada P(Condenação) × P(Insolvência)</font>",
                    style_body,
                ),
            ],
        ],
        colWidths=[page_w * 0.54, page_w * 0.46],
    )
    econ_grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, border_col),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, border_col),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(econ_grid)
    story.append(Spacer(1, 8))

    # 4. Ranking dos Motivos de Condenação / Vetores de Risco
    ranking_items = report["ranking_condenacoes"] or report["ranking_motivos"]
    ranking_title = (
        "Ranking dos Motivos de Condenação"
        if report["ranking_condenacoes"]
        else "Principais Vetores de Risco Processual"
    )
    story.append(Paragraph(ranking_title, style_h2))

    if ranking_items:
        rows = [[
            Paragraph("<b>#</b>", style_small),
            Paragraph("<b>Motivo / Vetor Identificado</b>", style_small),
            Paragraph("<b>Ocorrências</b>", style_small),
            Paragraph("<b>Participação</b>", style_small),
        ]]
        for idx, item in enumerate(ranking_items[:8], start=1):
            pct_str = _format_pct(item["percentual"], "—") if item.get("percentual") is not None else "—"
            rows.append([
                Paragraph(str(idx), style_small),
                Paragraph(_xml_escape(item["motivo"]), style_body),
                Paragraph(str(item["quantidade"]), style_body),
                Paragraph(pct_str, style_body),
            ])
        rank_table = Table(rows, colWidths=[page_w * 0.07, page_w * 0.65, page_w * 0.14, page_w * 0.14])
        rank_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), bg_soft),
            ("BOX", (0, 0), (-1, -1), 0.6, border_col),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, border_col),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(rank_table)
    else:
        story.append(Paragraph("Nenhuma condenação ou vetor de risco recorrente mapeado para esta empresa.", style_small))

    story.append(Spacer(1, 8))

    # 5. Parecer Executivo de Compliance (Súmula 331 TST)
    story.append(Paragraph("Parecer Executivo &amp; Compliance (Súmula 331 TST)", style_h2))
    rec_upper = comp["recomendacao"].upper()
    rec_color = "#c34a4a" if ("REPROVAR" in rec_upper or "BLOQUEAR" in rec_upper) else (
        "#25865a" if "SEM RESTRI" in rec_upper or "APROVAR" in rec_upper else "#245a81"
    )
    comp_header = Table(
        [[
            Paragraph(
                f"<b>Recomendação Súmula 331 TST:</b> <font color='{rec_color}'><b>{_xml_escape(comp['recomendacao'])}</b></font>",
                style_body,
            ),
            Paragraph(
                f"<b>Perfil de Risco:</b> {_xml_escape(comp['perfil_risco'])}",
                style_body,
            ),
        ]],
        colWidths=[page_w * 0.62, page_w * 0.38],
    )
    comp_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_soft),
        ("BOX", (0, 0), (-1, -1), 0.6, border_col),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(comp_header)
    story.append(Spacer(1, 5))
    story.append(Paragraph(_xml_escape(comp["resumo_executivo"]), style_body))

    if comp["recomendacoes"]:
        story.append(Spacer(1, 5))
        story.append(Paragraph("<b>Diretrizes de Governança &amp; Mitigação:</b>", style_small))
        for rec_item in comp["recomendacoes"]:
            story.append(Paragraph(f"• {_xml_escape(rec_item)}", style_small))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Fontes Oficiais Consultadas: Comunica PJe (CNJ), DataJud (CNJ), Receita Federal do Brasil, CGU (CEIS/CNEP) e PGFN. "
        "Relatório gerado automaticamente pelo RadarJus v2.0 (Motor Bayesiano Empírico + Auditoria de Solvência).",
        style_small,
    ))

    doc.build(story)
    return buf.getvalue()


def _generate_pdf_pure_python(report: Dict[str, Any]) -> bytes:
    """
    Gerador PDF 1.4 puro em Python (zero dependências externas).
    Garante que o endpoint sempre retorne um PDF válido mesmo sem ReportLab instalado.
    """
    econ = report["economic"]
    comp = report["compliance"]
    out = report["outcomes"]

    lines: List[str] = [
        "RADARJUS v2.0 - RELATORIO EXECUTIVO DE RISCO & SOLVENCIA (SUMULA 331 TST)",
        "=" * 78,
        f"Empresa / Grupo: {report['company_name']}",
        f"CNPJ Matriz: {report['cnpj_formatted']} | Emissao: {report['generated_at']}",
        "-" * 78,
        "1. INDICADORES PREDITIVOS & EXPOSICAO",
        f"   Indice de Exposicao Financeira: {_format_pct(report['exposure_score'])}",
        f"   Eventos Identificados: {report['total_processes']}",
        f"   Grau de Confianca Bayesiano: {report['conf_detail']}",
        f"   Desfechos: Condenacoes={out['condenacoes']} | Acordos={out['acordos']} | Improcedentes={out['improcedentes']} | Em Aberto={out['em_aberto']}",
        "-" * 78,
        "2. IDONEIDADE ECONOMICA & EXPOSICAO SUBSIDIARIA",
        f"   Cenario: {econ['cenario_badge']} - {econ['cenario_titulo']}",
        f"   Capital Social Consolidado: {econ['capital_fmt']} ({econ['badge_porte']})",
        f"   Unidades: {econ['subtitulo_unidades']}",
        f"   Situacao Cadastral RFB: {econ['situacao_rfb']} | Sancoes/Debitos: {econ['sancoes_texto']}",
        f"   Probabilidade de Insolvencia: {_format_pct(econ['prob_insolv'])} | Risco Subsidiario: {_format_pct(econ['risco_sub'])}",
        "-" * 78,
        "3. RANKING DOS MOTIVOS DE CONDENACAO / VETORES DE RISCO",
    ]

    ranking_items = report["ranking_condenacoes"] or report["ranking_motivos"]
    if ranking_items:
        for idx, item in enumerate(ranking_items[:8], start=1):
            lines.append(f"   {idx}. {item['motivo']} ({item['quantidade']} ocorrencias)")
    else:
        lines.append("   Nenhuma condenacao ou vetor recorrente mapeado.")

    lines.extend([
        "-" * 78,
        "4. PARECER EXECUTIVO DE COMPLIANCE (SUMULA 331 TST)",
        f"   Recomendacao: {comp['recomendacao']} | Perfil de Risco: {comp['perfil_risco']}",
    ])

    # Quebra linhas longas para caber confortavelmente em A4
    wrapped_lines: List[str] = []
    for raw_line in lines + [f"   {comp['resumo_executivo']}"]:
        clean = _ascii_latin1(raw_line)
        while len(clean) > 90:
            cut = clean.rfind(" ", 0, 90)
            if cut <= 20:
                cut = 90
            wrapped_lines.append(clean[:cut])
            clean = "   " + clean[cut:].lstrip()
        wrapped_lines.append(clean)

    def _pdf_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_ops = ["BT", "/F1 9 Tf", "40 800 Td", "13 TL"]
    for i, ln in enumerate(wrapped_lines[:56]):
        if i == 0:
            content_ops.append(f"({_pdf_escape(ln)}) Tj")
        else:
            content_ops.append(f"T* ({_pdf_escape(ln)}) Tj")
    content_ops.append("ET")
    stream_bytes = "\n".join(content_ops).encode("latin-1", "ignore")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >> endobj\n",
        f"5 0 obj << /Length {len(stream_bytes)} >> stream\n".encode("ascii") + stream_bytes + b"\nendstream endobj\n",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    trailer = (
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("ascii")
    pdf.extend(trailer)
    return bytes(pdf)


def generate_company_report_pdf(raw_data: Dict[str, Any]) -> bytes:
    """
    Gera o PDF executivo v2.0 (RiskTech + Solvência) para uma empresa.
    Utiliza ReportLab quando disponível e cai automaticamente para o gerador PDF 1.4 puro.
    """
    report = normalize_report_payload(raw_data)
    try:
        return _generate_pdf_reportlab(report)
    except Exception as exc:
        logger.warning("Fallback para gerador PDF puro (ReportLab indisponível/erro): %s", exc)
        return _generate_pdf_pure_python(report)

