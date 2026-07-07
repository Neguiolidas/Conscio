# Conscio v1.5 — Relatório de Implementação (v1.3.3 base)

**Data:** 2026-06-14
**Autor:** Hermet (Hermes Agent)
**Commits:** `d068ac7` → `8c8888f` → `790f9d6` → `323cbd9` → `cc2aa1f` → `37fddd7` → `fc38d1e`
**Repo:** https://github.com/Neguiolidas/Conscio
**Testes:** 745 verdes, zero regressões

---

## 1. O que foi feito nesta sessão

### 1.1 Universal Model Context Autodiscovery

**Problema:** O `ModelRegistry` conhecia apenas ~15 modelos estáticos. Trocar de modelo (ex: GLM 5.1 → mimo-v2.5-pro) exigia configuração manual. Modelos locais (LM Studio, Ollama) não eram detectados.

**Solução:** `ModelRegistry.autodiscover()` — método de classe que sonda endpoints na inicialização e popula um `_world_registry` (cache por-processo).

**Arquivo:** `conscio/models.py` (+204 linhas)

**Probes implementados:**

| Probe | Endpoint | Detecção |
|-------|----------|----------|
| LM Studio | `GET localhost:1234/v1/models` + `/v1/state` | Modelos carregados + contexto ativo |
| Ollama | `GET localhost:11434/api/tags` + `POST /api/show` | Modelos disponíveis + `context_length` |
| Anthropic | Verifica `ANTHROPIC_API_KEY` | 14 modelos Claude (200k ctx cada) |
| Google | Verifica `GOOGLE_API_KEY`/`GEMINI_API_KEY` | 7 modelos Gemini (1M–2M ctx) |
| Custom | `CONSCIO_ENDPOINTS` env var (comma-separated) | Qualquer endpoint OpenAI-compatible |

**Cadeia de prioridade do `detect()`:**
```
context_window explícito (arg)
  → CONSCIO_CONTEXT_WINDOW env var
  → _known_models (hardcoded)
  → _world_registry (autodiscovered)
  → heurística do nome (ex: "model-256k")
  → fallback 128k
```

**Bug corrigido:** LM Studio não expõe `context_length` na API (`/v1/models` e `/v1/state` não retornam). Fallback: registra modelos com contexto heurístico extraído do nome (128k default se não encontrar padrão).

**Integração no engine:** `ConsciousnessEngine.__init__` chama `autodiscover()` automaticamente na primeira instanciação. Cacheado no `_world_registry` — não redesce a cada engine novo. Falhas são silenciosas.

### 1.2 Graphify Bridge

**Problema:** O RAG do Conscio só buscava em reflections, eventos e sessões. Sem conhecimento estrutural do código.

**Solução:** `conscio/graphify_bridge.py` — ponte opcional que carrega output do Graphify e indexa no ContentStore.

**Arquivo:** `conscio/graphify_bridge.py` (230 linhas)

**O que indexa:**
- `GRAPH_REPORT.md` → overview do codebase como chunk pesquisável
- Comunidades → agrupamentos de entidades por módulo/fonte
- Hyperedges → padrões arquiteturais (pipelines, implementações)

**API:**
```python
from conscio.graphify_bridge import GraphifyBridge, auto_index_graphify

# Manual
bridge = GraphifyBridge("/path/to/graphify-out")
if bridge.available():
    bridge.index_all(content_store)

# Auto-detection
auto_index_graphify(store)  # procura em GRAPHIFY_DIR env, ou <pkg>/graphify-out
```

**Propriedades:**
- Zero deps (stdlib json + pathlib)
- Deduplicação por content hash (idempotente)
- Categoria `external` (não polui eventos de consciência)
- Totalmente opcional — Conscio funciona sem Graphify

### 1.3 Testes

**16 novos testes** para GraphifyBridge:
- `test_available_with_valid_dir` / `test_available_with_missing_dir` / `test_available_with_partial_dir`
- `test_index_all` / `test_index_all_idempotent` / `test_index_all_unavailable`
- `test_index_report` / `test_index_communities` / `test_index_hyperedges`
- `test_auto_index_with_explicit_dir` / `test_auto_index_not_found` / `test_auto_index_env_var`
- `test_search_by_class_name` / `test_search_by_relationship` / `test_search_by_file_path` / `test_search_by_pattern`

**7 novos testes** para autodiscovery (Anthropic/Google):
- `test_probe_anthropic_with_key` / `test_probe_anthropic_without_key`
- `test_probe_google_with_google_key` / `test_probe_google_with_gemini_key` / `test_probe_google_without_key`
- `test_autodiscover_includes_anthropic` / `test_autodiscover_includes_google`

**Total: 745 testes, zero regressões.**

### 1.4 Documentação

- `README.md` — seção "Recommended Companion: Graphify" com exemplo de uso
- `CHANGELOG.md` — entrada v1.3.3 com todas as mudanças
- `docs/graphify-autosync-design.md` — design para auto-sync na v1.5

---

## 2. Estado atual do código

### Arquivos modificados/criados

| Arquivo | Status | Linhas |
|---------|--------|--------|
| `conscio/models.py` | modificado | +204 (autodiscovery, probes, detect chain) |
| `conscio/engine.py` | modificado | +8 (autodiscover call no init) |
| `conscio/graphify_bridge.py` | **novo** | 230 |
| `tests/test_model_registry_autodiscover.py` | **novo** | 209 (21 testes) |
| `tests/test_graphify_bridge.py` | **novo** | 180 (16 testes) |
| `docs/graphify-autosync-design.md` | **novo** | 68 |
| `README.md` | modificado | +31 (Graphify section) |
| `CHANGELOG.md` | modificado | +35 (v1.3.3 entry) |
| `pyproject.toml` | modificado | versão 1.3.3 |

### O que NÃO foi tocado

- Módulos de consciência (Skeptic, TrustMatrix, Quarantine, Risk gating, Mixed-cortex, SkillLibrary)
- `session_rag.py` / `session_rag_factory.py`
- Output filter, dreaming, coherence, semantic
- EventBus, ContentStore, TokenTracker
- Zero breaking changes na API pública

---

## 3. O que falta pra v1.5 Live

### 3.1 SensorAdapter (não implementado)

O design do Graphify auto-sync (ver `docs/graphify-autosync-design.md`) precisa de:

```python
class GraphifySensor:
    """Watches graphify-out/ for changes and triggers re-index."""
    
    def poll(self) -> bool:
        current = self._graph_hash()  # SHA256 do graph.json
        if current != self._last:
            self._last = current
            return True  # triggers re-index no ContentStore
        return False
```

### 3.2 Daemon/Watch mode

O Graphify tem `--watch` e `graphify hook install`. O Conscio precisa de um daemon que:
1. Roda em background
2. Detecta mudanças no graphify-out (via hash ou filesystem watch)
3. Re-indexa automaticamente no ContentStore
4. Emite evento no EventBus (`graphify:updated`)

### 3.3 Inner Monologue integration

O `InnerMonologue.reflect()` pode consultar o grafo do Graphify para:
- Listar módulos não testados
- Identificar dependências não cobertas
- Gerar reflexões com base em estrutura real do código

### 3.4 GoalGenerator integration

O `GoalGenerator` pode gerar goals estruturais:
- "Módulo X tem 3 dependências não testadas"
- "Comunidade Y não tem reflexões associadas"
- "Padrão Z não foi validado em runtime"

### 3.5 CoherenceEngine: dimensão estrutural

Nova dimensão no score de coerência:
- **structural_score**: alinhamento entre o que o agente diz sobre si e o que o grafo mostra
- Compara entidades no WorldModel com entidades no Graphify
- Detecta "o agente diz que conhece o módulo X mas o grafo mostra que X depende de Y que não está no WorldModel"

---

## 4. Recomendações pra implementação

1. **Não acoplar Graphify ao Conscio.** A ponte é opcional. O Conscio deve funcionar 100% sem Graphify. O `auto_index_graphify()` já faz essa separação.

2. **Usar `graphify hook install` como setup padrão.** É mais leve que `--watch` (sem daemon). O rebuild acontece no commit, o re-index acontece no próximo `engine.reflect()`.

3. **Hash-based change detection.** Não usar filesystem watchers (inotify/fsevents) — são frágeis em containers e CI. SHA256 do `graph.json` é simples e confiável.

4. **Lazy re-index.** Não re-indexar em tempo real. Verificar hash no início de cada ciclo `reflect()` e re-indexar se mudou. Isso evita overhead desnecessário.

5. **Comunidades como contexto de reflexão.** Quando o `InnerMonologue` refletir sobre um módulo, injetar a comunidade correspondente do grafo como contexto adicional. Isso dá ao LLM informação sobre dependências e relacionamentos.

6. **Hyperedges como templates de goal.** Cada hyperedge (pipeline arquitetural) pode virar um template de goal: "verificar se o pipeline X está completo e coerente".

---

## 5. Commits

```
fc38d1e docs(graphify): auto-sync design notes for v1.5 Live
37fddd7 docs: Graphify as recommended companion + CHANGELOG update
cc2aa1f feat(graphify): bridge for Graphify knowledge graph RAG integration
323cbd9 fix(probe): LM Studio fallback when API lacks context_length
790f9d6 release: v1.3.3 — universal model autodiscovery
8c8888f feat(autodiscover): Anthropic + Google probes, engine integration
d068ac7 feat(models): universal autodiscovery of model context windows
```

---

## 6. Graphify: por que é recomendado

O Graphify transforma o Conscio de **consciência de runtime** pra **consciência estrutural**:

| Sem Graphify | Com Graphify |
|--------------|--------------|
| "Estou processando dados" | "Estou no módulo X que depende de Y e Z" |
| "Alguns módulos são menos testados" | "semantic.py tem 12 funções, 3 não testadas" |
| "Detectei uma anomalia" | "A anomalia está no pipeline Act→Audit→Ledger" |
| "Proponho modificar o output_filter" | "output_filter afeta 4 módulos downstream" |

**Instalação recomendada:**
```bash
pip install graphifyy
cd /path/to/conscio
graphify . --no-viz
graphify hook install  # auto-rebuild on commit
```
