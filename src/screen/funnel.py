"""Quant funnel — eligibility, scoring, red flags, ranking.

Pipeline: load TTM metrics -> drop ineligible stocks (data-quality fails +
disqualifying red flags) -> deterministic score -> subtract penalty flags ->
rank -> mark the top-N shortlist. Results are written to ``scores``, ``flags``
and ``rankings``. This is the stage that cuts the Nifty 500 down to the ~40
candidates the Phase 3 AI agents will deep-dive.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.config import load_config, resolve
from src.db.schema import connect
from src.scoring import engine, flags, sector_models

WEIGHTS_VERSION = "v1"


def _load(conn) -> pd.DataFrame:
    """Return a DataFrame indexed by ticker: company info + wide TTM metrics."""
    funda = pd.read_sql_query(
        "SELECT ticker, metric, value FROM fundamentals WHERE period='TTM'", conn)
    wide = funda.pivot_table(index="ticker", columns="metric",
                             values="value", aggfunc="mean")
    companies = pd.read_sql_query(
        "SELECT ticker, name, sector, industry FROM companies", conn
    ).set_index("ticker")
    df = companies.join(wide, how="left")
    df["template"] = [sector_models.classify(i, s)
                      for i, s in zip(df["industry"], df["sector"])]
    return df


def _gate_failed(conn) -> set[str]:
    """Tickers that failed the latest data-quality gate run."""
    row = conn.execute("SELECT MAX(run_at) FROM data_quality").fetchone()
    if not row or not row[0]:
        return set()
    return {r[0] for r in conn.execute(
        "SELECT ticker FROM data_quality WHERE run_at=? "
        "AND check_name='overall' AND status='fail'", (row[0],))}


def _write(conn, run_date, scored, weights, disqualified, penalty_rows) -> None:
    """Persist a scoring run to scores / rankings / flags (idempotent per date)."""
    for table in ("scores", "rankings", "flags"):
        conn.execute(f"DELETE FROM {table} WHERE run_date=?", (run_date,))

    for ticker, row in scored.iterrows():
        for cat in engine.CATEGORIES:
            conn.execute(
                """INSERT INTO scores
                   (ticker, run_date, category, score, weight, weights_version)
                   VALUES (?,?,?,?,?,?)""",
                (ticker, run_date, cat, float(row[cat]),
                 float(weights[cat]), WEIGHTS_VERSION))
        conn.execute(
            """INSERT INTO rankings
               (ticker, run_date, total_score, rank, in_shortlist, template)
               VALUES (?,?,?,?,?,?)""",
            (ticker, run_date, float(row["total_score"]), int(row["rank"]),
             int(row["in_shortlist"]), row["template"]))

    for ticker, reasons in disqualified.items():
        for reason in reasons:
            conn.execute(
                """INSERT INTO flags (ticker, run_date, flag, severity, detail)
                   VALUES (?,?,?,?,?)""",
                (ticker, run_date, reason, "disqualify",
                 "excluded from funnel"))
    for ticker, flag, points in penalty_rows:
        conn.execute(
            """INSERT INTO flags (ticker, run_date, flag, severity, detail)
               VALUES (?,?,?,?,?)""",
            (ticker, run_date, flag, "penalty", f"-{points} points"))


def run_funnel() -> dict:
    """Score the universe, rank it, build the shortlist. Returns a summary dict."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    shortlist_size = cfg["funnel"]["shortlist_size"]
    weights = cfg["scoring"]["weights"]
    run_date = date.today().isoformat()

    with connect(db_path) as conn:
        df = _load(conn)
        failed = _gate_failed(conn)

        # --- eligibility: drop gate fails and disqualifying red flags ---
        disqualified: dict[str, list[str]] = {}
        for ticker, row in df.iterrows():
            reasons = (["data_quality_fail"] if ticker in failed else [])
            reasons += flags.disqualifiers(row.to_dict())
            if reasons:
                disqualified[ticker] = reasons

        eligible = df.drop(index=list(disqualified))
        summary = {"universe": len(df), "disqualified": len(disqualified),
                   "scored": len(eligible), "shortlist": 0}
        if eligible.empty:
            return summary

        # --- deterministic score ---
        scored = engine.score(eligible)

        # --- red-flag penalties ---
        penalty_rows: list[tuple[str, str, int]] = []
        penalty_total = pd.Series(0.0, index=eligible.index)
        for ticker, row in eligible.iterrows():
            tpl = scored.loc[ticker, "template"]
            for flag, points in flags.penalties(row.to_dict(), tpl):
                penalty_total[ticker] += points
                penalty_rows.append((ticker, flag, points))
        scored["penalty"] = penalty_total
        scored["total_score"] = (
            (scored["raw_total"] - scored["penalty"]).clip(lower=0).round(2))

        # --- rank and shortlist ---
        scored = scored.sort_values("total_score", ascending=False)
        scored["rank"] = range(1, len(scored) + 1)
        scored["in_shortlist"] = (scored["rank"] <= shortlist_size).astype(int)
        summary["shortlist"] = int(scored["in_shortlist"].sum())

        _write(conn, run_date, scored, weights, disqualified, penalty_rows)
    return summary
