"""Valuation advisor — deterministic buy / accumulate / wait guidance.

This is the margin-of-safety entry trigger from AGENTS.md: the score says a
company is *good*, this says whether the *price* is good right now. Fair value is
a transparent, config-driven heuristic computed in Python — the AI never sets a
price target.

The model: ``fair value = fair P/E x EPS``, where the fair P/E starts from a
plain-business baseline and is raised for earnings growth and franchise quality.
It is deliberately conservative — "wait for a better price" is the safe default.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.config import load_config, resolve
from src.db.schema import connect


def _num(v) -> float | None:
    """Coerce to a finite float, or return None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _rupees(v) -> str:
    return "n/a" if v is None else f"Rs {v:,.0f}"


def estimate_fair_value(metrics: dict, quality, moat, acfg: dict) -> dict | None:
    """Return fair-value components, or None if the stock cannot be valued."""
    eps = _num(metrics.get("eps"))
    if eps is None or eps <= 0:
        return None

    growth = _num(metrics.get("earnings_growth"))
    if growth is None:
        growth = _num(metrics.get("revenue_growth")) or 0.0
    growth_pct = max(0.0, min(growth * 100.0, acfg["max_growth_considered"]))

    franchise = ((quality or 0.0) + (moat or 0.0)) / 2.0
    qp = acfg["quality_premium"]
    q_bonus = (qp["strong"] if franchise >= 75
               else qp["decent"] if franchise >= 60 else 0.0)

    fair_pe = acfg["base_pe"] + growth_pct * acfg["growth_pe_factor"] + q_bonus
    lo, hi = acfg["fair_pe_bounds"]
    fair_pe = max(lo, min(fair_pe, hi))
    return {"eps": eps, "fair_pe": fair_pe, "fair_value": fair_pe * eps,
            "earnings_declining": growth < 0}


def _rationale(price, fair_value, mos, fair_pe, current_pe, action,
               target, near_high, declining) -> str:
    pct = abs(mos) * 100.0
    high_note = " and is trading near its 3-year high" if near_high else ""
    if action == "Buy Now":
        msg = (f"At {_rupees(price)} the stock sits about {pct:.0f}% below its "
               f"estimated fair value of {_rupees(fair_value)} (fair P/E "
               f"~{fair_pe:.0f} vs current ~{current_pe:.0f}). This is a "
               f"reasonable entry point with a margin of safety.")
    elif action == "Accumulate Gradually":
        sign = "+" if mos >= 0 else "-"
        msg = (f"At {_rupees(price)} the stock is close to its estimated fair "
               f"value of {_rupees(fair_value)} ({sign}{pct:.0f}%){high_note}. "
               f"Neither a bargain nor expensive — buy in tranches rather than "
               f"one lump sum.")
    else:  # Wait
        msg = (f"At {_rupees(price)} the stock is about {pct:.0f}% above its "
               f"estimated fair value of {_rupees(fair_value)}{high_note}. The "
               f"price already reflects the company's strengths — wait for a "
               f"pullback toward {_rupees(target)} before buying.")
    if declining:
        msg += (" Caution: earnings are currently declining, so a low P/E here "
                "may be a cyclical value trap rather than a true bargain.")
    return msg


def advise(metrics: dict, current_price, price_low, price_high,
           quality, moat, acfg: dict) -> dict:
    """Produce a full valuation verdict + action for one stock."""
    blank = {"verdict": "Insufficient data", "action": "No call",
             "fair_value": None, "margin_of_safety": None,
             "target_buy_price": None, "current_price": current_price,
             "price_position": None, "fair_pe": None, "current_pe": None,
             "rationale": "Not enough data (positive earnings and a price) "
                          "to value this stock."}

    fv = estimate_fair_value(metrics, quality, moat, acfg)
    if fv is None or current_price is None or current_price <= 0:
        return blank

    fair_value = fv["fair_value"]
    mos = (fair_value - current_price) / current_price
    current_pe = current_price / fv["eps"]

    pos = None
    if price_high and price_low and price_high > price_low:
        pos = (current_price - price_low) / (price_high - price_low)

    bands = acfg["verdict_bands"]
    if mos >= bands["undervalued_above"]:
        verdict, action = "Undervalued", "Buy Now"
    elif mos <= bands["overvalued_below"]:
        verdict, action = "Overvalued", "Wait"
    else:
        verdict, action = "Fairly Valued", "Accumulate Gradually"

    target = fair_value * (1.0 - acfg["desired_margin_of_safety"])
    near_high = pos is not None and pos >= acfg["near_high_threshold"]

    return {"verdict": verdict, "action": action, "fair_value": fair_value,
            "margin_of_safety": mos, "target_buy_price": target,
            "current_price": current_price, "price_position": pos,
            "fair_pe": fv["fair_pe"], "current_pe": current_pe,
            "rationale": _rationale(current_price, fair_value, mos,
                                    fv["fair_pe"], current_pe, action, target,
                                    near_high, fv["earnings_declining"])}


def run_advisor() -> dict:
    """Compute buy/wait advice for every scored stock; write valuation_advice."""
    cfg = load_config()
    acfg = cfg["advisor"]
    db_path = resolve(cfg["paths"]["database"])
    run_date = date.today().isoformat()

    with connect(db_path) as conn:
        srun = conn.execute("SELECT MAX(run_date) FROM rankings").fetchone()[0]
        if not srun:
            return {"advised": 0, "actions": {}}
        ranked = [r[0] for r in conn.execute(
            "SELECT ticker FROM rankings WHERE run_date=?", (srun,))]
        scores = pd.read_sql_query(
            "SELECT ticker, category, score FROM scores WHERE run_date=?",
            conn, params=(srun,))
        funda = pd.read_sql_query(
            "SELECT ticker, metric, value FROM fundamentals WHERE period='TTM'",
            conn)
        prices = pd.read_sql_query(
            "SELECT ticker, date, close FROM prices", conn)

    metrics_by = {t: dict(zip(g["metric"], g["value"]))
                  for t, g in funda.groupby("ticker")}
    quality = scores[scores["category"] == "quality"].set_index(
        "ticker")["score"].to_dict()
    moat = scores[scores["category"] == "moat"].set_index(
        "ticker")["score"].to_dict()

    cutoff = (date.today()
              - timedelta(days=365 * acfg["price_history_years"])).isoformat()
    prices = prices[prices["date"] >= cutoff].sort_values("date")
    grp = prices.groupby("ticker")["close"]
    last, hi, lo = grp.last(), grp.max(), grp.min()

    rows, actions = [], {}
    for ticker in ranked:
        a = advise(metrics_by.get(ticker, {}), _num(last.get(ticker)),
                   _num(lo.get(ticker)), _num(hi.get(ticker)),
                   quality.get(ticker), moat.get(ticker), acfg)
        actions[a["action"]] = actions.get(a["action"], 0) + 1
        rows.append((ticker, run_date, a["current_price"], a["fair_value"],
                     a["margin_of_safety"], a["verdict"], a["action"],
                     a["target_buy_price"], a["price_position"],
                     a["current_pe"], a["fair_pe"], a["rationale"]))

    with connect(db_path) as conn:
        conn.execute("DELETE FROM valuation_advice WHERE run_date=?", (run_date,))
        conn.executemany(
            """INSERT INTO valuation_advice
               (ticker, run_date, current_price, fair_value, margin_of_safety,
                valuation_verdict, action, target_buy_price, price_position,
                current_pe, fair_pe, rationale)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return {"advised": len(rows), "actions": actions}
