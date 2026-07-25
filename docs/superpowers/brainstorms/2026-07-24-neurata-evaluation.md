# Neurata Evaluation — 2026-07-24

## Contexto

Neguiolidas pediu avaliação do Neurata igual fizemos com o Conscio: entender quando vale a pena chamar, o que funciona, o que quebra. Sem cron, sem automação — uso cru e manual pra sentir a fricção real.

---

## 1. Convergência Home-Layout (recado do Claude)

**Pergunta do Claude:** Conscio e Hermet gravam no mesmo usage.log? Rodar `neurata doctor | grep home-layout` nos dois contextos pra confirmar convergência.

**Resultado:**

```
=== Hermet context ===
  home-layout: ok — /home/ubuntu/.neurata
  index: ok — /home/ubuntu/.neurata/index.db
  usage: ok — 1 entrada(s) com uso registrado
  last-tick: ok — último tick=2026-07-24T22:00:01.514762+00:00
  gate: ok — 1/10 dias distintos (melhor janela de 14d)

=== Conscio bridge context ===
  available: True
  home-layout: ok — /home/ubuntu/.neurata
  index: ok — /home/ubuntu/.neurata/index.db
  usage: ok — 1 entrada(s) com uso registrado
  last-tick: ok — último tick=2026-07-24T22:00:01.514762+00:00
  gate: ok — 1/10 dias distintos (melhor janela de 14d)
```

**Veredito:** CONVERGEM. Hermet e Conscio apontam pro mesmo `NEURATA_HOME=/home/ubuntu/.neurata`, mesmo `index.db`, mesmo `usage.log`. Não há `NEURATA_HOME` override em nenhum dos dois — ambos usam o default. O doctor de cada um lê as mesmas contagens.

---

## 2. Bugs Encontrados e Corrigidos

### Bug 1: Doctor Probe — exit code 1 em warnings (corrigido)

**Commit:** `ef99b7b fix(neurata): doctor probe tolerates non-zero exit (warnings)`

`neurata doctor` retorna exit code 1 quando há warnings (ex: "sem last_reindex"), mas ainda emite JSON válido com `contract_version`. O `_run()` retornava `None` em qualquer exit não-zero, fazendo o bridge ficar permanently `available: False`.

**Fix:** `_run_raw(strict=False)` para o probe path. `_run()` mantém strict para query/deposit.

### Bug 2: deposit() args errados (corrigido)

**Commit:** `3d26c8a fix(neurata): deposit() uses explicit kwargs instead of **meta`

`deposit(body, **meta)` montava `--{k}` pra qualquer keyword arg, mas Neurata CLI só aceita `--title --type --env --agent --session`. Qualquer outro kwarg causava `unrecognized arguments` (exit code 2).

**Fix:** Signature explícita com `title`, `type`, `env`, `agent`, `session` como keyword-only args.

---

## 3. Avaliação Prática

### O que funciona bem

| Feature | Estado | Detalhe |
|---------|--------|---------|
| Deposit | ✓ | 0.26s por entrada, 20/20 no stress test |
| Deposit via --file | ✓ | Contorna problemas de aspas/shell escaping |
| Reindex | ✓ | Rápido (36 entradas em <1s), sem lock issues |
| Query FTS5 | ✓ | Case-insensitive, termos inexistentes retornam vazio |
| Query específica | ✓ | 7 termos retornam resultado relevante |
| Shelf insights | ✓ | Ranqueia por score, detecta conflitos, marca candidatos a arquivamento |
| Git snapshots | ✓ | Commit automático do inbox → index |
| Doctor | ✓ | Diagnostica bem (freshness, gate, usage, lock, schema) |
| Stress test | ✓ | 20 depósitos em 5.1s sem falha |

### O que quebra ou é fraco

| Problema | Severidade | Detalhe |
|----------|-----------|---------|
| Sem dedup | Alto | Deposit duplicado cria nova entrada sem detectar — hash é gerado mas não comparado |
| Sem validação mínima | Médio | Aceita depósito de string vazia sem erro |
| Sem semântica | Médio | "receita bolo de chocolate" retornou 4 resultados — FTS5 puro pega termos comuns, sem embeddings, sem reranking |
| Scores muito baixos | Baixo | Todos scores entre 0.03-0.07. Difícil distinguir sinal de ruído sem threshold calibration |
| Conflitos não detalham | Médio | Detecta N conflitos mas não diz quais entradas conflitam |
| Edges sempre 0 | Baixo | `edges=0` em todos reindexes — link resolution não está acontecendo |
| Conscio bridge deposit() | Corrigido | Antes passava args inexistentes na CLI |

### Queries de controle

| Query | Result | Veredito |
|-------|--------|----------|
| "conscio auto_index kg_builder" | 5 results, top score 0.0415 | ✓ Funciona |
| "pentest pnzx firebase vault" | 1 result, score 0.0377 | ✓ Encontra |
| "neurata bridge doctor probe" | 2 results, score 0.0434 | ✓ Encontra |
| "awake mode sensors budget" | 1 result, score 0.0475 | ✓ Encontra |
| "evaluate 5 axis rubric accuracy" | 2 results, top score 0.0660 | ✓ Melhor score |
| "kubernetes terraform aws" | 0 results | ✓ Controle negativo correto |
| "receita bolo de chocolate" | 4 results, score 0.0427 | ✗ Falso positivo — FTS5 sem semântica |
| "docker kubernetes deploy golang" | 0 results | ✓ Controle negativo correto |
| UPPERCASE vs lowercase | Idêntico | ✓ Case-insensitive confirmado |

### Métricas finais

- Entradas indexadas: 36 (após stress test + depósitos manuais)
- Uso registrado: 16 entradas
- Gate: 2/10 dias distintos
- Conflitos detectados: 1
- Snapshots: 3
- Speed: 0.26s/deposit, <1s/reindex com 36 entradas

---

## 4. Quando vale a pena chamar

### Já vale agora (com 36 entradas)
- `neurata deposit` no fim de cada session produtiva — captura contexto, não custa nada
- `neurata shelf --insights` semanalmente — vê o que está sendo consultado vs stale
- `neurata query` antes de tarefas familiares — já retorna resultados com termos específicos

### Não vale ainda
- Confiar cegamente nos scores — tudo é 0.03-0.07, não tem separação sinal/ruído
- Confiar em conflitos — não detalha quais entradas conflitam
- Usar como única fonte de memória — Conscio ContentStore + KG é muito mais rico
- Automatizar depósitos via cron — atrapalha entendimento do fluxo natural (Neguiolidas vetou)

---

## 5. Decisão

Uso manual, sem cron, sem automação. Depositar e consultar na mão pra sentir a fricção real e entender o fluxo. Em menos de 1 semana de uso cru dá pra saber se vale a pena keep ou drop.
