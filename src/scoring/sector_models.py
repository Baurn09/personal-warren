"""Sector classification and scoring templates (``config/sectors.yaml``).

A template decides which metrics feed each of the 6 score categories. Banks and
NBFCs use the ``Financials`` template because FCF / EV-based metrics do not
apply to balance-sheet businesses; everything else uses ``General``.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from src.config import ROOT


@lru_cache(maxsize=1)
def load_sectors() -> dict:
    """Load and cache ``config/sectors.yaml``."""
    with open(ROOT / "config" / "sectors.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def classify(industry: str | None, sector: str | None) -> str:
    """Return the scoring template for a company: ``General`` or ``Financials``."""
    cfg = load_sectors()["classification"]
    text = " ".join(x for x in (industry, sector) if x).lower()
    for keyword in cfg["financial_keywords"]:
        if keyword in text:
            return "Financials"
    return cfg["default_template"]


def template(name: str) -> dict[str, list[str]]:
    """Return the category -> metric-list map for a template."""
    return load_sectors()["templates"][name]


def metric_direction() -> dict[str, str]:
    """Return ``{metric: 'higher'|'lower'}`` — which way is better."""
    return load_sectors()["metric_direction"]


def peer_group_settings() -> dict:
    """Return the peer-group config (field + min_peers)."""
    return load_sectors()["peer_group"]
