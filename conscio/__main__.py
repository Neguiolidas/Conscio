# conscio/__main__.py
"""`python -m conscio` → the conscio CLI."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
