"""Rolling 1-month backtest of the Quality-Momentum strategy.

For each month-end in [start, end]:
  1. Slice the prices table to data available as of that date.
  2. Compute momentum signals per ticker (`momentum.signals.compute_signals`).
  3. Apply trend + RSI filters.
  4. Apply the quality filter.
  5. Sector-relative weighted-percentile rank on the momentum signals.
  6. Form a top-N shortlist; size inversely to ATR (same as the live advisor).
  7. Hold to the next month-end. Portfolio return = Σ weight_i × return_i,
     with cash (1 − Σ weight_i) earning 0.
  8. Compare to Nifty 50 TRI for the same window.

Look-ahead bias caveat (documented):
- Prices are point-in-time — clean.
- Fundamentals are the *current* TTM snapshot. The pipeline doesn't yet store
  historical fundamentals, so the quality scores used at every backtest month
  are today's scores. This biases the quality filter toward names that ended
  up surviving / improving and **inflates the hit rate**. Treat the result as
  an upper bound, not a forecast. A proper backtest requires point-in-time
  fundamentals (deferred).

The dividend-yield drip from `benchmark.ASSUMED_ANNUAL_DIV_YIELD` is added to
both the portfolio side and the Nifty side so the comparison is fair.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.advisor.monthly import _position_sizes
from src.backtest.benchmark import (
    ASSUMED_ANNUAL_DIV_YIELD, TRADING_DAYS_PER_YEAR,
    load_nifty_prices, period_return, trading_days_between,
)
from src.config import load_config, resolve
from src.db.schema import connect
from src.momentum.signals import compute_signals


# ─────────────────────── helpers ───────────────────────


def _month_ends(prices: pd.DataFrame) -> list[pd.Timestamp]:
    """Last trading day of each calendar month in the price series."""
    dates = pd.to_datetime(prices["date"]).sort_values()
    if dates.empty:
        return []
    ym = dates.dt.to_period("M")
    return [pd.Timestamp(d) for d in dates.groupby(ym).max().tolist()]


def _read_latest_quality_scores(conn) -> pd.Series:
    """Average score across the configured quality categories.

    Held constant across every backtest month — this is the look-ahead bias
    documented at the top of the module.
    """
    rd = conn.execute("SELECT MAX(run_date) FROM scores").fetchone()[0]
    if not rd:
        return pd.Series(dtype=float, name="quality_score")
    cats = load_config()["momentum"]["quality_floor"]["categories"]
    df = pd.read_sql_query(
        "SELECT ticker, category, score FROM scores WHERE run_date=?",
        conn, params=(rd,))
    if df.empty:
        return pd.Series(dtype=float, name="quality_score")
    wide = df.pivot(index="ticker", columns="category", values="score")
    for c in cats:
        if c not in wide.columns:
            wide[c] = np.nan
    return wide[cats].mean(axis=1).rename("quality_score")


def _quality_pass(scores: pd.Series, floor_pct: float) -> set[str]:
    if scores.empty or scores.isna().all():
        return set(scores.index)
    threshold = float(scores.quantile(1 - floor_pct))
    return set(scores[scores >= threshold].index)


def _signals_as_of(prices_by_ticker: dict[str, pd.DataFrame],
                   as_of: pd.Timestamp, windows: dict) -> pd.DataFrame:
    """Run `compute_signals` for every ticker using only data <= as_of."""
    rows = []
    for ticker, sub in prices_by_ticker.items():
        slc = sub[sub["date"] <= as_of]
        if len(slc) < windows["twelve_months_days"]:
            continue
        sigs = compute_signals(slc.reset_index(drop=True), windows)
        if sigs is None:
            continue
        sigs["ticker"] = ticker
        rows.append(sigs)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("ticker")


def _shortlist(signals: pd.DataFrame, companies: pd.DataFrame,
               quality_set: set[str], cfg: dict) -> pd.DataFrame:
    """Mirror of `monthly_funnel`'s eligibility + ranking logic."""
    mcfg = cfg["momentum"]
    rsi_cap = mcfg["filters"]["rsi_ceiling"]
    rank_weights = mcfg["rank_weights"]
    shortlist_size = cfg["funnel"]["shortlist_size"]

    df = signals.join(companies[["sector"]], how="left")
    df["quality_passed"] = df.index.isin(quality_set)
    df["trend_passed"] = (df["trend_filter"] == 1).fillna(False)
    df["rsi_passed"] = (df["rsi_14"] <= rsi_cap) | df["rsi_14"].isna()
    df["eligible"] = (df["quality_passed"] & df["trend_passed"]
                      & df["rsi_passed"])

    eligible = df[df["eligible"]].copy()
    if eligible.empty:
        return eligible

    eligible["peer_group"] = eligible["sector"].fillna("OTHER")
    counts = eligible["peer_group"].value_counts()
    small = set(counts[counts < 8].index)
    eligible.loc[eligible["peer_group"].isin(small), "peer_group"] = "ALL"

    total_w = float(sum(rank_weights.values()))
    weighted = pd.Series(0.0, index=eligible.index)
    for sig, w in rank_weights.items():
        if sig in eligible.columns:
            pct = (eligible.groupby("peer_group")[sig].rank(pct=True) * 100
                   ).fillna(50.0)
            weighted = weighted + pct * float(w)
    eligible["momentum_score"] = weighted / total_w

    eligible = eligible.sort_values("momentum_score", ascending=False,
                                    na_position="last")
    return eligible.head(shortlist_size)


def _portfolio_return(shortlist: pd.DataFrame,
                      prices_by_ticker: dict[str, pd.DataFrame],
                      start_date: pd.Timestamp, end_date: pd.Timestamp,
                      cfg: dict, drip: float) -> tuple[float, list[tuple]]:
    """Compute the weighted portfolio return for [start_date, end_date].

    Each holding's return is price-only; the same per-trading-day dividend
    drip used on the Nifty side is applied to the aggregate so the comparison
    is fair (see module docstring).
    """
    sizes = _position_sizes(shortlist, cfg)
    holdings: list[tuple[str, float, float]] = []
    total_return = 0.0
    n_days = 0
    for ticker, weight in sizes.items():
        sub = prices_by_ticker.get(ticker)
        if sub is None or sub.empty:
            continue
        entry_rows = sub[sub["date"] <= start_date]
        exit_rows = sub[(sub["date"] > start_date) & (sub["date"] <= end_date)]
        if entry_rows.empty or exit_rows.empty:
            continue
        entry_close = float(entry_rows["close"].iloc[-1])
        exit_close = float(exit_rows["close"].iloc[-1])
        if entry_close <= 0:
            continue
        ret = exit_close / entry_close - 1.0
        total_return += float(weight) * ret
        holdings.append((ticker, float(weight), float(ret)))
        n_days = max(n_days, len(exit_rows))

    # Symmetric dividend drip (same approximation as Nifty side).
    total_return += float(sizes.sum()) * drip * (n_days / TRADING_DAYS_PER_YEAR)
    return total_return, holdings


# ─────────────────────── runner ───────────────────────


def run_backtest(start: Optional[str] = None,
                 end: Optional[str] = None,
                 annual_yield: float = ASSUMED_ANNUAL_DIV_YIELD) -> dict:
    """Run a rolling 1-month backtest. Persists results; returns a summary."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    windows = cfg["momentum"]["windows"]
    floor_pct = cfg["momentum"]["quality_floor"]["score_percentile"]

    with connect(db_path) as conn:
        all_prices = pd.read_sql_query(
            "SELECT ticker, date, open, high, low, close, volume "
            "FROM prices ORDER BY ticker, date", conn,
            parse_dates=["date"])
        companies = pd.read_sql_query(
            "SELECT ticker, sector, industry FROM companies", conn
        ).set_index("ticker")
        quality_scores = _read_latest_quality_scores(conn)

    if all_prices.empty:
        return {"error": "no prices in DB — run run_pipeline.py first"}
    if quality_scores.empty:
        return {"error": "no quality scores — run run_scoring.py first"}

    prices_by_ticker = {t: g.reset_index(drop=True)
                        for t, g in all_prices.groupby("ticker", sort=False)}

    earliest = all_prices["date"].min()
    latest = all_prices["date"].max()
    # warm-up: need >= 12 months of trading history before computing signals
    warmup = earliest + pd.Timedelta(days=int(windows["twelve_months_days"] * 1.5))
    start_ts = pd.to_datetime(start) if start else warmup
    end_ts = pd.to_datetime(end) if end else latest

    try:
        nifty = load_nifty_prices(start=str(start_ts.date()),
                                  end=str(end_ts.date()))
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"failed to load Nifty 50 prices: {exc}"}
    if nifty.empty:
        return {"error": "Nifty 50 series is empty for the requested range"}

    month_ends = _month_ends(nifty)
    if len(month_ends) < 2:
        return {"error": "fewer than 2 month-ends in the date range"}

    quality_set = _quality_pass(quality_scores, floor_pct)

    monthly_rows: list[dict] = []
    for i in range(len(month_ends) - 1):
        rebal = month_ends[i]
        exit_d = month_ends[i + 1]
        sigs = _signals_as_of(prices_by_ticker, rebal, windows)
        if sigs.empty:
            continue
        sl = _shortlist(sigs, companies, quality_set, cfg)
        nifty_ret = period_return(nifty, rebal, exit_d, annual_yield)
        if sl.empty:
            monthly_rows.append({
                "month": rebal.strftime("%Y-%m"),
                "portfolio_return": 0.0,
                "nifty_return": nifty_ret,
                "n_holdings": 0,
                "holdings": [],
            })
            continue
        port_ret, holdings = _portfolio_return(
            sl, prices_by_ticker, rebal, exit_d, cfg, annual_yield)
        monthly_rows.append({
            "month": rebal.strftime("%Y-%m"),
            "portfolio_return": port_ret,
            "nifty_return": nifty_ret,
            "n_holdings": len(holdings),
            "holdings": [(t, round(w, 4)) for t, w, _ in holdings],
        })

    if not monthly_rows:
        return {"error": "no months produced any portfolio"}

    df = pd.DataFrame(monthly_rows)
    df["excess_return"] = df["portfolio_return"] - df["nifty_return"]
    months_total = len(df)
    months_beating = int((df["excess_return"] > 0).sum())
    avg_excess = float(df["excess_return"].mean())

    equity = (1.0 + df["portfolio_return"]).cumprod()
    nifty_eq = (1.0 + df["nifty_return"]).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    nifty_total = float(nifty_eq.iloc[-1] - 1.0)
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0
    max_dd = float(drawdown.min())

    cfg_hash = hashlib.sha256(json.dumps({
        "rank_weights": cfg["momentum"]["rank_weights"],
        "filters": cfg["momentum"]["filters"],
        "quality_floor": cfg["momentum"]["quality_floor"],
        "shortlist_size": cfg["funnel"]["shortlist_size"],
        "monthly_advisor": cfg["monthly_advisor"],
    }, sort_keys=True).encode()).hexdigest()[:16]

    with connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO backtest_runs
               (run_at, config_hash, start_date, end_date, months_total,
                months_beating_nifty, avg_excess_pct, total_return_pct,
                nifty_return_pct, max_drawdown_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (datetime.utcnow().isoformat(timespec="seconds"), cfg_hash,
             month_ends[0].strftime("%Y-%m-%d"),
             month_ends[-1].strftime("%Y-%m-%d"),
             months_total, months_beating,
             round(avg_excess * 100, 3),
             round(total_return * 100, 2),
             round(nifty_total * 100, 2),
             round(max_dd * 100, 2)))
        run_id = cur.lastrowid
        for r in monthly_rows:
            conn.execute(
                """INSERT INTO backtest_monthly
                   (run_id, month, portfolio_return, nifty_return,
                    excess_return, n_holdings, holdings_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (run_id, r["month"],
                 round(r["portfolio_return"], 6),
                 round(r["nifty_return"], 6),
                 round(r["portfolio_return"] - r["nifty_return"], 6),
                 r["n_holdings"], json.dumps(r["holdings"])))

    return {
        "run_id": run_id,
        "months_total": months_total,
        "months_beating_nifty": months_beating,
        "hit_rate_pct": round(months_beating / months_total * 100, 1),
        "avg_excess_pct": round(avg_excess * 100, 3),
        "total_return_pct": round(total_return * 100, 2),
        "nifty_return_pct": round(nifty_total * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "start_date": month_ends[0].strftime("%Y-%m-%d"),
        "end_date": month_ends[-1].strftime("%Y-%m-%d"),
    }
