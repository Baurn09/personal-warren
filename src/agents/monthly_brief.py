"""Monthly Brief agent — one ~100-word catalyst + 30-day risk per shortlist pick.

Replaces the 5-agent Bull/Bear/Value/Growth/Judge debate in the monthly mode
(retained in tree for the legacy long-term workflow). The horizon is 21 trading
days; the brief explains the *single* most likely catalyst and the *single*
biggest 30-day risk in plain prose. Numbers come from the deterministic dossier
— the LLM never invents figures.

Caching makes re-runs free. ~15 picks/month -> well under the OpenRouter free
tier (50 requests/day).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from src.agents.llm import LLMClient
from src.config import load_config, resolve
from src.db.schema import connect

_SYSTEM = (
    "You are a concise monthly research analyst for an Indian retail "
    "investor. Output is a single paragraph of plain prose for a "
    "21-trading-day horizon. Reason ONLY from the dossier — do not invent or "
    "recall any numbers. If a sector or business-quality observation is not "
    "directly supported by the dossier, omit it. ~100 words, no headings, "
    "no bullet points, no markdown."
)

_TASK = (
    "Write the monthly brief for this stock covering, in order:\n"
    "  (1) one sentence summarising momentum strength and the technical setup;\n"
    "  (2) one sentence naming the most likely catalyst over the next "
    "~21 trading days (sector tailwind, earnings cycle, technical breakout — "
    "ground it in something in the dossier);\n"
    "  (3) one sentence on the single biggest 30-day risk that could trip the "
    "stop-loss;\n"
    "  (4) one sentence restating the entry / stop / target levels and "
    "position size from the advisor's plan.\n"
    "Total ~100 words. No headings."
)


# ─────────────────────── dossier ───────────────────────


def _pct(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v) * 100:+.1f}%"


def _num(v, fmt: str = "{:.2f}") -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return fmt.format(float(v))


def build_brief_dossier(pick: dict) -> str:
    """Render the short dossier handed to the Monthly Brief agent.

    All numbers must already be computed in Python — this function only
    formats. `pick` carries momentum signals, quality scores, advisor plan
    and company basics.
    """
    sigs = pick["signals"]
    plan = pick["plan"]
    sector = pick.get("sector") or "n/a"
    mom_score = pick.get("momentum_score")
    quality = pick.get("quality_score")

    sector_n = pick.get("sector_picks_count")
    sector_blurb = (f"{sector_n} pick(s) from this sector in this month's "
                    f"shortlist" if sector_n else "no peer picks this month")
    entry = plan["entry_price"]
    stop = plan["stop_loss"]
    target = plan["target_price"]
    pos = plan["position_size_pct"]

    return f"""COMPANY: {pick['name']}  [{pick['ticker']}]
Sector: {sector}   |   Rank {pick['rank']} of {pick['n_shortlist']} this month
Sector context: {sector_blurb}.

MOMENTUM SIGNALS (deterministic, point-in-time)
  12-1 return : {_pct(sigs.get('mom_12_1'))}    6-month return : {_pct(sigs.get('mom_6m'))}
  Distance from 52-week high : {_pct(sigs.get('dist_52w_high'))}
  RSI(14) : {_num(sigs.get('rsi_14'), '{:.0f}')}    Trend filter : \
{'above 50 & 200 DMA' if sigs.get('trend_filter') == 1 else 'below trend'}
  Volume trend (20d/60d) : {_num(sigs.get('volume_trend'))}x
  ATR(14) : Rs {_num(sigs.get('atr_14'))}    1m volatility (annualised) : \
{_pct(sigs.get('vol_1m'))}

QUALITY GUARDRAIL
  Quality+Moat+FS composite : \
{_num(quality, '{:.0f}')} / 100  (sector-relative percentile average)
  Sector-relative momentum score : {_num(mom_score, '{:.0f}')} / 100

ADVISOR'S PLAN  (21-day horizon)
  Entry ~Rs {entry:,.0f}   Stop Rs {stop:,.0f} ({(stop / entry - 1) * 100:+.1f}%)\
   Target Rs {target:,.0f} ({(target / entry - 1) * 100:+.1f}%)
  Position size : {pos:.1f}% of portfolio (vol-adjusted)"""


# ─────────────────────── data loading ───────────────────────


def _load_picks(conn) -> list[dict]:
    rrun = conn.execute(
        "SELECT MAX(run_date) FROM monthly_rankings").fetchone()[0]
    arun = conn.execute(
        "SELECT MAX(run_date) FROM monthly_advice").fetchone()[0]
    mrun = conn.execute(
        "SELECT MAX(run_date) FROM momentum_signals").fetchone()[0]
    if not (rrun and arun and mrun):
        return []

    rankings = pd.read_sql_query(
        "SELECT * FROM monthly_rankings WHERE run_date=? AND in_shortlist=1 "
        "ORDER BY rank", conn, params=(rrun,))
    advice = pd.read_sql_query(
        "SELECT * FROM monthly_advice WHERE run_date=?", conn, params=(arun,))
    sigs = pd.read_sql_query(
        "SELECT * FROM momentum_signals WHERE run_date=?",
        conn, params=(mrun,))
    companies = pd.read_sql_query(
        "SELECT ticker, name, sector FROM companies", conn)

    if rankings.empty:
        return []

    n_shortlist = len(rankings)
    df = (rankings
          .merge(advice, on=["ticker", "run_date"], how="left")
          .merge(sigs, on="ticker", how="left", suffixes=("", "_sig"))
          .merge(companies, on="ticker", how="left"))

    sector_counts = df["sector"].value_counts().to_dict()

    picks: list[dict] = []
    for _, r in df.iterrows():
        sig_cols = ["mom_12_1", "mom_6m", "dist_52w_high", "volume_trend",
                    "trend_filter", "rsi_14", "atr_14", "vol_1m", "vol_3m",
                    "current_price"]
        picks.append({
            "ticker": r["ticker"],
            "name": r.get("name") or r["ticker"],
            "sector": r.get("sector"),
            "rank": int(r["rank"]) if pd.notna(r["rank"]) else 0,
            "n_shortlist": n_shortlist,
            "sector_picks_count": int(sector_counts.get(r.get("sector"), 0)),
            "quality_score": r.get("quality_score"),
            "momentum_score": r.get("momentum_score"),
            "signals": {c: r.get(c) for c in sig_cols},
            "plan": {
                "entry_price": float(r["entry_price"]),
                "stop_loss": float(r["stop_loss"]),
                "target_price": float(r["target_price"]),
                "position_size_pct": float(r["position_size_pct"]),
            },
            "run_date": r["run_date"],
        })
    return picks


# ─────────────────────── runner ───────────────────────


def run_monthly_briefs(limit: Optional[int] = None,
                       use_cache: bool = True) -> dict:
    """Generate one brief per shortlist pick. Returns a summary dict."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    model = cfg["ai"]["models"]["specialist"]
    run_date = date.today().isoformat()

    client = LLMClient(db_path, use_cache=use_cache)

    with connect(db_path) as conn:
        picks = _load_picks(conn)
    if limit:
        picks = picks[:limit]
    if not picks:
        return {"picks": 0, "completed": 0,
                "error": "no monthly shortlist — run run_scoring.py first"}

    stop_after = cfg["ai"].get("stop_after_consecutive_errors", 3)
    summary = {"picks": len(picks), "completed": 0, "errors": 0,
               "stopped_early": False}
    consecutive = 0
    for i, p in enumerate(picks, 1):
        tag = f"[{i}/{len(picks)}] {p['ticker']}"
        try:
            dossier = build_brief_dossier(p)
            user = f"{dossier}\n\nTASK: {_TASK}"
            brief = client.chat(_SYSTEM, user, model)
            with connect(db_path) as conn:
                conn.execute(
                    "DELETE FROM monthly_briefs WHERE ticker=? AND run_date=?",
                    (p["ticker"], run_date))
                conn.execute(
                    "INSERT INTO monthly_briefs (ticker, run_date, brief, model) "
                    "VALUES (?,?,?,?)",
                    (p["ticker"], run_date, brief, model))
            summary["completed"] += 1
            consecutive = 0
            preview = brief[:90].replace("\n", " ")
            print(f"  {tag}: {preview}...")
        except Exception as e:                                  # noqa: BLE001
            summary["errors"] += 1
            consecutive += 1
            print(f"  {tag}: ERROR - {e}")
            if consecutive >= stop_after:
                summary["stopped_early"] = True
                print(f"\n  Stopping early after {consecutive} consecutive "
                      f"failures (likely the daily free-tier limit). Re-run "
                      f"later — completed picks are cached and cost nothing.")
                break

    summary.update(api_calls=client.api_calls, cache_hits=client.cache_hits,
                   tokens_used=client.tokens_used)
    return summary
