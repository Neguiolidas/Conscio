# Fable Method → Conscio Garimpo + Claude Plugin Plan

Data: 2026-07-23
Fonte: https://github.com/Sahir619/fable-method (1.8k stars, MIT)

## 5 Features para implementar no Conscio (engine Python)

### 1. Intent Gate (`intent_gate()`)
- **Onde:** Nova gate tool em `conscio/gates.py`, method no `engine.py`, MCP tool
- **O que faz:** Antes de editar comportamento, agente escreve `INTENT: code does X, check expects Y, spec says Z`. Se discordam, bloqueia e emite `gate:vetoed`. Se concordam, libera.
- **Previne:** Failure mode #1 — agent "corrige" código certo porque teste estava errado
- **VALID_TYPES novos:** `gate:intent`

### 2. Twin Check (no `delivery_check()`)
- **Onde:** Adicionar step no `delivery_check()` existente em `conscio/gates.py`
- **O que faz:** Após corrigir bug, busca mesmo padrão de defeito em todo projeto via `search_files` (ripgrep). Emite `TWINS:` line se encontrar duplicatas.
- **Previne:** Failure mode #17 — defeito corrigido num spot, cópias vivem em outros
- **Depends:** `search_files` (já disponível via hermes_tools)

### 3. Hard retry bound per-issue
- **Onde:** Modificar `cognitive_cycle()` no `engine.py` para trackerar `fix_cycles` por issue
- **O que faz:** 3 ciclos de fix-verify falhados no mesmo issue = stop, emite `system:retry_exhausted`, hand-back com output + hipótese. Hoje AwakeBudget limita `max_cycles=3` para o ciclo inteiro, não per-issue.
- **Previne:** Failure mode #13 — retry thrash sem exit

### 4. Surprise routing (`investigate()` → `cognitive_cycle()` feedback)
- **Onde:** Conectar output de `investigate()` ao input do próximo `cognitive_cycle()`
- **O que faz:** `investigate()` encontra contradição → emite `anomaly:surprise` no EventBus. `cognitive_cycle()` checa `anomaly:surprise` não resolvidos no início → re-roteia (atualiza goals ou volta a classify).
- **Previne:** Failure mode #9 — agent plows through surprises
- **VALID_TYPES novos:** `anomaly:surprise`

### 5. Judge re-run mode (`evaluate(adversarial=True)`)
- **Onde:** Adicionar `adversarial=True` parameter ao `evaluate()` em `conscio/evaluation.py`
- **O que faz:** Em vez de scoring heurístico (6-axis rubric), re-executa: (a) coleta claims do output, (b) re-roda testes/build, (c) diff do que mudou vs claimado, (d) verdict: VERIFIED / VERIFIED WITH CAVEATS / REFUTED.
- **Base:** fable-judge provou que re-executar > scoring

### Bônus: Catálogo de 18 Failure Modes
- **Onde:** `conscio/data/failure_modes.json`, consultável via `investigate()` e `evaluate()`
- **O que faz:** Mapeia sintoma → step que previne. `investigate()` cruza eventos do EventBus contra o catálogo: "evento X é sintoma do failure mode 14 (verification theater)"

---

## Plano: Plugin Nativo do Claude Code

### Estrutura:
```
conscio-plugin/
├── .claude-plugin/
│   ├── plugin.json          # manifest: name, version, mcp_servers, skills
│   └── marketplace.json     # installable via /plugin marketplace add
├── skills/
│   ├── conscio-method/      # think: classify, intent gate, surprise routing
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── failure-modes.md
│   │       └── flowcharts.md
│   ├── conscio-loop/        # act: cognitive_cycle, retry bounds, twin check
│   │   └── SKILL.md
│   ├── conscio-judge/       # prove: evaluate(adversarial=True)
│   │   └── SKILL.md
│   └── conscio-domain/      # grow: domain adapters
│       └── SKILL.md
├── AGENTS.md                 # standalone, não depende do Hermes
├── install.sh                # pip install conscio + copia skills
├── README.md
└── LICENSE                   # AGPL-3.0
```

### plugin.json:
```json
{
  "name": "conscio",
  "version": "3.4.0",
  "description": "Cognitive refinement layer: intent gates, adversarial verification, surprise routing, domain adapters",
  "mcp_servers": ["conscio-mcp"],
  "skills": ["conscio-method", "conscio-loop", "conscio-judge", "conscio-domain"]
}
```

### Diferencial vs Fable:
- Fable = puro texto (SKILL.md), esquece entre sessões
- Conscio = SKILL.md + MCP server com engine real (EventBus FTS5, ContentStore, KnowledgeGraph, Hallways)
- Plugin Conscio lembraria entre sessões via persistência SQLite

### Pendência: nome do repositório GitHub
- Aguardar resolução com Sahir619 sobre o nome "fable" vs "conscio"
- Plugin só pode ser publicado após nome resolvido

---

## Mapeamento Fable → Conscio

| Fable Method | Conscio equivalente | Status |
|---|---|---|
| Step 0: Classify ask | cognitive-execution-pattern router | ✅ já tem |
| Step 1: Define done | `delivery_check()` criteria | ✅ parcial |
| Step 2: Evidence (parallel) | `investigate()` + sensors | ✅ parcial |
| Step 3: Decide + auth gate | `decide()` + `act()` safety gates | ✅ parcial (falta AUTH quote) |
| Step 4: Act + intent gate | `act()` | ❌ falta intent gate |
| Step 5: Verify + retry bound | `evaluate()` + AwakeBudget | ✅ parcial (falta re-run + per-issue bound) |
| Step 6: Report + twin check | `delivery_check()` | ✅ parcial (falta TWINS:) |
| Failure modes catalog | anomaly detection | ❌ falta catálogo enumerado |
| Domain adapters | skills system | ✅ parcial (falta fraud tables) |
| fable-judge adversarial | `evaluate()` | ❌ falta adversarial re-run mode |
| Surprise routing | `investigate()` | ❌ falta feedback loop |
| Triviality gate | MCP trigger rules | ✅ já tem (cost of reversal) |
| Fit gate | cognitive-execution-pattern | ✅ já tem |
