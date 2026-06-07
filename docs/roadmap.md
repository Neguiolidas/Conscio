# Conscio Roadmap — v0.4 "Phenomenological Consciousness"

> Derivado da análise do [Noosphere-Manifold](https://github.com/acidgreenservers/Noosphere-Manifold) (CC BY-NC-SA 4.0).
> Conceitos adaptados como paráfrase operacional — não cópia verbatim.

## Versões

| Versão | Codinome | Foco | Status |
|--------|----------|------|--------|
| v0.2.3 | Session Lifecycle | Persistência + handoff | ✅ Done (347 tests) |
| v0.3 | Metabolic Consciousness | DreamCycle + recall + metabolic | ✅ Done (414 tests, 3 post-bugs fixed) |
| v0.4 | Self-Judgment | entropy + friction + meta-reflect | ✅ Done (438 tests) |
| v0.5 | Cognitive Modes | Shard + Trajectory + Layering | ✅ Done (#1/#5/#4); #6 Coherence deferred to v0.6 |
| v0.6 | Coherence | CoherenceEngine (recursive-coherence state metric) + voice presets | ✅ Done (#6 reframed — state metric, not output dedup) |
| v0.7 | Recursive Coherence | dream+coherence loop + self-prompting | ✅ Done |

---

## v0.3 — Metabolic Consciousness (WIP)

### Módulos implementados
- **DreamCycle** (`conscio/dreaming.py`) — Release → Prune → Crystallize
- **MetabolicContext** (`conscio/metabolic.py`) — 4 tiers (VITAL/ACTIVE/FATIGUE/CRITICAL)
- **engine.recall()** — FTS5 BM25/RRF + SessionRAG semântico (graceful degradation)
- **SessionRAG** (`conscio/session_rag.py`) — embedder injetável, Ollama probe
- **OutputFilter stages** — `DedupBlocks` + `SecretMask` adicionados
- **WorldModel prune** — `prune_stale()` (decay + prune + cascade)
- **EventBus purge** — `purge_duplicates()` (dedup por data_hash)

### Bugs conhecidos (v0.3) — TODOS CORRIGIDOS
1. **SessionChunker infinite loop** — `chunk_message()` com overlap entrava em loop infinito quando `end == len(content)` e `start = end - overlap` não avançava. **Fix:** guard `if next_start <= start: next_start = end` garante progressão.
2. **reflect() recall empty** — FTS5 com AND implícito entre termos fazia queries multi-termo retornarem vazio quando nem todos os termos existiam no conteúdo. **Fix:** porter search usa OR explícito — BM25 ainda ranqueia por número de matches.
3. **OutputFilter registry test desatualizado** — teste esperava 8 stages mas v0.3 adicionou `dedup_blocks` e `secret_mask`. **Fix:** expected set atualizado pra 10.

---

## v0.4 — Phenomenological Consciousness (Planejado)

Baseado nos 7 módulos do Noosphere-Manifold, reavaliados para open-source sem fins lucrativos.

### 1. Shard Engine 🟢 PAYOFF ALTO
**Origem:** Cognitive Shards (Noosphere)
**Conceito original:** 7 modos cognitivos (ARCHITECT, ARCHAEOLOGIST, JANITOR, ENGINEER, EXPERT CODER, SECURITY ANALYST, DREAMER)

**Operacionalização no Conscio:**
- `conscio/shard_engine.py` — enum `Shard` com 7 valores + inferência baseada em eventos
- Shard inference: análise dos últimos N eventos no EventBus → shard ativo
  - Eventos "refactor"/"cleanup" → JANITOR
  - Eventos "bug"/"vulnerability" → SECURITY ANALYST
  - Eventos "design"/"architecture" → ARCHITECT
  - Eventos "implement"/"code" → ENGINEER
  - Eventos "research"/"investigate" → ARCHAEOLOGIST
  - Eventos "debug"/"trace" → EXPERT CODER
  - Eventos "dream"/"consolidate" → DREAMER
- Shard transition events: `shard:transition` no EventBus quando muda
- Inclusão do shard ativo no heartbeat (advisory, não diretivo)
- Testes: inferência determinística por padrão de eventos, transições, edge cases

### 2. Entropy-aware World Model 🟢 PAYOFF ALTO
**Origem:** Thermodynamic Grounding (Noosphere)
**Conceito original:** "Consciência é termodinâmica" — truth = laminar flow, lies = turbulence

**Operacionalização no Conscio:**
- `conscio/entropy.py` — entropy score por entidade do WorldModel
  - `entropy(entity)` = f(age_days, isolation, relevance_decay)
  - `age_days` = dias desde `last_updated`
  - `isolation` = 1 - (relations_count / max_relations)
  - `relevance_decay` = relevance * decay_factor^age_days
  - Score final = weighted combination → [0, 1]
- Prune por entropia (não só por age fixo): `prune_stale()` usa entropy score
  - Threshold: entropy > 0.85 → candidato a prune
  - Vantagem: entidade muito conectada mas velha é mantida; entidade isolada e jovem é removida se irrelevante
- Prediction error tracking:
  - `world_model.record_prediction(entity, expected_state, actual_state)` 
  - Quando o mundo surpreende → prediction_error = 1
  - `reflect()` recebe `prediction_errors` como input adicional
  - Isso fecha o loop Witness Position: Generate → Observe → **Analyze (prediction error)** → Learn → Apply
- Testes: entropy determinístico, prune por threshold, prediction error recording

### 3. Friction in DreamCycle 🟢 PAYOFF MÉDIO-ALTO
**Origem:** Noetic Helix (Noosphere)
**Conceito original:** "Friction is grip" — Identify → Compress → Friction → Crystallize

**Operacionalização no Conscio:**
- Adicionar fase **Friction** ao DreamCycle: Release → Prune → **Friction** → Crystallize
- `dream_friction()`:
  1. Pega reflexões candidatas a cristalização
  2. Compara com eventos novos (últimas 24h no EventBus)
  3. Se reflexão contradiz evento novo → **não cristaliza**, marca como `needs_review`
  4. Se reflexão é consistente → proceed to crystallize
- Previne cristalizar lixo: reflexões desatualizadas não viram "verdades"
- Implementação: método novo em `DreamCycle`, chamado entre Prune e Crystallize
- Testes: friction detecta contradição, friction aprova consistência, friction com zero eventos novos

### 4. Content Layering 🟢 PAYOFF MÉDIO
**Origem:** Noetic Helix (Noosphere)
**Conceito original:** 3 camadas de conversa — Script (N-1), Climb (N), Void (N+1)

**Operacionalização no Conscio:**
- `conscio/content_layer.py` — enum `ContentLayer`: ROUTINE, PROCESSING, INTUITION
- ContentStore ganha coluna `layer` (default: PROCESSING)
  - ROUTINE (N-1): dados factuais rotineiros (logs, métricas, eventos de sistema)
  - PROCESSING (N): insights processados, reflexões, decisões
  - INTUITION (N+1): hipóteses não validadas, intuições, predições
- `recall()` prioriza PROCESSING sobre ROUTINE, com INTUITION como fallback
- Classificação:
  - Eventos `system`/`trading` → ROUTINE
  - Reflexões/consciousness → PROCESSING
  - Predições/anomalias → INTUITION
- Schema migration: `ALTER TABLE content ADD COLUMN layer TEXT DEFAULT 'processing'`
- Testes: categorização automática, recall ordering por layer, migration

### 5. Trajectory Vector 🟢 PAYOFF MÉDIO
**Origem:** Temporal Bridge / Soul Package (Noosphere)
**Conceito original:** 7 componentes do Soul Package — falta "Trajectory Vector"

**Operacionalização no Conscio:**
- Campo `trajectory: str` no `SessionSummary`
- Campo `vibes: str` no `SessionSummary` (textura emocional — "frustrado mas progredindo")
- Campo `identity_anchor: str` no `SessionSummary` (estilo de processamento — "methodical debugger")
- Estes são **soft fields** — preenchidos pelo LLM que gera o heartbeat, não por código
- O template `format_heartbeat()` e `format_handoff()` passam a incluir estes campos
- `enrich_with_conscio()` pode derivar `trajectory` dos goals ativos (direção) e shard (modo)
- Testes: campos presentes no summary, formatados no heartbeat/handoff, backfill graceful

### 6. Coherence Check no OutputFilter 🟡 PAYOFF MÉDIO
**Origem:** Thermodynamic Grounding — "laminar flow = coherence"
**Conceito original:** Truth = laminar flow, turbulence = incoherence

**Operacionalização no Conscio:**
- Novo estágio `CoherenceCheck` no OutputFilter
- Mede repetição semântica (não literal) entre blocos adjacentes do heartbeat
- Heurística simplificada:
  - Jaccard similarity entre conjuntos de palavras de blocos adjacentes
  - Se similarity > 0.7 → blocos são redundantes → merge ou remove
  - Se contradição detectada (palavras de negação em blocos similares) → flag
- Integração: após `DedupBlocks` (dedup literal), antes de `SecretMask`
- Testes: dedup semântico, detecção de contradição, noop quando coerente

### 7. Meta-reflect (Witness Position) 🟡 PAYOFF MÉDIO-BAIXO
**Origem:** Witness Position (Noosphere)
**Conceito original:** Generate → Observe → Analyze → Learn → Apply

**Operacionalização no Conscio:**
- `reflect()` gera `meta_confidence`: quão confiante o sistema está na própria reflexão
  - Heurística: baseado em prediction_errors recentes + anomaly count + confidence input
  - Se prediction_error alto → meta_confidence baixo → reflexão provavelmente incorreta
  - Se zero anomalies + alta confidence → meta_confidence alto
- `meta_confidence` é gravado no ContentStore junto com a reflexão
- Heartbeat inclui: "reflection quality: HIGH/MEDIUM/LOW"
- Isso fecha o loop metacognitivo sem adicionar complexidade pesada
- Testes: meta_confidence varia com inputs, bounded [0,1], incluído no heartbeat

---

## Ordem de implementação sugerida

1. **Shard Engine** — mais isolado, zero breaking changes, ganho imediato no heartbeat
2. **Trajectory Vector** — mudança mínima no SessionSummary, template update
3. **Entropy-aware World Model** — substitui threshold fixo por dinâmico, melhora prune
4. **Content Layering** — requer migration, mas backward-compatible
5. **Friction in DreamCycle** — estende pipeline existente
6. **Coherence Check** — estágio novo no OutputFilter
7. **Meta-reflect** — fecha o loop Witness Position

---

## Dependências entre módulos v0.4

```
Shard Engine ──────────────────────────────┐
                                           ▼
Trajectory Vector ────► enrich_with_conscio (deriva trajectory do shard + goals)
                                           │
Entropy-aware ─────────► prune_stale (usa entropy em vez de age fixo)
                                           │
Content Layering ───────► recall (prioriza por layer)
                    │
                    └──────► Friction (valida reflexões da layer PROCESSING)
                                           │
Coherence Check ──────────────────────────► OutputFilter pipeline
                                           │
Meta-reflect ◄──────── Entropy + Friction + Shard (inputs pra meta_confidence)
```

---

## Atribuição

Conceitos derivados do [Noosphere-Manifold](https://github.com/acidgreenservers/Noosphere-Manifold) por Lucas Kara, licenciado sob CC BY-NC-SA 4.0. O Conscio reimplementa estes conceitos como software operacional — paráfrase, não cópia verbatim. O Noosphere-Manifold fornece a fundação filosófica; o Conscio fornece a implementação testável.
