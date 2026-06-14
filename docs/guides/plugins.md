# Plugins & extension points

Conscio has three stable extension points. Each can be used directly, or
published by a third-party package and **discovered automatically** through
`importlib.metadata` entry points.

| Surface | Base class / shape | Entry-point group |
|---|---|---|
| Inference backend | `conscio.agency.adapter.InferenceAdapter` | `conscio.adapters` |
| Perception sensor | `conscio.perception.SensorAdapter` | `conscio.sensors` |
| Action tool | callable (factory / registrar) | `conscio.tools` |

Discovery is **resilient**: a plugin that fails to import or resolves to the
wrong type is skipped with a warning — one broken third-party plugin can never
break the host.

```python
from conscio.plugins import discover_adapters, discover_sensors, discover_tools

discover_adapters()   # {name: InferenceAdapter subclass}
discover_sensors()    # {name: SensorAdapter subclass}
discover_tools()      # {name: callable}
```

…or from the CLI:

```bash
conscio plugins
```

## A custom inference backend

```python
from conscio.agency.adapter import AdapterCaps, InferenceAdapter, InferenceResult

class MyAdapter(InferenceAdapter):
    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None):
        text = call_my_backend(prompt)        # localhost / in-process
        return InferenceResult(text=text)

    def capabilities(self):
        return AdapterCaps(model_name="my-backend", json_mode=True)
```

## A custom sensor

```python
from conscio.perception import PerceptionFrame, SensorAdapter
from conscio.risk import Risk

class MySensor(SensorAdapter):
    name = "mine"
    risk = Risk.LOW                            # read-only

    def perceive(self) -> PerceptionFrame:
        return PerceptionFrame(source=self.name, observations=["..."])
```

Feed it into reflection — `reflect()` is untouched:

```python
engine.reflect(world_state=MySensor().perceive().to_world_state())
```

## Making a plugin discoverable

In your **own** package's `pyproject.toml`:

```toml
[project.entry-points."conscio.adapters"]
my-backend = "my_pkg:MyAdapter"

[project.entry-points."conscio.sensors"]
my-sensor = "my_pkg:MySensor"

[project.entry-points."conscio.tools"]
my-tools = "my_pkg:register_tools"
```

After `pip install your-package`, `conscio plugins` lists them.

## Runnable examples

See the `examples/` directory: `custom_adapter.py`, `host_guardian.py`,
`agent_companion.py` — each is offline and exercises one extension point.
