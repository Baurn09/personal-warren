"""Orchestrate the Company Analyzer: fetch -> score -> estimate -> swing -> AI.

``analyze_company`` ties the package together for one resolved ticker, persists
the result to ``company_analysis`` / ``company_estimates``, and returns the full
report dict. ``format_cli`` renders it for the terminal.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from src.advisor.monthly import compute_plan
from src.company import analyst, dossier, estimate, news, peers, profile, quality, swing
from src.config import load_config, resolve
from src.db.schema import connect
from src.momentum.signals import compute_signals

_HORIZON_LABELS = {"1m": "1 month", "6m": "6 months",
                   "12m": "12 months", "5y": "5 years"}


def _load_nifty():
    try:
        from src.backtest.benchmark import load_nifty_prices
        return load_nifty_prices()
    except Exception:                                          # noqa: BLE001
        return None


def analyze_company(ticker: str, *, refresh: bool = False, ai_mode: str = "lite",
                    no_ai: bool = False, use_cache: bool = True) -> dict:
    """Run the full analysis for one resolved ticker and persist it."""
    cfg = load_config()
    ca = cfg["company_analysis"]
    db_path = resolve(cfg["paths"]["database"])

    prof = profile.fetch_company_data(ticker, refresh=refresh)
    px = prof["prices"]
    if px.empty:
        raise RuntimeError(f"no price history available for {ticker}")

    # momentum signals (None if < ~1y history)
    signals = compute_signals(px.reset_index(drop=True), cfg["momentum"]["windows"])

    # quality vs sampled peers
    frame, mode, peer_list = peers.build_peer_frame(
        ticker, prof["sector"], prof["template"], prof["metrics"])
    q = quality.assess_quality(ticker, prof["template"], prof["metrics"],
                               frame, mode)
    spe = peers.sector_median_pe(frame, ticker) if mode == "relative" else None

    # multi-horizon estimates
    nifty = _load_nifty()
    estimates = estimate.estimate_all(
        px, prof["metrics"], q["composite"], signals, spe, nifty, ca)

    # 1-month plan + swing vs hold
    current = prof["current_price"]
    atr = signals.get("atr_14") if signals else None
    vol = signals.get("vol_1m") if signals else None
    plan = compute_plan(current, atr, vol, cfg["monthly_advisor"])
    hold_1m = estimates["1m"]["base"] if estimates.get("1m") else None
    sw = swing.swing_vs_hold(px, plan, hold_1m, int(ca["swing"]["horizon_days"]))

    # company-specific news
    news_items = news.fetch_company_news(prof["name"], ticker,
                                         limit=int(ca["news_items"]))

    report = {
        "profile": prof, "quality": q, "signals": signals,
        "estimates": estimates, "plan": plan, "swing": sw,
        "news": news_items, "peers": peer_list, "sector_pe": spe,
        "run_date": date.today().isoformat(),
    }

    # AI narrative over the finished dossier
    doss = dossier.build_company_dossier(report)
    report["dossier"] = doss
    report["ai"] = analyst.analyze(doss, mode=ai_mode, use_cache=use_cache,
                                   no_ai=no_ai)

    _persist(db_path, report)
    return report


def _overall_confidence(report: dict) -> str:
    confs = [e["confidence"] for e in report["estimates"].values() if e]
    if not confs or "low" in confs:
        return "low" if report["profile"]["history_days"] < 252 else "medium"
    return "high" if all(c == "high" for c in confs) else "medium"


def _persist(db_path, report: dict) -> None:
    p = report["profile"]
    q = report["quality"]
    sw = report["swing"]
    plan = report["plan"]
    ai = report["ai"]
    run_date = report["run_date"]
    conf = _overall_confidence(report)

    with connect(db_path) as conn:
        conn.execute("DELETE FROM company_analysis WHERE ticker=? AND run_date=?",
                     (p["ticker"], run_date))
        conn.execute(
            """INSERT INTO company_analysis
               (ticker, run_date, name, sector, template, business_summary,
                quality_mode, quality_total, quality_composite, sector_percentile,
                categories_json, flags_json, current_price, entry_price, stop_loss,
                target_price, swing_ev_pct, hold_ev_pct, swing_reco, confidence,
                ai_mode, ai_narrative, ai_verdict, ai_confidence, model, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["ticker"], run_date, p["name"], p["sector"], p["template"],
             p.get("business_summary"), q["mode"], q["final_total"],
             q["composite"], q["sector_percentile"],
             json.dumps(q["categories"]),
             json.dumps({"disqualifiers": q["disqualifiers"],
                         "penalties": q["penalties"]}),
             p["current_price"], plan["entry_price"], plan["stop_loss"],
             plan["target_price"],
             None if sw["swing_ev"] is None else sw["swing_ev"] * 100,
             None if sw["hold_ev"] is None else sw["hold_ev"] * 100,
             sw["recommendation"], conf, ai["mode"], ai["narrative"],
             ai["verdict"], ai["confidence"], ai["model"],
             report["run_date"]))

        conn.execute("DELETE FROM company_estimates WHERE ticker=? AND run_date=?",
                     (p["ticker"], run_date))
        for horizon, e in report["estimates"].items():
            if not e:
                continue
            conn.execute(
                """INSERT INTO company_estimates
                   (ticker, run_date, horizon, days, bear, base, bull,
                    prob_positive, expected_value, nifty_base, nifty_alpha,
                    components_json, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["ticker"], run_date, horizon, e["days"], e["bear"], e["base"],
                 e["bull"], e["prob_positive"], e["expected_value"],
                 e["nifty_base"], e["nifty_alpha"],
                 json.dumps(e["components"]), e["confidence"]))


# ─────────────────────── CLI rendering ───────────────────────


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def format_cli(report: dict) -> str:
    p = report["profile"]
    q = report["quality"]
    sw = report["swing"]
    plan = report["plan"]
    cfg = load_config()
    notional = float(cfg["company_analysis"]["notional_rupees"])

    out = []
    out.append("=" * 72)
    out.append(f" {p['name']}  [{p['ticker']}]   ·   {p.get('sector') or 'n/a'}")
    out.append("=" * 72)
    if p["current_price"]:
        out.append(f" Current price: Rs {p['current_price']:,.2f}   "
                   f"·   {p['history_days']} days of history   "
                   f"·   confidence: {_overall_confidence(report)}")
    out.append("")
    out.append(f" QUALITY / MOAT ({q['mode']} scoring)")
    sp = "n/a" if q["sector_percentile"] is None else f"{q['sector_percentile']:.0f}th pct"
    out.append(f"   Composite (Quality+Moat+FS): {q['composite']:.0f}/100   "
               f"·   total {q['final_total']:.0f}/100   ·   vs peers {sp}")
    cats = q["categories"]
    out.append("   " + "  ".join(f"{c[:4].title()} {cats[c]:.0f}" for c in
               ["quality", "moat", "financial_strength", "management",
                "valuation", "growth"]))
    if q["disqualifiers"] or q["penalties"]:
        out.append("   Red flags: " + ", ".join(q["disqualifiers"] + q["penalties"]))
    out.append("")
    out.append(" EXPECTED RETURN (Bear / Base / Bull · P(gain) · vs Nifty)")
    for h in ("1m", "6m", "12m", "5y"):
        e = report["estimates"].get(h)
        label = _HORIZON_LABELS[h]
        if not e:
            out.append(f"   {label:<10}: insufficient history")
            continue
        rupee = ""
        if e["base"] is not None:
            rupee = f"   (~Rs {e['base'] * notional:,.0f} on Rs {notional:,.0f})"
        alpha = "" if e["nifty_alpha"] is None else f"  vs Nifty {_pct(e['nifty_alpha'])}"
        out.append(
            f"   {label:<10}: {_pct(e['bear'])} / {_pct(e['base'])} / "
            f"{_pct(e['bull'])}   P {e['prob_positive'] * 100:.0f}%  "
            f"[{e['confidence']}]{alpha}{rupee}")
    out.append("")
    out.append(" 1-MONTH: SWING vs HOLD")
    out.append(f"   Entry ~Rs {plan['entry_price']:,.0f}   "
               f"Stop Rs {plan['stop_loss']:,.0f} "
               f"({(plan['stop_loss'] / plan['entry_price'] - 1) * 100:+.1f}%)   "
               f"Target Rs {plan['target_price']:,.0f} "
               f"({(plan['target_price'] / plan['entry_price'] - 1) * 100:+.1f}%)")
    if sw["swing"]:
        s = sw["swing"]
        out.append(f"   Swing hit rates: target {s['p_target'] * 100:.0f}%  "
                   f"stop {s['p_stop'] * 100:.0f}%  "
                   f"neither {s['p_neither'] * 100:.0f}%  "
                   f"(n={s['samples']})")
    out.append(f"   Swing EV: {_pct(sw['swing_ev'])}   ·   "
               f"Hold-1-month EV: {_pct(sw['hold_ev'])}   ·   "
               f">> {sw['recommendation']}")
    out.append("")
    news_items = report.get("news") or []
    if news_items:
        out.append(" RECENT NEWS")
        for n in news_items[:5]:
            if n.get("title"):
                out.append(f"   - {n['title']}  ({n['source']})")
        out.append("")
    ai = report["ai"]
    out.append(" AI ANALYST" + (f" ({ai['mode']})" if ai["narrative"] else ""))
    if ai["narrative"]:
        if ai.get("verdict"):
            out.append(f"   Verdict: {ai['verdict']}  (confidence {ai['confidence']})")
        for line in ai["narrative"].splitlines():
            out.append(f"   {line}" if line.strip() else "")
    else:
        out.append(f"   (no narrative — {ai.get('status', 'skipped')})")
    out.append("")
    out.append(" Estimates are probabilistic scenarios from historical behaviour")
    out.append(" + fundamentals, NOT predictions. Personal research aid, not advice.")
    out.append("=" * 72)
    return "\n".join(out)
