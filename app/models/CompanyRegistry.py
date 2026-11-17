class CompanyRegistry(db.Model):
    __tablename__ = "company_registry"

    id = db.Column(db.Integer, primary_key=True)

    cnpj = db.Column(db.String(14), unique=True, nullable=False, index=True)
    razao_social = db.Column(db.String(255), nullable=False, index=True)
    nome_fantasia = db.Column(db.String(255), nullable=True, index=True)

    situacao_cadastral = db.Column(db.String(50), nullable=True)
    matriz_filial = db.Column(db.String(1), nullable=True)

    municipio = db.Column(db.String(150), nullable=True)
    uf = db.Column(db.String(2), nullable=True, index=True)

    capital_social = db.Column(db.Numeric(15, 2), nullable=True)

    created_at = db.Column(db.DateTime, default=get_utc_now)
    updated_at = db.Column(
        db.DateTime,
        default=get_utc_now,
        onupdate=get_utc_now
    )