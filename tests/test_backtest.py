"""Unit tests for the rolling 1-month backtest.

Avoids hitting yfinance — tests target pure helpers and accounting identities.

Runnable two ways:
    pytest tests/test_backtest.py
    python tests/test_backtest.py        (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                          # noqa: E402
import pandas as pd                                         # noqa: E402

from src.backtest.benchmark import (                        # noqa: E402
    period_return, trading_days_between,
)
from src.backtest.engine import (                           # noqa: E402
    _month_ends, _portfolio_return, _quality_pass, _shortlist,
)


# ─────────────────────── benchmark.period_return ───────────────────────


def _flat_series(n: int, start: str = "2024-01-02",
                 close: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame({"date": dates,
                         "close": [close] * n})


def test_period_return_flat_price_is_just_dividend_drip():
    # 21 trading days flat -> price return 0; TRI = drip only.
    p = _flat_series(22)
    r = period_return(p, p["date"].iloc[0], p["date"].iloc[-1],
                      annual_yield=0.013)
    expected_drip = 0.013 * (21 / 252)
    assert abs(r - expected_drip) < 1e-12


def test_period_return_doubling_price():
    dates = pd.bdate_range(start="2024-01-02", periods=2)
    p = pd.DataFrame({"date": dates, "close": [100.0, 200.0]})
    r = period_return(p, p["date"].iloc[0], p["date"].iloc[-1],
                      annual_yield=0.0)
    assert abs(r - 1.0) < 1e-12


def test_period_return_short_window_returns_zero():
    p = _flat_series(1)
    assert period_return(p, p["date"].iloc[0], p["date"].iloc[0]) == 0.0


def test_trading_days_between():
    p = _flat_series(11)
    assert trading_days_between(p, p["date"].iloc[0], p["date"].iloc[-1]) == 10


# ─────────────────────── engine helpers ───────────────────────


def test_month_ends_returns_last_trading_day_per_month():
    dates = pd.to_datetime([
        "2024-01-02", "2024-01-15", "2024-01-31",
        "2024-02-01", "2024-02-28", "2024-02-29",
        "2024-03-04",
    ])
    df = pd.DataFrame({"date": dates, "close": np.arange(len(dates)) + 1.0})
    me = _month_ends(df)
    assert [d.strftime("%Y-%m-%d") for d in me] == [
        "2024-01-31", "2024-02-29", "2024-03-04",
    ]


def test_quality_pass_picks_top_pct():
    scores = pd.Series({"A": 90.0, "B": 70.0, "C": 60.0, "D": 50.0},
                       name="quality_score")
    # top 50% means scores >= the 0.50 quantile of the universe.
    chosen = _quality_pass(scores, floor_pct=0.5)
    assert chosen == {"A", "B"}


def test_quality_pass_handles_all_nan():
    scores = pd.Series({"A": np.nan, "B": np.nan}, name="quality_score")
    # All-NaN universe: keep everyone rather than empty the funnel.
    assert _quality_pass(scores, floor_pct=0.5) == {"A", "B"}


# ─────────────────────── shortlist filtering ───────────────────────


def _signals_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).set_index("ticker")
    return df


def _cfg() -> dict:
    return {
        "momentum": {
            "filters": {"rsi_ceiling": 80},
            "rank_weights": {"mom_12_1": 1.0, "dist_52w_high": 0.0,
                             "mom_6m": 0.0, "volume_trend": 0.0},
        },
        "funnel": {"shortlist_size": 2},
    }


def test_shortlist_drops_failing_trend_or_rsi():
    sigs = _signals_frame([
        {"ticker": "A", "trend_filter": 1, "rsi_14": 60.0,
         "mom_12_1": 0.30, "mom_6m": 0.10,
         "dist_52w_high": -0.02, "volume_trend": 1.1,
         "atr_14": 2.0, "current_price": 100.0},
        {"ticker": "B", "trend_filter": 0, "rsi_14": 50.0,           # bad trend
         "mom_12_1": 0.40, "mom_6m": 0.10,
         "dist_52w_high": -0.02, "volume_trend": 1.1,
         "atr_14": 2.0, "current_price": 100.0},
        {"ticker": "C", "trend_filter": 1, "rsi_14": 90.0,           # overheat
         "mom_12_1": 0.50, "mom_6m": 0.10,
         "dist_52w_high": -0.02, "volume_trend": 1.1,
         "atr_14": 2.0, "current_price": 100.0},
        {"ticker": "D", "trend_filter": 1, "rsi_14": 55.0,
         "mom_12_1": 0.05, "mom_6m": 0.10,
         "dist_52w_high": -0.02, "volume_trend": 1.1,
         "atr_14": 2.0, "current_price": 100.0},
    ])
    companies = pd.DataFrame(
        {"sector": ["IT", "IT", "IT", "IT"]},
        index=["A", "B", "C", "D"])
    cfg = _cfg()
    cfg["funnel"]["shortlist_size"] = 5     # capacity isn't the constraint

    sl = _shortlist(sigs, companies, quality_set={"A", "B", "C", "D"}, cfg=cfg)
    # B fails trend, C fails RSI -> only A and D remain, with A ranked first.
    assert list(sl.index) == ["A", "D"]


def test_shortlist_quality_filter_drops_failures():
    sigs = _signals_frame([
        {"ticker": "A", "trend_filter": 1, "rsi_14": 60.0,
         "mom_12_1": 0.30, "mom_6m": 0.10,
         "dist_52w_high": -0.02, "volume_trend": 1.1,
         "atr_14": 2.0, "current_price": 100.0},
        {"ticker": "B", "trend_filter": 1, "rsi_14": 55.0,
         "mom_12_1": 0.40, "mom_6m": 0.10,
         "dist_52w_high": -0.02, "volume_trend": 1.1,
         "atr_14": 2.0, "current_price": 100.0},
    ])
    companies = pd.DataFrame({"sector": ["IT", "IT"]}, index=["A", "B"])
    sl = _shortlist(sigs, companies, quality_set={"A"}, cfg=_cfg())
    assert list(sl.index) == ["A"]


# ─────────────────────── portfolio return accounting ───────────────────────


def _price_series(values: list[tuple[str, float]]) -> pd.DataFrame:
    """[(date, close), ...] -> a per-ticker prices frame."""
    return pd.DataFrame({
        "date": pd.to_datetime([d for d, _ in values]),
        "close": [c for _, c in values],
    })


def test_portfolio_return_matches_weighted_sum():
    # Two stocks, equal ATR -> equal weights; +10% and 0% -> portfolio = 5%
    # of the deployed capital. With single_stock_cap_pct = 50 and
    # cash_reserve_pct floor = 10%, deployed = 90% -> equal 45% each.
    # Hand calc: 0.45 * 0.10 + 0.45 * 0.0 = 0.045.
    shortlist = pd.DataFrame({
        "atr_14": [1.0, 1.0],
        "current_price": [100.0, 100.0],
    }, index=["A", "B"])

    prices = {
        "A": _price_series([("2024-01-31", 100.0), ("2024-02-29", 110.0)]),
        "B": _price_series([("2024-01-31", 100.0), ("2024-02-29", 100.0)]),
    }
    cfg = {"portfolio": {"single_stock_cap_pct": 50,
                         "cash_reserve_pct": [10, 20]}}
    start = pd.Timestamp("2024-01-31")
    end = pd.Timestamp("2024-02-29")
    ret, holdings = _portfolio_return(shortlist, prices, start, end, cfg,
                                      drip=0.0)
    assert abs(ret - 0.045) < 1e-9
    assert {h[0] for h in holdings} == {"A", "B"}


def test_portfolio_return_skips_missing_history():
    # B has no data after the rebalance date -> dropped from the basket.
    shortlist = pd.DataFrame({
        "atr_14": [1.0, 1.0],
        "current_price": [100.0, 100.0],
    }, index=["A", "B"])
    prices = {
        "A": _price_series([("2024-01-31", 100.0), ("2024-02-29", 110.0)]),
        "B": _price_series([("2024-01-31", 100.0)]),    # no exit row
    }
    cfg = {"portfolio": {"single_stock_cap_pct": 50,
                         "cash_reserve_pct": [10, 20]}}
    ret, holdings = _portfolio_return(
        shortlist, prices,
        pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29"),
        cfg, drip=0.0)
    assert [h[0] for h in holdings] == ["A"]
    assert abs(ret - 0.45 * 0.10) < 1e-9


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
        except Exception as e:                              # noqa: BLE001
            failed += 1
            print(f"ERROR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
