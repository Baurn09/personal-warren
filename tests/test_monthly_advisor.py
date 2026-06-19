"""Unit tests for the monthly advisor — entry, stop, target, position sizing.

Runnable two ways:
    pytest tests/test_monthly_advisor.py
    python tests/test_monthly_advisor.py        (no pytest needed)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd                                            # noqa: E402

from src.advisor.monthly import compute_plan, _position_sizes  # noqa: E402

ADVISOR_CFG = {
    "stop_loss": {"fixed_pct": 0.07, "atr_multiple": 1.5},
    "target":    {"fixed_pct": 0.12, "stdev_multiple": 1.5},
}


# ─────────────────────── stop-loss ───────────────────────


def test_stop_uses_atr_when_tighter():
    # ATR=1, entry=100 -> atr_stop=98.5. fixed_pct stop = 93. Tighter = 98.5.
    plan = compute_plan(100.0, atr=1.0, vol_annual=None,
                        advisor_cfg=ADVISOR_CFG)
    assert plan["stop_loss"] == 98.5


def test_stop_uses_pct_when_atr_loose():
    # ATR=20, entry=100 -> atr_stop=70. fixed_pct stop = 93. Tighter = 93.
    plan = compute_plan(100.0, atr=20.0, vol_annual=None,
                        advisor_cfg=ADVISOR_CFG)
    assert plan["stop_loss"] == 93.0


def test_stop_falls_back_to_pct_when_atr_missing():
    plan = compute_plan(100.0, atr=None, vol_annual=None,
                        advisor_cfg=ADVISOR_CFG)
    assert plan["stop_loss"] == 93.0


# ─────────────────────── target ───────────────────────


def test_target_picks_more_conservative():
    # vol_annual = 0.30 -> monthly stdev ~ 0.0866 -> stdev_target ~ 113
    # fixed_pct target = 112 -> conservative = 112
    plan = compute_plan(100.0, atr=1.0, vol_annual=0.30,
                        advisor_cfg=ADVISOR_CFG)
    assert abs(plan["target_price"] - 112.0) < 1e-9


def test_target_uses_stdev_when_low_vol():
    # vol_annual = 0.10 -> monthly stdev ~ 0.0289 -> stdev_target ~ 104.33
    # fixed_pct target = 112 -> conservative = 104.33
    plan = compute_plan(100.0, atr=1.0, vol_annual=0.10,
                        advisor_cfg=ADVISOR_CFG)
    expected = 100.0 * (1.0 + 1.5 * (0.10 / math.sqrt(12.0)))
    assert abs(plan["target_price"] - expected) < 1e-9
    assert plan["target_price"] < 112.0


def test_target_falls_back_to_pct_when_vol_missing():
    plan = compute_plan(100.0, atr=1.0, vol_annual=None,
                        advisor_cfg=ADVISOR_CFG)
    assert abs(plan["target_price"] - 112.0) < 1e-9


# ─────────────────────── position sizing ───────────────────────


def _portfolio_cfg(single_cap_pct, cash_min_pct=10):
    return {"portfolio": {
        "cash_reserve_pct": [cash_min_pct, 20],
        "single_stock_cap_pct": single_cap_pct,
    }}


def test_position_sizes_inverse_to_atr():
    # All same price; ATRs differ -> low-vol stock gets the biggest weight.
    df = pd.DataFrame({
        "atr_14":        [2.0, 4.0, 6.0],
        "current_price": [100.0, 100.0, 100.0],
    }, index=["A", "B", "C"])
    sizes = _position_sizes(df, _portfolio_cfg(single_cap_pct=100))
    # With min cash reserve 10%, target equity = 90%
    assert abs(sizes.sum() - 0.90) < 1e-9
    assert sizes.loc["A"] > sizes.loc["B"] > sizes.loc["C"]


def test_position_sizes_cap_clips_and_rest_to_cash():
    # Equal ATR -> equal weights of 45% each. Cap at 30% -> 30% each, 40% cash.
    df = pd.DataFrame({
        "atr_14":        [1.0, 1.0],
        "current_price": [100.0, 100.0],
    }, index=["A", "B"])
    sizes = _position_sizes(df, _portfolio_cfg(single_cap_pct=30))
    assert (sizes <= 0.30 + 1e-9).all()
    assert abs(sizes.sum() - 0.60) < 1e-9


def test_position_sizes_handle_missing_atr():
    # Missing ATR for one stock — falls back to equal weight contribution.
    df = pd.DataFrame({
        "atr_14":        [None, 2.0],
        "current_price": [100.0, 100.0],
    }, index=["A", "B"])
    sizes = _position_sizes(df, _portfolio_cfg(single_cap_pct=100))
    # Should still sum to ≤ target equity and be finite for both.
    assert sizes.notna().all()
    assert sizes.sum() <= 0.90 + 1e-9


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL   {t.__name__}: {e}")
        except Exception as e:   # noqa: BLE001
            failed += 1
            print(f"ERROR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
