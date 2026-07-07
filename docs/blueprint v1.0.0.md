# Blueprint: Conscio OS — Arquitetura Agêntica (v1.0.0+)

## 1. Objetivo Global

Evoluir o framework *Conscio* de um estado puramente **Advisory** (introspecção e reflexão) para um sistema **Agentic** (execução e interação externa). A orquestração usará LLMs de forma *stateless*, garantindo integridade estrutural (sintaxe via Pydantic/GBNF), integridade semântica (auditoria interna) e resiliência sistêmica (Circuit Breaker local).

## 2. Restrições Arquiteturais (Non-Negotiable Constraints)

Ao implementar as novas *features*, as seguintes regras do *codebase* devem ser respeitadas irrestritamente:

* **Preservação do Core Advisory:** O método `engine.reflect()` atual é intocável em sua natureza passiva. Ele não interage com o LLM diretamente para ação. Ele deve continuar retornando o `dict` de estado gerado por suas reflexões.
* **Isolamento de Ação:** O fluxo agêntico deve ser implementado em um novo método `engine.act()` ou `engine.dispatch()`. Este método roda *downstream* (após) o `reflect()`, consumindo os *active_goals* e a *dominant_dissonance*.
* **Filosofia Zero-Deps:** O *core* do Conscio mantém dependência apenas de `numpy>=1.24` e `sqlite3`.
* **Integração Nativa:** Não crie barramentos de mensagem soltos. Utilize o `EventBus` (com as funções `emit` e `query`), o `WorldModel` e o `SessionLifecycle` já existentes.

## 3. O Contrato de Saída (The Output Gateway)

A interface entre o framework e os *webhooks* externos deve ser fortemente tipada.

* **Pydantic Contracts:** Todas as ações devem ser mapeadas em classes Pydantic.
* **Graceful Degradation via Adapters:** A garantia de sintaxe deve ter camadas.
* *Camada Primária (Opcional):* O uso de *Constrained Decoding* (ex: `outlines` ou GBNF) deve ser implementado via plugin (`pip install conscio[constrained]`).
* *Camada Secundária (Core):* Sem o plugin, o framework utiliza o *JSON Mode* da API e realiza *retry* automático em caso de `ValidationError` do Pydantic.

## 4. O Sistema de Imunidade Semântica (Persona Swapping)

O sistema audita a si mesmo em tempo de execução via chamadas *stateless*. O código Python é o maestro manipulando as chamadas.

* **Actor Phase:** O código usa `engine.recall(world_state)` para recuperar *snippets* FTS5 e alimenta o RAG. O LLM propõe uma ação estruturada.
* **Skeptic Phase:** O código Python realiza uma *nova* chamada limpa (zero vazamento de histórico), injetando a persona "Auditor Hostil" e o JSON gerado pelo *Actor*. O LLM avalia e retorna `PASS` ou `FAIL`. A ação externa só é disparada mediante aprovação.

## 5. Circuit Breaker de Ação (The Paralysis Instinct)

Se o ciclo *Actor/Skeptic* ou a requisição da ferramenta externa falhar múltiplas vezes consecutivas (`max_action_retries`), o framework colapsa intencionalmente aquela *thread*.

* **EventBus Integration:** O colapso dispara um evento utilizando a primitiva nativa: `emit(type="error", category="system", data="Intractable dissonance...")`.
* **Persistência via Dataclass:** O bloqueio não é um dicionário em memória. O engenheiro deve:
1. Adicionar o campo `action_lockdown: bool = False` na dataclass `ConsciousnessState`.
2. Modificar o método `save_state()` para serializar a chave `"action_lockdown"`.
3. Modificar o método `load_state()` para extrair `data.get("action_lockdown", False)`.


* **Comportamento:** Com a flag ativa, o `act()` aborta precocemente novas execuções, mas o `reflect()` continua operando normalmente para registros passivos, sobrevivendo a sessões via handoff.

## 6. Matriz de Confiança Dinâmica (Substituindo o Model Tiering)

O cálculo de tentativas (`max_action_retries`) deve ser dinâmico, derivando das primitivas existentes no `MetaCognition`.

* **Capacidade Estática (`ModelInfo`):** Adicionar apenas duas *flags* booleanas à estrutura atual: `has_json_mode: bool` e `supports_gbnf: bool`.
* **Confiança Dinâmica (`MetaCognition`):** Não crie variáveis hardcoded. O `max_action_retries` deve ser computado *on-the-fly* cruzando:
* `calibration_score()` (retorna 0-1 cruzando confiança vs acurácia).
* `accuracy(task_type)` (taxa de sucesso inversa aos erros).
* O rastreio de `record_error()` e `frequent_errors()`.

## 7. InferenceAdapter (Desacoplamento de Rede)

O método `engine.act()` não deve fazer requisições HTTP hardcoded (`requests.post`) diretamente para o OpenRouter, NIM ou Ollama.

* **Interface Abstrata:** Implementar uma classe base `InferenceAdapter` com métodos genéricos como `generate_structured_action(prompt, schema)`.
* **Agnosticidade:** Diferentes provedores (API nuvem, inferência local, ou Mock para os testes) devem estender essa interface. O Conscio instanciará o adaptador correspondente no carregamento, garantindo que o núcleo agêntico permaneça totalmente desacoplado da infraestrutura de inferência.
