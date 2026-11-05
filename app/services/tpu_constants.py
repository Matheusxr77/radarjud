"""
Constantes e Tabelas de Referência TPU (Tabelas Processuais Unificadas do CNJ).
Mapeamento de códigos de movimentos decisórios, perícias e taxas basais estatísticas.
"""

# Códigos TPU de Movimentos Decisórios (Sentenças / Acórdãos / Homologações)
TPU_OUTCOME_CODES = {
    219: {
        "nome": "Procedência",
        "categoria": "condenacoes",
        "peso_risco": 1.0,
        "descricao": "Condenação total do empregador/prestador"
    },
    221: {
        "nome": "Procedência em Parte",
        "categoria": "condenacoes",
        "peso_risco": 1.0,
        "descricao": "Condenação parcial do empregador/prestador"
    },
    220: {
        "nome": "Improcedência",
        "categoria": "improcedentes",
        "peso_risco": 0.0,
        "descricao": "Pedido rejeitado / sem condenação"
    },
    466: {
        "nome": "Homologação de Acordo",
        "categoria": "acordos",
        "peso_risco": 0.65,
        "descricao": "Acordo judicial homologado (reconhecimento implícito de risco)"
    },
    848: {
        "nome": "Trânsito em Julgado",
        "categoria": "fase_recursal_encerrada",
        "peso_risco": 0.0,
        "descricao": "Sentença definitiva irrecorrível"
    }
}

# Códigos TPU de Perícia e Prova Técnica
TPU_EXPERT_REPORT_CODES = {
    83: "Designação de Perícia",
    11383: "Juntada de Laudo Pericial",
    84: "Apresentação de Quesitos",
    85: "Nomeação de Perito"
}

# Assuntos TPU mais comuns na Justiça do Trabalho e suas Taxas Basais Médias (Pbase_TRT)
# Base estatística histórica dos Tribunais Regionais do Trabalho (DataJud/TST)
TPU_SUBJECT_BASELINES = {
    # Verbas Rescisórias e Obrigações Contratuais (Alta taxa de procedência)
    1998: {"nome": "Verbas Rescisórias / FGTS", "pbase": 0.79},
    2041: {"nome": "Multa do Artigo 477 da CLT", "pbase": 0.81},
    2040: {"nome": "Multa do Artigo 467 da CLT", "pbase": 0.77},
    2007: {"nome": "Aviso Prévio", "pbase": 0.78},
    1997: {"nome": "Atraso no Pagamento de Salário", "pbase": 0.82},

    # Jornada de Trabalho
    1855: {"nome": "Horas Extras", "pbase": 0.68},
    1858: {"nome": "Intervalo Intrajornada", "pbase": 0.66},
    1857: {"nome": "Adicional Noturno", "pbase": 0.64},

    # Saúde e Segurança do Trabalho
    2029: {"nome": "Adicional de Insalubridade", "pbase": 0.61},
    2034: {"nome": "Adicional de Periculosidade", "pbase": 0.58},

    # Responsabilidade Civil / Dano Moral
    2576: {"nome": "Indenização por Dano Moral", "pbase": 0.42},
    2575: {"nome": "Indenização por Dano Material", "pbase": 0.45},
    1854: {"nome": "Assédio Moral", "pbase": 0.38},

    # Terceirização / Responsabilidade Solidária / Subsidiária (Súmula 331 TST)
    2054: {"nome": "Terceirização / Tomador de Serviços", "pbase": 0.75},
    2055: {"nome": "Responsabilidade Solidária / Subsidiária", "pbase": 0.76},
}

# Taxa Basal Padrão para assuntos não catalogados expressamente
DEFAULT_TRT_BASE_PROBABILITY = 0.68

# Fator de Suavização Bayesiana (alpha_prior) definido na metodologia
BAYESIAN_ALPHA_PRIOR = 3.0

# Ajuste Técnico por Laudo Pericial (+5% de risco técnico)
EXPERT_REPORT_RISK_ADJUSTMENT = 0.05

