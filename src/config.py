"""Configuration loader.

All settings live in ``config/settings.yaml``. Paths in that file are relative
to the project root and are resolved with :func:`resolve`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

# project root = parent of the ``src`` package
ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load and cache ``config/settings.yaml``."""
    with open(ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(rel_path: str) -> Path:
    """Resolve a settings.yaml path against the project root."""
    return ROOT / rel_path
