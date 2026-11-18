import React, { useEffect, useState } from 'react';

const KNOWN_VERIFIED_DOMAINS = new Set([
  'bb.com.br', 'caixa.gov.br', 'petrobras.com.br', 'correios.com.br',
  'embratel.com.br', 'picpay.com', 'itau.com.br', 'bradesco.com.br',
  'santander.com.br', 'nubank.com.br', 'compesa.com.br', 'globo.com',
  'ambev.com.br', 'magazineluiza.com.br', 'natura.com.br', 'vale.com'
]);

function isLogoVerified(logo) {
  if (!logo) return false;
  if (logo.is_verified_domain === true) return true;
  if (logo.is_verified_domain === false || logo.is_synthetic_domain === true) return false;

  // Compatibilidade com consultas salvas antes da flag is_verified_domain:
  const dom = String(logo.domain || '').toLowerCase().replace(/^www\./, '');
  if (KNOWN_VERIFIED_DOMAINS.has(dom)) return true;

  const url = String(logo.logo_url || logo.google_favicon_url || '');
  if (url.includes('google.com/s2/favicons') || url.includes('gstatic.com') || url.includes('unavatar.io')) {
    return false;
  }
  return Boolean(url);
}

// Logo da empresa com cadeia de fallback: logo oficial verificado → favicon → iniciais.
export function CompanyLogo({ logo, companyName }) {
  const [imgErrorStep, setImgErrorStep] = useState(0);

  const canLoad = isLogoVerified(logo);

  const logoUrl = !canLoad
    ? null
    : (imgErrorStep === 0
      ? (logo?.logo_url || logo?.google_favicon_url)
      : (imgErrorStep === 1 ? logo?.logo_secondary_url : null));

  useEffect(() => {
    setImgErrorStep(0);
  }, [companyName, logo?.logo_url, logo?.google_favicon_url, logo?.is_synthetic_domain]);

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