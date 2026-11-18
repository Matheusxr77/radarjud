# RadarJus: Monitor Preditivo de Risco Trabalhista e Idoneidade de Terceiros (Súmula 331 TST)

[![CI Suíte de Testes](https://img.shields.io/badge/Testes-101%20passed%20(100%25)-success?style=flat&logo=pytest&logoColor=white)](https://github.com/radarjud/radarjud/actions)
[![Linter Ruff](https://img.shields.io/badge/Ruff-Passed%20(E9%2C%20F63%2C%20F7%2C%20F82)-brightgreen?style=flat&logo=ruff&logoColor=black)](https://github.com/radarjud/radarjud/actions)
[![Versão](https://img.shields.io/badge/Versão-v2.0.0--risktech-blue?style=flat)](https://github.com/radarjud/radarjud)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/Frontend-React%2018%20%2B%20Vite-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Licença](https://img.shields.io/badge/Conformidade-Súmula%20331%20TST-orange?style=flat)](https://www.tst.jus.br)

Plataforma inteligente de auditoria e conformidade contínua para contratação de prestadores de serviços, terceirizados e fornecedores. Cruza metadados abertos do Poder Judiciário (CNJ) com bases governamentais da Receita Federal, CGU e PGFN para antecipar desfechos processuais e calcular o risco de calote subsidiário ao tomador de serviços.

---

## 🛠️ Stack Tecnológica & Ferramentas

| Domínio | Tecnologias e Ferramentas |
|:---|:---|
| **Backend e Arquitetura** | ![Python](https://img.shields.io/badge/Python%203.11%20%2F%203.12-3776AB?style=flat&logo=python&logoColor=white) ![Flask](https://img.shields.io/badge/Flask%203.0-000000?style=flat&logo=flask&logoColor=white) ![Gunicorn](https://img.shields.io/badge/Gunicorn-499848?style=flat&logo=gunicorn&logoColor=white) ![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=flat&logo=sqlalchemy&logoColor=white) ![Pydantic](https://img.shields.io/badge/Pydantic%20v2-E92063?style=flat&logo=pydantic&logoColor=white) ![ReportLab](https://img.shields.io/badge/ReportLab%20PDF-C0392B?style=flat&logo=adobeacrobatreader&logoColor=white) |
| **Modelagem Preditiva & Métricas** | ![Estatística Bayesiana](https://img.shields.io/badge/Dirichlet--Multinomial-00599C?style=flat&logo=scipy&logoColor=white) ![Beta-Binomial](https://img.shields.io/badge/Beta--Binomial%20Credibilidade%2090%25-4A90E2?style=flat&logo=googleanalytics&logoColor=white) ![Modelagem Atuarial](https://img.shields.io/badge/Solvência%20Atuarial-27AE60?style=flat&logo=safari&logoColor=white) ![Parser TPU CNJ](https://img.shields.io/badge/NLP%20%26%20TPU%20Parser-1ABC9C?style=flat&logo=target&logoColor=white) |
| **Mensageria & Integração Governamental** | ![Comunica PJe](https://img.shields.io/badge/Comunica%20PJe%20(CNJ)-1B365D?style=flat&logo=googleearth&logoColor=white) ![DataJud](https://img.shields.io/badge/DataJud%20(Elasticsearch)-005571?style=flat&logo=elasticsearch&logoColor=white) ![Receita Federal](https://img.shields.io/badge/Receita%20Federal%20%2F%20RFB-003366?style=flat&logo=gov.br&logoColor=white) ![CGU Transparência](https://img.shields.io/badge/CGU%20(CEIS%20%2F%20CNEP)-0B6623?style=flat&logo=open-access&logoColor=white) ![PGFN](https://img.shields.io/badge/PGFN%20Dívida%20Ativa-8E44AD?style=flat&logo=datagrip&logoColor=white) |
| **Web Frontend** | ![React](https://img.shields.io/badge/React%2018-20232A?style=flat&logo=react&logoColor=61DAFB) ![Vite](https://img.shields.io/badge/Vite%206-646CFF?style=flat&logo=vite&logoColor=white) ![JavaScript](https://img.shields.io/badge/JavaScript%20ES6%2B-F7DF1E?style=flat&logo=javascript&logoColor=black) ![Lucide React](https://img.shields.io/badge/Lucide%20Icons-F56565?style=flat&logo=feather&logoColor=white) ![Modern CSS](https://img.shields.io/badge/Modern%20CSS3-1572B6?style=flat&logo=css3&logoColor=white) |
| **Bancos de Dados & Armazenamento** | ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat&logo=postgresql&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-07405E?style=flat&logo=sqlite&logoColor=white) ![Memory Cache L0](https://img.shields.io/badge/Cache%20em%20Memória%20(TTL%2015m)-FF6F00?style=flat&logo=redis&logoColor=white) |
| **DevOps, Garantia de Qualidade & Nuvem** | ![Docker](https://img.shields.io/badge/Docker%20%26%20Compose-2496ED?style=flat&logo=docker&logoColor=white) ![Vercel](https://img.shields.io/badge/Vercel%20Edge%20(gru1)-000000?style=flat&logo=vercel&logoColor=white) ![Render](https://img.shields.io/badge/Render%20Cloud-46E3B7?style=flat&logo=render&logoColor=black) ![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF?style=flat&logo=githubactions&logoColor=white) ![Pytest](https://img.shields.io/badge/Pytest%20(101%20testes)-0A9EDC?style=flat&logo=pytest&logoColor=white) ![Ruff](https://img.shields.io/badge/Ruff%20Linter-D7FF64?style=flat&logo=ruff&logoColor=black) ![Git](https://img.shields.io/badge/Git-F05032?style=flat&logo=git&logoColor=white) |

---

## 🏛️ 1. O Problema e a Solução

### O Risco Jurídico da Súmula 331 do TST
Ao terceirizar atividades, a legislação brasileira impõe ao tomador de serviços a **responsabilidade subsidiária** pelas dívidas trabalhistas não honradas pela contratada (Súmula 331, itens IV e VI do TST). Se a empresa terceirizada sofrer condenações e não tiver capital social, saldo em conta ou patrimônio suficiente, o juiz do trabalho efetua a penhora eletrônica direta nas contas bancárias do **tomador dos serviços**.

### A Abordagem do RadarJus
O RadarJus realiza uma auditoria preditiva e cadastral em duas frentes complementares:

1. **Passivo Trabalhista Real nos Tribunais**: Mapeia o histórico processual, desfechos de sentenças (condenações, acordos, improcedências) e estima a probabilidade de êxito nos processos em aberto via **pura matemática preditiva bayesiana (sem alucinações de IA/LLM)**.
2. **Idoneidade Econômico-Financeira e Cadastral**: Verifica a saúde financeira da contratada (Capital Social, regularidade na Receita Federal, sanções da CGU, Dívida Ativa da União e ações de falência/recuperação judicial), calculando o **Risco Real de Calote Subsidiário**:

$$\text{Risco Real de Calote Subsidiário} = P(\text{Condenação}) \times P(\text{Insolvência})$$

> **Onde:**
> - $P(\text{Condenação})$: Probabilidade preditiva estimada pelo motor bayesiano Dirichlet-Multinomial para os processos em aberto da empresa.
> - $P(\text{Insolvência})$: Taxa atuarial calculada a partir do porte da empresa, Capital Social consolidado perante a RFB e registros em certidões negativas / recuperações judiciais.

---

## 🌐 2. Fontes Governamentais e APIs Públicas Integradas

| Fonte Governamental | Dados Extraídos | Propósito na Plataforma |
|:---|:---|:---|
| **Comunica PJe (CNJ)** | Publicações e intimações oficiais dos diários de justiça trabalhistas. | Descoberta do acervo de processos por Razão Social/CNPJ e extração do teor das decisões. |
| **DataJud (CNJ / Elasticsearch)** | Metadados TPU (Tabela Processual Unificada), níveis de sigilo e movimentos. | Classificação exata de assuntos (código TPU), detecção de perícias e desfechos de mérito. |
| **BrasilAPI / Receita Federal** | Situação cadastral, Capital Social, Quadro de Sócios (QSA) e e-mail. | Avaliação da solidez societária, triangulação de CNPJ e busca de logomarca corporativa. |
| **CGU Portal da Transparência** | Cadastros CEIS (Inidôneas), CNEP (Punidas) e CEPIM (Impedidas). | Identificação de proibições de licitar, fraudes e sanções administrativas ativas. |
| **PGFN Dados Abertos** | Dívida Ativa da União e FGTS. | Verificação de certidão negativa e inadimplência de FGTS ou multas trabalhistas do MTE. |

---

## 🛡️ 3. Arquitetura Tripla Anti-Geoblocking (Render + Vercel)

Quando o backend é hospedado em provedores de nuvem internacionais (ex: plano gratuito do Render nos EUA), os firewalls dos órgãos governamentais brasileiros (PJe, DataJud, CGU) bloqueiam os IPs externos com `HTTP 403 Forbidden` ou `Timeout`. 

O RadarJus implementa uma **Arquitetura Tripla Anti-Geoblocking 100% transparente para o usuário final**:

1. **Relay Serverless Edge na Vercel (`gru1`)**: Uma Serverless Edge Function (`frontend/api/cnj-proxy.js`) forçada no datacenter de Guarulhos/SP atua como ponte oficial de IP brasileiro com whitelist de domínios.
2. **Cliente HTTP Inteligente com Headers Brasileiros**: No backend Python (`app/utils/http_client.py`), todas as chamadas enviam cabeçalhos completos de navegador (`pt-BR`) e chaveiam automaticamente para o `CNJ_PROXY_URL` em caso de erro 403 ou timeout.
3. **Browser Edge Fetch Silencioso**: No frontend (`frontend/src/App.jsx`), uma rotina invisível pré-carrega as intimações via conexão local do próprio usuário (`client_prefetched_items`) e as envia ao backend caso a coleta direta nos EUA seja barrada.

---

## 📐 4. Inteligência Preditiva Bayesiana (Pura Matemática)

O motor de previsão (`BayesianRiskPredictor`) baseia-se em formulações estatísticas analíticas exatas da **Conjugação Dirichlet-Multinomial e Beta-Binomial**:

- **Distribuição Tripartida**: Separa formalmente as probabilidades de Vitória/Improcedência ($\theta_{\text{imp}}$), Acordo Judicial ($\theta_{\text{acordo}}$) e Condenação ($\theta_{\text{cond}}$).
- **Intervalo de Credibilidade de 90% (IC 90%)**: Derivado diretamente da distribuição acumulada da função Beta marginal ($F_{\text{Beta}}^{-1}$).
- **Regra Crítica de Previsão Zero**: Empresas sem condenações pretéritas registradas nos tribunais consultados **não recebem previsão de condenação** ($P_{\text{cond}} = 0.0$), respeitando a presunção legal de idoneidade.
- **Ranking de Motivos de Condenação**: Mapeia os assuntos onde a empresa foi historicamente condenada, utilizando esse percentual como ponderador empírico (*Empirical Bayes Leave-One-Subject-Out*) para calibrar processos abertos do mesmo tema.

---

## 🏗️ 5. Diagrama de Arquitetura do Sistema

```mermaid
flowchart TD
    subgraph Frontend["Frontend SPA & Edge Proxy (Vercel)"]
        UI["Interface do Usuário (React / Vite)"]
        EdgeProxy["Edge Relay Function (gru1 - São Paulo)"]
    end

    subgraph Backend["Backend API (Flask / Gunicorn no Render)"]
        API["Rotas REST (/api/v1)"]
        CacheL0["Cache em Memória L0 (TTL 15 min)"]
        CacheL1["PostgreSQL / SQLite (Histórico L1)"]
        HttpClient["HTTP Client Resiliente (pt-BR + Fallback)"]
        BayesEngine["Motor Preditivo Bayesiano (Dirichlet)"]
        EconEngine["Serviço de Solvência & Capital Social"]
        PDFGen["Gerador de Relatórios Executivos (ReportLab)"]
    end

    subgraph GovAPIs["Sistemas Públicos Oficiais"]
        ComunicaPJe["Comunica PJe (CNJ)"]
        DataJud["DataJud CNJ (Elasticsearch)"]
        Receita["Receita Federal / BrasilAPI"]
        CGU["Portal da Transparência (CGU)"]
    end

    UI -->|"POST /search"| API
    API --> CacheL0
    CacheL0 -- "Cache Miss" --> CacheL1
    CacheL1 -- "Cache Miss" --> HttpClient
    HttpClient -->|"Direto (ou Fallback se 403)"| EdgeProxy
    HttpClient -.->|"Conexão Direta"| GovAPIs
    EdgeProxy -->|"IP Brasileiro (gru1)"| ComunicaPJe
    EdgeProxy -->|"IP Brasileiro (gru1)"| DataJud
    EdgeProxy -->|"IP Brasileiro (gru1)"| CGU
    GovAPIs --> BayesEngine
    GovAPIs --> EconEngine
    BayesEngine --> API
    EconEngine --> API
    API --> PDFGen
    API --> UI
```

---

## 🚀 6. Como Subir e Executar o Projeto Localmente

### Pré-requisitos
- **Python 3.11+** ou **3.12+** instalado.
- **Node.js 18+** e **npm** instalados.
- **PostgreSQL 14+** (ou SQLite para desenvolvimento local / Docker).

---

### Execução Local Rápida (Recomendada)

#### 1. Configurar e Ativar o Ambiente Virtual Python
Na raiz do projeto (`radarjud/`):
```bash
# Cria o ambiente virtual
python3 -m venv .venv

# Ativa o ambiente virtual no Linux/macOS:
source .venv/bin/activate

# Ou no Windows (PowerShell):
# .venv\Scripts\Activate.ps1

# Instala todas as dependências do backend
pip install -r requirements.txt
```

#### 2. Configurar Variáveis de Ambiente
Copie o arquivo de exemplo:
```bash
cp .env.example .env
```

#### 3. Executar os Testes Automatizados & Linter
Garanta que todos os módulos estão 100% íntegros:
```bash
# Executa a suíte completa de 101 testes
.venv/bin/pytest -v

# Valida sintaxe e contratos do código com Ruff
ruff check . --select=E9,F63,F7,F82
```
*(Todos os **101 testes automatizados** devem passar com 100% de sucesso em menos de 1 segundo).*

#### 4. Iniciar o Servidor Backend (Flask)
```bash
python run.py
```
*O backend estará rodando em `http://localhost:5000` (com endpoints em `/api/v1`).*

#### 5. Configurar e Iniciar o Frontend (React + Vite)
Abra outro terminal e acesse a pasta `frontend/`:
```bash
cd frontend

# Instala as dependências
npm install

# Inicia o servidor de desenvolvimento Vite
npm run dev
```
*O frontend estará disponível em `http://localhost:5173`.*

---

### Execução via Docker Compose

Caso prefira executar todos os serviços containerizados:
```bash
docker-compose up -d --build
```
- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:5000`

---

## 📄 7. Exportação de Relatórios em PDF

A plataforma conta com um módulo nativo de exportação de **Relatórios Executivos em PDF** (`ReportLab` + motor vetorial puro):
- Resumo executivo cadastral e situação na Receita Federal.
- Panorama preditivo e matriz Dirichlet-Multinomial.
- Auditoria de Solvência e Risco de Calote Subsidiário (**Cenário A / Cenário B**).
- Tabela analítica completa dos desfechos processuais mapeados.

Rotas disponíveis:
- `GET /api/v1/history/<id>/summary.pdf`
- `POST /api/v1/report/pdf` (gera o PDF instantaneamente a partir dos dados em tela).

---

## 🛡️ Licença e Propriedade
Desenvolvido sob rigorosos padrões de conformidade jurídica para prevenção de riscos de terceirização sob a égide da **Súmula 331 do Tribunal Superior do Trabalho**.
