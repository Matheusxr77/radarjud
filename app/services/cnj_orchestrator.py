# app/services/cnj_orchestrator.py
# Conector Híbrido Otimizado (Batch + Rate-Limit Safe): Comunica PJe + DataJud CNJ
import os
import math
import time
import requests
from collections import Counter, defaultdict


class CNJMetadataOrchestrator:
    def __init__(self):
        self.comunica_url = os.getenv("COMUNICA_PJE_URL", "https://comunicaapi.pje.jus.br/api/v1/comunicacao")
        self.datajud_base_url = os.getenv("DATAJUD_BASE_URL", "https://api-publica.datajud.cnj.jus.br")
        self.datajud_key = os.getenv(
            "DATAJUD_API_KEY",
            "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="
        )
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) LexGuardAI/1.0"
        })
        self._cache = {}

    def _fetch_comunica_pje(self, company_name: str, max_pages: int = 2) -> dict:
        """
        Passo 1: Busca paginada no Comunica PJe respeitando o limite de 50 itens/página,
        deduplicando processos e concatenando os textos decisórios.
        """
        processes_map = {}
        for page in range(1, max_pages + 1):
            try:
                resp = self.session.get(
                    self.comunica_url,
                    params={
                        "nomeParte": company_name,
                        "itensPorPagina": 50,
                        "pagina": page
                    },
                    timeout=10
                )
                if not resp.ok:
                    break
                items = resp.json().get("items", [])
                if not items:
                    break
            except requests.RequestException:
                break

            for item in items:
                num_raw = item.get("numero_processo") or ""
                num_proc = "".join(filter(str.isdigit, str(num_raw)))
                tribunal = (item.get("siglaTribunal") or "").lower().strip()
                texto = (item.get("texto") or "").lower()

                if len(num_proc) != 20 or not tribunal:
                    continue

                if num_proc not in processes_map:
                    processes_map[num_proc] = {
                        "numero_processo": num_proc,
                        "tribunal": tribunal,
                        "textos_pje": [texto]
                    }
                else:
                    processes_map[num_proc]["textos_pje"].append(texto)

            if len(items) < 50:
                break
            time.sleep(0.25)

        return processes_map

    def _enrich_with_datajud(self, processes_map: dict) -> list:
        """
        Passo 2: Agrupa os números CNJ por siglaTribunal e executa 1 única query 'terms'
        em lote por Tribunal no Elasticsearch do DataJud.
        """
        by_tribunal = defaultdict(list)
        for num_proc, info in processes_map.items():
            by_tribunal[info["tribunal"]].append(num_proc)

        enriched = []
        headers = {
            "Authorization": f"APIKey {self.datajud_key}",
            "Content-Type": "application/json"
        }

        for tribunal, proc_numbers in by_tribunal.items():
            endpoint = f"{self.datajud_base_url}/api_publica_{tribunal}/_search"
            payload = {
                "size": len(proc_numbers),
                "_source": ["numeroProcesso", "assuntos", "nivelSigilo", "movimentos"],
                "query": {
                    "terms": {
                        "numeroProcesso": proc_numbers[:500]
                    }
                }
            }

            try:
                resp = self.session.post(endpoint, json=payload, headers=headers, timeout=12)
                if not resp.ok:
                    continue
                hits = resp.json().get("hits", {}).get("hits", [])
            except requests.RequestException:
                continue

            for hit in hits:
                src = hit.get("_source", {})
                num = src.get("numeroProcesso")
                pje_texts = " ".join(processes_map.get(num, {}).get("textos_pje", []))

                raw_assuntos = src.get("assuntos") or []
                subjects = [a.get("nome") for a in raw_assuntos if isinstance(a, dict) and a.get("nome")]
                if not subjects:
                    subjects = ["Outros Motivos Trabalhistas"]

                mov_codes = {m.get("codigo") for m in (src.get("movimentos") or []) if isinstance(m, dict) and m.get("codigo")}

                if 466 in mov_codes or "homologo o acordo" in pje_texts:
                    outcome = "acordos"
                elif 219 in mov_codes or 221 in mov_codes or "julgo procedente" in pje_texts or "procedentes em parte" in pje_texts:
                    outcome = "condenacoes"
                elif 220 in mov_codes or "julgo improcedente" in pje_texts or "totalmente improcedentes" in pje_texts:
                    outcome = "improcedentes"
                else:
                    outcome = "em_aberto"

                has_expert = (83 in mov_codes) or ("laudo pericial" in pje_texts) or ("perícia técnica" in pje_texts)

                enriched.append({
                    "numero_processo": num,
                    "subjects": list(set(subjects)),
                    "nivelSigilo": src.get("nivelSigilo", 0),
                    "sentence_outcome": outcome,
                    "has_expert_report": has_expert
                })

        return enriched

    def analyze_company_by_name(self, company_name: str) -> dict:
        cache_key = company_name.strip().lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 1. Coleta processos no Comunica PJe
        processes_map = self._fetch_comunica_pje(company_name, max_pages=2)
        if not processes_map:
            return {
                "empresa": company_name,
                "total_processos": 0,
                "ranking_motivos": [],
                "desfechos_sentencas": {"condenacoes": 0, "acordos": 0, "improcedentes": 0, "em_aberto": 0},
                "niveis_sigilo": {},
                "plus_previsao_condenacao": []
            }

        # 2. Enriquecimento em lote no DataJud
        processes = self._enrich_with_datajud(processes_map)

        # 3. Agregação de Motivos, Sigilo e Desfechos
        total = len(processes)
        reasons_counter = Counter()
        outcomes = {"condenacoes": 0, "acordos": 0, "improcedentes": 0, "em_aberto": 0}
        secrecy_levels = Counter()
        history_by_reason = defaultdict(lambda: {
            "condenacoes": 0, "acordos": 0, "improcedentes": 0, "laudo": 0, "em_aberto": 0
        })

        for p in processes:
            sigilo = p.get("nivelSigilo", 0)
            status = p["sentence_outcome"]
            secrecy_levels[sigilo] += 1
            outcomes[status] += 1

            for reason in p["subjects"]:
                reasons_counter[reason] += 1
                history_by_reason[reason][status] += 1
                if p.get("has_expert_report"):
                    history_by_reason[reason]["laudo"] += 1

        # 4. Gera o Ranking Percentual de Motivos
        ranking = [
            {
                "motivo": mot,
                "quantidade": qtd,
                "porcentagem": round((qtd / max(total, 1)) * 100, 1)
            }
            for mot, qtd in reasons_counter.most_common()
        ]

        # 5. Módulo PLUS: Previsão Bayesiana de Condenação e Grau de Confiança (%)
        predictions = []
        for mot, stats in history_by_reason.items():
            julgados = stats["condenacoes"] + stats["acordos"] + stats["improcedentes"]
            delta_laudo = 5.0 if stats["laudo"] > 0 else 0.0
            prob = (((stats["condenacoes"] + 0.65 * stats["acordos"] + 3 * 0.68) / (julgados + 3)) * 100) + delta_laudo
            confidence = min(98.0, round((1 - (1 / math.sqrt(1 + julgados))) * 70 + 20 + 8, 1))

            predictions.append({
                "motivo": mot,
                "processos_abertos": stats["em_aberto"],
                "probabilidade_condenacao_pct": round(min(98.0, prob), 1),
                "confianca_previsao_pct": confidence
            })

        result = {
            "empresa": company_name,
            "total_processos": total,
            "ranking_motivos": ranking,
            "desfechos_sentencas": outcomes,
            "niveis_sigilo": dict(secrecy_levels),
            "plus_previsao_condenacao": predictions
        }
        self._cache[cache_key] = result
        return result
