# RadarJud — frontend React

Frontend independente (React 18 + Vite) com identidade visual e navegação baseada no Figma RadarJud.

## Instalação

```bash
cd frontend
npm install
npm run dev
```

Acesse http://localhost:5173. Para build de produção: `npm run build`.

## Integração com backend existente

O `vite.config.js` encaminha `/api` para `http://localhost:5000`. Execute o Flask separadamente. Não é necessário alterar arquivos Python.

Se o Flask estiver em outro endereço, configure `VITE_API_BASE_URL` em `.env` (veja `.env.example`) e reinicie o Vite. Esse valor deve apontar para a origem da API, não para `/api/v1`.

Endpoints utilizados:
- `POST /api/v1/search-company`
- `GET /api/v1/history?limit=50`
- `GET /api/v1/history/:id`

## Navegação e disponibilidade de dados

As cinco páginas podem ser abertas independentemente da pesquisa. Sem dados, as páginas mostram um estado vazio explicativo, em vez de redirecionar silenciosamente. O botão **Visualizar demonstração** apresenta informações **fictícias e explicitamente identificadas**, apenas para conferir o layout do Figma.

O backend atual retorna dados agregados e pode não fornecer processos individuais. Nesse caso, a tela Processos apresenta um estado vazio informativo, sem inventar números de processos. A tela Estimativas só apresenta probabilidades retornadas pela API ou dados fictícios no modo demonstração.

O histórico depende da disponibilidade do banco de dados no backend. Erros de conexão e de API são exibidos na interface.

## Adicionar ao repositório sem alterar o backend

Copie **somente esta pasta `frontend/`** para a raiz do repositório atualizado. Confira `git status`, depois:

```bash
git add frontend/
git diff --cached --name-only
git commit -m "fix(frontend): libera navegação e adiciona estados vazios e demonstração"
```

Não copie ou sobrescreva `app/`, `run.py`, `requirements.txt` ou outros arquivos do backend.


## Telas sem dados

As rotas visuais **Visão geral**, **Processos** e **Estimativas** permanecem acessíveis antes de qualquer pesquisa. Cada uma apresenta estrutura de cartões e painéis vazios, com indicação clara de que ainda não há dados. Nenhum valor fictício é tratado como resultado real. O botão **Visualizar exemplo do Figma** habilita separadamente o modo demonstração, identificado por aviso.

A ausência de uma integração com IA não impede a navegação: o frontend consome os endpoints Flask já existentes. A IA pode futuramente enriquecer as respostas da API, sem ser pré-requisito para renderizar as telas.
