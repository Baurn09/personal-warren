"""Unit tests for the momentum signals (Phase 4).

Runnable two ways:
    pytest tests/test_momentum.py
    python tests/test_momentum.py        (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                        # noqa: E402
import pandas as pd                                       # noqa: E402

from src.momentum.earnings_momentum import earnings_tilt  # noqa: E402
from src.momentum.signals import (                        # noqa: E402
    _wilder_atr, _wilder_rsi, compute_signals,
)

WINDOWS = {
    "one_month_days": 21, "six_months_days": 126, "twelve_months_days": 252,
    "high_52w_days": 252, "ma_short_days": 50, "ma_long_days": 200,
    "rsi_period": 14, "atr_period": 14,
    "volume_short_days": 20, "volume_long_days": 60,
    "vol_short_days": 21, "vol_long_days": 63,
}


def _ohlcv(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "open": closes,
        "high": closes if highs is None else highs,
        "low": closes if lows is None else lows,
        "close": closes,
        "volume": [1000] * n,
    })


# ─────────────────────── compute_signals ───────────────────────


def test_insufficient_history_returns_none():
    # Only 100 days — less than the required 12-month window (252).
    assert compute_signals(_ohlcv([100.0] * 100), WINDOWS) is None


def test_mom_12_1_excludes_recent_month():
    # 260-day series. Anchor specific indices so the formula is verifiable.
    closes = [100.0 + i * 0.1 for i in range(260)]
    closes[7] = 100.0      # ~12 months ago: close[-1-252] = close[7]
    closes[238] = 130.0    # ~1 month ago:   close[-1-21]  = close[238]
    closes[259] = 150.0    # today
    sigs = compute_signals(_ohlcv(closes), WINDOWS)
    assert sigs is not None
    # 12-1 momentum = price 1m ago / price 12m ago - 1 = 130/100 - 1
    assert abs(sigs["mom_12_1"] - 0.30) < 1e-9
    # mom_1m is the raw 1-month return
    assert abs(sigs["mom_1m"] - (150.0 / 130.0 - 1.0)) < 1e-9


def test_dist_52w_high_at_high_is_zero():
    closes = list(np.linspace(100.0, 200.0, 260))   # last value is the high
    sigs = compute_signals(_ohlcv(closes), WINDOWS)
    assert abs(sigs["dist_52w_high"]) < 1e-9


def test_trend_filter_binary():
    rising = list(np.linspace(100.0, 200.0, 260))
    falling = list(np.linspace(200.0, 100.0, 260))
    assert compute_signals(_ohlcv(rising), WINDOWS)["trend_filter"] == 1
    assert compute_signals(_ohlcv(falling), WINDOWS)["trend_filter"] == 0


# ─────────────────────── RSI ───────────────────────


def test_rsi_monotonic_rise_is_100():
    # No losses -> RSI = 100
    closes = np.linspace(100.0, 200.0, 300)
    assert _wilder_rsi(closes, 14) == 100.0


def test_rsi_flat_series_is_100():
    # No gains, no losses -> convention RSI = 100
    assert _wilder_rsi(np.array([100.0] * 100), 14) == 100.0


# ─────────────────────── ATR ───────────────────────


def test_atr_constant_range():
    # Daily range of 5 with stable close -> ATR == 5
    closes = np.array([100.0] * 100)
    atr = _wilder_atr(closes + 2.5, closes - 2.5, closes, 14)
    assert atr is not None
    assert abs(atr - 5.0) < 1e-9


# ─────────────────────── earnings tilt ───────────────────────


def test_earnings_tilt_falls_back_and_clamps():
    assert earnings_tilt({"earnings_growth": 0.20}) == 0.20
    assert earnings_tilt({"earnings_growth": -0.30}) == -0.30
    assert earnings_tilt({"earnings_growth": 5.0}) == 1.0
    assert earnings_tilt({"earnings_growth": -5.0}) == -1.0
    # falls back to revenue growth when earnings is missing
    assert earnings_tilt({"revenue_growth": 0.10}) == 0.10
    assert earnings_tilt({}) is None


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
