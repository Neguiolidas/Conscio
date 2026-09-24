# Contributing to Conscio

Thank you for your interest in contributing! This guide covers the essentials.

## Quick Start

```bash
git clone https://github.com/Neguiolidas/Conscio.git
cd Conscio
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

## Development Workflow

1. **Create a branch** from `main`: `git checkout -b feature/your-feature`
2. **Write tests first** (TDD — RED → GREEN → REFACTOR)
3. **Implement** the minimum to pass
4. **Run the suite one file per process** (the full run in a single
   pytest process OOMs on small machines; CI matches this rule):

   ```bash
   for f in tests/test_*.py; do pytest "$f" -q; done
   pytest tests/test_<module>.py -v    # a specific module
   ```

5. **Lint & types**: `ruff check conscio/ tests/` and `pyright conscio/`
6. **Commit** with conventional messages: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
7. **Push** and open a Pull Request against `main`

## Testing

- **~4,250 tests** (333 files) — all must pass before merge; the count is
  re-measured on every release, so trust `pytest --collect-only -q` over any
  number printed in docs
- Run tests **one file per process** (see above)
- **Do not skip tests** — if a test is genuinely flaky, mark it
  `@pytest.mark.xfail(strict=True)` with the reason in the marker and open an
  issue; never `skip`

## Code Style

- **Formatter/Linter**: Ruff (`ruff check conscio/ tests/`)
- **Types**: Pyright is the enforced checker (strict configuration in
  `pyproject.toml`); `mypy` is not used by this project
- **Line length**: 100 (set in `pyproject.toml [tool.ruff]`)
- **Imports**: Absolute imports from `conscio.`

## Architecture Notes

- **Core modules** (engine, meta_cognition, goal_generator, etc.) — changes here affect everything
- **SQLite modules** (content_store, event_bus, token_tracker, world_model, session_lifecycle) — each manages its own tables via migrations
- **Storage**: everything the engine writes lives under its *space* — one
  directory per agent host, one file per concern; `conscio.db` is the
  event/ledger store (no FTS5 — full-text lives in `content_store.db` and
  `obs.db`)
- **Confidence contract (v4.7+)**: any confidence-like number a producer
  emits must be a `ConfidenceValue` (`conscio/calibration.py`) — category
  `none/asserted/derived/measured`; `None` cold start is absence, never a
  fabricated prior; `as_gate_input()` raises on `none`
- **Embeddings (v4.7+)**: native-first with zero network probes;
  Ollama/LM Studio are explicit opt-ins via `CONSCIO_EMBED_BACKEND`
- **ContentStore.index()** takes `label` as first arg (not `source`)
- **EventBus.emit()** returns `int` (event_id), not an Event object
- **SessionRAG** is lazy-initialized; use `ConsciousnessEngine._RAG_DISABLED` sentinel to disable in tests
- **Testing modules with optional dependencies**: do not
  `mock.patch("some_optional_pkg.X")` — the patch itself raises
  `ModuleNotFoundError` when the package is absent (CI installs only light
  deps). Inject a fake module into `sys.modules` instead.

## Pull Request Checklist

- [ ] The full suite passes (one file per process)
- [ ] New code has tests, and every new test has an adversarial mutant that
      turns it red
- [ ] No hardcoded secrets or API keys
- [ ] `ruff check conscio/ tests/` passes with zero errors
- [ ] `pyright conscio/` passes with zero errors
- [ ] Docs updated in the same PR when behavior changes (README, USAGE,
      guides) — docs are part of every ship; nothing stays stale
- [ ] CHANGELOG.md updated (if user-facing change)
- [ ] Commit messages follow conventional format

## Reporting Issues

- Use [GitHub Issues](https://github.com/Neguiolidas/Conscio/issues)
- Include: Python version, OS, minimal reproduction steps, full traceback

## License

By contributing, you agree that your contributions will be licensed under the [GNU Affero General Public License v3.0 or later](LICENSE).
