#!/usr/bin/env python3
"""Example: a custom InferenceAdapter — the inference extension point.

Subclass InferenceAdapter to plug any backend into the agency pipeline. Make it
discoverable by third parties via an entry point in your own pyproject.toml::

    [project.entry-points."conscio.adapters"]
    echo = "your_pkg:EchoAdapter"

`conscio plugins` will then list it. This example is fully offline (no network).
"""
from __future__ import annotations

import json

from conscio.agency.adapter import AdapterCaps, InferenceAdapter, InferenceResult
from conscio.agency.contracts import PROPOSAL_SCHEMA, validate


class EchoAdapter(InferenceAdapter):
    """A canned backend that emits a single valid action proposal."""

    def __init__(self, model_name: str = "echo-1") -> None:
        self._caps = AdapterCaps(model_name=model_name, json_mode=True)

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None) -> InferenceResult:
        proposal = {
            "tool": "fs_read",
            "args": {"path": "notes.md"},
            "rationale": "inspect the notes before acting",
            "expected_outcome": "file content returned",
        }
        return InferenceResult(text=json.dumps(proposal))

    def capabilities(self) -> AdapterCaps:
        return self._caps


def main(storage: str | None = None) -> int:
    adapter = EchoAdapter()
    result = adapter.generate("Propose a safe first action.",
                              schema=PROPOSAL_SCHEMA)
    payload = json.loads(result.text)
    errors = validate(payload, PROPOSAL_SCHEMA)
    print(f"adapter:  {adapter.capabilities().model_name}")
    print(f"proposal: {payload['tool']} -> {payload['args']}")
    print(f"valid:    {not errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
