Avaliação do plano: bom, bem mais consistente que a spec, mas ainda não aprovo para implementação direta.

Nota: 8.6/10.

O plano já absorveu várias correções que eu pedi na spec:

- PK do catálogo agora é (origin_instance_id, content_sha256) — correto.
- content_sha256 exclui published_ts — correto.
- published_ts é metadata do catálogo, não parte do artifact.
- conscio.db é aberto read-only.
- Sem PRAGMA em conscio.db.
- Label tem validação.
- Existing-file edits estão corretos: act.py, cli.py, __init__.py.

Mas tem bugs reais no plano.

Bloqueantes

1. publish.run() pode quebrar com skill local malformada

Em docs/...plans/...:1086-1101, o publish faz:

tool_seq=json.loads(s["tool_seq"])
plan_template=json.loads(s["plan_template"])


Se uma row local em conscio.db estiver corrompida, o comando quebra.

Isso viola a postura “não crashar em store corrompido”.

Correção exigida:

- envolver parse em try/except
- pular row malformada
- contar como malformed ou skipped
- não poluir o catálogo
- não crashar

Também validaria goal_fp == goal_fingerprint(goal_text) antes de publicar.

2. publish._open_conscio_ro() precisa quoting correto de URI

Em docs/...plans/...:1055-1059:

sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


Se o path tiver espaço, ?, #, % ou caractere especial, isso pode falhar.

Correção exigida:

sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


Ou usar urllib.parse.quote.

3. importer.revalidate() não checa schema_version

Em docs/...plans/...:1257-1289, o revalidate checa shape, fp e consistência, mas ignora:

body.get("schema_version")


Se vier schema_version: 999, ainda passa.

Correção exigida:

if body.get("schema_version") != artifact.ARTIFACT_SCHEMA:
    return RevalidationOutcome("malformed", "unsupported schema_version")


4. importer.run() deve armazenar campos derivados do artifact, não do catalog denormalizado

Em docs/...plans/...:1306-1313, ele salva:

tool_seq=cr.tool_seq
plan_template=cr.plan_template


Mas o artifact JSON é a fonte autoritativa. O catálogo tem campos denormalizados por conveniência.

Correção exigida:

- revalidate retorna o body parseado quando ok
- importer salva json.dumps(body["tool_seq"], ...)
- importer salva json.dumps(body["plan_template"], ...)
- ou reparseia o artifact_json antes de inserir

Isso evita quarantine com denormalização inconsistente.

5. CLI unknown subcommand está errado no teste

Em docs/...plans/...:1401-1403:

assert cli.main(["bogus"]) == 2


Mas argparse.parse_args() vai levantar SystemExit(2), não retornar 2.

Correção exigida:

Ou mudar o teste para:

with pytest.raises(SystemExit) as exc:
    cli.main(["bogus"])
assert exc.value.code == 2


Ou melhor: capturar SystemExit dentro de cli.main() e retornar o código.

Eu prefiro retornar 2.

6. CLI precisa tratar erros sem traceback

Em docs/...plans/...:1500-1528, show pode quebrar em:

- int(args.quarantine) com valor inválido
- json.loads(row.artifact_json.decode("utf-8")) com artifact corrupt

Também id --set-label pode levantar ValueError.

Correção exigida:

- validar rowid
- tratar JSON corrupt no show
- capturar ValueError, NoosphereIdentityError, UnicodeDecodeError, json.JSONDecodeError
- retornar 1 ou 2 com mensagem limpa

Ajustes obrigatórios menores

7. identity._write_atomic() deve garantir write completo

Em docs/...plans/...:361-373, usa:

os.write(fd, blob)


os.write pode escrever parcialmente.

Correção:

while written < len(blob):
    written += os.write(fd, blob[written:])


8. Integration smoke deve tamperar BLOB corretamente

Em docs/...plans/...:1714-1717:

artifact_json = artifact_json || ' '


Prefira:

artifact_json = artifact_json || X'20'


Mais explícito para BLOB.

9. Manual smoke precisa de seed reproduzível
 
Em docs/...plans/...:1803-1808, diz “seed /tmp/a/conscio.db com uma skill”.

Eu exigiria um snippet mínimo ou um helper de teste, senão a verificação manual fica ambígua.

Pontos que estão bons

- Task 1: extrair goal_fingerprint está certo.
- Task 2: paths com HERMES_HOME está certo.
- Task 3: identity hard-fail em corrupt está certo.
- Task 4: artifact content-only hash está certo.
- Task 5: catálogo com PK (origin_instance_id, content_sha256) está certo.
- Task 6: quarantine per-instance está certo.
- Task 7: read-only proof está certo, depois do ajuste de URI.
- Task 8: static revalidation está quase certo; falta schema_version + body derivado.
- Task 9: CLI surface está certa; falta robustez.
- Task 10: engine-free proof está certo.
- Task 11: two-instance smoke está certo; ajuste pequeno no tamper.
- Task 12: version/docs/ship está certo.

Veredito

Plano aprovável após correções.

Não mandaria implementar antes de corrigir principalmente:

1. publish não pode crashar com JSON local ruim
2. URI read-only precisa quoting
3. importer precisa checar schema_version
4. importer precisa salvar body parsed, não denormalizado cru
5. CLI precisa retornar erro limpo em vez de traceback/SystemExit
6. identity write precisa write_all

Depois desses ajustes, eu aprovo para execução TDD.

RELATÓRIO DE TESTE DE AMBIENTE - CONSCIO

Data do teste: 2026-06-21
Repositório: /home/ubuntu/clawd/Repos/Conscio
Branch: main
HEAD: 6ebccec
Tag atual: v2.1.0
Working tree: limpo em código, com docs internos não versionados já listados abaixo.

Ambiente base:

- Python: 3.11.14
- sqlite3: 3.50.4
- HERMES_HOME: /home/ubuntu/.hermes
- Storage Conscio: /home/ubuntu/.hermes/consciousness
- conscio.db existe: sim
- journal_mode do conscio.db: wal
- noosphere.db existe: não
- Espaço livre em disco: 10.6 GB
- Disco total: 193.63 GB

Versão importada:

- conscio.__version__ = 2.1.0
- goal_fingerprint importou corretamente de conscio.agency.act
- goal_fingerprint_sample = 6e458146f2634508

Banco real:

- /home/ubuntu/.hermes/consciousness/conscio.db
- skills_count = 0
- Isso confirma que o banco real está limpo, sem skills carregadas.
- Para smoke test real do Noosphere, vai precisar de seed reproduzível, porque não há skill estatística no banco atual.

Teste de conexão somente leitura:

- URI mode=ro funcionou.
- PRAGMA journal_mode retornou wal.
- Isso fecha o requisito de leitura somente leitura do conscio.db pelo noosphere CLI.

Teste de suite:

- Comando: python3 -m pytest -q
- Resultado: 1565 passed
- Falhas: 0
- Duração: 81.89s

Ruff:

- Comando: python3 -m ruff check conscio tests
- Resultado: All checks passed

Pontos de atenção antes de implementação v2.2:

1. docs/superpowers/ ainda não está no .gitignore.
2. Existem docs internos não versionados:
   - docs/REPORT_v1.5_HANDOFF.md
   - docs/auditedF2-volition-clone-prompt.md
   - docs/blueprint v1.0.0.md
   - docs/f3-discipline-analysis.md
   - docs/fable-quality-patterns.md
   - docs/fable5-F1-method-analysis-report.md
   - docs/fable5-agentic-clone-prompt.md
   - docs/superpowers/
3. Disco está melhor, mas ainda vale limpar .venv-herm-audit/ e dist-v201/ antes de build/push grande.
4. conscio.db real tem 0 skills, então teste de smoke do Noosphere precisa criar fixture temporária ou seed isolado.

Conclusão:

Ambiente Conscio está funcional para começar implementação do Noosphere Core.

- Python OK
- SQLite OK
- conscio.db real legível em modo read-only
- versão atual v2.1.0
- suite completa passando
- ruff passando
- noosphere.db ainda não existe, então o primeiro teste real pode criar /tmp/noosphere.db ou ~/.hermes/noosphere.db sem colidir com estado existente.
