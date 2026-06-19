"""Monthly advisor — Take / Hold / Skip + entry, stop, target, position size.

For each stock in the current monthly shortlist this produces a deterministic
action plan:

- **Action** — `Take` for new picks (v1 doesn't yet track existing holdings;
  `Hold` / `Skip` come in once a portfolio state is persisted).
- **Entry price** — current close (a pullback rule can layer in later).
- **Stop-loss** — the *tighter* of a fixed % stop and an ATR-based stop.
- **Target price** — the more *conservative* of a fixed % target and one
  based on 1-month realised volatility.
- **Position size** — inverse-volatility (ATR-aware) weights, normalised to
  the configured equity target and capped per stock.

Every price and percentage is computed in Python. The AI never sets a number
that gets stored here.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pandas as pd

from src.config import load_config, resolve
from src.db.schema import connect


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# ─────────────────────── per-stock plan ───────────────────────


def compute_plan(current_price: float, atr: Optional[float],
                 vol_annual: Optional[float], advisor_cfg: dict) -> dict:
    """Compute entry, stop and target for one stock (no position sizing).

    Stop is the tighter (closer to entry) of:
      ``entry × (1 - fixed_pct)``    and    ``entry − atr_multiple × ATR``
    Target is the more conservative (closer to entry) of:
      ``entry × (1 + fixed_pct)``    and    ``entry × (1 + stdev_multiple × monthly_stdev)``
    where monthly_stdev = annualised vol / sqrt(12).
    """
    entry = current_price

    s = advisor_cfg["stop_loss"]
    pct_stop = entry * (1.0 - float(s["fixed_pct"]))
    atr_stop = (entry - float(s["atr_multiple"]) * atr) if atr else None
    stop = pct_stop if atr_stop is None else max(atr_stop, pct_stop)

    t = advisor_cfg["target"]
    pct_target = entry * (1.0 + float(t["fixed_pct"]))
    if vol_annual:
        monthly_stdev = vol_annual / math.sqrt(12.0)
        stdev_target = entry * (1.0 + float(t["stdev_multiple"]) * monthly_stdev)
    else:
        stdev_target = None
    target = pct_target if stdev_target is None else min(stdev_target, pct_target)

    return {"entry_price": entry, "stop_loss": stop, "target_price": target}


# ─────────────────────── position sizing ───────────────────────


def _position_sizes(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Inverse-volatility position weights (sum ≤ 1 − cash_reserve).

    Weight ∝ price / ATR — less volatile names get bigger positions. Then we
    clip each at the single-stock cap; excess from clipping rolls to cash
    (we don't over-deploy by redistributing into already-large positions).
    """
    pcfg = cfg["portfolio"]
    cash_lo_pct = float(pcfg["cash_reserve_pct"][0])
    target_equity = 1.0 - (cash_lo_pct / 100.0)
    cap = float(pcfg["single_stock_cap_pct"]) / 100.0

    inv = []
    for atr, price in zip(df["atr_14"], df["current_price"]):
        a, p = _num(atr), _num(price)
        inv.append((p / a) if (a and p and a > 0 and p > 0) else 1.0)
    inv = pd.Series(inv, index=df.index, dtype=float)
    total = inv.sum()
    if total <= 0:
        return pd.Series(0.0, index=df.index)
    return (inv / total * target_equity).clip(upper=cap)


# ─────────────────────── rationale ───────────────────────


def _rationale(row: pd.Series, plan: dict, pos_size: float) -> str:
    parts: list[str] = []
    mom_score = row.get("momentum_score")
    if pd.notna(mom_score):
        parts.append(f"momentum {float(mom_score):.0f}/100")
    mom_12_1 = row.get("mom_12_1")
    if pd.notna(mom_12_1):
        parts.append(f"12-1 ret {float(mom_12_1) * 100:+.0f}%")
    rsi = row.get("rsi_14")
    if pd.notna(rsi):
        parts.append(f"RSI {float(rsi):.0f}")
    sig_blurb = ", ".join(parts) if parts else "momentum signals available"

    entry = plan["entry_price"]
    stop = plan["stop_loss"]
    target = plan["target_price"]
    return (f"{sig_blurb}. Take. "
            f"Entry ~Rs {entry:,.0f}; "
            f"stop Rs {stop:,.0f} ({(stop / entry - 1) * 100:+.1f}%); "
            f"target Rs {target:,.0f} ({(target / entry - 1) * 100:+.1f}%). "
            f"Position ~{pos_size * 100:.1f}% (vol-adjusted).")


# ─────────────────────── runner ───────────────────────


def run_monthly_advisor(run_date: Optional[str] = None) -> dict:
    """Build the action plan for every stock in the current monthly shortlist.

    Writes to ``monthly_advice``. Returns ``{advised, total_allocated_pct}``.
    """
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    advisor_cfg = cfg["monthly_advisor"]
    holding_days = int(advisor_cfg["desired_holding_days"])
    if run_date is None:
        run_date = date.today().isoformat()

    with connect(db_path) as conn:
        rrun = conn.execute(
            "SELECT MAX(run_date) FROM monthly_rankings").fetchone()[0]
        mrun = conn.execute(
            "SELECT MAX(run_date) FROM momentum_signals").fetchone()[0]
        if not rrun or not mrun:
            return {"advised": 0,
                    "error": "monthly rankings or momentum signals missing"}

        rankings = pd.read_sql_query(
            "SELECT * FROM monthly_rankings "
            "WHERE run_date=? AND in_shortlist=1 ORDER BY rank",
            conn, params=(rrun,))
        sig = pd.read_sql_query(
            "SELECT ticker, current_price, atr_14, vol_1m, mom_12_1, rsi_14 "
            "FROM momentum_signals WHERE run_date=?",
            conn, params=(mrun,))

    if rankings.empty:
        return {"advised": 0}

    df = rankings.merge(sig, on="ticker", how="left").set_index("ticker")
    sizes = _position_sizes(df, cfg)

    rows: list[tuple] = []
    for ticker, row in df.iterrows():
        cp = _num(row.get("current_price"))
        if cp is None:
            continue
        plan = compute_plan(cp, _num(row.get("atr_14")),
                            _num(row.get("vol_1m")), advisor_cfg)
        pos = float(sizes.loc[ticker]) if ticker in sizes.index else 0.0
        rationale = _rationale(row, plan, pos)
        rows.append((ticker, run_date, "Take",
                     round(plan["entry_price"], 2),
                     round(plan["stop_loss"], 2),
                     round(plan["target_price"], 2),
                     round(pos * 100.0, 2), holding_days, rationale))

    with connect(db_path) as conn:
        conn.execute("DELETE FROM monthly_advice WHERE run_date=?",
                     (run_date,))
        conn.executemany(
            """INSERT INTO monthly_advice
               (ticker, run_date, action, entry_price, stop_loss, target_price,
                position_size_pct, holding_days, rationale)
               VALUES (?,?,?,?,?,?,?,?,?)""", rows)

    return {"advised": len(rows),
            "total_allocated_pct": round(sizes.sum() * 100.0, 1)}
