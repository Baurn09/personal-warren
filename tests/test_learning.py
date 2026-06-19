"""Unit tests for the learning loop's outcome classification.

Runnable two ways:
    pytest tests/test_learning.py
    python tests/test_learning.py        (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd                                      # noqa: E402

from src.learning.loop import classify_outcome           # noqa: E402


def _bars(specs: list[tuple]) -> pd.DataFrame:
    """[(date_str, open, high, low, close), ...] -> a forward-prices frame."""
    return pd.DataFrame({
        "date": pd.to_datetime([s[0] for s in specs]),
        "open": [s[1] for s in specs],
        "high": [s[2] for s in specs],
        "low":  [s[3] for s in specs],
        "close": [s[4] for s in specs],
    })


# ─────────────────────── stop_hit ───────────────────────


def test_stop_hit_intraday_at_stop_level():
    # Day 1: low touches stop exactly.
    bars = _bars([
        ("2026-02-01", 100.0, 101.0, 93.0, 95.0),
        ("2026-02-02", 95.0, 99.0, 94.0, 98.0),
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "stop_hit"
    assert out["exit_price"] == 93.0
    assert out["exit_date"] == pd.Timestamp("2026-02-01")


def test_stop_hit_gap_down_fills_at_open():
    # Gap-down open BELOW the stop -> exit at the open (worse fill).
    bars = _bars([
        ("2026-02-02", 88.0, 90.0, 85.0, 89.0),
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "stop_hit"
    assert out["exit_price"] == 88.0


# ─────────────────────── target_hit ───────────────────────


def test_target_hit_intraday():
    bars = _bars([
        ("2026-02-01", 100.0, 113.0, 99.0, 110.0),
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "target_hit"
    assert out["exit_price"] == 112.0


def test_target_hit_gap_up_fills_at_open():
    bars = _bars([
        ("2026-02-02", 115.0, 116.0, 113.0, 114.0),
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "target_hit"
    assert out["exit_price"] == 115.0


# ─────────────────────── stop first when both touched same bar ───────────────


def test_stop_wins_when_both_breached_same_day():
    # Wild day — high above target AND low below stop. Conservative: stop wins.
    bars = _bars([
        ("2026-02-01", 100.0, 113.0, 92.0, 110.0),
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "stop_hit"


# ─────────────────────── held ───────────────────────


def test_held_when_window_elapses_without_touch():
    bars = _bars([
        ("2026-02-01", 100.0, 101.0, 99.0, 100.0),
        ("2026-02-02", 100.0, 102.0, 99.5, 101.5),
        ("2026-02-03", 101.5, 102.0, 100.5, 101.0),
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=3)
    assert out["outcome"] == "held"
    assert out["exit_price"] == 101.0          # close of last bar in window
    assert out["exit_date"] == pd.Timestamp("2026-02-03")


# ─────────────────────── not_yet ───────────────────────


def test_not_yet_when_window_incomplete():
    bars = _bars([
        ("2026-02-01", 100.0, 101.0, 99.0, 100.0),
        ("2026-02-02", 100.0, 102.0, 99.5, 101.5),
    ])
    # Only 2 of the 21 holding days have passed -> not yet.
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "not_yet"
    assert out["exit_price"] is None
    assert out["exit_date"] is None


def test_not_yet_when_no_forward_data():
    out = classify_outcome(_bars([]), entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "not_yet"


# ─────────────────────── ordering — stop on later bar ───────────────────────


def test_stop_hit_on_a_later_bar_after_safe_days():
    bars = _bars([
        ("2026-02-01", 100.0, 102.0, 99.0, 101.0),
        ("2026-02-02", 101.0, 102.0, 100.0, 100.5),
        ("2026-02-03", 100.0, 100.5, 92.0, 93.5),    # stop touched on day 3
    ])
    out = classify_outcome(bars, entry_price=100.0, stop_loss=93.0,
                           target_price=112.0, holding_days=21)
    assert out["outcome"] == "stop_hit"
    assert out["exit_date"] == pd.Timestamp("2026-02-03")
    assert out["exit_price"] == 93.0


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
        except Exception as e:                          # noqa: BLE001
            failed += 1
            print(f"ERROR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
