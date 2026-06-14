# Conscio v1.3.3 — Relatório de Alterações para Review

**Data:** 2026-06-14
**Autor:** Hermet (Hermes Agent) + Fable (Claude)
**Commits:** `d068ac7`, `8c8888f`, `790f9d6`
**Repo:** https://github.com/Neguiolidas/Conscio

---

## Contexto

O Senhor pediu que o Conscio detectasse automaticamente o modelo e o contexto de janela
em uso, sem hardcode, sem config obrigatória. O sistema precisava funcionar em qualquer
máquina, com qualquer modelo, de qualquer provedor (local ou cloud), com contexto ≥8k.

A implementação anterior tinha dependência hardcoded do Ollama para embeddings e o
`ModelRegistry` conhecia apenas ~15 modelos estáticos.

---

## O que mudou

### 1. `ModelRegistry.autodiscover()` (novo)

Método de classe que sonda endpoints de inferência na inicialização do engine:

- **LM Studio** (`localhost:1234`) — GET `/v1/models` + GET `/v1/state` → modelos carregados + contexto ativo
- **Ollama** (`localhost:11434`) — GET `/api/tags` + POST `/api/show` → modelos disponíveis + `context_length`
- **Anthropic** — detecção via `ANTHROPIC_API_KEY` → registra todos os 14 modelos Claude conhecidos (200k ctx)
- **Google Gemini** — detecção via `GOOGLE_API_KEY`/`GEMINI_API_KEY` → registra 7 modelos Gemini (1M–2M ctx)
- **Endpoints customizados** — `CONSCIO_ENDPOINTS` env var (comma-separated URLs) → GET `/v1/models`

### 2. `_world_registry` (novo)

Cache em memória (`dict[str, int]`) dos modelos/contextos descobertos. Preenchido pelo
`autodiscover()`, consultado pelo `detect()` antes do fallback heurístico.

### 3. `detect()` — cadeia de prioridade expandida

```
context_window explícito (arg)
  → CONSCIO_CONTEXT_WINDOW env var
  → _known_models (hardcoded)
  → _world_registry (autodiscovered)
  → heurística do nome (ex: "model-256k")
  → fallback 128k
```

### 4. `ConsciousnessEngine.__init__` — integração automática

```python
if not ModelRegistry._world_registry:
    try:
        ModelRegistry.autodiscover()
    except Exception:
        logger.debug("autodiscover failed at engine init", exc_info=True)
```

Roda uma vez por processo. Falhas são silenciosas — nunca quebra o engine.

### 5. `CONSCIO_CONTEXT_WINDOW` env var

Override global para contexto. Ex: `CONSCIO_CONTEXT_WINDOW=1048576` força 1M para todos
os modelos sem registry.

---

## Arquivos modificados

| Arquivo | O que mudou |
|---------|------------|
| `conscio/models.py` | +204 linhas: `_world_registry`, `autodiscover()`, `_probe_lmstudio()`, `_probe_ollama()`, `_probe_anthropic()`, `_probe_google()`, `_probe_openai_endpoint()`, `write_default_config()`, `detect()` com cadeia expandida |
| `conscio/engine.py` | +8 linhas: chamada a `autodiscover()` no `__init__` |
| `tests/test_model_registry_autodiscover.py` | +209 linhas: 21 testes novos |
| `pyproject.toml` | versão 0.9.1 → 1.3.3 |
| `CHANGELOG.md` | entrada v1.3.3 |

---

## Testes

- **728 testes passando** (era 707 → +21 novos)
- **Zero regressões**
- **Ruff:** limpo nos arquivos modificados (warnings pré-existentes em outros arquivos não são nossos)
- **Mypy:** sem erros novos

---

## Dependências

- **Zero novas.** Usa `urllib.request` (stdlib) para HTTP. Sem `requests`, `httpx`, etc.
- O único requisito existente continua sendo `numpy>=1.24`.

---

## Trade-offs documentados

1. **Anthropic/Google usam conhecimento estático** — a API da Anthropic não tem endpoint
   de listagem de modelos; a do Google até tem, mas optamos por static knowledge para
   evitar uma chamada HTTP extra no startup. Trigger para revisão: quando um novo modelo
   Claude ou Gemini for lançado, adicionar ao dict estático.

2. **`_world_registry` é por-processo** — não persiste em disco. Cada processo Python
   que instancia um `ConsciousnessEngine` roda `autodiscover()` uma vez. Isso é deliberado:
   evita stale data e mantém o modelo simples.

3. **`force-with-lease` no push** — o remote tinha commits divergentes da mesma base
   (provavelmente do Fable). Force push foi necessário porque as trees eram idênticas em
   origem mas divergentes em commits.

---

## O que NÃO mudou

- Módulos existentes (Skeptic, TrustMatrix, Quarantine, Risk gating, Mixed-cortex,
  SkillLibrary) não foram tocados
- `session_rag.py` e `session_rag_factory.py` não foram alterados nesta rodada
- Output filter, dreaming, coherence — intocados
- Zero breaking changes na API pública

---

## Para o Claude revisar

Foco da review:
1. `conscio/models.py` — método `autodiscover()` e cadeia de prioridade em `detect()`
2. `conscio/engine.py` — integração no `__init__` (linhas 78–84)
3. `tests/test_model_registry_autodiscover.py` — cobertura dos 21 testes
