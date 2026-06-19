"""Quality-of-earnings red flags.

Two kinds, per AGENTS.md:

* **disqualifiers** — the stock is removed from the funnel entirely.
* **penalties** — points are subtracted from the 0-100 total score.

Phase 2 implements the checks feasible from yfinance data. Promoter pledging,
auditor changes and falling promoter holding need corporate-filings data and
are added in a later phase.
"""
from __future__ import annotations

import math

# --- tunable thresholds ---
WEAK_CASH_CONVERSION = 0.6     # CFO / PAT below this is a concern
HIGH_LEVERAGE_DTE = 150.0      # debt/equity on yfinance's % scale (>1.5x)
LOW_PROMOTER_HOLDING = 0.25    # promoter / insider holding below 25%

PENALTY_POINTS = {
    "weak_cash_conversion": 8,
    "high_leverage": 8,
    "negative_fcf": 5,
    "low_promoter_holding": 5,
}


def _val(m: dict, key: str) -> float | None:
    """Fetch a metric, treating NaN / missing as absent."""
    v = m.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def disqualifiers(m: dict) -> list[str]:
    """Reasons the stock should be excluded from the funnel entirely."""
    out: list[str] = []
    net = _val(m, "net_profit")
    if net is not None and net <= 0:
        out.append("loss_making")
    book = _val(m, "book_value")
    if book is not None and book <= 0:
        out.append("negative_equity")
    return out


def penalties(m: dict, template: str) -> list[tuple[str, int]]:
    """``(flag, points)`` penalties that reduce the total score."""
    out: list[tuple[str, int]] = []

    cc = _val(m, "cash_conversion")
    if cc is not None and cc < WEAK_CASH_CONVERSION:
        out.append(("weak_cash_conversion", PENALTY_POINTS["weak_cash_conversion"]))

    # leverage penalty does not apply to banks/NBFCs (high leverage is structural)
    dte = _val(m, "debt_to_equity")
    if template != "Financials" and dte is not None and dte > HIGH_LEVERAGE_DTE:
        out.append(("high_leverage", PENALTY_POINTS["high_leverage"]))

    fcf = _val(m, "fcf")
    if fcf is not None and fcf < 0:
        out.append(("negative_fcf", PENALTY_POINTS["negative_fcf"]))

    ph = _val(m, "promoter_holding")
    if ph is not None and ph < LOW_PROMOTER_HOLDING:
        out.append(("low_promoter_holding", PENALTY_POINTS["low_promoter_holding"]))
    return out
