"""Deterministic, sector-relative scoring engine.

Each metric is converted to a 0-100 percentile within the stock's sector peer
group (direction-adjusted, so a low PE and a high ROE both score well). A
category score is the mean percentile of the metrics that feed it; the total is
the weighted mean of the 6 categories. Pure computation — no AI, no network.
"""
from __future__ import annotations

import pandas as pd

from src.config import load_config
from src.scoring import sector_models

CATEGORIES = ["quality", "moat", "financial_strength",
              "management", "valuation", "growth"]
NEUTRAL = 50.0   # category score used when no metric data is available


def _percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """Return a 0-100 percentile for each metric, ranked within ``peer_group``.

    For ``lower``-is-better metrics the percentile is inverted so that, in every
    column, a higher number is always better.
    """
    directions = sector_models.metric_direction()
    out = pd.DataFrame(index=df.index)
    for metric, direction in directions.items():
        if metric not in df.columns:
            continue
        pct = df.groupby("peer_group")[metric].rank(pct=True) * 100.0
        out[metric] = (100.0 - pct) if direction == "lower" else pct
    return out


def score(df: pd.DataFrame) -> pd.DataFrame:
    """Score a metrics DataFrame.

    ``df`` is indexed by ticker and must contain ``sector``, ``template`` and
    metric columns. Returns a DataFrame (same index) with the 6 category scores
    and ``raw_total`` — the weighted total (0-100) before red-flag penalties.
    """
    weights = load_config()["scoring"]["weights"]
    min_peers = sector_models.peer_group_settings()["min_peers"]

    df = df.copy()
    # small sectors fall back to a single whole-universe peer group
    counts = df["sector"].value_counts()
    small = set(counts[counts < min_peers].index)
    df["peer_group"] = df["sector"].where(
        df["sector"].notna() & ~df["sector"].isin(small), "ALL")

    pct = _percentiles(df)
    templates = {name: sector_models.template(name)
                 for name in df["template"].dropna().unique()}

    result = pd.DataFrame(index=df.index)
    result["template"] = df["template"]
    result["peer_group"] = df["peer_group"]

    for cat in CATEGORIES:
        col = pd.Series(NEUTRAL, index=df.index, dtype=float)
        for tpl_name, tpl in templates.items():
            mask = df["template"] == tpl_name
            metrics = [m for m in tpl[cat] if m in pct.columns]
            if metrics and mask.any():
                col[mask] = pct.loc[mask, metrics].mean(axis=1, skipna=True)
        result[cat] = col.fillna(NEUTRAL).round(1)

    total_w = sum(weights[c] for c in CATEGORIES)
    result["raw_total"] = (
        sum(result[c] * weights[c] for c in CATEGORIES) / total_w).round(2)
    return result
