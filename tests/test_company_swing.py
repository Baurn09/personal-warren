"""Tests for the swing-vs-hold path simulation — pure, no DB or network."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.company import swing as S   # noqa: E402


def _path(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": closes, "high": closes * 1.001,
        "low": closes * 0.999, "close": closes, "volume": np.full(len(closes), 1)})


def test_rising_series_hits_target():
    # +0.5%/day for 200 days: a +5% target is always reached within a month
    closes = 100.0 * np.cumprod(np.full(200, 1.005))
    px = _path(closes)
    res = S.simulate_swing(px, entry=closes[-1], stop=closes[-1] * 0.93,
                           target=closes[-1] * 1.05, horizon_days=21)
    assert res is not None
    assert res["p_target"] > 0.9
    assert res["ev"] > 0


def test_falling_series_hits_stop():
    closes = 100.0 * np.cumprod(np.full(200, 0.995))
    px = _path(closes)
    res = S.simulate_swing(px, entry=closes[-1], stop=closes[-1] * 0.93,
                           target=closes[-1] * 1.05, horizon_days=21)
    assert res is not None
    assert res["p_stop"] > 0.9
    assert res["ev"] < 0


def test_probabilities_sum_to_one():
    rng = np.random.default_rng(3)
    closes = 100.0 * np.cumprod(1.0 + 0.0 + 0.015 * rng.standard_normal(400))
    px = _path(closes)
    res = S.simulate_swing(px, entry=closes[-1], stop=closes[-1] * 0.93,
                           target=closes[-1] * 1.05, horizon_days=21)
    assert res is not None
    assert abs(res["p_target"] + res["p_stop"] + res["p_neither"] - 1.0) < 1e-9


def test_insufficient_history():
    px = _path(100.0 * np.cumprod(np.full(20, 1.001)))
    assert S.simulate_swing(px, 100, 93, 105, 21) is None


def test_recommendation_prefers_higher_ev():
    closes = 100.0 * np.cumprod(np.full(200, 1.005))
    px = _path(closes)
    plan = {"entry_price": closes[-1], "stop_loss": closes[-1] * 0.93,
            "target_price": closes[-1] * 1.05}
    # swing EV is strongly positive here; tiny hold EV -> recommend swing
    out = S.swing_vs_hold(px, plan, hold_base_1m=0.001, horizon_days=21)
    assert out["recommendation"] == "Swing trade"
    assert out["edge"] is not None and out["edge"] > 0


def test_recommendation_avoid_when_all_negative():
    closes = 100.0 * np.cumprod(np.full(200, 0.995))
    px = _path(closes)
    plan = {"entry_price": closes[-1], "stop_loss": closes[-1] * 0.93,
            "target_price": closes[-1] * 1.05}
    out = S.swing_vs_hold(px, plan, hold_base_1m=-0.02, horizon_days=21)
    assert out["recommendation"] == "Avoid for now"


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
