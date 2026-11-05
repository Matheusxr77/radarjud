from datetime import datetime
from app.extensions import db


class CompanySearch(db.Model):
    """
    Registra histórico de consultas e diagnóstico de risco de terceiros (empresas).
    Atua como cache persistente para auditoria e rapidez nas consultas recorrentes.
    """
    __tablename__ = "company_searches"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), nullable=False, index=True)
    normalized_name = db.Column(db.String(255), nullable=False, index=True)
    total_processes = db.Column(db.Integer, default=0, nullable=False)
    secrecy_count = db.Column(db.Integer, default=0, nullable=False)
    secrecy_rate = db.Column(db.Float, default=0.0, nullable=False)

    # Sumários estruturados em JSON
    outcomes_summary = db.Column(db.JSON, nullable=True)
    ranking_motivos = db.Column(db.JSON, nullable=True)
    predictions_summary = db.Column(db.JSON, nullable=True)
    overall_risk_score = db.Column(db.Float, default=0.0, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relacionamento com os processos
    processes = db.relationship("ProcessRecord", backref="search", cascade="all, delete-orphan", lazy="select")

    def to_dict(self):
        return {
            "id": self.id,
            "company_name": self.company_name,
            "total_processes": self.total_processes,
            "secrecy_count": self.secrecy_count,
            "secrecy_rate": self.secrecy_rate,
            "outcomes_summary": self.outcomes_summary or {},
            "ranking_motivos": self.ranking_motivos or [],
            "plus_previsao_condenacao": self.predictions_summary or [],
            "overall_risk_score": self.overall_risk_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

