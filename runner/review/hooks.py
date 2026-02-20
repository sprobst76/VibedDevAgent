"""Configurable review/test hooks."""

from __future__ import annotations

import json
from pathlib import Path


def load_hooks(config_file: str | None = None) -> list[str]:
    """Load hook commands from JSON file or use sensible default."""
    if not config_file:
        return ["python3 -m unittest discover -s tests -p 'test_*.py'"]

    path = Path(config_file)
    payload = json.loads(path.read_text(encoding="utf-8"))
    hooks = payload.get("hooks", [])
    return [str(hook) for hook in hooks]
