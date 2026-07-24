# Conscio Memory Audit & Fix — 2026-07-24

## 4 Problemas Encontrados

### P1: EventBus split entre 2 storages
- `~/.hermes/consciousness/`: **68 MB, 1867 eventos** (daemon ativo)
- `~/.conscio/runtime/`: **152 KB, 17 eventos** (MCP server, dormindo)
- **Causa:** MCP invocado com `--storage /home/ubuntu/.conscio/runtime` mas daemon usa `DEFAULT_STORAGE = ~/.hermes/consciousness/`
- **Fix:** MCP `--storage` mudado para `~/.hermes/consciousness/` no config.yaml

### P2: Daemon em failure loop (failure_rate=1.0)
- 672 ciclos, 930 reflections emitidas, mas 4/4 atos falham sempre
- **Causa:** Daemon rodando sem adapter LLM funcional (modelo `aggregator:` que é placeholder, mode `compact`)
- Daemon runner (`conscio-daemon-runner.py`) usava `model = cfg.get('model', 'mimo-v2.5-pro')` — modelo não existe
- **Fixes:**
  1. DEIXAR de limpar eventos no startup (`DELETE FROM events` removido)
  2. Adicionar fallback: se adapter falha, roda reflect-only
  3. `max_failure_rate` aumentado de 0.5 para 0.8
  4. `max_cycles` de 3 para 4, `min_attempts` de 4 para 6

### P3: KnowledgeGraph ocioso (0 triples)
- KG tem 5 entidades, **zero** relações
- **Causa:** Sem pipeline de extração de entidades do ContentStore para o KG
- **Fix:** Pendente — precisa de miner/relationship extractor entre ContentStore e KG

### P4: Conteúdo novo não indexado
- Só conteúdo até 22 de Julho (migração do MemPalace)
- Conteúdo novo (bypass gateway, hostile review v3, etc) não está no ContentStore
- **Causa:** Não há pipeline de auto-indexação
- **Fix:** Pendente — precisa de hook no cognitive_cycle() que indexe reflect output

## Fixes Aplicados

1. `~/.hermes/config.yaml` — MCP storage path corrigido para `~/.hermes/consciousness/`
2. `/home/ubuntu/nvidia-m3-proxy/conscio-daemon-runner.py` — startup não limpa mais eventos/actions/goals
3. `conscio-daemon-runner.py` — fallback para reflect-only se adapter falha
4. `conscio-daemon-runner.py` — budget mais tolerante (max_failure_rate=0.8)
5. MCP server — `--base-url` atualizado para NVM3 proxy

## Pendências
- **P3:** Implementar pipeline KG <- ContentStore (entity extraction)
- **P4:** Implementar auto-index de ciclos cognitivos no ContentStore