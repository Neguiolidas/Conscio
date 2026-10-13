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

   Three SHIPPED copies must be bumped to the same value in the same
   commit — `tests/test_marketplace_version.py` fails the gate if any
   drifts:
   - `conscio/integrations/claude_code/assets/.claude-plugin/plugin.json`
     (`"version"`);
   - `.claude-plugin/marketplace.json` (the plugin entry's `"version"` —
     the zcode auto-update compares the installed version against THIS
     field; a missing or stale value hides the release from the
     auto-update flow);
   - `conscio/integrations/claude_code/assets/.mcp.json` (the
     `--from conscio==x.y.z` uvx pin — a stale pin makes the plugin's
     MCP server run the old package even after the plugin cache
     updates).
2. **Update `CHANGELOG.md`** — add the `[x.y.z] — DATE` section.
3. **Update `README.md`** and `docs/CLAIMS.md` if the surface changed
   (shipped-state only).
4. **Verify locally** (mirrors the CI gate):
   ```bash
   for f in tests/test_*.py; do python -m pytest "$f" -q; done
   ruff check conscio/ tests/
   pyright conscio/                # the gate runs pyright, not mypy
   vulture conscio/ vulture_whitelist.py --min-confidence 60
   bandit -r conscio/ -q -ll --exit-zero -c .bandit
   python -m build && twine check dist/*
   unzip -l dist/*.whl | grep -q __pycache__ && echo "BYTECODE IN WHEEL"
   mkdocs build --strict          # needs: pip install "conscio[docs]"
   ```
   Run the test loop on the **oldest** Python the gate covers, not just the
   local one. `pathlib`, `asyncio` and `typing` all changed behaviour inside
   the supported range; v3.9.2 was tagged with a green sweep on 3.14 and still
   failed the gate on 3.11:
   ```bash
   uv venv /tmp/gate310 --python 3.10 && uv pip install --python /tmp/gate310/bin/python pytest numpy
   for f in tests/test_*.py; do PYTHONPATH=. /tmp/gate310/bin/python -m pytest "$f" -q; done
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

### The package-data guard (wheel contents)

v4.9.2 published a wheel with no `model-providers.json`, no `presets/axes/*`
and no `presets/voice/*`. Nothing failed: the three loaders are advisory by
design, so every install ran with no provider table, no voice preset and no
axis pack. The lesson is that a guard must judge the ARTIFACT — a check that
reads the source tree is green by construction, because in the source tree every
file exists.

`tests/test_wheel_contents.py` is that check. It compares `git ls-files conscio`
filtered to non-`.py` (what SHOULD ship) against the namelist of a real wheel
(what DID ship), with exceptions only in a documented `EXCLUDED` dict.

It builds its wheel from a `git ls-files`-staged copy of the tree, never from
the working directory: measured, a build in place is fed by a stale `build/` and
`conscio.egg-info/`, which re-inject files the configuration no longer lists —
with the fix reverted that guard stayed green twice, until both directories
were removed. `CONSCIO_WHEEL` judging an already-built wheel skips the build
entirely, which is how the release workflow reuses the artifact it publishes.

Prove it by hand (the check the test automates). Build into a directory you
have just emptied — a `dist/` that already holds an older release makes
`ls dist/*.whl` match two wheels, and `unzip` then extracts nothing and every
file reads as missing:

```bash
work="$HOME/.cache/conscio-release-check"
rm -rf "$work"
python3 -m build --outdir "$work/dist"
mkdir -p "$work/scratch"
unzip -q "$(ls "$work/dist"/*.whl)" -d "$work/scratch"
git ls-files conscio | grep -v '\.py$' | while read -r f; do
  [ -f "$work/scratch/$f" ] || echo "MISSING FROM WHEEL: $f"
done
rm -rf "$work"
```

Silence above means every tracked data file shipped. To judge a wheel that is
already built, without building a second one:

```bash
CONSCIO_WHEEL=dist/conscio-*.whl \
  python -m pytest tests/test_wheel_contents.py -q --tb=short
```

When it goes red, something reads that file at runtime and it is not packaged.
Two legitimate endings, one of each kind:

- put the path in `[tool.setuptools.package-data]` — the normal fix, and it
  feeds the sdist as well as the wheel;
- or add it to `EXCLUDED` in the test **with the reason written**. The three
  4.9.2 families (`model-providers.json`, `presets/axes/*.json`,
  `presets/voice/*.md`) may never be excluded; the test refuses it.

## Docs

The **Docs** workflow builds `mkdocs build --strict` and deploys to GitHub Pages
on every push to `main`. No tag needed.
