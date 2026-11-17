import React, { useMemo, useState } from 'react';
import {
  Search, RefreshCw, AlertCircle, Info, ChevronDown, MapPin, GitBranch, Network, ArrowRight, Library,
} from 'lucide-react';
import { CompanyLogo } from "./CompanyLogo.jsx";

const PAGE_SIZE = 15;
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export const onlyDigits = (value) => String(value ?? '').replace(/\D/g, '');

export function formatCnpj(value) {
  const d = onlyDigits(value);
  if (d.length !== 14) return d;
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

const formatCapital = (value) => {
  if (value == null || value === '') return null;
  if (typeof value === 'string' && Number.isNaN(Number(value))) return value;
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value));
};

// Aceita variações de nomes de campo para não acoplar o frontend a um único formato de resposta.
export function normalizeCatalog(raw) {
  const candidates = [raw, raw?.items, raw?.empresas, raw?.companies, raw?.results, raw?.data, raw?.data?.items];
  const list = candidates.find(Array.isArray) || [];
  const items = list.map((r) => {
    if (!isRecord(r)) return null;
    const cnpj = onlyDigits(r.cnpj ?? r.cnpj_completo);
    if (cnpj.length !== 14) return null;
    const razao = r.razao_social ?? r.nome ?? r.company_name ?? r.legal_name ?? 'Razão social não informada';
    const logo = isRecord(r.logo)
      ? r.logo
      : { logo_url: typeof r.logo === 'string' ? r.logo : (r.logo_url || null) };
    return {
      cnpj,
      razao,
      fantasia: r.nome_fantasia ?? r.fantasia ?? '',
      situacao: String(r.situacao_cadastral ?? r.situacao ?? '').trim().toUpperCase(),
      matriz: typeof r.matriz === 'boolean' ? r.matriz : cnpj.slice(8, 12) === '0001',
      municipio: r.municipio ?? r.cidade ?? '',
      uf: String(r.uf ?? '').toUpperCase(),
      capital: formatCapital(r.capital_social_formatado ?? r.capital_social),
      logo,
    };
  }).filter(Boolean);
  const suggestion = isRecord(raw) ? (raw.sugestao ?? raw.did_you_mean ?? '') : '';
  return { items, suggestion: typeof suggestion === 'string' ? suggestion : '' };
}

// Uma linha por raiz de CNPJ (8 dígitos): a matriz em destaque e as filiais recolhidas.
function groupByRoot(items) {
  const map = new Map();
  items.forEach((item) => {
    const root = item.cnpj.slice(0, 8);
    if (!map.has(root)) map.set(root, []);
    map.get(root).push(item);
  });
  return [...map.entries()].map(([root, list]) => {
    const sorted = [...list].sort((a, b) => a.cnpj.localeCompare(b.cnpj));
    const head = sorted.find((i) => i.matriz) || sorted[0];
    return { root, head, branches: sorted.filter((i) => i !== head) };
  });
}

function situationTone(situacao) {
  if (situacao === 'ATIVA') return 'green';
  if (!situacao) return 'gray';
  if (['INAPTA', 'BAIXADA', 'NULA', 'SUSPENSA'].includes(situacao)) return 'red';
  return 'amber';
}

function AnalyzeButton({ item, onAnalyze, analyzing, disabled }) {
  const loading = analyzing === item.cnpj;
  return (
    <button
      type="button"
      className="primary catalog-analyze"
      disabled={disabled}
      onClick={() => onAnalyze(item)}
      aria-label={`Analisar ${item.razao}, CNPJ ${formatCnpj(item.cnpj)}`}
    >
      {loading ? <><RefreshCw size={16} className="spin" /> Consultando</> : <>Analisar <ArrowRight size={16} /></>}
    </button>
  );
}

function Meta({ item }) {
  return (
    <div className="catalog-meta">
      <span className="mono">{formatCnpj(item.cnpj)}</span>
      <span className={`tag ${item.matriz ? '' : 'amber'}`}>{item.matriz ? 'Matriz' : 'Filial'}</span>
      {item.situacao && <span className={`tag ${situationTone(item.situacao)}`}>{item.situacao}</span>}
      {(item.municipio || item.uf) && (
        <span className="catalog-place"><MapPin size={13} />{[item.municipio, item.uf].filter(Boolean).join(' / ')}</span>
      )}
      {item.capital && <span className="catalog-capital">Capital social {item.capital}</span>}
    </div>
  );
}

function CatalogEntry({ group, onAnalyze, analyzing, disabled }) {
  const [open, setOpen] = useState(false);
  const { head, branches } = group;
  const showFantasia = head.fantasia && head.fantasia.toUpperCase() !== head.razao.toUpperCase();
  return (
    <li className="catalog-entry">
      <div className="catalog-row">
        <div className="company-logo-wrapper"><CompanyLogo logo={head.logo} companyName={head.razao} /></div>
        <div className="catalog-main">
          <b>{head.razao}</b>
          {showFantasia && <small>Nome fantasia: {head.fantasia}</small>}
          <Meta item={head} />
          {branches.length > 0 && (
            <button
              type="button"
              className="text-button catalog-toggle"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              <ChevronDown size={15} className={open ? 'flip' : ''} />
              {open ? 'Ocultar' : 'Ver'} {branches.length} {branches.length === 1 ? 'filial' : 'filiais'}
            </button>
          )}
        </div>
        <AnalyzeButton item={head} onAnalyze={onAnalyze} analyzing={analyzing} disabled={disabled} />
      </div>
      {open && (
        <ul className="catalog-branches">
          {branches.map((branch) => (
            <li className="catalog-row branch" key={branch.cnpj}>
              <GitBranch size={18} className="branch-icon" />
              <div className="catalog-main">
                <Meta item={branch} />
              </div>
              <AnalyzeButton item={branch} onAnalyze={onAnalyze} analyzing={analyzing} disabled={disabled} />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function CatalogPage({
  query, setQuery, catalog, onSubmit, onAnalyze, onAnalyzeGroup, analyzing, analysisBusy, analysisError,
}) {
  const [onlyActive, setOnlyActive] = useState(false);
  const [uf, setUf] = useState('');
  const [visible, setVisible] = useState(PAGE_SIZE);

  const ufs = useMemo(() => [...new Set(catalog.items.map((i) => i.uf).filter(Boolean))].sort(), [catalog.items]);
  const groups = useMemo(() => groupByRoot(
    catalog.items.filter((i) => (!onlyActive || i.situacao === 'ATIVA') && (!uf || i.uf === uf)),
  ), [catalog.items, onlyActive, uf]);

  const hasTerm = Boolean(catalog.term);
  const hasItems = catalog.items.length > 0;
  const disabled = analysisBusy || catalog.busy;

  return (
    <div className="stack catalog-page">
      <div className="section-intro">
        <div>
          <div className="eyebrow">EMPRESAS / CATÁLOGO</div>
          <h1>Catálogo de empresas</h1>
          <p>Busque pelo nome e escolha a empresa exata que deseja analisar.</p>
        </div>
      </div>

      <form
        className="search-box"
        onSubmit={(event) => { event.preventDefault(); setVisible(PAGE_SIZE); setUf(''); onSubmit(); }}
      >
        <Search size={22} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Nome da empresa (ex.: RadarJud, Google, Petrobras)"
          aria-label="Nome da empresa"
          required
          minLength={3}
        />
        <button className="primary" disabled={disabled}>
          {catalog.busy ? <><RefreshCw size={17} className="spin" /> Buscando</> : <>Buscar <ArrowRight size={17} /></>}
        </button>
      </form>

      {analysisError && <div className="alert error" role="alert"><AlertCircle size={18} />{analysisError}</div>}

      {!hasTerm && !catalog.busy && (
        <section className="card panel">
          <div className="empty" role="status">
            <Library size={28} />
            <strong>Nenhuma busca no catálogo ainda</strong>
            <p>Digite o nome de uma empresa. Você verá todas as empresas com esse nome, cada uma com logo, razão social e CNPJ, e escolhe qual analisar.</p>
          </div>
        </section>
      )}

      {catalog.busy && <div className="loading"><RefreshCw className="spin" /> Buscando empresas chamadas “{catalog.term}”...</div>}

      {hasTerm && !catalog.busy && catalog.unavailable && (
        <div className="info-banner warning" role="status">
          <Info size={20} />
          <div>
            <b>O catálogo ainda não está disponível neste servidor</b>
            <p>A API não respondeu em <code>/api/v1/company-catalog</code>. Enquanto isso, você pode analisar “{catalog.term}” diretamente.</p>
            <div className="empty-actions">
              <button className="primary" onClick={onAnalyzeGroup} disabled={disabled}>
                {analyzing === 'GROUP' ? <><RefreshCw size={16} className="spin" /> Consultando</> : <>Analisar “{catalog.term}” diretamente <ArrowRight size={16} /></>}
              </button>
            </div>
          </div>
        </div>
      )}

      {hasTerm && !catalog.busy && catalog.error && !catalog.unavailable && (
        <div className="alert error" role="alert"><AlertCircle size={18} />{catalog.error}</div>
      )}

      {hasTerm && !catalog.busy && !catalog.unavailable && !catalog.error && !hasItems && (
        <section className="card panel">
          <div className="empty" role="status">
            <Search size={28} />
            <strong>Nenhuma empresa encontrada para “{catalog.term}”</strong>
            <p>Confira a grafia ou tente um trecho menor do nome.{catalog.suggestion ? ' Você quis dizer:' : ''}</p>
            {catalog.suggestion && (
              <button
                className="secondary"
                onClick={() => { setQuery(catalog.suggestion); onSubmit(catalog.suggestion); }}
              >
                {catalog.suggestion}
              </button>
            )}
          </div>
        </section>
      )}

      {hasItems && !catalog.busy && (
        <>
          <div className="info-banner catalog-group-banner">
            <Network size={20} />
            <div>
              <b>Avaliar o grupo inteiro?</b>
              <p>Consolida processos e capital social de todas as empresas e filiais da marca “{catalog.term}”. Para avaliar só uma empresa, escolha-a na lista.</p>
            </div>
            <button className="secondary" onClick={onAnalyzeGroup} disabled={disabled}>
              {analyzing === 'GROUP' ? <><RefreshCw size={16} className="spin" /> Consultando</> : 'Analisar grupo'}
            </button>
          </div>

          <div className="catalog-toolbar">
            <div className="catalog-count">
              <b>{groups.length}</b> {groups.length === 1 ? 'empresa' : 'empresas'} para “{catalog.term}”
            </div>
            <div className="catalog-filters">
              <label className="chip-toggle">
                <input type="checkbox" checked={onlyActive} onChange={(e) => { setOnlyActive(e.target.checked); setVisible(PAGE_SIZE); }} />
                Somente ativas
              </label>
              {ufs.length > 1 && (
                <select className="catalog-select" value={uf} onChange={(e) => { setUf(e.target.value); setVisible(PAGE_SIZE); }} aria-label="Filtrar por estado">
                  <option value="">Todos os estados</option>
                  {ufs.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              )}
            </div>
          </div>

          {groups.length ? (
            <section className="card catalog-list">
              <ul>
                {groups.slice(0, visible).map((group) => (
                  <CatalogEntry key={group.root} group={group} onAnalyze={onAnalyze} analyzing={analyzing} disabled={disabled} />
                ))}
              </ul>
            </section>
          ) : (
            <section className="card panel">
              <div className="empty" role="status">
                <Info size={28} />
                <strong>Nenhuma empresa com esses filtros</strong>
                <p>Remova “Somente ativas” ou escolha outro estado para ver as demais.</p>
              </div>
            </section>
          )}

          {groups.length > visible && (
            <button className="secondary catalog-more" onClick={() => setVisible((v) => v + PAGE_SIZE)}>
              Mostrar mais {Math.min(PAGE_SIZE, groups.length - visible)} de {groups.length - visible} restantes
            </button>
          )}
        </>
      )}
    </div>
  );
}