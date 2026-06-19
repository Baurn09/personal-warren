"""Tests for single-company quality assessment.

Relative mode exercises the real sector-relative scoring engine (reads the
config files, no network); absolute mode and flags are pure.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.company import quality as Q   # noqa: E402

_METRICS = ["roe", "roa", "net_margin", "operating_margin", "gross_margin",
            "revenue_growth", "earnings_growth", "current_ratio",
            "cash_conversion", "promoter_holding", "fcf", "pe",
            "price_to_book", "ev_ebitda", "debt_to_equity"]


def _peer_frame(target_metrics, n_peers=9, sector="Technology"):
    rng = np.random.default_rng(7)
    rows = {}
    for i in range(n_peers):
        rows[f"PEER{i}"] = {m: float(rng.uniform(0.05, 0.3)) for m in _METRICS}
    rows["TGT"] = dict(target_metrics)
    df = pd.DataFrame.from_dict(rows, orient="index")
    df["sector"] = sector
    df["template"] = "General"
    return df


def test_relative_mode_returns_scores():
    target = {m: 0.5 for m in _METRICS}          # strong on higher-is-better
    target.update({"pe": 8.0, "price_to_book": 1.0, "ev_ebitda": 5.0,
                   "debt_to_equity": 10.0})       # cheap + low debt
    frame = _peer_frame(target)
    out = Q.assess_quality("TGT", "General", target, frame, "relative")
    assert out["mode"] == "relative"
    for c in Q.CATEGORIES:
        assert 0.0 <= out["categories"][c] <= 100.0
    assert out["sector_percentile"] is not None
    # a clearly strong company should land in the upper half of its peers
    assert out["composite"] >= 50.0


def test_absolute_mode_when_no_frame():
    target = {"roe": 0.20, "roa": 0.10, "net_margin": 0.15, "gross_margin": 0.40,
              "operating_margin": 0.20, "pe": 18.0, "price_to_book": 3.0,
              "revenue_growth": 0.15, "earnings_growth": 0.18,
              "promoter_holding": 0.55, "fcf": 1e8, "current_ratio": 2.0,
              "debt_to_equity": 30.0}
    out = Q.assess_quality("TGT", "General", target, None, "absolute")
    assert out["mode"] == "absolute"
    assert out["sector_percentile"] is None
    assert 0.0 <= out["final_total"] <= 100.0
    assert out["composite"] > 50.0          # genuinely high-quality inputs


def test_disqualifier_loss_making():
    target = {"net_profit": -100.0, "roe": 0.05}
    out = Q.assess_quality("TGT", "General", target, None, "absolute")
    assert "loss_making" in out["disqualifiers"]


def test_penalty_reduces_total():
    # negative FCF + weak cash conversion should subtract from the raw total
    target = {"roe": 0.18, "fcf": -1e7, "cash_conversion": 0.2,
              "net_profit": 1e7}
    out = Q.assess_quality("TGT", "General", target, None, "absolute")
    assert out["final_total"] <= out["raw_total"]
    assert any(f in out["penalties"]
               for f in ("negative_fcf", "weak_cash_conversion"))


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
