# Teste de navegação — RadarJud React

O frontend funciona mesmo com a API desligada. Abra `http://localhost:5173/#/overview`, `/#/processes` e `/#/predictions`.

## Instalação

```bash
cd frontend
npm install
npm run dev
```

Para verificar a compilação: `npm run build`.

## Correção

- A URL (`location.hash`) determina a página ativa.
- A falta de dados **não** redireciona para pesquisa.
- As páginas vazias têm cartões e painéis próprios.
- O backend Flask não foi incluído ou alterado.
- Use Ctrl+Shift+R para atualizar o navegador após substituir o frontend.

Se aparecer a tela antiga, pare todas as instâncias do Vite e reinicie o servidor na pasta `frontend` correta.
