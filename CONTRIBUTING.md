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
4. **Run the full suite**: `python -m pytest tests/ -q`
5. **Lint**: `ruff check conscio/ tests/`
6. **Commit** with conventional messages: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
7. **Push** and open a Pull Request against `main`

## Testing

- **707+ tests** — all must pass before merge
- Run: `python -m pytest tests/ -q --tb=short`
- Coverage: `python -m pytest tests/ --cov=conscio --cov-report=term-missing`
- **Do not skip tests** — if a test is genuinely flaky, mark with `@pytest.mark.skip` and open an issue

## Code Style

- **Formatter/Linter**: Ruff (`ruff check conscio/ tests/`)
- **Type hints**: Encouraged but not enforced (mypy available for checking)
- **Line length**: 120 (ruff default)
- **Imports**: Absolute imports from `conscio.`

## Architecture Notes

- **Core modules** (engine, meta_cognition, goal_generator, etc.) — changes here affect everything
- **SQLite modules** (content_store, event_bus, token_tracker, world_model, session_lifecycle) — each manages its own tables via migrations
- **ContentStore.index()** takes `label` as first arg (not `source`)
- **EventBus.emit()** returns `int` (event_id), not an Event object
- **SessionRAG** is lazy-initialized; use `ConsciousnessEngine._RAG_DISABLED` sentinel to disable in tests

## Pull Request Checklist

- [ ] All 707+ tests pass
- [ ] New code has tests
- [ ] No hardcoded secrets or API keys
- [ ] Ruff passes with zero errors
- [ ] CHANGELOG.md updated (if user-facing change)
- [ ] Commit messages follow conventional format

## Reporting Issues

- Use [GitHub Issues](https://github.com/Neguiolidas/Conscio/issues)
- Include: Python version, OS, minimal reproduction steps, full traceback

## License

By contributing, you agree that your contributions will be licensed under the [GNU Affero General Public License v3.0 or later](LICENSE).
