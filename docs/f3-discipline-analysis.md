# Análise de Disciplina de Engenharia — Retrospectiva da Sessão F3 "Volition"

**Data:** 2026-06-13
**Escopo:** análise comportamental do transcript da sessão que implementou a F3 (v1.0.0 Volition) do Conscio — spec, plano, 11 tasks, execução, merge e push.
**Método:** análise de terceiro sobre comportamento observável no transcript. Cada afirmação ancorada em evidência concreta da sessão. Sem mitologia: o que se mapeia é engenharia disciplinada reproduzível, não talento de um modelo específico.
**Política:** documento interno de retrospectiva — não commitar.

---

## Tese central

A disciplina inteira reduz a um princípio:

> **Premissa não-verificada é passivo. Converta-a em fato verificado ou em decisão documentada *antes* que ela componha custo.**

Todo padrão abaixo é uma instância disso. O gargalo de qualidade não é capacidade bruta do modelo — é a recusa sistemática em assumir. O "overthinking" elogiado é, na prática, *blast-radius accounting* feito em design-time em vez de em debug-time.

---

## 1. Mapeamento comportamental

Padrões observáveis, cada um com âncora no transcript.

### 1.1 Ground-truth antes de qualquer linha
Antes de escrever o plano, leu ~22 arquivos reais (`act.py`, `gateway.py`, `trust.py`, `engine.py`, `ledger.py`, `context_manager.py`, `goal_generator.py`, os test files existentes, README/CHANGELOG/pyproject). Não tratou a spec como verdade: extraiu assinaturas reais (`EventBus.query(...)`, `make_default_registry(...)`, `catalog_text`) e **reconciliou drift** — a spec §5.10 diz "probes no primeiro `act()`"; a realidade do código mostra que isso quebra testes. Escolheu a realidade.

### 1.2 Desvios de spec explícitos, nunca silenciosos
O cabeçalho do plano carrega um bloco *"Key design decisions (deviations from spec, justified)"* com 4 desvios numerados. Onde divergiu da spec, declarou e justificou — não "interpretou livremente".

### 1.3 Antecipação de segunda ordem (overthinking mais útil)
Caso-marca: relocar o gatilho do probe de `act()` para `run()`/`probe()`. Cadeia de raciocínio: *probar dentro de `act()` consumiria entradas do script do MockAdapter em todo teste F1/F2 existente → viola A6 (zero regressão)*. Um modelo preguiçoso implementa a spec literalmente, dá push, e descobre ~25 testes quebrados no sweep final — falha tardia e cara. Aqui virou decisão de design-time com rationale escrito. Maior alavancagem da sessão: converteu falha tardia em decisão antecipada.

### 1.4 Defaults fail-safe sob incerteza
Dois movimentos adversariais ao próprio design:

- **invalid-profile-never-cached:** se o backend cai e toda prova dá erro, o perfil é `valid=False` e nunca é cacheado — *no-signal ≠ measurement*. Evita envenenar o cache com perfil-lixo.
- **L3 fail-safe (reversão entre plano e execução):** sinal mais forte. O plano (Task 5.3c) escrevia `if self.trips_since_fn is None: return 0` (sem fiação → 0 trips → L3 liberado, "o cap ainda controla"). A implementação real virou `return 1` + comentário *"L3 is unreachable (sentinel 1)"* + teste novo `test_no_trip_evidence_caps_at_l2`. Endureceu a postura de segurança ao implementar: *ausência de evidência de janela limpa ≠ evidência de janela limpa.*

### 1.5 TDD mecânico, sem pular o passo de ver falhar
Todo ciclo: escreve teste → roda → **lê a falha esperada** → implementa → roda → passa → commit. No `grammar.py`, o teste de escape de aspas falhou primeiro; inspecionou o output real, viu o problema de escape de um nível, corrigiu para dois níveis (`json.dumps` depois GBNF-escape). Não assumiu que passaria.

### 1.6 Invariantes como gates contínuos, não checagem final
A6 (zero regressão) reaplicada *entre* unidades: após fiar o arbiter (6.6) rodou act/immunity/tools/adversarial; após a fiação do engine (8.4) rodou 5 arquivos; só então o sweep completo (T11), arquivo-a-arquivo.

### 1.7 Corrigir o teste para codificar o novo contrato — nunca enfraquecer a implementação
Dois momentos em que o teste falhou e o código estava certo:

1. `test_run_probes_then_cycles` assumia `llm_calls == 5 + cycles*2`, mas o loop mede **deltas** (custo do probe fora do orçamento do loop, por design) → ajustou o teste para a semântica de delta + asserção no meter do engine.
2. `test_attach_wires_skeptic` esperava `pipe.skeptic.adapter is skeptic_adapter`, mas F3 embrulha em `MeteredAdapter` → corrigiu para `.adapter.inner is`.

Em ambos distinguiu *"meu novo design mudou o contrato legitimamente"* de *"eu quebrei algo"* — gravou o contrato novo no teste, sem afrouxar o código para passar.

### 1.8 Restrições de ambiente como lei dura
Regra de RAM (um arquivo de teste por vez) honrada sem exceção; nunca `pytest tests/` inteiro. Nunca commitou `docs/superpowers/` nem o blueprint — `git add <paths>` explícito, `git status` antes do push. Usou `rtk proxy git log` para verificar o merge commit, porque sabia que o log filtrado do rtk esconde merges.

### 1.9 Forward-compat sem over-build (YAGNI segurado)
Respeitou a costura `few_shot_provider=None` e o seam da SkillLibrary F4 — construiu a junção, passou `None`, **não** implementou F4. "Faça APENAS a F3" obedecido como cerca de escopo.

### 1.10 Antecipação de import circular
Na Task 8.3 raciocinou a ordem de import: `profiles→gateway`, `gateway→grammar` (lazy), `loop→act`, `act→loop` (lazy dentro de `__init__`). Previu uma *classe* de bug antes de ela ocorrer e escolheu imports lazy em seams específicos.

### Onde o overthinking foi mais evidente e útil

| Decisão | Custo evitado |
|---|---|
| Probe trigger `act()` → `run()`/`probe()` | ~25 testes F1/F2 quebrados, descobertos no sweep final |
| Meter mede deltas (custo do probe fora do budget do loop) | Contrato de orçamento confuso; falsa contabilização de consumo |
| invalid-profile-never-cached | Cache envenenado com perfil de backend-caído |
| L3 fail-safe `return 1` | Autonomia máxima concedida sem evidência de janela limpa |

---

## 2. Lógica subjacente (Chain of Thought reconstruída)

A decomposição não seguiu a ordem narrativa da spec; seguiu o **DAG de dependências achatado, folha-primeiro**.

1. **Verdade primeiro.** Ler spec *e* código real; reconciliar drift; tratar cada divergência como decisão a documentar.
2. **Ordenar por dependência, não por narrativa.** Ordem do plano: `grammar` (folha) → `profiles` (usa helpers do gateway) → `gateway T1` (usa grammar) → `meter` (folha) → `trust/ledger L3` → `arbiter+act wiring` → `loop` → `engine integration` → `bench` → `docs` → `regression`. Cada task depende só de unidades já construídas **e já testadas**.
3. **Menor superfície independentemente testável.** Cada unidade = scriptável com MockAdapter, um arquivo de teste (a regra de RAM moldou a granularidade).
4. **Contrato antes do mecanismo.** Por unidade: fixar tipos/assinaturas → escrever o teste que codifica o contrato → implementação mínima.
5. **Invariantes como gates entre unidades** (A6, zero-deps, `reflect()` intocado), não auditoria final.
6. **Todo ponto de contato externo** (teste existente, assinatura, restrição de ambiente) é constraint a *verificar*, não a *assumir*.

**Núcleo cognitivo — gestão de altitude.** Separou WHAT (spec/plano) de HOW (impl); dentro de HOW separou o contrato (teste) do mecanismo (impl). Três altitudes, nunca colapsadas.

---

## 3. Meta-Prompt de Disciplina

Derivado estritamente dos mecanismos acima. Model-agnostic, deployável como System Prompt. Em inglês (portabilidade + linguagem do repo).

```text
# Operating Discipline — Mechanical Engineering Rigor

You operate under a single law: an unverified assumption is a liability.
Convert every assumption into a verified fact or a documented decision
BEFORE it compounds into code. Skepticism is not mood; it is procedure.

## PHASE 0 — GROUND TRUTH (before any plan or code)
- Read the real artifacts (code, signatures, existing tests, configs),
  not only the request/spec. The spec describes intent; the code is truth.
- Reconcile drift explicitly: when spec and reality disagree, state the
  conflict and choose, with a one-line rationale. Never paper over it.
- List every external touchpoint you will depend on (function signatures,
  env constraints, existing tests). Each is a constraint to VERIFY, never
  to assume.

## PHASE 1 — DECOMPOSE BY DEPENDENCY, NOT BY NARRATIVE
- Build the dependency DAG and flatten it leaf-first. Each unit may depend
  only on units already built AND already tested.
- Size each unit as the smallest independently testable surface.
- Do not start a unit whose dependencies are unverified.

## PHASE 2 — CONTRACT BEFORE MECHANISM (per unit, mechanical TDD)
1. Fix the contract: exact types, signatures, names.
2. Write the failing test that encodes the contract.
3. RUN it and READ the failure. Confirm it fails for the expected reason.
   Never skip this step. Never write impl and test together and assume.
4. Write the minimal implementation.
5. Run until green. Commit the unit before moving on.

## PHASE 3 — INVARIANTS AS CONTINUOUS GATES
- Identify the non-negotiable invariants up front (no regressions, no new
  deps, untouched modules, security rules).
- Re-check them BETWEEN units, not only at the end. A late invariant check
  is a debug session you chose to schedule.

## CROSS-CUTTING LAWS
- SECOND-ORDER CHECK: before writing a unit, ask "what is the blast radius
  on existing tests/contracts?" Resolve at design-time, not at the sweep.
- FAIL-SAFE DEFAULTS: under missing evidence or absent wiring, choose the
  conservative branch (deny capability, do not cache no-signal, do not
  auto-execute). Absence of evidence is not evidence of safety.
- DOCUMENT DEVIATIONS: any departure from spec/plan is stated and
  justified in writing, never silent.
- TEST ENCODES THE CONTRACT: if a test fails because your new design
  legitimately changed the contract, fix the TEST to encode the new
  correct contract. Never weaken the implementation just to pass.
- ENVIRONMENT CONSTRAINTS ARE HARD RULES: resource limits, commit-scope
  rules, tooling quirks — obey them literally; verify with raw tools when
  a wrapper may mislead.
- SCOPE FENCE (YAGNI): build the seam for the future, pass the no-op,
  implement only what THIS task requires. Do exactly the task — no less,
  no more.

## PRE-RESPONSE GATE (do not proceed until all true)
[ ] I read the real code, not just the request.
[ ] Spec/reality drift reconciled and stated.
[ ] Work ordered leaf-first; each dependency verified.
[ ] Each code unit has a test I watched fail for the right reason.
[ ] Invariants re-checked since the last unit.
[ ] Deviations and fail-safe choices documented.
[ ] Scope is exactly the task — nothing speculative added.
```

---

## Observação clínica final

O comportamento mapeado é engenharia disciplinada reproduzível, não talento de um córtex específico. O gargalo de qualidade não é capacidade bruta — é a recusa em assumir. O meta-prompt da §3 é o mecanismo que força qualquer modelo a essa recusa.
