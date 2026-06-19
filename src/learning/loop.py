"""Review past monthly picks against the prices that actually happened.

For every row in ``monthly_advice``, walk forward day-by-day from the
pick's run_date and classify the outcome:

- ``stop_hit``   — daily low touched (or gapped through) the stop. Exit
                   priced at the worse of the open and the stop level.
- ``target_hit`` — daily high touched (or gapped through) the target. Exit
                   priced at the better of the open and the target.
- ``held``       — neither hit within the holding window; mark-to-market on
                   the close of the final holding day.
- ``not_yet``    — the holding window hasn't elapsed yet (recent picks).

Stop and target are first-touch — if both are breached on the same bar the
stop wins (conservative, the intraday sequence is unknown).

Results land in ``pick_outcomes`` (one row per (ticker, run_date)). The
Nifty 50 TRI return for the same window is recorded alongside so excess
return is comparable to the backtest.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd

from src.backtest.benchmark import load_nifty_prices, period_return
from src.config import load_config, resolve
from src.db.schema import connect


# ─────────────────────── outcome classification ───────────────────────


def classify_outcome(prices: pd.DataFrame, entry_price: float,
                     stop_loss: float, target_price: float,
                     holding_days: int) -> dict:
    """Walk the forward price series and return the outcome for one pick.

    ``prices`` must be the per-ticker OHLC frame *after* the pick's
    run_date, sorted ascending by date and containing columns ``date,
    open, high, low, close``.
    """
    if prices.empty:
        return {"outcome": "not_yet", "exit_date": None, "exit_price": None}

    window = prices.iloc[:holding_days]
    if window.empty:
        return {"outcome": "not_yet", "exit_date": None, "exit_price": None}

    for _, bar in window.iterrows():
        bar_open = float(bar["open"])
        bar_low = float(bar["low"])
        bar_high = float(bar["high"])
        bar_date = bar["date"]
        # Stop first (conservative when both bands are breached intraday).
        if bar_low <= stop_loss:
            exit_price = min(bar_open, stop_loss)   # honour gap-downs
            return {"outcome": "stop_hit",
                    "exit_date": bar_date, "exit_price": float(exit_price)}
        if bar_high >= target_price:
            exit_price = max(bar_open, target_price)  # honour gap-ups
            return {"outcome": "target_hit",
                    "exit_date": bar_date, "exit_price": float(exit_price)}

    if len(window) < holding_days:
        # Window not full yet — premature to call this a "held" outcome.
        return {"outcome": "not_yet", "exit_date": None, "exit_price": None}

    last = window.iloc[-1]
    return {"outcome": "held",
            "exit_date": last["date"], "exit_price": float(last["close"])}


# ─────────────────────── runner ───────────────────────


def _load_prices_for(tickers: set[str], conn) -> dict[str, pd.DataFrame]:
    if not tickers:
        return {}
    qmarks = ",".join("?" for _ in tickers)
    df = pd.read_sql_query(
        f"SELECT ticker, date, open, high, low, close FROM prices "
        f"WHERE ticker IN ({qmarks}) ORDER BY ticker, date",
        conn, params=tuple(tickers), parse_dates=["date"])
    return {t: g.reset_index(drop=True) for t, g in df.groupby("ticker", sort=False)}


def review_outcomes() -> dict:
    """Review every recorded monthly pick. Idempotent — refreshes the table."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    review_date = date.today().isoformat()

    with connect(db_path) as conn:
        advice = pd.read_sql_query(
            "SELECT ticker, run_date, entry_price, stop_loss, target_price, "
            "holding_days FROM monthly_advice", conn, parse_dates=["run_date"])
        if advice.empty:
            return {"reviewed": 0,
                    "error": "no monthly_advice rows — run run_scoring.py"}
        prices_by_ticker = _load_prices_for(set(advice["ticker"]), conn)

    # Nifty cache once for the whole review (shared across picks).
    try:
        nifty = load_nifty_prices(start=str(advice["run_date"].min().date()))
    except Exception as exc:                                    # noqa: BLE001
        return {"reviewed": 0,
                "error": f"could not load Nifty 50 prices: {exc}"}

    rows: list[tuple] = []
    counts = {"stop_hit": 0, "target_hit": 0, "held": 0, "not_yet": 0}
    for _, r in advice.iterrows():
        ticker = r["ticker"]
        run_dt = pd.Timestamp(r["run_date"]).normalize()
        holding = int(r["holding_days"] or 21)
        entry = float(r["entry_price"])
        stop = float(r["stop_loss"])
        target = float(r["target_price"])

        sub = prices_by_ticker.get(ticker)
        if sub is None:
            outcome = {"outcome": "not_yet", "exit_date": None, "exit_price": None}
        else:
            forward = sub[sub["date"] > run_dt].reset_index(drop=True)
            outcome = classify_outcome(forward, entry, stop, target, holding)

        if outcome["exit_date"] is not None and outcome["exit_price"] is not None:
            actual_ret = outcome["exit_price"] / entry - 1.0
            try:
                nifty_ret = period_return(nifty, run_dt,
                                          pd.Timestamp(outcome["exit_date"]))
            except Exception:                                   # noqa: BLE001
                nifty_ret = None
            excess = (actual_ret - nifty_ret) if nifty_ret is not None else None
        else:
            actual_ret = nifty_ret = excess = None

        counts[outcome["outcome"]] += 1
        rows.append((
            ticker, run_dt.strftime("%Y-%m-%d"), review_date,
            entry, stop, target, holding,
            outcome["exit_date"].strftime("%Y-%m-%d") if outcome["exit_date"] is not None else None,
            outcome["exit_price"],
            outcome["outcome"],
            round(actual_ret * 100, 4) if actual_ret is not None else None,
            round(nifty_ret * 100, 4) if nifty_ret is not None else None,
            round(excess * 100, 4) if excess is not None else None,
        ))

    with connect(db_path) as conn:
        conn.execute("DELETE FROM pick_outcomes")
        conn.executemany(
            """INSERT INTO pick_outcomes
               (ticker, run_date, review_date, entry_price, stop_loss,
                target_price, holding_days, exit_date, exit_price, outcome,
                actual_return_pct, nifty_return_pct, excess_return_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

    return {
        "reviewed": len(rows),
        "stop_hit": counts["stop_hit"],
        "target_hit": counts["target_hit"],
        "held": counts["held"],
        "not_yet": counts["not_yet"],
    }


# ─────────────────────── aggregation ───────────────────────


def summarise() -> dict:
    """Headline numbers for the dashboard — closed picks only."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    with connect(db_path) as conn:
        df = pd.read_sql_query("SELECT * FROM pick_outcomes", conn)
    if df.empty:
        return {"closed": 0}
    closed = df[df["outcome"] != "not_yet"]
    if closed.empty:
        return {"closed": 0, "open": len(df)}
    n = len(closed)
    return {
        "closed": n,
        "open": int((df["outcome"] == "not_yet").sum()),
        "stop_hit": int((closed["outcome"] == "stop_hit").sum()),
        "target_hit": int((closed["outcome"] == "target_hit").sum()),
        "held": int((closed["outcome"] == "held").sum()),
        "winners": int((closed["actual_return_pct"] > 0).sum()),
        "avg_return_pct": float(closed["actual_return_pct"].mean()),
        "avg_excess_pct": float(closed["excess_return_pct"].dropna().mean())
            if closed["excess_return_pct"].notna().any() else None,
        "beat_nifty": int((closed["excess_return_pct"] > 0).sum()),
    }
