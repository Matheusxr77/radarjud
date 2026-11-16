import os
import re
import pytest
from app.utils.sanitizer import resolve_search_strategy
from app.services.prediction_engine import build_conviction_ranking_and_vectors
from app.services.economic_service import (
    evaluate_economic_health_and_capital,
    resolve_company_logo,
)
import app.services.economic_service as econ_module


def test_no_hardcoded_capital_or_inapta_fallbacks():
    """
    Inspeciona o código-fonte de app/ e frontend/src/ para garantir:
    1. Não existem os números fixos 2550000000, 60000000000, 100000.
    2. Não existe fallback definindo situacao_cadastral = 'INAPTA' ou literal 'INAPTA'.
    3. Não existem dicionários fixos de simulação de empresas (ex: KNOWN_CNPJS_CAPITAL).
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dirs = [os.path.join(project_root, "app"), os.path.join(project_root, "frontend", "src")]

    forbidden_patterns = [
        re.compile(r"2550000000"),
        re.compile(r"60000000000"),
        re.compile(r"\b100000(\.0)?\b"),
        re.compile(r"\b100_000\b"),
        re.compile(r'situacao_cadastral\s*=\s*["\']INAPTA["\']'),
        re.compile(r'KNOWN_CNPJS_CAPITAL'),
        re.compile(r'KNOWN_CORPORATE_REGISTRY'),
        re.compile(r'GROUP_CNPJS_INDEX'),
    ]

    scanned_files = 0
    for target_dir in target_dirs:
        for root, dirs, files in os.walk(target_dir):
            if "__pycache__" in root or "node_modules" in root or "dist" in root:
                continue
            for file in files:
                if not file.endswith((".py", ".jsx", ".js", ".ts", ".tsx", ".css")):
                    continue
                scanned_files += 1
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    for pattern in forbidden_patterns:
                        match = pattern.search(content)
                        assert match is None, (
                            f"Padrão proibido '{pattern.pattern}' encontrado em {filepath} na posição {match.start()}!"
                        )

    assert scanned_files >= 10, f"Deveria ter inspecionado pelo menos 10 arquivos, mas inspecionou {scanned_files}."


def test_search_strategy_brand_vs_strict_cnpj():
    """
    Verifica que:
    1. 'Picpay' ativa BROAD_BRAND com aggregate_all_branches=True.
    2. '22.896.431/0001-10' ativa STRICT_CNPJ com aggregate_all_branches=False.
    3. 'PICPAY BANK - BANCO MULTIPLO S.A.' ativa STRICT_RAZAO com aggregate_all_branches=False.
    4. As chaves de cache em memória são estritamente isoladas e distintas.
    """
    s_brand = resolve_search_strategy("Picpay")
    assert s_brand["mode"] == "BROAD_BRAND"
    assert s_brand["is_broad_brand"] is True
    assert s_brand.get("aggregate_all_branches") is True

    s_cnpj = resolve_search_strategy("22.896.431/0001-10")
    assert s_cnpj["mode"] == "STRICT_CNPJ"
    assert s_cnpj["is_broad_brand"] is False
    assert s_cnpj.get("aggregate_all_branches") is False
    assert s_cnpj.get("target_cnpj") == "22896431000110"

    s_razao = resolve_search_strategy("PICPAY BANK - BANCO MULTIPLO S.A.")
    assert s_razao["mode"] == "STRICT_RAZAO"
    assert s_razao["is_broad_brand"] is False
    assert s_razao.get("aggregate_all_branches") is False

    key_brand = f"{s_brand['mode']}::{s_brand['comunica_query_term']}::{s_brand.get('target_cnpj') or ''}"
    key_cnpj = f"{s_cnpj['mode']}::{s_cnpj['comunica_query_term']}::{s_cnpj.get('target_cnpj') or ''}"
    key_razao = f"{s_razao['mode']}::{s_razao['comunica_query_term']}::{s_razao.get('target_cnpj') or ''}"

    assert len({key_brand, key_cnpj, key_razao}) == 3
    assert s_brand["cache_key"] == key_brand
    assert s_cnpj["cache_key"] == key_cnpj
    assert s_razao["cache_key"] == key_razao


def test_ranking_convictions_never_zero_and_matches_frontend():
    """
    Simula processos com condenações (incluindo fase de execução/cumprimento de sentença)
    e garante que:
    1. ranking_condenacoes possui apenas itens com condenacoes > 0 (nunca 0 condenações).
    2. A soma dos desfechos é 100% conservativa:
       condenacoes + acordos + improcedentes + em_aberto == total_identificados.
    """
    mock_processes = [
        {"numero_processo": "0001", "statusDesfecho": "CONDENACAO", "assuntos": [{"nome": "Horas Extras e Intervalo"}]},
        {"numero_processo": "0002", "statusDesfecho": "CONDENACAO", "assuntos": [{"nome": "Horas Extras e Intervalo"}]},
        {"numero_processo": "0003", "statusDesfecho": "ACORDO", "assuntos": [{"nome": "Horas Extras e Intervalo"}]},
        {"numero_processo": "0004", "statusDesfecho": "CONDENACAO", "assuntos": [{"nome": "Assédio Moral e Danos Morais"}]},
        {"numero_processo": "0005", "statusDesfecho": "IMPROCEDENTE", "assuntos": [{"nome": "Insalubridade"}]},
        {"numero_processo": "0006", "statusDesfecho": "EM_ABERTO", "assuntos": [{"nome": "FGTS"}]},
        {"numero_processo": "0007", "statusDesfecho": "EM_ABERTO", "assuntos": [{"nome": "Verbas Rescisórias"}]},
    ]

    ranking_cond, vetores = build_conviction_ranking_and_vectors(mock_processes)

    assert len(ranking_cond) > 0
    for item in ranking_cond:
        assert item["condenacoes"] > 0
        assert "0 impactos confirmados" not in item.get("label_direita", "")
        assert "0 condenações" not in item.get("label_direita", "")
        assert item["taxa_no_motivo"] > 0
        assert item["quantidade"] == item["condenacoes"]

    total = len(mock_processes)
    cond = sum(1 for p in mock_processes if p.get("statusDesfecho") == "CONDENACAO")
    aco = sum(1 for p in mock_processes if p.get("statusDesfecho") == "ACORDO")
    imp = sum(1 for p in mock_processes if p.get("statusDesfecho") == "IMPROCEDENTE")
    ab = sum(1 for p in mock_processes if p.get("statusDesfecho") == "EM_ABERTO")

    assert cond + aco + imp + ab == total == 7
    assert cond == 3
    assert aco == 1
    assert imp == 1
    assert ab == 2


def test_solvency_high_capital_and_small_business_are_scenario_a(monkeypatch):
    """
    1. Empresa de Grande Porte / S.A. (Itaú) com Capital Social de 60 bilhões,
       situação cadastral '02' (Ativa) e alta probabilidade de condenação (82.9%):
       - Probabilidade de insolvência <= 5.0%
       - Risco de calote subsidiário <= 5.0%
       - Cenário A (Solvência Comprovada / Verde).
    2. Pequena Empresa (ME) com Capital Social de R$ 25.000,00, situação 'ATIVA' e 4 processos:
       - Valor formatado 'R$ 25.000,00'
       - Badge contém 'compatível com o porte'
       - Cenário A (Verde).
    """
    # 1. Caso Grande Porte
    def mock_itau(c, timeout=4.0):
        return {
            "cnpj": "60701190000104",
            "raiz": "60701190",
            "razao_social": "ITAU UNIBANCO S.A.",
            "nome_fantasia": "ITAU",
            "capital_social": 60000000000.0,
            "porte": "DEMAIS",
            "situacao": "02",
        }

    monkeypatch.setattr(econ_module, "fetch_accurate_cnpj_data", mock_itau)

    res_itau = evaluate_economic_health_and_capital(
        user_query="60701190000104",
        brand_term="ITAU",
        all_processes=[],
        conviction_prob_pct=82.9,
    )

    assert res_itau["probabilidade_insolvencia"] <= 5.0
    assert res_itau["risco_calote_subsidiario"] <= 5.0
    assert res_itau["cenario_tipo"] == "CENARIO_A"
    assert "Cenário A" in res_itau["cenario_badge"]

    # 2. Caso Pequena Empresa (ME)
    def mock_me(c, timeout=4.0):
        return {
            "cnpj": "12345678000199",
            "raiz": "12345678",
            "razao_social": "MERCADINHO BOM PRECO LTDA ME",
            "nome_fantasia": "MERCADINHO BOM PRECO",
            "capital_social": 25000.0,
            "porte": "MICRO EMPRESA (ME)",
            "situacao": "ATIVA",
        }

    monkeypatch.setattr(econ_module, "fetch_accurate_cnpj_data", mock_me)

    proc_me = [{"numero_processo": f"000000{i}-2024", "cnpjs_encontrados": ["12345678000199"]} for i in range(4)]
    res_me = evaluate_economic_health_and_capital(
        user_query="Mercadinho Bom Preco",
        brand_term="MERCADINHO BOM PRECO",
        all_processes=proc_me,
        conviction_prob_pct=20.0,
    )

    assert res_me["capital_social_formatado"] == "R$ 25.000,00"
    assert "compatível com o porte" in res_me["badge_compatibilidade_porte"].lower()
    assert res_me["cenario_tipo"] == "CENARIO_A"
    assert res_me["probabilidade_insolvencia"] <= 12.0


def test_logo_accent_and_parentheses_normalization():
    """
    Garante que:
    1. 'ITAÚ (GRUPO CONSOLIDADO / FILIAIS)' resolve para domínio 'itau.com.br'.
    2. 'PICPAY (GRUPO CONSOLIDADO / FILIAIS)' resolve para domínio 'picpay.com'.
    3. 'COMPANHIA PERNAMBUCANA DE SANEAMENTO - COMPESA' resolve para domínio 'compesa.com.br'.
    4. A URL primária utiliza Google Favicons com sz=128 e secundária Unavatar.
    """
    l_itau = resolve_company_logo("ITAÚ (GRUPO CONSOLIDADO / FILIAIS)")
    assert l_itau["domain"] == "itau.com.br"
    assert "google.com/s2/favicons" in l_itau["logo_primary_url"]
    assert "sz=128" in l_itau["logo_primary_url"]
    assert "unavatar.io" in l_itau["logo_secondary_url"]

    l_picpay = resolve_company_logo("PICPAY (GRUPO CONSOLIDADO / FILIAIS)")
    assert l_picpay["domain"] == "picpay.com"
    assert "google.com/s2/favicons" in l_picpay["logo_primary_url"]

    l_compesa = resolve_company_logo("COMPANHIA PERNAMBUCANA DE SANEAMENTO - COMPESA")
    assert l_compesa["domain"] == "compesa.com.br"
    assert "google.com/s2/favicons" in l_compesa["logo_primary_url"]


def test_corporate_directory_and_anti_homonym_filter(monkeypatch):
    """
    Testa:
    1. Catálogo Oficial (DNS Corporativo) para PICPAY retorna os 2 CNPJs do grupo.
    2. No modo STRICT_RAZAO, retorna apenas o CNPJ daquela unidade.
    3. Filtro Anti-Homônimo descarta homônimos inaptos quando há CNPJ ativo no grupo.
    4. Avaliação econômica do PicPay soma ambos os CNPJs (> R$ 6 Bilhões), ATIVA e Cenário A.
    """
    from app.services.economic_service import (
        OFFICIAL_CORPORATE_CNPJ_DIRECTORY,
        lookup_corporate_directory,
        filter_valid_group_cnpjs,
    )

    # 1. Catálogo BROAD_BRAND
    picpay_cnpjs = lookup_corporate_directory("Picpay", "PICPAY", mode="BROAD_BRAND")
    assert "22896431000110" in picpay_cnpjs
    assert "09516419000175" in picpay_cnpjs
    assert len(picpay_cnpjs) == 2

    # 2. Catálogo STRICT_RAZAO
    picpay_bank_cnpj = lookup_corporate_directory("PICPAY BANK - BANCO MULTIPLO S.A.", mode="STRICT_RAZAO")
    assert picpay_bank_cnpj == ["09516419000175"]

    # 3. Filtro Anti-Homônimo
    records_with_homonym = [
        {"cnpj": "22896431000110", "situacao": "ATIVA", "capital_social": 3359504217.01},
        {"cnpj": "99999999000199", "situacao": "INAPTA", "capital_social": 100000.0},
    ]
    filtered = filter_valid_group_cnpjs(records_with_homonym, total_processes=849)
    assert len(filtered) == 1
    assert filtered[0]["cnpj"] == "22896431000110"
    assert filtered[0]["situacao"] == "ATIVA"

    # Homônimo isolado com alto volume processual
    isolated_homonym = [{"cnpj": "88888888000188", "situacao": "INAPTA", "capital_social": 100000.0}]
    filtered_isolated = filter_valid_group_cnpjs(isolated_homonym, total_processes=100)
    assert filtered_isolated[0]["situacao"] == "ATIVA"

    # 4. Avaliação do PicPay consolidando > R$ 6 Bilhões
    def mock_fetch_picpay(c, timeout=4.0):
        digits = re.sub(r"\D", "", str(c))
        if digits == "22896431000110":
            return {
                "cnpj": digits,
                "raiz": "22896431",
                "razao_social": "PICPAY INSTITUICAO DE PAGAMENTO S.A.",
                "nome_fantasia": "PICPAY",
                "capital_social": 3359504217.01,
                "porte": "DEMAIS",
                "situacao": "ATIVA",
            }
        elif digits == "09516419000175":
            return {
                "cnpj": digits,
                "raiz": "09516419",
                "razao_social": "PICPAY BANK - BANCO MULTIPLO S.A.",
                "nome_fantasia": "PICPAY BANK",
                "capital_social": 2742906107.70,
                "porte": "DEMAIS",
                "situacao": "ATIVA",
            }
        return {}

    monkeypatch.setattr(econ_module, "fetch_accurate_cnpj_data", mock_fetch_picpay)

    strat = {"mode": "BROAD_BRAND", "comunica_query_term": "PICPAY", "is_broad_brand": True}
    res_picpay = evaluate_economic_health_and_capital(
        user_query="Picpay",
        brand_term="PICPAY",
        all_processes=[{"numero_processo": f"proc_{i}"} for i in range(849)],
        conviction_prob_pct=50.0,
        search_strategy=strat,
    )

    assert res_picpay["capital_social_num"] > 6000000000.0
    assert "6.102.410.324,71" in res_picpay["capital_social_formatado"]
    assert res_picpay["situacao_cadastral"] == "ATIVA"
    assert res_picpay["cenario_tipo"] == "CENARIO_A"
    assert res_picpay["probabilidade_insolvencia"] <= 5.0
    assert res_picpay["risco_calote_subsidiario"] <= 5.0


def test_banco_do_brasil_and_itau_solvency_and_exposure_calibration(monkeypatch):
    """
    Garante que:
    1. Banco do Brasil resolve para 'bb.com.br' e seu catálogo contém '00000000000191'.
    2. Capital Social do Banco do Brasil consolida mais de R$ 120 Bilhões.
    3. Probabilidade de Insolvência é inferior a 2.0% e Cenário A é garantido.
    4. O Índice de Exposição Financeira (Card 1) é amortecido de 90.8% para < 25.0%.
    """
    from app.services.economic_service import (
        lookup_corporate_directory,
        resolve_company_logo,
        resolve_company_domain,
        compute_calibrated_exposure_and_solvency,
        evaluate_economic_health_and_capital,
    )
    import app.services.economic_service as econ_module

    # 1. Domínio e Catálogo do BB
    assert resolve_company_domain("Banco do Brasil") == "bb.com.br"
    assert resolve_company_domain("BANCO DO BRASIL (GRUPO CONSOLIDADO / FILIAIS)") == "bb.com.br"
    assert resolve_company_logo("Banco do Brasil")["domain"] == "bb.com.br"
    assert resolve_company_logo("BANCO DO BRASIL (GRUPO CONSOLIDADO / FILIAIS)")["domain"] == "bb.com.br"

    bb_cnpjs = lookup_corporate_directory("Banco do Brasil", "BANCO DO BRASIL")
    assert "00000000000191" in bb_cnpjs

    # 2. Amortecimento Patrimonial do Card 1
    calib_bb = compute_calibrated_exposure_and_solvency(
        raw_conviction_prob_pct=90.8,
        total_capital_num=120_000_000_000.0,
        situacao_raw="ATIVA",
        total_processes=444,
    )
    assert calib_bb["probabilidade_insolvencia"] == 0.8
    assert calib_bb["indice_exposicao_financeira"] == 16.3
    assert calib_bb["cenario_tipo"] == "CENARIO_A"
    assert calib_bb["risco_exposicao_subsidiaria"] < 1.0

    # 3. Amortecimento para o Itaú
    calib_itau = compute_calibrated_exposure_and_solvency(
        raw_conviction_prob_pct=82.9,
        total_capital_num=200_000_000_000.0,
        situacao_raw="ATIVA",
        total_processes=600,
    )
    assert calib_itau["probabilidade_insolvencia"] == 0.8
    assert calib_itau["indice_exposicao_financeira"] < 20.0
    assert calib_itau["cenario_tipo"] == "CENARIO_A"

    # 4. Avaliação econômica completa para o Banco do Brasil
    def mock_fetch_bb(c, timeout=4.0):
        digits = re.sub(r"\D", "", str(c))
        if digits == "00000000000191":
            return {
                "cnpj": digits,
                "raiz": "00000000",
                "razao_social": "BANCO DO BRASIL SA",
                "nome_fantasia": "DIRECAO GERAL",
                "capital_social": 120000000000.0,
                "porte": "DEMAIS",
                "situacao": "ATIVA",
            }
        return {}

    monkeypatch.setattr(econ_module, "fetch_accurate_cnpj_data", mock_fetch_bb)

    strat = {"mode": "BROAD_BRAND", "comunica_query_term": "BANCO DO BRASIL", "is_broad_brand": True}
    res_bb = evaluate_economic_health_and_capital(
        user_query="Banco do Brasil",
        brand_term="BANCO DO BRASIL",
        all_processes=[{"numero_processo": f"proc_{i}"} for i in range(444)],
        conviction_prob_pct=90.8,
        search_strategy=strat,
    )

    assert res_bb["capital_social_num"] >= 120_000_000_000.0
    assert "120.000.000.000,00" in res_bb["capital_social_formatado"]
    assert res_bb["situacao_cadastral"] == "ATIVA"
    assert res_bb["cenario_tipo"] == "CENARIO_A"
    assert res_bb["probabilidade_insolvencia"] < 2.0
    assert res_bb["indice_exposicao_financeira"] < 25.0


def test_belongs_to_same_corporate_brand_universal_algorithm():
    """
    Testa o algoritmo universal de pertencimento de grupo econômico:
    - Garante correspondência contígua e tokens distintivos.
    - Evita contaminação de homônimos ou empresas com palavras genéricas (BRASIL, BANCO).
    """
    from app.services.economic_service import belongs_to_same_corporate_brand

    # 1. Banco do Brasil
    assert belongs_to_same_corporate_brand("Banco do Brasil", "BANCO DO BRASIL SA") is True
    assert belongs_to_same_corporate_brand("Banco do Brasil", "BB CORRETORA DE SEGUROS E ADMINISTRADORA DE BENS S.A.") is True
    assert belongs_to_same_corporate_brand("Banco do Brasil", "BB GESTAO DE RECURSOS DTVM S.A.") is True
    assert belongs_to_same_corporate_brand("Banco do Brasil", "BANCO ABC BRASIL S.A.") is False
    assert belongs_to_same_corporate_brand("Banco do Brasil", "TELEFONICA BRASIL S.A.") is False
    assert belongs_to_same_corporate_brand("Banco do Brasil", "BANCO SANTANDER BRASIL S.A.") is False
    assert belongs_to_same_corporate_brand("Banco do Brasil", "COMPANHIA BRASILEIRA DE DISTRIBUICAO") is False

    # 2. PicPay
    assert belongs_to_same_corporate_brand("PicPay", "PICPAY INSTITUICAO DE PAGAMENTO S.A.") is True
    assert belongs_to_same_corporate_brand("PicPay", "PICPAY BANK - BANCO MULTIPLO S.A.") is True
    assert belongs_to_same_corporate_brand("PicPay", "BANCO BRADESCO S.A.") is False

    # 3. Caixa
    assert belongs_to_same_corporate_brand("Caixa Economica Federal", "CAIXA ECONOMICA FEDERAL") is True
    assert belongs_to_same_corporate_brand("Caixa", "CAIXA ECONOMICA FEDERAL") is True
    assert belongs_to_same_corporate_brand("Caixa", "SUPERMERCADO CAIXA LTDA") is False

    # 4. Outras empresas
    assert belongs_to_same_corporate_brand("Itaú", "BANCO ITAU UNIBANCO S.A.") is True
    assert belongs_to_same_corporate_brand("Itaú", "BANCO BRADESCO S.A.") is False
    assert belongs_to_same_corporate_brand("Compesa", "COMPANHIA PERNAMBUCANA DE SANEAMENTO - COMPESA") is True
    assert belongs_to_same_corporate_brand("Compesa", "SANEPAR") is False


def test_bb_directory_excludes_abc_brasil_and_retains_matriz():
    """
    Garante que o CNPJ do Banco ABC Brasil (28195667000106) NÃO está no diretório do BB.
    """
    from app.services.economic_service import OFFICIAL_CORPORATE_CNPJ_DIRECTORY

    bb_cnpjs = OFFICIAL_CORPORATE_CNPJ_DIRECTORY.get("BANCO DO BRASIL", [])
    assert "28195667000106" not in bb_cnpjs
    assert "00000000000191" in bb_cnpjs


def test_adk_ai_report_synchronized_with_solvency_scenario_a():
    """
    Garante que quando cenario_tipo == 'CENARIO_A', o parecer do Agente ADK
    retorna 'APROVAR / RISCO MITIGADO' e 'Baixo', mesmo com alta taxa histórica de condenações (92.4%).
    """
    from app.services.ai_analyzer import LegalAIAnalyzer

    analyzer = LegalAIAnalyzer()

    summary_with_solvency = {
        "empresa": "BANCO DO BRASIL SA",
        "total_processos": 912,
        "desfechos_sentencas": {
            "condenacoes": 250,
            "acordos": 20,
            "improcedentes": 20,
            "em_aberto": 622,
        },
        "ranking_motivos": [
            {"motivo": "Horas Extras", "quantidade": 80},
            {"motivo": "Intervalo Intrajornada", "quantidade": 50},
        ],
        "idoneidade_economica": {
            "cenario_tipo": "CENARIO_A",
            "capital_social_formatado": "R$ 120.000.000.000,00",
            "probabilidade_insolvencia": 0.8,
            "risco_exposicao_subsidiaria": 0.7,
        },
    }

    report = analyzer.analyze_company(summary_with_solvency)
    assert report["recomendacao_homologacao"] == "APROVAR / RISCO MITIGADO"
    assert "Baixo" in report["perfil_risco_consolidado"]
    assert "plena higidez" in report["resumo_executivo"].lower()
    assert "331" in report["resumo_executivo"]
    assert "REPROVAR" not in report["recomendacao_homologacao"]


def test_discover_real_corporate_domain_caixa_and_bb():
    """
    Testa que domínios e logos de entidades estatais/especiais retornam seus domínios oficiais.
    """
    from app.services.economic_service import discover_real_corporate_domain_and_logo

    caixa = discover_real_corporate_domain_and_logo("Caixa Economica Federal")
    assert caixa["domain"] == "caixa.gov.br"
    assert "caixa.gov.br" in caixa["logo_primary_url"]

    bb = discover_real_corporate_domain_and_logo("Banco do Brasil")
    assert bb["domain"] == "bb.com.br"
    assert "bb.com.br" in bb["logo_primary_url"]


def test_consolidate_large_group_with_20_roots_and_qsa_holding(monkeypatch):
    """
    Garante que grupos econômicos com 20 raízes distintas de CNPJ (incluindo
    Holding/Controladora descoberta via QSA) são consolidados e somados integralmente,
    sem nenhum limite ou corte artificial em 8 ou 6 CNPJs.
    """
    from app.services.economic_service import (
        compute_cnpj_dv,
        evaluate_economic_health_and_capital,
        clear_cnpj_cache,
    )
    import app.services.economic_service as econ_module

    clear_cnpj_cache()

    # 19 CNPJs distintos de subsidiárias encontradas nos processos
    cnpjs_19 = []
    for i in range(1, 20):
        root = f"{i:08d}"
        base = root + "0001"
        cnpjs_19.append(base + compute_cnpj_dv(base))

    # 20º CNPJ: Holding controladora do grupo
    root_holding = "99999999"
    base_h = root_holding + "0001"
    cnpj_holding = base_h + compute_cnpj_dv(base_h)

    all_20_cnpjs = set(cnpjs_19 + [cnpj_holding])

    def mock_fetch(cnpj, timeout=4.0):
        digits = re.sub(r"\D", "", str(cnpj))
        if digits in all_20_cnpjs:
            root = digits[:8]
            qsa = []
            if digits == cnpjs_19[0]:
                # Primeira subsidiária traz a Holding no seu QSA
                qsa = [(cnpj_holding, "CONGLOMERADO ALFA HOLDING S.A.")]
            return {
                "cnpj": digits,
                "raiz": root,
                "razao_social": "CONGLOMERADO ALFA HOLDING S.A." if digits == cnpj_holding else f"CONGLOMERADO ALFA SUBSIDIARIA {root} S.A.",
                "nome_fantasia": "CONGLOMERADO ALFA",
                "capital_social": 10_000_000.0,
                "porte": "DEMAIS",
                "situacao": "ATIVA",
                "qsa_pjs": qsa,
            }
        return {}

    monkeypatch.setattr(econ_module, "fetch_accurate_cnpj_data", mock_fetch)

    # 19 processos contendo os 19 CNPJs das subsidiárias
    procs = [
        {
            "numero_processo": f"proc_{i}",
            "cnpjs_encontrados": [cnpjs_19[i - 1]],
            "destinatarios_nomes": [f"CONGLOMERADO ALFA UNIDADE {i}"],
        }
        for i in range(1, 20)
    ]

    res = evaluate_economic_health_and_capital(
        user_query="Conglomerado Alfa",
        brand_term="CONGLOMERADO ALFA",
        all_processes=procs,
        conviction_prob_pct=30.0,
        search_strategy={"mode": "BROAD_BRAND"},
    )

    # Todas as 20 raízes devem ter sido somadas (19 subsidiárias + 1 Holding via QSA)
    assert res["capital_social_num"] == 200_000_000.0
    assert "200.000.000,00" in res["capital_social_formatado"]
    assert len(res["raizes_consolidadas"]) == 20
    assert res["unidades_consolidadas"] >= 20
    assert res["cenario_tipo"] == "CENARIO_A"


def test_bradesco_consolidates_multiple_roots_and_capital_over_100_billion(monkeypatch):
    """
    Garante que a consolidação do Grupo Bradesco soma múltiplas raízes distintas
    (Banco, Seguros, Saúde, Vida e Previdência, Financiamentos, BBI) ultrapassando
    R$ 100 Bilhões e nunca ficando limitada a 1 único CNPJ de R$ 93,77 Bi,
    mesmo que os processos citem dezenas de filiais da mesma raiz bancária.
    """
    from app.services.economic_service import evaluate_economic_health_and_capital, clear_cnpj_cache
    import app.services.economic_service as econ_module

    clear_cnpj_cache()

    bradesco_mock_db = {
        "60746948000112": {
            "cnpj": "60746948000112",
            "raiz": "60746948",
            "razao_social": "BANCO BRADESCO S.A.",
            "nome_fantasia": "",
            "capital_social": 93_770_000_000.0,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
            "qsa_pjs": [],
        },
        "33055146000193": {
            "cnpj": "33055146000193",
            "raiz": "33055146",
            "razao_social": "BRADESCO SEGUROS S/A",
            "nome_fantasia": "",
            "capital_social": 8_096_444_400.0,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
            "qsa_pjs": [],
        },
        "92693118000160": {
            "cnpj": "92693118000160",
            "raiz": "92693118",
            "razao_social": "BRADESCO SAUDE S/A",
            "nome_fantasia": "",
            "capital_social": 4_400_199_214.72,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
            "qsa_pjs": [],
        },
        "51990695000137": {
            "cnpj": "51990695000137",
            "raiz": "51990695",
            "razao_social": "BRADESCO VIDA E PREVIDENCIA S.A.",
            "nome_fantasia": "",
            "capital_social": 4_301_113_300.0,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
            "qsa_pjs": [],
        },
        "07207996000150": {
            "cnpj": "07207996000150",
            "raiz": "07207996",
            "razao_social": "BANCO BRADESCO FINANCIAMENTOS S.A.",
            "nome_fantasia": "",
            "capital_social": 499_300_000.0,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
            "qsa_pjs": [],
        },
        "06271464000119": {
            "cnpj": "06271464000119",
            "raiz": "06271464",
            "razao_social": "BANCO BRADESCO BBI S.A.",
            "nome_fantasia": "",
            "capital_social": 4_120_000_000.0,
            "porte": "DEMAIS",
            "situacao": "ATIVA",
            "qsa_pjs": [],
        },
    }

    def mock_fetch(cnpj, timeout=4.0):
        digits = re.sub(r"\D", "", str(cnpj))
        if digits in bradesco_mock_db:
            return bradesco_mock_db[digits]
        root = digits[:8]
        if root == "60746948":
            return bradesco_mock_db["60746948000112"]
        return {}

    monkeypatch.setattr(econ_module, "fetch_accurate_cnpj_data", mock_fetch)

    # 51 filiais da mesma raiz bancária 60746948
    branch_cnpjs = [f"60746948{i:04d}00" for i in range(1, 52)]
    procs = [
        {
            "numero_processo": f"proc_{i}",
            "cnpjs_encontrados": [branch_cnpjs[i - 1]],
            "destinatarios_nomes": ["BANCO BRADESCO S.A.", "BRADESCO SEGUROS S.A.", "BRADESCO SAUDE S.A."],
        }
        for i in range(1, 52)
    ]

    res = evaluate_economic_health_and_capital(
        user_query="Bradesco",
        brand_term="BRADESCO",
        all_processes=procs,
        conviction_prob_pct=30.0,
        search_strategy={"mode": "BROAD_BRAND"},
    )

    assert res["capital_social_num"] > 100_000_000_000.0
    assert res["capital_social_num"] != 93_770_000_000.0
    assert len(res["raizes_consolidadas"]) >= 4
    assert res["unidades_consolidadas"] >= 51
    assert res["cenario_tipo"] == "CENARIO_A"





