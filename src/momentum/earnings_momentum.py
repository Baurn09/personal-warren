"""Earnings momentum — a soft tilt for the monthly ranking.

Uses fundamentals already ingested by Phase 1. This is intentionally simple —
yfinance gives us a single TTM snapshot, so we can only read *direction*, not
true revision history. Used as a tilt (small weight), not a primary signal.
"""
from __future__ import annotations

import math
from typing import Optional


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def earnings_tilt(metrics: dict) -> Optional[float]:
    """Return a tilt in roughly [-1, +1] from available earnings/revenue growth.

    Falls back to revenue growth if earnings growth is missing. Caller decides
    how to fold this into the combined momentum score.
    """
    g = _num(metrics.get("earnings_growth"))
    if g is None:
        g = _num(metrics.get("revenue_growth"))
    if g is None:
        return None
    return max(-1.0, min(1.0, g))
