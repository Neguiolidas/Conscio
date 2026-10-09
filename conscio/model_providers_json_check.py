"""Tiny helper so tests and code share one canonical path constant."""
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent / "model-providers.json"
