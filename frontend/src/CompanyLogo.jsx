import React, { useEffect, useState } from 'react';

// Logo da empresa com cadeia de fallback: logo oficial → favicon → logo secundária → iniciais.
export function CompanyLogo({ logo, companyName }) {
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