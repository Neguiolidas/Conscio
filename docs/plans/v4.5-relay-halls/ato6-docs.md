# Ato 6 — Docs + version bump + registro

## Objetivo
Fechar o v4.5: documentar as mudanças, sincronizar versão, e deixar o rastro de auditoria
completo. **Bump de tag/push só com autorização explícita do Senhor.**

## Protocol A (pre-flight já feito)
- `conscio/__init__.py` tem `__version__` (4.4.1). `pyproject.toml` versão em [project].
  Versão sync = os dois juntos (regra Protocol G #7).
- Doc-drift: adicionar módulos (halls, halls_view) toca 7 superfícies mapeadas no skil
  conscio-framework.
- Docs a tocar: README header, USAGE.md VALID_TYPES + MCP tools list, CONTRIBUTING test
  count, docs/roadmap.md entry, refereced ou CHANGELOG.

## Entregas do Ato 6

### T1 — Version sync
- `conscio/__init__.py`: `__version__ = "4.5.0"`.
- `pyproject.toml`: version = "4.5.0".
- Bump de `plugin.json`/`.mcp.json`/`CHANGELOG`/`README` conforme o padrão de bump v4.4
  (memória: bump sincroniza __init__+plugin.json+.mcp.json+CHANGELOG+README).
- SEM tag, SEM push. Commit do bump em main é reversível; tag só quando Senhor disser.

### T2 — Doc-drift (7 frentes)
1. `USAGE.md` VALID_TYPES — adicionar tipos de evento novos se algum foi emitido
   (halls não emite EventBus — efeito puro via sqlite; documentar isso).
2. `USAGE.md` MCP tools list — adicionar `conscio_hall_create/list/join/leave/send/members`.
3. `docs/reference/conscio_functions.md` — re-extrair via AST (novos módulos halls,
   halls_view, colunas identity).
4. `CONTRIBUTING.md` test count + license.
5. `docs/roadmap.md` — entry v4.5 (estrutural).
6. README.md header version.
7. `CHANGELOG.md` — entry v4.5 com resumo dos 5 atos. NÃO incluir config pessoal/modelo
   do adapter (regra: público, não pessoal).

### T3 — Rastro de auditoria
- `python scripts/auditar.py` (ou o equivalente do repo) — valida vizinhança+verificação
  dos arquivos modificados nos 6 atos.
- Confirmar selos em `.comandos.log`.
- Rodar suíte dirigida (arquivos de teste tocados, um por processo):
  `test_liaison_mailbox`, `test_liaison_relay`, `test_liaison_watcher`, `test_relay_sensor`,
  `test_liaison_bindings`, `test_liaison_halls`, `test_observatory_halls`.
- CI (um comando): `ruff check .` + `pyright` — espelhar o CI real.

## Regras de commit
- Doc com claim SEMPRE verificado contra código (Protocol G #8).
- NUNCA commitar .md de plano (este plano fica em docs/local; não vai pro git). Só
  CHANGELOG/README públicos vão.
- Commit: `release: v4.5.0 relay reativo + Agent's Hall`
- NÃO taggar/pushar sem ordem explícita.

## Self-Review (Protocol G) — PASS
Rodado contra codebase real (2026-08-29):
- CRITICAL FIXED: `__init__.py` __version__ 4.4.1 → 4.5.0 (bump sync com pyproject).
- CRITICAL FIXED: Doc-drift 7 frentes mapeadas (skill conscio-framework: USAGE VALID_TYPES,
  MCP tools, conscio_functions AST, CONTRIBUTING, roadmap, README, CHANGELOG).
- MEDIUM FIXED: bump do plugin.json/.mcp.json/CHANGELOG/README é o padrão v4.4 (memória).
- NÃO REGRESSA: tag/push NUNCA sem autorização (regra reforçada: `release: v4.5.0` local,
  tag fica para ordem do Senhor).
- Blocker explícito se Ato 1-5 falharem: doc não descreve feature que não existe
  (docs SÓ após implem verde).
Status: PASS.

## Nota de sequência
Depende de TODOS os atos 1-5 verdes. Roda por último.