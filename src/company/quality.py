"""Quality / MOAT assessment for one company.

Relative mode runs the existing sector-relative ``scoring.engine.score`` on the
``{target + peers}`` frame and reads off the target's row. Absolute mode (used
when too few peers are available) scores the company against fixed Buffett-style
thresholds. Red flags reuse ``scoring.flags``.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from src.scoring import engine, flags

CATEGORIES = ["quality", "moat", "financial_strength",
              "management", "valuation", "growth"]

# absolute-mode thresholds: (metric, good_value, higher_is_better) per category.
# Each metric maps to 0-100 by comparison to the threshold (capped).
_ABSOLUTE = {
    "quality":            [("roe", 0.15, True), ("roa", 0.08, True),
                           ("net_margin", 0.10, True)],
    "moat":               [("gross_margin", 0.30, True),
                           ("operating_margin", 0.15, True)],
    "financial_strength": [("debt_to_equity", 60.0, False),
                           ("current_ratio", 1.5, True), ("fcf", 0.0, True)],
    "management":         [("roe", 0.15, True), ("promoter_holding", 0.40, True)],
    "valuation":          [("pe", 22.0, False), ("price_to_book", 4.0, False)],
    "growth":             [("revenue_growth", 0.10, True),
                           ("earnings_growth", 0.10, True)],
}


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _score_metric(value: float, good: float, higher: bool) -> float:
    """Map a metric to 0-100 relative to a 'good' threshold (50 = at threshold)."""
    if higher:
        if good <= 0:                       # threshold at/above zero baseline
            return 100.0 if value > 0 else 25.0
        ratio = value / good
    else:
        if value <= 0:
            return 50.0
        ratio = good / value
    return float(max(0.0, min(100.0, 50.0 * ratio)))


def _absolute_scores(metrics: dict) -> dict:
    out = {}
    for cat, specs in _ABSOLUTE.items():
        vals = []
        for metric, good, higher in specs:
            v = _num(metrics.get(metric))
            if v is not None:
                vals.append(_score_metric(v, good, higher))
        out[cat] = round(sum(vals) / len(vals), 1) if vals else 50.0
    return out


def assess_quality(ticker: str, template: str, metrics: dict,
                   frame: Optional[pd.DataFrame], mode: str) -> dict:
    """Return category scores, the penalised total, the quality composite and flags."""
    if mode == "relative" and frame is not None and ticker in frame.index:
        scored = engine.score(frame)
        row = scored.loc[ticker]
        cats = {c: float(row[c]) for c in CATEGORIES}
        raw_total = float(row["raw_total"])
        totals = scored["raw_total"].dropna()
        sector_pct = float((totals < raw_total).mean() * 100.0) if len(totals) > 1 \
            else None
    else:
        mode = "absolute"
        cats = _absolute_scores(metrics)
        weights = {"quality": 20, "moat": 20, "financial_strength": 20,
                   "management": 15, "valuation": 15, "growth": 10}
        tw = sum(weights.values())
        raw_total = round(sum(cats[c] * weights[c] for c in CATEGORIES) / tw, 2)
        sector_pct = None

    disq = flags.disqualifiers(metrics)
    pens = flags.penalties(metrics, template)
    penalty_pts = sum(p for _f, p in pens)
    final_total = round(max(0.0, raw_total - penalty_pts), 2)
    composite = round(
        (cats["quality"] + cats["moat"] + cats["financial_strength"]) / 3.0, 1)

    return {
        "mode": mode,
        "categories": cats,
        "raw_total": raw_total,
        "final_total": final_total,
        "composite": composite,
        "sector_percentile": sector_pct,
        "disqualifiers": disq,
        "penalties": [f for f, _p in pens],
    }
