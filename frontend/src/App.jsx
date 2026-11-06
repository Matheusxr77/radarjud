import React, { useEffect, useState } from 'react';
import {
  Search, LayoutDashboard, FileText, ChartNoAxesCombined, History, Scale,
  Menu, X, ArrowRight, RefreshCw, AlertCircle, ShieldCheck, ShieldAlert,
  Info, ChevronRight, Building2, TrendingUp, Filter, CheckCircle2,
} from 'lucide-react';

// Vite encaminha /api ao Flask. Defina VITE_API_BASE_URL se a API estiver em outro host.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const PAGE_LABELS = {
  search: 'Pesquisar empresas', overview: 'Visão geral', processes: 'Processos',
  predictions: 'Estimativas', history: 'Histórico',
};
const NAV_ITEMS = [
  { id: 'search', icon: Search }, { id: 'overview', icon: LayoutDashboard },
  { id: 'processes', icon: FileText }, { id: 'predictions', icon: ChartNoAxesCombined },
  { id: 'history', icon: History },
];
const OUTCOME_LABELS = {
  condenacoes: 'Condenações', acordos: 'Acordos',
  improcedentes: 'Improcedências', em_aberto: 'Em aberto',
};
const CHART_COLORS = ['#245a81', '#674b3c', '#b79b85', '#8998a7'];
const formatNumber = (value) => new Intl.NumberFormat('pt-BR').format(Number(value) || 0);
const formatPercent = (value) => `${Number(value || 0).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`;
const formatDate = (value) => {
  if (!value) return 'Não disponível';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Não disponível' : parsed.toLocaleDateString('pt-BR');
};
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function normalize(raw) {
  if (!isRecord(raw)) return null;
  const payload = isRecord(raw.data) ? raw.data : raw;
  return {
    name: payload.empresa || payload.company_name || 'Empresa consultada',
    total: Number(payload.total_processos ?? payload.total_processes ?? 0),
    reasons: Array.isArray(payload.ranking_motivos) ? payload.ranking_motivos : [],
    outcomes: isRecord(payload.desfechos_sentencas) ? payload.desfechos_sentencas
      : isRecord(payload.outcomes_summary) ? payload.outcomes_summary : {},
    secrecy: isRecord(payload.niveis_sigilo) ? payload.niveis_sigilo : null,
    predictions: Array.isArray(payload.plus_previsao_condenacao) ? payload.plus_previsao_condenacao : [],
    processes: Array.isArray(payload.processos) ? payload.processos
      : Array.isArray(payload.process_records) ? payload.process_records : [],
    searchId: payload.search_id ?? payload.id ?? null,
    searchedAt: payload.updated_at || payload.created_at || null,
  };
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch {
    throw new Error('Não foi possível conectar à API. Verifique se o Flask está em execução.');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(body?.error || body?.message || `Erro HTTP ${response.status}`);
  }
  if (body === null) throw new Error('A API não retornou JSON válido.');
  return body;
}

function Stat({ label, value, hint, icon: Icon, tone }) {
  return (
    <article className="stat card">
      <div className="stat-label">{label}<Icon size={18} /></div>
      <strong className={tone || ''}>{value}</strong>
      <span>{hint}</span>
    </article>
  );
}

function Empty({ title, description, icon: Icon = Info }) {
  return (
    <div className="empty" role="status">
      <Icon size={28} />
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

function Panel({ title, subtitle, children, action }) {
  return (
    <section className="card panel">
      <div className="panel-heading">
        <div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Tag({ children, type = 'blue' }) {
  return <span className={`tag ${type}`}>{children}</span>;
}

function Sidebar({ page, navigate, mobileOpen, setMobileOpen }) {
  return (
    <>
      <div className={`mobile-overlay ${mobileOpen ? 'visible' : ''}`} onClick={() => setMobileOpen(false)} />
      <aside className={`sidebar ${mobileOpen ? 'open' : ''}`}>
        <div className="brand">
          <div className="brand-mark"><Scale size={26} /></div>
          <div><strong>Radar<span>Jud</span></strong><small>INTELIGÊNCIA JURÍDICA</small></div>
          <button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Fechar menu"><X size={20} /></button>
        </div>
        <div className="nav-label">PLATAFORMA</div>
        <nav aria-label="Navegação principal">
          {NAV_ITEMS.map(({ id, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={`nav-item ${page === id ? 'active' : ''}`}
              aria-current={page === id ? 'page' : undefined}
              onClick={() => { navigate(id); setMobileOpen(false); }}
            >
              <Icon size={19} /><span>{PAGE_LABELS[id]}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="side-help">
            <ShieldCheck size={19} />
            <div><b>Dados com contexto</b><small>Metadados públicos não representam a totalidade dos processos.</small></div>
          </div>
          <div className="account">
            <span className="avatar">RJ</span>
            <div><b>RadarJud</b><small>Pesquisa jurídica trabalhista</small></div>
          </div>
        </div>
      </aside>
    </>
  );
}

function SearchPage({ onSearch, query, setQuery, busy, error, navigate, result }) {
  const features = [
    { icon: Building2, title: 'Visão da empresa', text: 'Indicadores consolidados de registros identificados.' },
    { icon: FileText, title: 'Assuntos e processos', text: 'Temas recorrentes e detalhes quando disponíveis.' },
    { icon: TrendingUp, title: 'Estimativas', text: 'Análises experimentais com limitações explícitas.' },
  ];
  return (
    <div className="search-page">
      <div className="search-hero">
        <div className="hero-icon"><Scale size={31} /></div>
        <div className="eyebrow">PESQUISA JURÍDICA TRABALHISTA</div>
        <h1>Informação jurídica para<br /><em>decisões mais seguras.</em></h1>
        <p>Consulte registros judiciais, explore assuntos recorrentes e compreenda os indicadores de uma empresa.</p>
        <form className="search-box" onSubmit={(event) => { event.preventDefault(); onSearch(); }}>
          <Search size={22} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Digite o nome da empresa" aria-label="Nome da empresa" required minLength={2} />
          <button className="primary" disabled={busy}>
            {busy ? <><RefreshCw size={17} className="spin" /> Consultando</> : <>Pesquisar <ArrowRight size={17} /></>}
          </button>
        </form>
        <div className="search-hint"><Info size={15} /> A pesquisa é feita por nome. Confirme a identificação da empresa antes de interpretar os resultados.</div>
        {error && <div className="alert error" role="alert"><AlertCircle size={18} />{error}</div>}
      </div>
      <div className="feature-grid">
        {features.map(({ icon: Icon, title, text }) => (
          <div className="feature card" key={title}>
            <div className="feature-icon"><Icon size={23} /></div>
            <h3>{title}</h3><p>{text}</p>
          </div>
        ))}
      </div>
      {result && (
        <div className="continue">
          <span>Última consulta: <b>{result.name}</b></span>
          <button className="text-button" onClick={() => navigate('overview')}>Ver análise <ArrowRight size={16} /></button>
        </div>
      )}
    </div>
  );
}

function Overview({ data, navigate }) {
  const secrecyEntries = data?.secrecy ? Object.entries(data.secrecy) : [];
  const observed = secrecyEntries.reduce((sum, [, count]) => sum + Number(count || 0), 0);
  const knownSecret = secrecyEntries.reduce((sum, [level, count]) => sum + (Number(level) > 0 ? Number(count || 0) : 0), 0);
  const reasons = data?.reasons || [];
  const outcomes = data?.outcomes || {};
  const hasData = Boolean(data);
  const classified = Object.values(outcomes).reduce((sum, value) => sum + Number(value || 0), 0);

  return (
    <div className="stack">
      <div className="section-intro">
        <div><div className="eyebrow">ANÁLISE DE EMPRESA</div><h1>{data?.name || 'Visão geral'}</h1><p>{hasData ? 'Panorama dos registros localizados nas fontes consultadas.' : 'Pesquise uma empresa para preencher os indicadores abaixo.'}</p></div>
        <Tag type={hasData ? 'blue' : 'amber'}>{hasData ? 'Dados da consulta' : 'Aguardando pesquisa'}</Tag>
      </div>
      <div className="stats">
        <Stat label="REGISTROS IDENTIFICADOS" value={hasData ? formatNumber(data.total) : '—'} hint="Não representa necessariamente todos os processos" icon={FileText} />
        <Stat label="ASSUNTOS IDENTIFICADOS" value={hasData ? formatNumber(reasons.length) : '—'} hint="Categorias presentes nos registros" icon={Filter} />
        <Stat label="DESFECHOS CLASSIFICADOS" value={hasData ? formatNumber(classified) : '—'} hint="Inclui processos em aberto" icon={CheckCircle2} />
        <Stat label="COBERTURA DO SIGILO" value={secrecyEntries.length ? 'Parcial' : 'Indisponível'} hint="Não permite medir sigilo total da empresa" icon={ShieldAlert} tone="amber" />
      </div>
      <div className="two-col">
        <Panel title="Assuntos mais recorrentes" subtitle="Frequência entre os registros identificados" action={<button className="text-button" onClick={() => navigate('processes')}>Ver processos <ArrowRight size={15} /></button>}>
          {reasons.length ? (
            <div className="bars">
              {reasons.slice(0, 6).map((reason, index) => {
                const percentage = Number(reason.porcentagem) || Number(reason.quantidade || 0) / Math.max(data.total, 1) * 100;
                return (
                  <div className="bar-item" key={`${reason.motivo}-${index}`}>
                    <div className="bar-top"><span title={reason.motivo}>{reason.motivo}</span><b>{formatNumber(reason.quantidade)}</b></div>
                    <div className="track"><div style={{ width: `${Math.max(0, Math.min(100, percentage))}%`, background: CHART_COLORS[index % CHART_COLORS.length] }} /></div>
                  </div>
                );
              })}
            </div>
          ) : <Empty title="Sem assuntos identificados" description="Os assuntos aparecerão aqui após uma pesquisa que retorne esses dados." />}
        </Panel>
        <Panel title="Desfechos dos registros" subtitle="Classificação produzida pelo backend">
          {Object.keys(outcomes).length ? (
            <div className="outcome-grid">
              {Object.entries(OUTCOME_LABELS).map(([key, label], index) => (
                <div className="outcome" key={key}>
                  <span className="outcome-dot" style={{ background: CHART_COLORS[index] }} />
                  <span>{label}</span><strong>{formatNumber(outcomes[key])}</strong>
                </div>
              ))}
            </div>
          ) : <Empty title="Sem desfechos disponíveis" description="Os desfechos aparecerão após uma consulta com classificações." />}
          <div className="muted-note"><Info size={15} /> Classificações automáticas podem exigir revisão humana.</div>
        </Panel>
      </div>
      <div className="two-col">
        <Panel title="Sigilo nos registros consultados" subtitle="Níveis oficiais dos metadados disponíveis">
          {secrecyEntries.length ? (
            <>
              <div className="secrecy-summary">
                <div><strong>{formatNumber(knownSecret)}</strong><span>registros com nível acima de zero</span></div>
                <div><strong>{formatNumber(observed)}</strong><span>registros com nível informado</span></div>
              </div>
              <div className="secrecy-levels">
                {[...secrecyEntries].sort(([a], [b]) => Number(a) - Number(b)).map(([level, count]) => (
                  <div className="secrecy-row" key={level}>
                    <span>Nível {level}</span>
                    <div className="track"><div style={{ width: `${Math.round(Number(count) / Math.max(observed, 1) * 100)}%`, background: Number(level) === 0 ? '#25865a' : Number(level) === 2 ? '#b67b23' : '#c34a4a' }} /></div>
                    <b>{count}</b>
                  </div>
                ))}
              </div>
            </>
          ) : <Empty title="Sigilo não informado" description="Não é possível calcular um indicador de sigilo sem dados da API." />}
          <div className="muted-note"><ShieldAlert size={16} /> Sigilo não é um indicador validado de confiabilidade empresarial. Processos não públicos podem estar ausentes.</div>
        </Panel>
        <Panel title="Próximos passos" subtitle="Como aprofundar a análise">
          <div className="next-steps">
            {[
              { page: 'search', icon: Search, title: 'Pesquisar empresa', text: 'Inicie uma consulta para preencher os painéis.' },
              { page: 'processes', icon: FileText, title: 'Explorar processos', text: 'Verifique números, assuntos e detalhes disponíveis.' },
              { page: 'predictions', icon: ChartNoAxesCombined, title: 'Consultar estimativas', text: 'Veja projeções experimentais por assunto.' },
            ].map(({ page, icon: Icon, title, text }) => (
              <button key={page} onClick={() => navigate(page)}>
                <Icon size={20} /><span><b>{title}</b><small>{text}</small></span><ChevronRight size={18} />
              </button>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Processes({ data }) {
  const [filter, setFilter] = useState('');
  const processes = data?.processes || [];
  const list = processes.filter((process) => JSON.stringify(process).toLowerCase().includes(filter.toLowerCase()));
  return (
    <div className="stack">
      <div className="section-intro"><div><div className="eyebrow">EMPRESA / PROCESSOS</div><h1>Processos e movimentações</h1><p>{data ? `${data.name} · detalhes fornecidos pela API.` : 'Aguardando consulta de uma empresa.'}</p></div></div>
      <Panel title="Registros individuais" subtitle="A pesquisa pode retornar apenas indicadores agregados">
        <div className="filter-row"><Search size={18} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filtrar por número, assunto ou desfecho" aria-label="Filtrar processos" disabled={!processes.length} /></div>
        {processes.length ? (
          <div className="table-scroll">
            <table>
              <thead><tr><th>Número do processo</th><th>Assunto</th><th>Desfecho</th><th>Sigilo</th><th>Último movimento</th></tr></thead>
              <tbody>{list.map((process, index) => (
                <tr key={process.id || process.numero_processo || index}>
                  <td className="mono">{process.numero_processo || process.process_number || 'Não informado'}</td>
                  <td>{Array.isArray(process.subjects) ? process.subjects.join(', ') : process.assunto || 'Não informado'}</td>
                  <td>{OUTCOME_LABELS[process.sentence_outcome] || process.sentence_outcome || 'Não informado'}</td>
                  <td>{process.nivelSigilo ?? process.secrecy_level ?? 'Não informado'}</td>
                  <td>{process.ultima_movimentacao || process.last_movement || 'Não disponível'}</td>
                </tr>
              ))}</tbody>
            </table>
            {!list.length && <Empty title="Nenhum resultado para o filtro" description="Experimente outro termo." />}
          </div>
        ) : <Empty icon={FileText} title="Nenhum processo disponível" description={data ? 'A API não forneceu registros individuais nesta consulta.' : 'A lista será preenchida quando uma pesquisa retornar processos individuais.'} />}
      </Panel>
      <div className="info-banner"><Info size={19} /> A interface exibe somente os processos efetivamente recebidos da API.</div>
    </div>
  );
}

function Predictions({ data }) {
  const predictions = data?.predictions || [];
  return (
    <div className="stack">
      <div className="section-intro"><div><div className="eyebrow">EMPRESA / ESTIMATIVAS</div><h1>Estimativas por assunto</h1><p>{data ? `Projeções exploratórias para ${data.name}.` : 'Aguardando dados de uma consulta.'}</p></div><Tag type="amber">Modelo experimental</Tag></div>
      <div className="info-banner warning"><AlertCircle size={20} /><div><b>Interpretação necessária</b><p>As probabilidades usam parâmetros heurísticos e não representam previsões estatisticamente validadas.</p></div></div>
      <Panel title="Estimativas disponíveis" subtitle="Dados retornados por plus_previsao_condenacao">
        {predictions.length ? (
          <div className="table-scroll">
            <table>
              <thead><tr><th>Assunto</th><th>Estimativa de condenação</th><th>Confiança calculada</th><th>Processos em aberto</th></tr></thead>
              <tbody>{predictions.map((prediction, index) => (
                <tr key={`${prediction.motivo}-${index}`}>
                  <td><b>{prediction.motivo || 'Não informado'}</b></td>
                  <td><span className="percent">{formatPercent(prediction.probabilidade_condenacao_pct)}</span></td>
                  <td>{formatPercent(prediction.confianca_previsao_pct)}</td>
                  <td>{formatNumber(prediction.processos_abertos)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <Empty title="Sem estimativas disponíveis" description="As estimativas serão exibidas quando a API retornar projeções por assunto." />}
      </Panel>
      <Panel title="Como usar estes resultados">
        <div className="explain-grid">
          <div><b>O que é mostrado</b><p>Estimativas por assunto com base nos registros encontrados e nas regras do backend.</p></div>
          <div><b>O que não significa</b><p>Não é garantia de condenação, avaliação de caráter da empresa ou substituição de parecer jurídico.</p></div>
          <div><b>Para produção</b><p>Validar as taxas-base, a calibração e a cobertura dos dados antes de decisões automatizadas.</p></div>
        </div>
      </Panel>
    </div>
  );
}

function HistoryPage({ onOpen, history, busy, error, onReload }) {
  return (
    <div className="stack">
      <div className="section-intro"><div><div className="eyebrow">PESQUISAS / HISTÓRICO</div><h1>Pesquisas anteriores</h1><p>Consulte resultados armazenados no backend.</p></div><button className="secondary" onClick={onReload} disabled={busy}><RefreshCw size={16} className={busy ? 'spin' : ''} /> Atualizar</button></div>
      {error && <div className="alert error" role="alert"><AlertCircle size={18} />{error}</div>}
      <Panel title="Consultas salvas" subtitle="Ordenadas pela atualização mais recente">
        {busy ? <div className="loading"><RefreshCw className="spin" /> Carregando histórico...</div> : history.length ? (
          <div className="history-list">{history.map((item, index) => (
            <button className="history-item" key={item.id ?? index} onClick={() => onOpen(item.id)} disabled={item.id == null}>
              <div className="history-icon"><Building2 size={20} /></div>
              <div className="history-name"><b>{item.company_name || 'Empresa não informada'}</b><small>Consultado em {formatDate(item.updated_at || item.created_at)}</small></div>
              <span>{formatNumber(item.total_processes)} registros</span><ChevronRight size={19} />
            </button>
          ))}</div>
        ) : <Empty icon={History} title="Nenhuma pesquisa salva" description="As pesquisas aparecerão aqui quando o backend persistir os resultados." />}
      </Panel>
    </div>
  );
}

function getPageFromHash() {
  const page = window.location.hash.replace(/^#\/?/, '').split('/')[0];
  return Object.hasOwn(PAGE_LABELS, page) ? page : 'search';
}

export default function App() {
  const [page, setPage] = useState(getPageFromHash);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState('');

  function navigate(destination) {
    if (!Object.hasOwn(PAGE_LABELS, destination)) return;
    setPage(destination);
    window.location.hash = `/${destination}`;
  }

  useEffect(() => {
    const syncHash = () => setPage(getPageFromHash());
    window.addEventListener('hashchange', syncHash);
    return () => window.removeEventListener('hashchange', syncHash);
  }, []);

  async function search() {
    if (query.trim().length < 2) return;
    setBusy(true);
    setError('');
    try {
      const result = await request('/search-company', {
        method: 'POST', body: JSON.stringify({ company_name: query.trim() }),
      });
      const normalized = normalize(result);
      if (!normalized) throw new Error('A API não retornou dados de empresa válidos.');
      setData(normalized);
      navigate('overview');
    } catch (failure) {
      setError(failure.message || 'Não foi possível pesquisar a empresa.');
    } finally {
      setBusy(false);
    }
  }

  async function loadHistory() {
    setHistoryBusy(true);
    setHistoryError('');
    try {
      const result = await request('/history?limit=50');
      setHistory(Array.isArray(result) ? result : Array.isArray(result.items) ? result.items : Array.isArray(result.history) ? result.history : []);
    } catch (failure) {
      setHistoryError(failure.message || 'Falha ao carregar histórico.');
    } finally {
      setHistoryBusy(false);
    }
  }

  async function openHistory(id) {
    setHistoryBusy(true);
    setHistoryError('');
    try {
      const result = await request(`/history/${encodeURIComponent(id)}`);
      const normalized = normalize(result);
      if (!normalized) throw new Error('A consulta salva não contém dados válidos.');
      setData(normalized);
      setQuery(normalized.name);
      navigate('overview');
    } catch (failure) {
      setHistoryError(failure.message || 'Não foi possível abrir a pesquisa.');
    } finally {
      setHistoryBusy(false);
    }
  }

  useEffect(() => {
    if (page === 'history') loadHistory();
  }, [page]);

  return (
    <div className="app">
      <Sidebar page={page} navigate={navigate} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} />
      <div className="main">
        <header className="topbar">
          <div className="topbar-left"><button className="menu-button" onClick={() => setMobileOpen(true)} aria-label="Abrir menu"><Menu size={23} /></button><span className="breadcrumb">RadarJud <ChevronRight size={14} /> <b>{PAGE_LABELS[page]}</b></span></div>
          <div className="region"><span className="region-dot" /> Pernambuco · TRT-6</div>
        </header>
        <main className="content">
          {page === 'search' && <SearchPage onSearch={search} query={query} setQuery={setQuery} busy={busy} error={error} navigate={navigate} result={data} />}
          {page === 'overview' && <Overview data={data} navigate={navigate} />}
          {page === 'processes' && <Processes data={data} />}
          {page === 'predictions' && <Predictions data={data} />}
          {page === 'history' && <HistoryPage history={history} busy={historyBusy} error={historyError} onOpen={openHistory} onReload={loadHistory} />}
        </main>
        <footer className="footer">RadarJud · Dados públicos e metadados judiciais · Informações sujeitas a limitações de cobertura</footer>
      </div>
    </div>
  );
}
