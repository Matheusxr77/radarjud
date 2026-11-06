"""
Constantes e Tabelas de Referência TPU (Tabelas Processuais Unificadas do CNJ).
Mapeamento de códigos de movimentos decisórios, perícias e taxas basais estatísticas
para o modelo Dirichlet-Multinomial / Beta-Binomial (Empirical Bayes).
"""

# Códigos TPU de Movimentos Decisórios (Sentenças / Acórdãos / Homologações / Extinções)
TPU_OUTCOME_CODES = {
    219: {
        "nome": "Procedência",
        "categoria": "condenacoes",
        "subcategoria": "procedencia_total",
        "peso_risco": 1.0,
        "descricao": "Condenação total do empregador/prestador",
    },
    221: {
        "nome": "Procedência em Parte",
        "categoria": "condenacoes",
        "subcategoria": "procedencia_parcial",
        "peso_risco": 0.75,
        "descricao": "Condenação parcial do empregador/prestador",
    },
    220: {
        "nome": "Improcedência",
        "categoria": "improcedentes",
        "subcategoria": "improcedencia",
        "peso_risco": 0.0,
        "descricao": "Pedido rejeitado / vitória integral da empresa",
    },
    466: {
        "nome": "Homologação de Acordo",
        "categoria": "acordos",
        "subcategoria": "acordo_homologado",
        "peso_risco": 0.50,
        "descricao": "Acordo judicial homologado (desfecho conciliatório)",
    },
    218: {
        "nome": "Extinção sem Resolução do Mérito",
        "categoria": "extintos_sem_merito",
        "subcategoria": "extinto_sem_merito",
        "peso_risco": 0.0,
        "descricao": "Processo extinto sem julgamento de mérito / desistência / arquivamento",
    },
    246: {
        "nome": "Arquivamento Definitivo / Baixa",
        "categoria": "baixa_arquivado",
        "subcategoria": "arquivado",
        "peso_risco": 0.0,
        "descricao": "Processo baixado ou arquivado definitivamente",
    },
    848: {
        "nome": "Trânsito em Julgado",
        "categoria": "fase_recursal_encerrada",
        "subcategoria": "transito_em_julgado",
        "peso_risco": 0.0,
        "descricao": "Sentença definitiva irrecorrível",
    },
}

# Códigos TPU de Perícia e Prova Técnica
TPU_EXPERT_REPORT_CODES = {
    83: "Designação de Perícia",
    11383: "Juntada de Laudo Pericial",
    84: "Apresentação de Quesitos",
    85: "Nomeação de Perito",
}

# Priors Dirichlet-Multinomial por Assunto TPU na Justiça do Trabalho:
# pbase_cond: proporção basal de procedência (total + parcial) nos julgamentos de mérito/conciliação
# pbase_acordo: proporção basal de acordo homologado
# pbase_imp: proporção basal de improcedência (1 - pbase_cond - pbase_acordo)
# logit_laudo_beta: efeito em escala logit da proporção de processos com perícia técnica
TPU_SUBJECT_BASELINES = {
    # Verbas Rescisórias e Obrigações Contratuais
    1998: {"nome": "Verbas Rescisórias / FGTS", "pbase": 0.62, "pbase_acordo": 0.23, "pbase_imp": 0.15, "logit_laudo_beta": 0.05, "sumula_331_core": True},
    2041: {"nome": "Multa do Artigo 477 da CLT", "pbase": 0.64, "pbase_acordo": 0.22, "pbase_imp": 0.14, "logit_laudo_beta": 0.0, "sumula_331_core": True},
    2040: {"nome": "Multa do Artigo 467 da CLT", "pbase": 0.60, "pbase_acordo": 0.24, "pbase_imp": 0.16, "logit_laudo_beta": 0.0, "sumula_331_core": True},
    2007: {"nome": "Aviso Prévio", "pbase": 0.61, "pbase_acordo": 0.23, "pbase_imp": 0.16, "logit_laudo_beta": 0.0, "sumula_331_core": True},
    1997: {"nome": "Atraso no Pagamento de Salário", "pbase": 0.65, "pbase_acordo": 0.22, "pbase_imp": 0.13, "logit_laudo_beta": 0.0, "sumula_331_core": True},

    # Jornada de Trabalho
    1855: {"nome": "Horas Extras", "pbase": 0.52, "pbase_acordo": 0.25, "pbase_imp": 0.23, "logit_laudo_beta": 0.10, "sumula_331_core": False},
    1858: {"nome": "Intervalo Intrajornada", "pbase": 0.50, "pbase_acordo": 0.25, "pbase_imp": 0.25, "logit_laudo_beta": 0.08, "sumula_331_core": False},
    1857: {"nome": "Adicional Noturno", "pbase": 0.49, "pbase_acordo": 0.25, "pbase_imp": 0.26, "logit_laudo_beta": 0.08, "sumula_331_core": False},

    # Saúde e Segurança do Trabalho (fortemente dependentes de prova pericial)
    2029: {"nome": "Adicional de Insalubridade", "pbase": 0.46, "pbase_acordo": 0.24, "pbase_imp": 0.30, "logit_laudo_beta": 0.35, "sumula_331_core": False},
    2034: {"nome": "Adicional de Periculosidade", "pbase": 0.44, "pbase_acordo": 0.24, "pbase_imp": 0.32, "logit_laudo_beta": 0.35, "sumula_331_core": False},

    # Responsabilidade Civil / Dano Moral
    2576: {"nome": "Indenização por Dano Moral", "pbase": 0.32, "pbase_acordo": 0.24, "pbase_imp": 0.44, "logit_laudo_beta": 0.15, "sumula_331_core": False},
    2575: {"nome": "Indenização por Dano Material", "pbase": 0.35, "pbase_acordo": 0.23, "pbase_imp": 0.42, "logit_laudo_beta": 0.20, "sumula_331_core": False},
    1854: {"nome": "Assédio Moral", "pbase": 0.29, "pbase_acordo": 0.23, "pbase_imp": 0.48, "logit_laudo_beta": 0.10, "sumula_331_core": False},

    # Terceirização / Responsabilidade Solidária / Subsidiária (Súmula 331 TST)
    2054: {"nome": "Terceirização / Tomador de Serviços", "pbase": 0.58, "pbase_acordo": 0.24, "pbase_imp": 0.18, "logit_laudo_beta": 0.0, "sumula_331_core": True},
    2055: {"nome": "Responsabilidade Solidária / Subsidiária", "pbase": 0.60, "pbase_acordo": 0.23, "pbase_imp": 0.17, "logit_laudo_beta": 0.0, "sumula_331_core": True},
}

# Mapeamento semântico normalizado (sem acentos, minúsculas) -> baseline
SUBJECT_KEYWORD_BASELINES = [
    (("rescis", "fgts", "477", "467", "aviso prev", "atraso", "salari"), {"pbase": 0.62, "pbase_acordo": 0.23, "pbase_imp": 0.15, "logit_laudo_beta": 0.05, "sumula_331_core": True}),
    (("terceiriz", "subsidiar", "331", "tomador"), {"pbase": 0.59, "pbase_acordo": 0.23, "pbase_imp": 0.18, "logit_laudo_beta": 0.0, "sumula_331_core": True}),
    (("execuc", "penhora", "sisbajud", "liquidac", "cumprimento de sentenca"), {"pbase": 0.65, "pbase_acordo": 0.20, "pbase_imp": 0.15, "logit_laudo_beta": 0.05, "sumula_331_core": True}),
    (("hora", "jornada", "intervalo", "noturno", "ponto", "bancari"), {"pbase": 0.52, "pbase_acordo": 0.25, "pbase_imp": 0.23, "logit_laudo_beta": 0.10, "sumula_331_core": False}),
    (("insalubr", "periculos", "acidente", "doenca", "epi"), {"pbase": 0.45, "pbase_acordo": 0.24, "pbase_imp": 0.31, "logit_laudo_beta": 0.35, "sumula_331_core": False}),
    (("moral", "assedio", "existencial", "indenizac"), {"pbase": 0.32, "pbase_acordo": 0.24, "pbase_imp": 0.44, "logit_laudo_beta": 0.15, "sumula_331_core": False}),
]

# Modificadores regionais de propensão a condenação/acordo por Tribunal (TRT) em escala logit
TRT_REGIONAL_LOGIT_MODIFIERS = {
    "trt2": 0.05,   # SP Capital / Grande SP
    "trt15": 0.04,  # Campinas / Interior SP
    "trt1": 0.05,   # RJ
    "trt3": 0.03,   # MG
    "trt4": 0.08,   # RS (histórico mais protetivo ao reclamante)
    "trt9": 0.02,   # PR
    "trt12": -0.04, # SC (maior índice relativo de conciliação/improcedência)
    "tst": 0.06,    # Tribunal Superior do Trabalho
}

# Taxas Basais Padrão para assuntos não catalogados expressamente
DEFAULT_TRT_BASE_PROBABILITY = 0.50
DEFAULT_TRT_ACORDO_PROBABILITY = 0.25
DEFAULT_TRT_IMP_PROBABILITY = 0.25

# Fator de Concentração a Priori da Dirichlet (alpha_0)
BAYESIAN_ALPHA_PRIOR = 3.0

# Coeficiente logit padrão para proporção de laudos periciais quando não especificado
EXPERT_REPORT_RISK_ADJUSTMENT = 0.20
