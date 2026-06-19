"""Unit tests for the Phase 2 scoring math.

Runnable two ways:
    pytest tests/test_scoring.py
    python tests/test_scoring.py        (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd                       # noqa: E402

from src.scoring import engine, flags     # noqa: E402


def _frame(n: int = 10, **metric_cols) -> pd.DataFrame:
    """A synthetic metrics DataFrame: n stocks, one sector, General template."""
    idx = [f"T{i}" for i in range(n)]
    data = {"sector": ["TestSector"] * n,
            "industry": ["Test"] * n,
            "template": ["General"] * n}
    data.update(metric_cols)
    return pd.DataFrame(data, index=idx)


def test_percentile_higher_is_better():
    # quality is driven solely by roe here (other quality metrics absent)
    df = _frame(roe=[i / 100 for i in range(1, 11)])
    q = engine.score(df)["quality"]
    assert q.loc["T0"] == 10.0      # lowest roe -> lowest percentile
    assert q.loc["T9"] == 100.0     # highest roe -> highest percentile
    assert list(q) == sorted(q)     # monotonic increasing with roe


def test_percentile_lower_is_better():
    # valuation is driven solely by pe; a low pe must score well
    df = _frame(pe=[10 * i for i in range(1, 11)])
    v = engine.score(df)["valuation"]
    assert v.loc["T0"] == 90.0      # cheapest pe -> best valuation
    assert v.loc["T9"] == 0.0       # priciest pe -> worst valuation
    assert list(v) == sorted(v, reverse=True)


def test_neutral_when_no_data():
    out = engine.score(_frame())    # no metric columns at all
    for cat in engine.CATEGORIES:
        assert (out[cat] == engine.NEUTRAL).all()
    assert (out["raw_total"] == engine.NEUTRAL).all()


def test_weighted_total_in_range():
    df = _frame(roe=[i / 100 for i in range(1, 11)],
                pe=[10 * i for i in range(1, 11)])
    out = engine.score(df)
    assert out["raw_total"].between(0, 100).all()


def test_disqualifiers():
    assert flags.disqualifiers({"net_profit": -5}) == ["loss_making"]
    assert flags.disqualifiers({"book_value": -1}) == ["negative_equity"]
    assert flags.disqualifiers({"net_profit": 100, "book_value": 50}) == []
    assert flags.disqualifiers({"net_profit": float("nan")}) == []


def test_penalties():
    assert "weak_cash_conversion" in dict(
        flags.penalties({"cash_conversion": 0.3}, "General"))
    assert "high_leverage" in dict(
        flags.penalties({"debt_to_equity": 200}, "General"))
    # banks/NBFCs are exempt from the leverage penalty
    assert "high_leverage" not in dict(
        flags.penalties({"debt_to_equity": 200}, "Financials"))
    pen = dict(flags.penalties({"fcf": -100, "promoter_holding": 0.1}, "General"))
    assert "negative_fcf" in pen and "low_promoter_holding" in pen


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
