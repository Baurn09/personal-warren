"""Monthly funnel — quality filter + sector-relative momentum rank.

Pipeline:
  1. Read latest quality scores (from the existing `funnel.run_funnel()`).
  2. Read latest momentum signals (from `momentum.compute_and_store()`).
  3. Quality filter — combined Quality+Moat+FS score must be above a
     universe-relative floor (default top 50%).
  4. Hard filters — trend (close > SMA50 > SMA200) and RSI not extreme.
  5. Sector-relative percentile rank on the configured momentum signals; weighted
     blend into a 0–100 ``momentum_score``.
  6. Top-N → monthly shortlist.

Writes to ``monthly_rankings``.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.config import load_config, resolve
from src.db.schema import connect


def _quality_score(scores: pd.DataFrame, categories: list[str]) -> pd.Series:
    """Average percentile across the chosen quality categories (one row/ticker)."""
    wide = scores.pivot(index="ticker", columns="category", values="score")
    for c in categories:
        if c not in wide.columns:
            wide[c] = float("nan")
    return wide[categories].mean(axis=1).rename("quality_score")


def run_monthly_funnel(run_date: str | None = None) -> dict:
    """Run the monthly funnel. Returns a summary dict.

    Requires both ``scores`` (from the quality scoring run) and
    ``momentum_signals`` (from the momentum step) to be populated.
    """
    cfg = load_config()
    mcfg = cfg["momentum"]
    db_path = resolve(cfg["paths"]["database"])
    shortlist_size = cfg["funnel"]["shortlist_size"]
    rsi_cap = mcfg["filters"]["rsi_ceiling"]
    qcats = mcfg["quality_floor"]["categories"]
    floor_pct = mcfg["quality_floor"]["score_percentile"]
    rank_signals = list(mcfg["rank_weights"].keys())
    weights = mcfg["rank_weights"]
    if run_date is None:
        run_date = date.today().isoformat()

    with connect(db_path) as conn:
        srun = conn.execute("SELECT MAX(run_date) FROM scores").fetchone()[0]
        mrun = conn.execute(
            "SELECT MAX(run_date) FROM momentum_signals").fetchone()[0]
        if not srun:
            return {"error": "no quality scoring run yet — run run_scoring.py"}
        if not mrun:
            return {"error": "no momentum signals — run momentum.compute_and_store first"}

        scores = pd.read_sql_query(
            "SELECT * FROM scores WHERE run_date=?", conn, params=(srun,))
        sig = pd.read_sql_query(
            "SELECT * FROM momentum_signals WHERE run_date=?",
            conn, params=(mrun,))
        companies = pd.read_sql_query(
            "SELECT ticker, sector, industry FROM companies", conn)
        rrun = conn.execute("SELECT MAX(run_date) FROM rankings").fetchone()[0]
        if rrun:
            rk = pd.read_sql_query(
                "SELECT ticker, template FROM rankings WHERE run_date=?",
                conn, params=(rrun,))
            disq = {r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM flags WHERE run_date=? "
                "AND severity='disqualify'", (rrun,))}
        else:
            rk = pd.DataFrame({"ticker": [], "template": []})
            disq = set()

    # ─── assemble per-ticker frame ───
    qs = _quality_score(scores, qcats)
    df = sig.set_index("ticker").join(qs, how="outer")
    df = df.join(companies.set_index("ticker")[["sector", "industry"]],
                 how="left")
    df = df.join(rk.set_index("ticker"), how="left")
    df["disqualified"] = df.index.isin(disq).astype(int)

    # ─── quality filter ───
    if df["quality_score"].notna().any():
        threshold = float(df["quality_score"].quantile(1 - floor_pct))
    else:
        threshold = float("nan")
    df["quality_passed"] = (df["quality_score"] >= threshold).fillna(False).astype(int)

    # ─── hard filters ───
    df["trend_passed"] = (df["trend_filter"] == 1).fillna(False).astype(int)
    df["rsi_passed"] = (
        (df["rsi_14"] <= rsi_cap) | df["rsi_14"].isna()
    ).fillna(False).astype(int)

    df["eligible"] = (
        (df["disqualified"] == 0) & (df["quality_passed"] == 1)
        & (df["trend_passed"] == 1) & (df["rsi_passed"] == 1)
    ).astype(int)

    # ─── sector-relative momentum percentiles (within eligible set) ───
    eligible = df[df["eligible"] == 1].copy()
    if not eligible.empty:
        eligible["peer_group"] = eligible["sector"].fillna("OTHER")
        sector_counts = eligible["peer_group"].value_counts()
        small = set(sector_counts[sector_counts < 8].index)
        eligible.loc[eligible["peer_group"].isin(small), "peer_group"] = "ALL"

        for s in rank_signals:
            if s not in eligible.columns:
                eligible[f"{s}_pct"] = 50.0
            else:
                eligible[f"{s}_pct"] = (
                    eligible.groupby("peer_group")[s].rank(pct=True) * 100.0
                ).fillna(50.0)

        total_w = float(sum(weights.values()))
        weighted = pd.Series(0.0, index=eligible.index)
        for s in rank_signals:
            weighted = weighted + eligible[f"{s}_pct"] * float(weights[s])
        eligible["momentum_score"] = (weighted / total_w).round(2)

        eligible = eligible.sort_values("momentum_score", ascending=False,
                                        na_position="last")
        eligible["rank"] = range(1, len(eligible) + 1)
        eligible["in_shortlist"] = (
            eligible["rank"] <= shortlist_size).astype(int)
    else:
        eligible["momentum_score"] = pd.Series(dtype=float)
        eligible["rank"] = pd.Series(dtype=int)
        eligible["in_shortlist"] = pd.Series(dtype=int)

    df = df.join(eligible[["momentum_score", "rank", "in_shortlist"]],
                 how="left")
    df["in_shortlist"] = df["in_shortlist"].fillna(0).astype(int)
    df["combined_score"] = df["momentum_score"]   # momentum drives the rank

    # ─── persist ───
    rows = []
    for ticker, r in df.iterrows():
        rows.append((
            ticker, run_date,
            float(r["quality_score"]) if pd.notna(r["quality_score"]) else None,
            float(r["momentum_score"]) if pd.notna(r["momentum_score"]) else None,
            float(r["combined_score"]) if pd.notna(r["combined_score"]) else None,
            int(r["rank"]) if pd.notna(r["rank"]) else None,
            int(r["in_shortlist"]),
            int(r["quality_passed"]),
            int(r["trend_passed"]),
            r["template"] if isinstance(r.get("template"), str) else None,
        ))

    with connect(db_path) as conn:
        conn.execute("DELETE FROM monthly_rankings WHERE run_date=?",
                     (run_date,))
        conn.executemany(
            """INSERT INTO monthly_rankings
               (ticker, run_date, quality_score, momentum_score, combined_score,
                rank, in_shortlist, quality_passed, trend_passed, template)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)

    return {
        "universe": int(len(df)),
        "quality_passed": int(df["quality_passed"].sum()),
        "eligible": int(df["eligible"].sum()),
        "shortlist": int(df["in_shortlist"].sum()),
        "quality_threshold": threshold if threshold == threshold else None,
    }
