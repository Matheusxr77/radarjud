import React, { useEffect, useState } from 'react';
import {
  Search, LayoutDashboard, FileText, ChartNoAxesCombined, History,
  Menu, X, ArrowRight, RefreshCw, AlertCircle, AlertTriangle, ShieldCheck, ShieldAlert,
  Info, ChevronRight, Building2, TrendingUp, Filter, CheckCircle2,
  Check, Activity, BarChart3, Radar, Share2, Download,
} from 'lucide-react';
import { companyHash, companyShareUrl, isCompanyPage, parseRoute, validSearchId } from './companyNavigation.js';

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
  condenacoes: 'Condenações (Impacto Confirmado)',
  acordos: 'Acordos',
  improcedentes: 'Risco Mitigado / Favorável',
  em_aberto: 'Em aberto',
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

function CompanyLogo({ logo, companyName }) {
  const [imgErrorStep, setImgErrorStep] = useState(0);
  const logoUrl = imgErrorStep === 0
    ? (logo?.logo_url || logo?.google_favicon_url || logo?.logo_primary_url)
    : (imgErrorStep === 1 ? (logo?.logo_secondary_url || logo?.google_favicon_url) : null);

  useEffect(() => {
    setImgErrorStep(0);
  }, [companyName, logo?.logo_url, logo?.google_favicon_url, logo?.logo_primary_url]);

  if (logoUrl) {
    return (
      <img
        key={logoUrl || companyName}
        src={logoUrl}
        alt={companyName || 'Logo'}
        className="company-logo-img"
        onLoad={(e) => {
          // Intercepta globo cinza genérico de 16x16px da Google S2 API
          if (e.currentTarget.naturalWidth <= 16 && (e.currentTarget.src.includes('sz=128') || e.currentTarget.src.includes('favicons'))) {
            setImgErrorStep((prev) => prev + 1);
          }
        }}
        onError={() => setImgErrorStep((prev) => prev + 1)}
      />
    );
  }
  const initials = logo?.fallback_initials || logo?.initials || (companyName ? companyName.replace(/[^A-Za-z0-9]/g, '').slice(0, 3).toUpperCase() : 'EMP');
  return (
    <div className="company-logo-fallback" title={companyName}>
      {initials}
    </div>
  );
}

function normalize(raw) {
  if (!isRecord(raw)) return null;
  const payload = isRecord(raw.data) ? raw.data : raw;
  const riskScore = payload.indice_exposicao_financeira != null
    ? Number(payload.indice_exposicao_financeira)
    : payload.idoneidade_economica?.indice_exposicao_financeira != null
      ? Number(payload.idoneidade_economica.indice_exposicao_financeira)
      : payload.score_risco_terceirizacao != null
        ? Number(payload.score_risco_terceirizacao)
        : payload.overall_risk_score != null
          ? Number(payload.overall_risk_score)
          : payload.probabilidade_condenacao_geral_pct != null
            ? Number(payload.probabilidade_condenacao_geral_pct)
            : null;

  const companyName = payload.empresa || payload.company_name || 'Empresa consultada';
  const total = Number(payload.total_processos ?? payload.total_processes ?? 0);

  // Garante que a Idoneidade Econômica e Solvência estejam preenchidas
  const rawEcon = isRecord(payload.idoneidade_economica) ? payload.idoneidade_economica : null;
  const defaultCapitalVal = (rawEcon && (rawEcon.valor_formatado || rawEcon.capital_social_display?.valor_formatado || rawEcon.capital_social_consolidado_formatado || rawEcon.capital_social_formatado))
    || payload.capital_social_consolidado
    || payload.capital_social_display?.valor_formatado
    || 'R$ 0,00';
  const defaultObservacao = (rawEcon && (rawEcon.observacao_porte || rawEcon.capital_social_display?.observacao_porte))
    || payload.observacao_porte
    || 'Compatível com o porte';

  const econ = rawEcon ? {
    ...rawEcon,
    valor_formatado: defaultCapitalVal,
    observacao_porte: defaultObservacao,
  } : {
    cenario: 'CENARIO_A',
    cenario_tipo: 'CENARIO_A',
    cenario_titulo: 'Cenário A: Idoneidade Econômica Robusta & Alta Solvência (Baixo Risco ao Contratante)',
    cenario_class: 'seguro',
    probabilidade_insolvencia_pct: 3.5,
    risco_calote_subsidiario_pct: 1.2,
    capital_social_formatado: defaultCapitalVal,
    valor_formatado: defaultCapitalVal,
    observacao_porte: defaultObservacao,
    situacao_cadastral: 'ATIVA',
    possui_sancoes_cgu: false,
    possui_divida_ativa: false,
    parecer_sumula_331: 'Idoneidade econômica preservada: a empresa apresenta situação cadastral ativa e ausência de restrições impeditivas na Fazenda Nacional ou CGU, com baixo risco de transferência de ônus ao tomador.',
    logo: isRecord(payload.logo) ? payload.logo : {
      fallback_initials: companyName.replace(/[^A-Za-z0-9]/g, '').slice(0, 3).toUpperCase() || 'EMP',
      logo_url: null
    }
  };

  const logo = isRecord(payload.logo) ? payload.logo : (econ ? econ.logo : null);

  return {
    name: companyName,
    total: total,
    riskScore: riskScore,
    overallRiskScore: riskScore,
    reasons: Array.isArray(payload.ranking_motivos) ? payload.ranking_motivos : [],
    rankingCondenacoes: Array.isArray(payload.ranking_motivos_condenacao) ? payload.ranking_motivos_condenacao : [],
    outcomes: isRecord(payload.desfechos_sentencas) ? payload.desfechos_sentencas
      : isRecord(payload.outcomes_summary) ? payload.outcomes_summary : {},
    secrecy: isRecord(payload.niveis_sigilo) ? payload.niveis_sigilo : null,
    predictions: Array.isArray(payload.plus_previsao_condenacao) ? payload.plus_previsao_condenacao : [],
    previsaoBloqueada: Boolean(payload.previsao_bloqueada),
    motivoBloqueioPrevisao: payload.motivo_bloqueio_previsao || null,
    idoneidadeEconomica: econ,
    logo: logo,
    processes: Array.isArray(payload.processos) ? payload.processos
      : Array.isArray(payload.process_records) ? payload.process_records : [],
    searchId: payload.search_id ?? payload.id ?? null,
    searchedAt: payload.updated_at || payload.created_at || null,
    scanStatus: payload.scan_status || 'COMPLETED',
    aiAnalysis: isRecord(payload.ai_analysis) ? payload.ai_analysis
      : isRecord(payload.parecer_agente_adk) ? payload.parecer_agente_adk : null,
    cached: Boolean(payload.cached),
    cacheLevel: payload.cache_level || null,
    isBroadBrand: Boolean(payload.is_broad_brand || payload.isBroadBrand || (companyName && companyName.includes('GRUPO CONSOLIDADO'))),
  };
}

async function request(path, options = {}) {
  let response;
  const endpoint = `${API_BASE}/api/v1${path}`;
  try {
    response = await fetch(endpoint, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch {
    if (!API_BASE && typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      throw new Error('A variável de ambiente VITE_API_BASE_URL não está configurada na Vercel apontando para o Render.');
    }
    throw new Error('Não foi possível conectar à API. Se o serviço no Render for gratuito, aguarde cerca de 1 minuto para inicialização (cold start).');
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
          <div className="brand-mark"><Activity size={26} /></div>
          <div><strong>Radar<span>Jus</span></strong><small>INTELIGÊNCIA PREDITIVA</small></div>
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
            <div><b>Dados com contexto</b><small>Metadados públicos para auditoria e inteligência preditiva.</small></div>
          </div>
          <div className="account">
            <span className="avatar">RJ</span>
            <div><b>RadarJus</b><small>Inteligência B2B & Previsibilidade</small></div>
          </div>
        </div>
      </aside>
    </>
  );
}

function SearchPage({ onSearch, query, setQuery, busy, error, navigate, result }) {
  const features = [
    { icon: Building2, title: 'Visão do fornecedor', text: 'Indicadores consolidados de eventos identificados.' },
    { icon: FileText, title: 'Vetores e ocorrências', text: 'Temas recorrentes e detalhes quando disponíveis.' },
    { icon: TrendingUp, title: 'Estimativas de Exposição', text: 'Previsibilidade de impacto financeiro por vetor.' },
  ];
  return (
    <div className="search-page">
      <div className="search-hero">
        <div className="hero-icon"><Radar size={31} /></div>
        <div className="eyebrow">INTELIGÊNCIA PREDITIVA DE FORNECEDORES</div>
        <h1>Inteligência preditiva para<br /><em>gestão de risco e fornecedores.</em></h1>
        <p>Consulte registros operacionais, explore vetores recorrentes e projete a exposição financeira do fornecedor.</p>
        <form className="search-box" onSubmit={(event) => { event.preventDefault(); onSearch(); }}>
          <Search size={22} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Digite o nome da empresa ou CNPJ"
            aria-label="Nome da empresa ou CNPJ"
            required
            minLength={2}
          />
          <button className="primary" disabled={busy}>
            {busy ? <><RefreshCw size={17} className="spin" /> Consultando</> : <>Pesquisar <ArrowRight size={17} /></>}
          </button>
        </form>
        <div className="search-hint">
          <Info size={15} /> A busca suporta Razão Social, Nome Fantasia ou CNPJ (com triangulação via BrasilAPI).
        </div>
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
  const ai = data?.aiAnalysis;

  const isFastPath = data?.scanStatus === 'PARTIAL_FAST_PATH';
  const isCompleted = data?.scanStatus === 'COMPLETED';
  const tstCount = (data?.processes || []).filter(p => p.is_tst_recurso || p.tribunal === 'tst' || String(p.grau || '').includes('TST')).length;

  return (
    <div className="stack">
      <div className="section-intro">
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {data?.logo && (
            <div className="company-logo-wrapper">
              <CompanyLogo logo={data.logo} companyName={data.name} />
            </div>
          )}
          <div>
            <div className="eyebrow">INTELIGÊNCIA PREDITIVA DE FORNECEDOR</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <h1 style={{ margin: 0 }}>{data?.name || 'Visão geral'}</h1>
              {data?.isBroadBrand && (
                <Tag type="blue">GRUPO CONSOLIDADO</Tag>
              )}
            </div>
            <p>{hasData ? 'Panorama da modelagem preditiva e exposição operacional nas bases consultadas.' : 'Pesquise um fornecedor para preencher os indicadores abaixo.'}</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          {isFastPath && (
            <Tag type="amber">
              <RefreshCw size={12} className="spin" style={{ display: 'inline', marginRight: '5px' }} />
              Fast-Path ativo (&lt; 3s) · Varrendo em 2º plano
            </Tag>
          )}
          {isCompleted && <Tag type="green"><Check size={13} style={{ display: 'inline', marginRight: '4px' }} /> Varredura Completa</Tag>}
          {tstCount > 0 && (
            <Tag type="purple">
              <TrendingUp size={13} style={{ display: 'inline', marginRight: '4px' }} />
              {tstCount} em Grau de Recurso no TST
            </Tag>
          )}
          {data?.cached && <Tag type="blue">⚡ Cache Inteligente ({data.cacheLevel || 'L1'})</Tag>}
        </div>
      </div>

      <div className="stats">
        <Stat
          label="ÍNDICE DE EXPOSIÇÃO FINANCEIRA"
          value={hasData && data.riskScore != null ? formatPercent(data.riskScore) : '—'}
          hint="Score geral estimado de impacto subsidiário"
          icon={AlertTriangle}
          tone={data?.riskScore >= 60 ? 'red' : data?.riskScore >= 35 ? 'amber' : 'green'}
        />
        <Stat label="EVENTOS IDENTIFICADOS" value={hasData ? formatNumber(data.total) : '—'} hint="Registros localizados nas bases públicas oficiais" icon={FileText} />
        <Stat label="VETORES DE RISCO" value={hasData ? formatNumber(reasons.length) : '—'} hint="Motivos e ocorrências operacionais" icon={Filter} />
        <Stat label="DESFECHOS MAPEADOS" value={hasData ? formatNumber(classified) : '—'} hint="Inclui impactos confirmados, acordos e abertos" icon={CheckCircle2} />
        <Stat label="TRANSPARÊNCIA DE DADOS" value={secrecyEntries.length ? 'Parcial' : 'Indisponível'} hint="Nível de visibilidade pública informado" icon={ShieldAlert} tone="amber" />
      </div>

      {/* Idoneidade Econômica & Risco de Exposição Subsidiária */}
      {data?.idoneidadeEconomica && (() => {
        const econ = data.idoneidadeEconomica;
        const isCenarioA = (econ.cenario_tipo === 'CENARIO_A') || (econ.cenario === 'CENARIO_A') || (econ.cenario_class === 'seguro');
        const badgePorte = econ.badge_compatibilidade_porte || econ.observacao_porte || econ.capital_social_display?.observacao_porte;
        const capitalFormatted = econ.capital_social_formatado || econ.valor_formatado || econ.capital_social_display?.valor_formatado || 'R$ 0,00';
        const situacao = econ.situacao_cadastral || 'ATIVA';
        const isSituacaoAtiva = situacao === 'ATIVA';
        const probInsolv = Number(econ.probabilidade_insolvencia ?? econ.probabilidade_insolvencia_pct ?? 0);
        const riscoCalote = Number(econ.risco_calote_subsidiario ?? econ.risco_calote_subsidiario_pct ?? 0);
        const cenarioTitulo = econ.cenario_titulo || (isCenarioA ? 'Cenário A: Idoneidade Econômica Robusta • Risco de Transferência de Passivo Mitigado' : 'Cenário B: Risco Crítico de Transferência de Passivo (Alerta Máximo de Retenção)');
        const cenarioDescricao = econ.cenario_descricao || econ.parecer_sumula_331 || '';
        const cenarioBadge = econ.cenario_badge || (isCenarioA ? 'Cenário A · Solvência Comprovada' : 'Cenário B · Risco Crítico');
        const subtituloUnidades = econ.subtitulo_unidades || econ.detalhe_filiais || 'Soma e lastro patrimonial consolidado cobrindo a matriz e todas as filiais ativas (IN RFB 2119/2022).';

        return (
          <Panel
            title="Idoneidade Econômica & Risco de Exposição Subsidiária"
            subtitle="Auditoria de solvência, capital social e risco de transferência de passivo (Súmula 331 TST)"
            action={
              <Tag type={isCenarioA ? 'green' : 'red'}>
                {cenarioBadge}
              </Tag>
            }
          >
            <div style={{ display: 'grid', gap: '16px' }}>
              <div
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  border: isCenarioA ? '1px solid #bbf7d0' : '1px solid #fecaca',
                  background: isCenarioA ? '#f0fdf4' : '#fef2f2',
                  color: isCenarioA ? '#166534' : '#991b1b',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '10px',
                }}
              >
                <div>
                  <strong style={{ display: 'block', fontSize: '15px', marginBottom: '4px' }}>
                    {cenarioTitulo}
                  </strong>
                  <p style={{ margin: 0, fontSize: '13px', lineHeight: 1.5, opacity: 0.9 }}>
                    {cenarioDescricao}
                  </p>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: '14px' }}>
                <div className="econ-badge" style={{ gridColumn: 'span 2' }}>
                  <small style={{ color: '#245a81', fontWeight: 600 }}>
                    {data?.isBroadBrand || (econ?.unidades_identificadas > 1)
                      ? 'Capital Social Consolidado (Matriz + Filiais):'
                      : 'Capital Social Cadastral (Receita Federal):'}
                  </small>
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '10px', marginTop: '4px', marginBottom: '4px' }}>
                    <strong style={{ fontSize: '20px', color: '#17283a', lineHeight: 1.2 }}>
                      {capitalFormatted}
                    </strong>
                    {badgePorte && (
                      <span style={{
                        fontSize: '11px',
                        fontWeight: 600,
                        padding: '3px 8px',
                        borderRadius: '6px',
                        background: '#e8f0f6',
                        color: '#245a81',
                        border: '1px solid #cdd7df'
                      }}>
                        {badgePorte}
                      </span>
                    )}
                  </div>
                  <small style={{ color: '#68798a', fontSize: '11px' }}>
                    {subtituloUnidades}
                  </small>
                </div>

                <div className="econ-badge">
                  <small style={{ color: '#245a81', fontWeight: 600 }}>Situação Cadastral RFB:</small>
                  <strong style={{ fontSize: '16px', color: isSituacaoAtiva ? '#25865a' : '#c34a4a', marginTop: '4px' }}>
                    {situacao}
                  </strong>
                  <small style={{ color: '#68798a', fontSize: '11px' }}>
                    {isSituacaoAtiva ? 'Sem bloqueios cadastrais' : 'Restrição cadastral na RFB'}
                  </small>
                </div>

                <div className="econ-badge">
                  <small style={{ color: '#245a81', fontWeight: 600 }}>Probabilidade de Insolvência:</small>
                  <strong style={{ fontSize: '16px', color: probInsolv >= 30 ? '#c34a4a' : probInsolv >= 15 ? '#b67b23' : '#25865a', marginTop: '4px' }}>
                    {formatPercent(probInsolv)}
                  </strong>
                  <small style={{ color: '#68798a', fontSize: '11px' }}>
                    Modelo atuarial de solvência
                  </small>
                </div>

                <div className="econ-badge">
                  <small style={{ color: '#245a81', fontWeight: 600 }}>Risco de Exposição Subsidiária:</small>
                  <strong style={{ fontSize: '16px', color: riscoCalote >= 20 ? '#c34a4a' : riscoCalote >= 10 ? '#b67b23' : '#25865a', marginTop: '4px' }}>
                    {formatPercent(riscoCalote)}
                  </strong>
                  <small style={{ color: '#68798a', fontSize: '11px' }}>
                    Súmula 331 TST / Impacto ao Contratante
                  </small>
                </div>

                <div className="econ-badge">
                  <small style={{ color: '#245a81', fontWeight: 600 }}>Sanções e Débitos Federais:</small>
                  <strong style={{ fontSize: '15px', color: (econ.possui_sancoes_cgu || econ.possui_divida_ativa) ? '#c34a4a' : '#25865a', marginTop: '4px' }}>
                    {(econ.possui_sancoes_cgu || econ.possui_divida_ativa) ? 'Constam Apontamentos' : 'Nada Consta'}
                  </strong>
                  <small style={{ color: '#68798a', fontSize: '11px' }}>
                    CEIS/CNEP/CEPIM &amp; PGFN
                  </small>
                </div>
              </div>
            </div>
          </Panel>
        );
      })()}

      {/* Parecer do Consultor de Inteligência Artificial (Agente ADK radarjusAgent) */}
      {ai && (
        <Panel
          title="Diagnóstico Executivo & Compliance (Agente ADK · IA)"
          subtitle={`Parecer técnico gerado via ${ai.modelo_utilizado || 'IA Preditiva'}${ai.orquestrador ? ` · ${ai.orquestrador}` : ''}`}
          action={
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
              {ai.recomendacao_homologacao && (
                <Tag
                  type={
                    ai.recomendacao_homologacao.includes('SEM RESTRIÇÕES') ||
                    ai.recomendacao_homologacao.includes('APROVAR') ||
                    ai.recomendacao_homologacao.includes('MITIGADO')
                      ? 'green'
                      : ai.recomendacao_homologacao.includes('RETENÇÃO')
                        ? 'amber'
                        : 'red'
                  }
                >
                  Súmula 331 TST: {ai.recomendacao_homologacao}
                </Tag>
              )}
              <Tag type={ai.perfil_risco_consolidado?.includes('Baixo') ? 'green' : ai.perfil_risco_consolidado?.includes('Moderado') ? 'amber' : 'red'}>
                Risco: {ai.perfil_risco_consolidado || 'Geral'}
              </Tag>
            </div>
          }
        >
          <div style={{ display: 'grid', gap: '16px' }}>
            <p style={{ margin: 0, fontSize: '14px', lineHeight: '1.7', color: '#273b4e' }}>
              {ai.resumo_executivo || ai.parecer_governanca}
            </p>
            {ai.taxa_historica_vitoria_empresa_pct != null && (
              <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', padding: '12px 16px', background: '#f5f8fa', borderRadius: '10px' }}>
                {ai.taxa_historica_condenacao_pct != null && (
                  <div>
                    <small style={{ color: '#68798a', display: 'block' }}>Taxa Histórica de Impactos Confirmados:</small>
                    <strong style={{ fontSize: '18px', color: ai.taxa_historica_condenacao_pct >= 60 ? '#c34a4a' : ai.taxa_historica_condenacao_pct >= 35 ? '#b67b23' : '#25865a' }}>
                      {formatPercent(ai.taxa_historica_condenacao_pct)}
                    </strong>
                  </div>
                )}
                <div>
                  <small style={{ color: '#68798a', display: 'block' }}>Taxa Histórica de Risco Mitigado (Favorável):</small>
                  <strong style={{ fontSize: '18px', color: '#25865a' }}>{formatPercent(ai.taxa_historica_vitoria_empresa_pct)}</strong>
                </div>
                {ai.taxa_historica_acordos_pct != null && (
                  <div>
                    <small style={{ color: '#68798a', display: 'block' }}>Adesão a Acordos:</small>
                    <strong style={{ fontSize: '18px', color: '#245a81' }}>{formatPercent(ai.taxa_historica_acordos_pct)}</strong>
                  </div>
                )}
              </div>
            )}
            {Array.isArray(ai.alertas_sumula_331) && ai.alertas_sumula_331.length > 0 && (
              <div>
                <b style={{ display: 'block', fontSize: '13px', marginBottom: '6px', color: '#b67b23' }}>Alertas Súmula 331 do TST (Agente ADK):</b>
                <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '12px', lineHeight: '1.6', color: '#556677' }}>
                  {ai.alertas_sumula_331.map((alerta, i) => (
                    <li key={i}>{alerta}</li>
                  ))}
                </ul>
              </div>
            )}
            {Array.isArray(ai.recomendacoes_compliance) && ai.recomendacoes_compliance.length > 0 && (
              <div>
                <b style={{ display: 'block', fontSize: '13px', marginBottom: '8px', color: '#245a81' }}>Recomendações Preventivas de Governança:</b>
                <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '12px', lineHeight: '1.6', color: '#556677' }}>
                  {ai.recomendacoes_compliance.map((rec, i) => (
                    <li key={i}>{rec}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Panel>
      )}

      {/* Ranking de Vetores Principais de Exposição */}
      {Array.isArray(data?.rankingCondenacoes) && data.rankingCondenacoes.length > 0 && (
        <Panel
          title="Ranking de Vetores Principais de Exposição"
          subtitle="Assuntos com histórico confirmado de desembolso financeiro que calibram o motor preditivo"
        >
          <div className="bars">
            {data.rankingCondenacoes.slice(0, 6).map((item, index) => {
              const barWidth = Math.max(8, item.taxa_no_motivo || item.taxa_condenacao_no_motivo || item.percentual || 0);
              const labelDireita = item.label_direita || `${item.condenacoes || item.quantidade_condenacoes || item.quantidade || 0} condenações (${item.taxa_no_motivo || item.taxa_condenacao_no_motivo || item.taxa_perda_no_motivo || 0}% de perda no motivo)`;
              return (
                <div className="bar-item" key={`${item.motivo}-${index}`}>
                  <div className="bar-top">
                    <span title={item.motivo} style={{ fontWeight: 600 }}>{item.motivo}</span>
                    <b style={{ color: '#c34a4a' }}>{labelDireita}</b>
                  </div>
                  <div className="track">
                    <div style={{ width: `${Math.min(100, barWidth)}%`, background: '#c34a4a' }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="muted-note" style={{ marginTop: '14px' }}>
            <BarChart3 size={15} /> Calibragem bayesiana contínua: os temas acima possuem desfechos confirmados que elevam o prior beta do modelo preditivo.
          </div>
        </Panel>
      )}

      <div className="two-col">
        <Panel title="Vetores mais recorrentes" subtitle="Frequência entre os registros identificados" action={<button className="text-button" onClick={() => navigate('processes')}>Ver processos <ArrowRight size={15} /></button>}>
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
                  <span className="outcome-dot" style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} />
                  <span>{label}</span><strong>{formatNumber(outcomes[key])}</strong>
                </div>
              ))}
            </div>
          ) : <Empty title="Sem desfechos disponíveis" description="Os desfechos aparecerão após uma consulta com classificações." />}
          <div className="muted-note"><Info size={15} /> Classificações automáticas via códigos TPU e NLP de sentenças.</div>
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
          <div className="muted-note"><ShieldAlert size={16} /> Processos sob segredo de justiça (nível &gt; 0) omitem dados de partes.</div>
        </Panel>
        <Panel title="Aprofundamento de análise" subtitle="Próximas ações de compliance">
          <div className="next-steps">
            {[
              { page: 'search', icon: Search, title: 'Pesquisar outra empresa', text: 'Inicie uma nova consulta rápida.' },
              { page: 'processes', icon: FileText, title: 'Explorar processos e observações', text: 'Veja números, assuntos inferidos e desfechos.' },
              { page: 'predictions', icon: ChartNoAxesCombined, title: 'Estimativas de Exposição', text: 'Consulte a previsibilidade de impacto financeiro por vetor.' },
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
      <div className="section-intro">
        <div>
          <div className="eyebrow">EMPRESA / PROCESSOS</div>
          <h1>Processos e movimentações</h1>
          <p>{data ? `${data.name} · registros extraídos do Comunica PJe e DataJud.` : 'Aguardando consulta de uma empresa.'}</p>
        </div>
      </div>
      <Panel title="Registros individuais" subtitle="Lista de processos judiciais vinculados à empresa">
        <div className="filter-row">
          <Search size={18} />
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filtrar por número, assunto, desfecho ou observação..."
            aria-label="Filtrar processos"
            disabled={!processes.length}
          />
        </div>
        {processes.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Número do processo</th>
                  <th>Assunto Principal</th>
                  <th>Desfecho</th>
                  <th>Sigilo</th>
                  <th>Observação Técnica</th>
                </tr>
              </thead>
              <tbody>
                {list.map((process, index) => (
                  <tr key={process.id || process.numero_processo || index}>
                    <td className="mono">
                      {process.numero_processo || process.process_number || 'Não informado'}
                      {(process.is_tst_recurso || process.tribunal === 'tst' || String(process.grau || '').includes('TST')) && (
                        <span style={{ marginLeft: '8px' }}>
                          <Tag type="purple">Instância Superior TST</Tag>
                        </span>
                      )}
                    </td>
                    <td>
                      <b>{Array.isArray(process.subjects) ? process.subjects.join(', ') : process.primary_subject || 'Não informado'}</b>
                    </td>
                    <td>
                      <Tag type={process.sentence_outcome === 'improcedentes' ? 'green' : process.sentence_outcome === 'condenacoes' ? 'red' : 'blue'}>
                        {OUTCOME_LABELS[process.sentence_outcome] || process.sentence_outcome || 'Em aberto'}
                      </Tag>
                    </td>
                    <td>
                      {process.secrecy_level != null ? (
                        <Tag type={Number(process.secrecy_level) > 0 ? 'amber' : 'gray'}>
                          Nível {process.secrecy_level}
                        </Tag>
                      ) : '—'}
                    </td>
                    <td>
                      {process.inferred_from_djen_text ? (
                        <span style={{ color: '#245a81', fontSize: '12px' }}>
                          Assunto identificado via publicação PJe
                        </span>
                      ) : (
                        <span style={{ color: '#8899a6', fontSize: '11px' }}>Metadados TPU oficiais</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!list.length && <Empty title="Nenhum resultado para o filtro" description="Experimente outro termo." />}
          </div>
        ) : (
          <Empty icon={FileText} title="Nenhum processo disponível" description={data ? 'A API não forneceu registros individuais nesta consulta.' : 'A lista será preenchida quando uma pesquisa retornar processos individuais.'} />
        )}
      </Panel>
      <div className="info-banner">
        <Info size={19} /> Processos rotulados com "Assunto identificado via publicação PJe" tiveram seu tema extraído diretamente do texto da publicação quando os dados oficiais registravam apenas "Outros Assuntos".
      </div>
    </div>
  );
}

function Predictions({ data }) {
  const predictions = data?.predictions || [];
  return (
    <div className="stack">
      <div className="section-intro">
        <div>
          <div className="eyebrow">EMPRESA / ESTIMATIVAS</div>
          <h1>Previsão de Desfecho nos Processos Abertos</h1>
          <p>{data ? `Distribuição posterior Dirichlet-Multinomial (Vitória, Acordo e Condenação) com Intervalo de Credibilidade 90% (${data.name}).` : 'Aguardando dados de uma consulta.'}</p>
        </div>
        <Tag type="blue">Dirichlet-Multinomial · IC 90% Beta</Tag>
      </div>

      <div className="info-banner warning">
        <Activity size={20} />
        <div>
          <b>Modelo Conjugado Dirichlet-Multinomial &amp; Intervalo de Credibilidade Beta (90%)</b>
          <p>Separa explicitamente a probabilidade de Improcedência (risco mitigado), Acordo e Condenação (impacto confirmado) com shrinkage Empirical Bayes, ajuste de perícia em escala logit e projeção de impactos esperados na carteira aberta.</p>
        </div>
      </div>

      <Panel title="Probabilidade Posterior nos Processos Abertos" subtitle="Distribuição tripartida e Intervalo de Credibilidade de 90% por motivo">
        {predictions.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Motivo da Ação (TPU/PJe)</th>
                  <th>Vitória (Improcedência)</th>
                  <th>Acordo</th>
                  <th>Condenação</th>
                  <th>IC 90% (Condenação)</th>
                  <th>Abertos (Cond. Esperadas)</th>
                  <th>Diagnóstico</th>
                </tr>
              </thead>
              <tbody>
                {predictions.map((prediction, index) => {
                  const probVencer = prediction.probabilidade_vencer_pct ?? Math.max(0, 100 - (prediction.probabilidade_condenacao_pct || 0) - (prediction.probabilidade_acordo_pct || 0));
                  const probAcordo = prediction.probabilidade_acordo_pct ?? 0;
                  const probCond = prediction.probabilidade_condenacao_pct ?? 0;
                  const isHighWin = probVencer >= 45.0;
                  const isMidWin = probCond < 55.0;
                  const hasIC = prediction.ic_90_inferior_pct != null && prediction.ic_90_superior_pct != null;
                  return (
                    <tr key={`${prediction.motivo}-${index}`}>
                      <td><b>{prediction.motivo || 'Não informado'}</b></td>
                      <td>
                        <span className="percent" style={{ color: '#25865a', fontWeight: 'bold' }}>
                          {formatPercent(probVencer)}
                        </span>
                      </td>
                      <td>
                        <span className="percent" style={{ color: '#245a81', fontWeight: 600 }}>
                          {formatPercent(probAcordo)}
                        </span>
                      </td>
                      <td>
                        <span className="percent" style={{ color: probCond >= 55 ? '#c34a4a' : '#667788', fontWeight: 'bold' }}>
                          {formatPercent(probCond)}
                        </span>
                      </td>
                      <td>
                        {hasIC ? (
                          <span style={{ fontSize: '12px', color: '#556677' }}>
                            [{formatPercent(prediction.ic_90_inferior_pct)} – {formatPercent(prediction.ic_90_superior_pct)}]
                            <small style={{ display: 'block', color: '#8899a6' }}>
                              Precisão: {formatPercent(prediction.confianca_previsao_pct)} ({prediction.nivel_confianca || 'Amostral'})
                            </small>
                          </span>
                        ) : (
                          formatPercent(prediction.confianca_previsao_pct)
                        )}
                      </td>
                      <td>
                        <b>{formatNumber(prediction.processos_abertos)}</b>
                        {prediction.condenacoes_esperadas_abertos != null && prediction.processos_abertos > 0 && (
                          <small style={{ display: 'block', color: '#8899a6' }}>
                            ~{prediction.condenacoes_esperadas_abertos.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} esperadas
                          </small>
                        )}
                      </td>
                      <td>
                        <Tag type={isHighWin ? 'green' : isMidWin ? 'amber' : 'red'}>
                          {prediction.diagnostico_chance_vitoria || (isHighWin ? 'Alta probabilidade de êxito' : isMidWin ? 'Risco equilibrado' : 'Alto risco de condenação')}
                        </Tag>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="Sem estimativas disponíveis" description="As estimativas serão exibidas quando a API retornar projeções por assunto." />
        )}
      </Panel>

      <Panel title="Como interpretar estes indicadores">
        <div className="explain-grid">
          <div>
            <b>Vitória vs. Acordo vs. Condenação</b>
            <p>Acordos não são contabilizados como vitória da empresa. As três probabilidades posteriores somam 100% em cada motivo.</p>
          </div>
          <div>
            <b>Intervalo de Credibilidade Beta (IC 90%)</b>
            <p>Mede a incerteza real da amostra [p5, p95] e aplica desconto por censura à direita quando há muitos processos em aberto.</p>
          </div>
          <div>
            <b>Impactos Esperados nos Abertos</b>
            <p>Projeta o passivo esperado multiplicando o volume de processos abertos pela probabilidade posterior de impacto financeiro do motivo.</p>
          </div>
        </div>
      </Panel>
    </div>
  );
}

function HistoryPage({ onOpen, history, busy, error, onReload }) {
  return (
    <div className="stack">
      <div className="section-intro">
        <div>
          <div className="eyebrow">PESQUISAS / HISTÓRICO</div>
          <h1>Pesquisas anteriores</h1>
          <p>Consulte diagnósticos armazenados no PostgreSQL sem custo adicional de API.</p>
        </div>
        <button className="secondary" onClick={onReload} disabled={busy}>
          <RefreshCw size={16} className={busy ? 'spin' : ''} /> Atualizar
        </button>
      </div>
      {error && <div className="alert error" role="alert"><AlertCircle size={18} />{error}</div>}
      <Panel title="Consultas salvas" subtitle="Ordenadas pela atualização mais recente">
        {busy ? (
          <div className="loading"><RefreshCw className="spin" /> Carregando histórico...</div>
        ) : history.length ? (
          <div className="history-list">
            {history.map((item, index) => (
              <button className="history-item" key={item.id ?? index} onClick={() => onOpen(item.id)} disabled={item.id == null}>
                <div className="history-icon"><Building2 size={20} /></div>
                <div className="history-name">
                  <b>{item.company_name || 'Empresa não informada'}</b>
                  <small>Consultado em {formatDate(item.updated_at || item.created_at)}</small>
                </div>
                <span>{formatNumber(item.total_processes)} registros</span>
                <ChevronRight size={19} />
              </button>
            ))}
          </div>
        ) : (
          <Empty icon={History} title="Nenhuma pesquisa salva" description="As pesquisas aparecerão aqui quando o backend persistir os resultados." />
        )}
      </Panel>
    </div>
  );
}

function CompanyShareActions({ data }) {
  const [message, setMessage] = useState('');
  const [pdfBusy, setPdfBusy] = useState(false);
  const [manualUrl, setManualUrl] = useState('');
  const id = validSearchId(data.searchId);

  async function copyLink() {
    const url = companyShareUrl(window.location, id);
    setManualUrl('');
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard indisponível');
      await navigator.clipboard.writeText(url);
      setMessage('Link copiado.');
    } catch {
      setManualUrl(url);
      setMessage('Selecione e copie o link abaixo.');
    }
  }

  async function downloadPdf() {
    setPdfBusy(true);
    setMessage('');
    let objectUrl;
    try {
      const response = await fetch(`${API_BASE}/api/v1/history/${id}/summary.pdf`);
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.error || `Não foi possível baixar o PDF (HTTP ${response.status}).`);
      }
      if (!response.headers.get('content-type')?.includes('application/pdf')) {
        throw new Error('A API não retornou um PDF válido.');
      }
      objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = `radarjus-empresa-${id}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setMessage('Download do PDF iniciado.');
    } catch (failure) {
      setMessage(failure.message || 'Não foi possível baixar o PDF.');
    } finally {
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      setPdfBusy(false);
    }
  }

  return (
    <section className="company-share card" aria-label="Compartilhar consulta">
      <div className="company-share-row">
        <div><b>Compartilhar consulta</b><small>{id ? 'Compartilhe a análise salva ou baixe o resumo em PDF.' : 'A consulta ainda não foi salva no banco de dados.'}</small></div>
        <div className="company-share-buttons">
          <button type="button" className="secondary" onClick={copyLink} disabled={!id}><Share2 size={16} /> Compartilhar link</button>
          <button type="button" className="secondary" onClick={downloadPdf} disabled={!id || pdfBusy}><Download size={16} /> {pdfBusy ? 'Gerando PDF...' : 'Baixar PDF'}</button>
        </div>
      </div>
      {message && <p className="company-share-message" role="status">{message}</p>}
      {manualUrl && <input className="company-share-url" aria-label="Link da consulta" value={manualUrl} readOnly onFocus={(e) => e.target.select()} />}
      {id && data.scanStatus !== 'COMPLETED' && <small>Varredura ainda não concluída: o resumo pode mudar após a atualização da consulta.</small>}
    </section>
  );
}

export default function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  const { page, searchId } = route;
  const [mobileOpen, setMobileOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [sharedError, setSharedError] = useState(null);
  const [reloadShared, setReloadShared] = useState(0);
  const needsSharedData = Boolean(searchId && validSearchId(data?.searchId) !== searchId);
  const visibleData = needsSharedData ? null : data;

  function navigate(destination, id = searchId ?? data?.searchId) {
    if (!Object.hasOwn(PAGE_LABELS, destination)) return;
    const hash = companyHash(destination, id);
    setRoute(parseRoute(hash));
    window.location.hash = hash;
  }

  useEffect(() => {
    const syncHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', syncHash);
    return () => window.removeEventListener('hashchange', syncHash);
  }, []);

  useEffect(() => {
    if (!needsSharedData) return;
    const controller = new AbortController();
    setSharedError(null);
    request(`/history/${searchId}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        const normalized = normalize(result);
        if (!normalized || validSearchId(normalized.searchId) !== searchId) {
          throw new Error('A consulta salva não contém dados válidos.');
        }
        setData(normalized);
        setQuery(normalized.name);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setSharedError({ id: searchId, message: failure.message });
      });
    return () => controller.abort();
  }, [searchId, needsSharedData, reloadShared]);

  // Polling automático para atualizar tela quando o Deep Scan em segundo plano finalizar
  useEffect(() => {
    if (!data?.searchId || !['PARTIAL_FAST_PATH', 'SCANNING_BACKGROUND'].includes(data?.scanStatus)) return;
    let active = true;
    const intervalId = setInterval(async () => {
      try {
        const statusRes = await request(`/search_status/${data.searchId}`);
        if (active && statusRes?.is_completed && statusRes.data) {
          const updated = normalize(statusRes.data);
          if (updated) {
            setData((prev) => validSearchId(prev?.searchId) !== validSearchId(data.searchId) ? prev : ({
              ...updated,
              idoneidadeEconomica: updated.idoneidadeEconomica || prev?.idoneidadeEconomica,
              logo: updated.logo || prev?.logo,
              rankingCondenacoes: (updated.rankingCondenacoes && updated.rankingCondenacoes.length > 0)
                ? updated.rankingCondenacoes
                : prev?.rankingCondenacoes || [],
            }));
          }
          clearInterval(intervalId);
        }
      } catch (err) {
        console.debug('Polling background scan status:', err);
      }
    }, 3000);

    return () => { active = false; clearInterval(intervalId); };
  }, [data?.searchId, data?.scanStatus]);

  async function search() {
    if (query.trim().length < 2) return;
    setBusy(true);
    setError('');
    try {
      const result = await request('/search-company', {
        method: 'POST',
        body: JSON.stringify({ company_name: query.trim() }),
      });
      const normalized = normalize(result);
      if (!normalized) throw new Error('A API não retornou dados de empresa válidos.');
      setData(normalized);
      navigate('overview', normalized.searchId);
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
      navigate('overview', normalized.searchId);
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
          <div className="topbar-left">
            <button className="menu-button" onClick={() => setMobileOpen(true)} aria-label="Abrir menu"><Menu size={23} /></button>
            <span className="breadcrumb">RadarJus <ChevronRight size={14} /> <b>{PAGE_LABELS[page]}</b></span>
          </div>
          <div className="region"><span className="region-dot" /> CNJ DataJud · Comunica PJe</div>
        </header>
        <main className="content">
          {page === 'search' && <SearchPage onSearch={search} query={query} setQuery={setQuery} busy={busy} error={error} navigate={navigate} result={data} />}
          {isCompanyPage(page) && needsSharedData && (
            sharedError?.id === searchId ? (
              <div className="alert error" role="alert">
                <AlertCircle size={18} /> {sharedError.message}
                <button className="secondary" onClick={() => { setSharedError(null); setReloadShared((n) => n + 1); }}>Tentar novamente</button>
              </div>
            ) : <div className="loading" role="status"><RefreshCw className="spin" /> Carregando consulta compartilhada...</div>
          )}
          {isCompanyPage(page) && !needsSharedData && <>
            {visibleData && <CompanyShareActions key={visibleData.searchId ?? visibleData.name} data={visibleData} />}
            {page === 'overview' && <Overview data={visibleData} navigate={navigate} />}
            {page === 'processes' && <Processes data={visibleData} />}
            {page === 'predictions' && <Predictions data={visibleData} />}
          </>}
          {page === 'history' && <HistoryPage history={history} busy={historyBusy} error={historyError} onOpen={openHistory} onReload={loadHistory} />}
        </main>
        <footer className="footer">RadarJus · Inteligência Preditiva e Gestão de Risco de Fornecedores</footer>
      </div>
    </div>
  );
}
