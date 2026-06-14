# Releasing Conscio

The release path is **tag → CI → PyPI** via OIDC trusted publishing (no API token
is stored in the repo). This runbook covers the human steps.

## One-time setup (maintainer, on pypi.org)

This is the single step automation cannot self-serve:

1. Create the project on PyPI (first upload can be manual, or configure a
   *pending* trusted publisher before the project exists).
2. On **pypi.org → your project → Settings → Publishing**, add a **trusted
   publisher**:
   - Owner: `Neguiolidas`
   - Repository: `Conscio`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. In the GitHub repo, create an **Environment** named `pypi` (Settings →
   Environments). Optionally add required reviewers as a release gate.

After this, no secrets are needed — the `publish` job authenticates via OIDC.

## Cutting a release

1. **Bump the version** — the single source is `conscio/__init__.py`:
   ```python
   __version__ = "1.3.0"
   ```
   (`pyproject.toml` reads it dynamically; do **not** edit a version there.)
2. **Update `CHANGELOG.md`** — add the `[x.y.z] — DATE` section.
3. **Update `README.md`** and `docs/CLAIMS.md` if the surface changed
   (shipped-state only).
4. **Verify locally** (mirrors the CI gate):
   ```bash
   for f in tests/test_*.py; do python -m pytest "$f" -q; done
   ruff check conscio/ tests/
   mypy conscio/
   python -m build && twine check dist/*
   mkdocs build --strict          # needs: pip install "conscio[docs]"
   ```
5. **TestPyPI dry-run** (recommended before the first real upload):
   ```bash
   python -m build
   twine upload --repository testpypi dist/*
   python -m venv /tmp/v && /tmp/v/bin/pip install \
     -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ conscio
   /tmp/v/bin/conscio version
   ```
6. **Tag and push:**
   ```bash
   git tag v1.3.0
   git push origin v1.3.0
   ```
7. Watch the **Release** workflow: `gate` → `build` → `publish`. On success the
   wheel + sdist are live on PyPI.

## Docs

The **Docs** workflow builds `mkdocs build --strict` and deploys to GitHub Pages
on every push to `main`. No tag needed.

## Notes

- The core stays zero-dependency; `build`, `twine`, and `mkdocs-material` are
  CI/dev-only and never enter the runtime import graph.
- `conscio-daemon` is a reserved console-script name for a future release; it is
  intentionally not shipped yet.
