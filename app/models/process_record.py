from datetime import datetime, timezone
from app.extensions import db


def get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProcessRecord(db.Model):
    """
    Representa os metadados de um processo judicial extraído do Comunica PJe e DataJud.
    Focado estritamente na Pessoa Jurídica (Compliance LGPD).
    """
    __tablename__ = "process_records"

    id = db.Column(db.Integer, primary_key=True)
    company_search_id = db.Column(db.Integer, db.ForeignKey("company_searches.id", ondelete="CASCADE"), nullable=False, index=True)

    numero_processo = db.Column(db.String(30), nullable=False, index=True)
    tribunal = db.Column(db.String(20), nullable=True, index=True)
    grau = db.Column(db.String(10), nullable=True)
    nivel_sigilo = db.Column(db.Integer, default=0, nullable=False)

    # Assunto principal e lista de assuntos TPU
    primary_subject = db.Column(db.String(255), nullable=True, index=True)
    primary_subject_code = db.Column(db.Integer, nullable=True)
    subjects_json = db.Column(db.JSON, nullable=True)

    # Observação textual (quando o assunto é inferido da publicação PJe)
    observation = db.Column(db.String(500), nullable=True)

    # Desfecho da sentença classificado via TPU + NLP
    sentence_outcome = db.Column(db.String(50), default="em_aberto", nullable=False, index=True)
    has_expert_report = db.Column(db.Boolean, default=False, nullable=False)

    # Armazena metadados brutos do Elasticsearch / PJe para rastreabilidade
    raw_metadata = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=get_utc_now, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "numero_processo": self.numero_processo,
            "tribunal": self.tribunal,
            "grau": self.grau,
            "nivel_sigilo": self.nivel_sigilo,
            "primary_subject": self.primary_subject,
            "primary_subject_code": self.primary_subject_code,
            "subjects": self.subjects_json or [],
            "observation": self.observation,
            "sentence_outcome": self.sentence_outcome,
            "has_expert_report": self.has_expert_report,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
