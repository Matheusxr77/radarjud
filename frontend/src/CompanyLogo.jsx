import React, { useEffect, useState } from 'react';

// Logo da empresa com cadeia de fallback: logo oficial verificado → favicon → iniciais.
export function CompanyLogo({ logo, companyName }) {
  const [imgErrorStep, setImgErrorStep] = useState(0);

  // Se o domínio for sintético/não verificado (ex: slugs derivados como av2engenharia.com.br),
  // não tenta carregar via Google Favicons para evitar erro 404 no DevTools do navegador.
  const isSynthetic = logo?.is_synthetic_domain === true || logo?.is_verified_domain === false;

  const logoUrl = isSynthetic
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