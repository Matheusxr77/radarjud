import hashlib
import json
from typing import List, Dict, Any


def generate_movements_hash(processes_data: List[Dict[str, Any]]) -> str:
    """
    Gera um hash SHA-256 representativo do estado atual das movimentações e metadados
    dos processos judiciais de uma empresa.

    O hash é calculado de forma determinística ordenando:
    1. Número do processo.
    2. Data da última atualização processual (dataHoraUltimaAtualizacao).
    3. Lista ordenada de códigos de movimentação e suas respectivas datas/horas.

    Se os metadados dos processos não mudarem entre consultas, o hash gerado
    será exatamente idêntico, permitindo o curto-circuito antes de chamar o Gemini/IA.
    """
    if not processes_data:
        return hashlib.sha256(b"empty_movements").hexdigest()

    normalized_entries = []

    for item in processes_data:
        proc_num = str(item.get("numero_processo") or item.get("numeroProcesso") or "").strip()
        last_update = str(item.get("dataHoraUltimaAtualizacao") or item.get("dataHora") or "").strip()

        # Extrai movimentos de raw_metadata ou do dicionário direto
        raw_meta = item.get("raw_metadata") or {}
        raw_movs = item.get("movimentos") or raw_meta.get("movimentos") or []

        movement_tuples = []
        if isinstance(raw_movs, list):
            for m in raw_movs:
                if isinstance(m, dict):
                    code = str(m.get("codigo") or "")
                    dt = str(m.get("dataHora") or "")
                    movement_tuples.append((code, dt))

        movement_tuples.sort()

        normalized_entries.append({
            "processo": proc_num,
            "ultima_atualizacao": last_update,
            "movimentos": movement_tuples,
            "desfecho": item.get("sentence_outcome", ""),
        })

    # Ordena pelo número do processo para garantir determinismo
    normalized_entries.sort(key=lambda x: x["processo"])

    serialized = json.dumps(normalized_entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

