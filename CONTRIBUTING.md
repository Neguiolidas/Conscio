# Contributing to Conscio

Thank you for your interest in contributing! This guide covers the essentials for local setup, testing standards, CI gates, and contribution rules.

## Quick Start

```bash
git clone https://github.com/Neguiolidas/Conscio.git
cd Conscio
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

## Development Workflow

1. **Create a branch** from `main`: `git checkout -b feature/your-feature`
2. **Write tests first** (TDD — RED → GREEN → REFACTOR).
3. **Tests must have teeth**: every new test or fix must prove its teeth — deliberately break what it claims, watch it fail RED, then restore it GREEN.
4. **Implement** the minimum code required to pass.
5. **Run tests one file per process**: running the full suite in a single pytest process exhausts system RAM (OOMs). CI enforces one test file per process; your local run must mirror this rule:

   ```bash
   for f in tests/test_*.py; do pytest "$f" -q; done
   pytest tests/test_<module>.py -v    # a specific module
   ```

   Always run the architectural tests to protect system contracts:
   ```bash
   pytest tests/test_*guard*.py -q
   pytest tests/test_*contract*.py -q
   pytest tests/test_*invariant*.py -q
   pytest tests/test_*no_*.py -q
   ```

6. **Run all 4 CI Linters & Static Analysis** exactly as CI runs them:
   - **Ruff**: `ruff check conscio/ tests/`
   - **Pyright**: `pyright conscio/`
   - **Vulture**: `vulture conscio/ vulture_whitelist.py --min-confidence 60`
   - **Bandit**: `bandit -r conscio/ -q -ll --exit-zero -c .bandit`
     > [!WARNING]
     > The `--exit-zero` flag in the CI bandit command prevents exit code failure on medium/low warnings, but **exit-zero does not mean clean**. You must read the bandit output and ensure **0 HIGH** severity findings. A clean run requires 0 HIGH issues.

7. **All code and documentation must be in English.**
8. **Internal docs are never versioned**: implementation plans, specs, PRDs, audits, field reports, and scratch notes stay on local disk and must **never** be committed. Keep `.gitignore` and mkdocs `exclude_docs` in step — git ignoring a file does not stop mkdocs from publishing it, because the local site build reads the disk.
9. **Docs in the same PR**: update documentation (README, USAGE, guides) in the same PR whenever behavior, tool schemas, or flags change. Docs are part of every ship; nothing stays stale.
10. **Commit & PR**: commit with conventional messages (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`) and open a Pull Request against `main`.

## Testing

- **~4,530 tests across 370 files** — all must pass before merge. Re-measured on every release; trust `for f in tests/test_*.py; do pytest "$f" -q; done` over any static number.
- **Run tests one file per process** (see above).
- **Do not skip tests** — if a test is genuinely flaky, mark it `@pytest.mark.xfail(strict=True)` with the explicit reason in the marker and open an issue; never use bare `skip`.
- **Architectural & Guard tests**: tests covering invariants, guards, schema contracts, and lack of bare calls (`guard`, `contract`, `invariant`, `no_`) must always remain green.

## Code Style & Linters

- **Formatter & Linter**: Ruff (`ruff check conscio/ tests/`). Line length: 100 (`pyproject.toml`).
- **Types**: Pyright is the enforced type checker (`pyright conscio/`). `mypy` is not used.
- **Dead Code**: Vulture with whitelist (`vulture conscio/ vulture_whitelist.py --min-confidence 60`).
- **Security**: Bandit (`bandit -r conscio/ -q -ll --exit-zero -c .bandit`). Must maintain 0 HIGH findings.
- **Imports**: Absolute imports from `conscio.`.

## Architecture Notes

- **Core modules** (`engine`, `meta_cognition`, `goal_generator`, `council_traits`, etc.) — changes here affect the entire framework.
- **SQLite modules** (`content_store`, `event_bus`, `token_tracker`, `world_model`, `session_lifecycle`, `obsstore`) — each manages its own schema via migrations.
- **Storage**: everything the engine writes lives under its *space* — one directory per agent host, one file per concern; `conscio.db` is the event/ledger store (no FTS5 — full-text lives in `content_store.db` and `obs.db`).
- **Confidence contract (v4.7+)**: any confidence-like number a producer emits must be a `ConfidenceValue` (`conscio/calibration.py`) — category `none/asserted/derived/measured`; `None` cold start is absence, never a fabricated prior; `as_gate_input()` raises on `none`.
- **Embeddings (v4.7+)**: native-first with zero network probes; Ollama/LM Studio are explicit opt-ins via `CONSCIO_EMBED_BACKEND`.
- **Testing modules with optional dependencies**: do not `mock.patch("some_optional_pkg.X")` — the patch itself raises `ModuleNotFoundError` when the package is absent in light CI environments. Inject a fake module into `sys.modules` instead.

## Pull Request Checklist

- [ ] The full suite passes (one file per process).
- [ ] Architectural tests (`guard`, `contract`, `invariant`, `no_`) pass.
- [ ] New code has tests, and every new test has an adversarial mutant (teeth proof) that turns it red.
- [ ] No hardcoded machine paths (`/home/...`), hostnames, personal agent names, or secrets.
- [ ] All 4 CI linters pass:
  - `ruff check conscio/ tests/` (0 errors)
  - `pyright conscio/` (0 errors)
  - `vulture conscio/ vulture_whitelist.py --min-confidence 60` (0 errors)
  - `bandit -r conscio/ -q -ll --exit-zero -c .bandit` (0 HIGH findings)
- [ ] Docs updated in the same PR when behavior changes (README, USAGE, guides, CHANGELOG).
- [ ] Both code and documentation are written strictly in English.
- [ ] Internal notes/plans/PRDs are not tracked; `.gitignore` and `exclude_docs` in `mkdocs.yml` are synchronized.
- [ ] `mkdocs build --strict` builds without warnings or errors.
- [ ] Commit messages follow conventional commit format.

## Reporting Issues

- Use [GitHub Issues](https://github.com/Neguiolidas/Conscio/issues).
- Include: Python version, OS, minimal reproduction steps, full traceback.

## License

By contributing, you agree that your contributions will be licensed under the [GNU Affero General Public License v3.0 or later](LICENSE).
