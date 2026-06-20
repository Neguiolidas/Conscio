# Install

## From PyPI

```bash
pip install conscio
```

That is the whole runtime. Conscio's core depends only on `numpy` and the Python
standard library (`sqlite3`, `urllib`, `importlib.metadata`).

Requires Python ≥ 3.10.

## Console scripts

The wheel ships four commands:

| command | purpose |
|---|---|
| `conscio` | version / info / reflect / plugins / bench / consent / structure |
| `conscio-bench` | measure an inference backend against the agency pipeline |
| `conscio-daemon` | run Conscio as a living process (sensors → reflect → act) |
| `conscio-mcp` | stdlib-only MCP stdio server — embed Conscio in any MCP host (v2.0) |

```bash
conscio version
conscio --help
```

## Optional extras

Conscio keeps the core dependency-free; tooling lives in extras:

```bash
pip install "conscio[dev]"    # pytest, ruff, mypy — for contributing
pip install "conscio[docs]"   # mkdocs-material — to build this site
```

These never enter the runtime import graph.

## From source

```bash
git clone https://github.com/Neguiolidas/Conscio
cd Conscio
pip install -e ".[dev]"
```

## Local inference backends (optional)

`reflect()` needs no model. `act()` needs an inference backend — by default a
**local** one (Ollama, llama.cpp, or any OpenAI-compatible server such as LM
Studio, on `localhost`). See [Plugins](plugins.md) to wire your own.
