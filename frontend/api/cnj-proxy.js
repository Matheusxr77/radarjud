export const config = {
  runtime: 'edge',
  regions: ['gru1'], // Força execução física em São Paulo (Guarulhos - Brasil)
};

const ALLOWED_HOSTS = new Set([
  'comunicaapi.pje.jus.br',
  'api-publica.datajud.cnj.jus.br',
  'api.portaldatransparencia.gov.br',
]);

export default async function handler(req) {
  if (req.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, chave-api-dados, X-Target-Url',
      },
    });
  }

  const urlObj = new URL(req.url);
  const targetUrl = urlObj.searchParams.get('target') || req.headers.get('x-target-url');

  if (!targetUrl) {
    return new Response(JSON.stringify({ error: 'Missing target URL' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }

  let parsedTarget;
  try {
    parsedTarget = new URL(targetUrl);
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid target URL' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }

  if (!ALLOWED_HOSTS.has(parsedTarget.hostname)) {
    return new Response(JSON.stringify({ error: 'Host not allowed by proxy whitelist' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }

  const forwardHeaders = new Headers();
  forwardHeaders.set(
    'User-Agent',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
  );
  forwardHeaders.set('Accept', 'application/json, text/plain, */*');
  forwardHeaders.set('Accept-Language', 'pt-BR,pt;q=0.9,en-US;q=0.8');

  // Repassa headers de autenticação do DataJud (Authorization: APIKey ...) ou CGU (chave-api-dados)
  const authHeader = req.headers.get('authorization');
  if (authHeader) forwardHeaders.set('Authorization', authHeader);

  const cguHeader = req.headers.get('chave-api-dados');
  if (cguHeader) forwardHeaders.set('chave-api-dados', cguHeader);

  const contentType = req.headers.get('content-type');
  if (contentType) forwardHeaders.set('Content-Type', contentType);

  if (parsedTarget.hostname.includes('pje.jus.br')) {
    forwardHeaders.set('Origin', 'https://comunica.pje.jus.br');
    forwardHeaders.set('Referer', 'https://comunica.pje.jus.br/');
  }

  try {
    const bodyText =
      req.method !== 'GET' && req.method !== 'HEAD' ? await req.text() : undefined;

    const upstreamRes = await fetch(parsedTarget.toString(), {
      method: req.method,
      headers: forwardHeaders,
      body: bodyText,
    });

    const responseText = await upstreamRes.text();
    return new Response(responseText, {
      status: upstreamRes.status,
      headers: {
        'Content-Type': upstreamRes.headers.get('content-type') || 'application/json',
        'Access-Control-Allow-Origin': '*',
        'X-Proxy-Region': 'gru1-sp',
      },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }
}

