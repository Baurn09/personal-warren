"""Phase 3 orchestrator — runs the agent debate over the funnel shortlist.

For each shortlisted stock: build the dossier -> run the 4 specialists -> run
the Judge -> persist the thesis. Per-stock errors are isolated so one bad call
does not abort the whole run, and cached responses make re-runs free.
"""
from __future__ import annotations

from datetime import date

from src.agents.dossier import build_dossier
from src.agents.judge import run_judge
from src.agents.llm import LLMClient
from src.agents.specialists import run_specialist
from src.config import load_config, resolve
from src.db.schema import SCHEMA, connect

_ROLES = ("bull", "bear", "value", "growth")


def _ensure_theses_schema(conn) -> None:
    """Rebuild `theses` if it still has the old placeholder shape (it is empty)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(theses)")}
    if cols and "thesis" not in cols:
        conn.execute("DROP TABLE theses")
    conn.execute(SCHEMA["theses"])


def _shortlist(conn) -> list[dict]:
    """Latest-run shortlisted stocks, each with the data its dossier needs."""
    run = conn.execute("SELECT MAX(run_date) FROM rankings").fetchone()[0]
    if not run:
        return []
    n_scored = conn.execute(
        "SELECT COUNT(*) FROM rankings WHERE run_date=?", (run,)).fetchone()[0]
    rows = conn.execute(
        "SELECT r.ticker, r.total_score, r.rank, r.template, c.name, c.sector "
        "FROM rankings r JOIN companies c ON c.ticker = r.ticker "
        "WHERE r.run_date=? AND r.in_shortlist=1 ORDER BY r.rank", (run,)
    ).fetchall()

    out = []
    for ticker, total, rank, template, name, sector in rows:
        metrics = {m[0]: m[1] for m in conn.execute(
            "SELECT metric, value FROM fundamentals "
            "WHERE ticker=? AND period='TTM'", (ticker,))}
        scores = {s[0]: s[1] for s in conn.execute(
            "SELECT category, score FROM scores WHERE ticker=? AND run_date=?",
            (ticker, run))}
        pflags = [f[0] for f in conn.execute(
            "SELECT flag FROM flags WHERE ticker=? AND run_date=? "
            "AND severity='penalty'", (ticker, run))]
        out.append({"ticker": ticker, "name": name, "sector": sector,
                    "template": template, "total_score": total, "rank": rank,
                    "n_scored": n_scored, "metrics": metrics, "scores": scores,
                    "penalty_flags": pflags})
    return out


def run_agents(limit: int | None = None, use_cache: bool = True) -> dict:
    """Run the full agent debate over the shortlist. Returns a summary dict."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    models = cfg["ai"]["models"]
    run_date = date.today().isoformat()

    client = LLMClient(db_path, use_cache=use_cache)   # raises if no API key

    with connect(db_path) as conn:
        _ensure_theses_schema(conn)
        stocks = _shortlist(conn)
    if limit:
        stocks = stocks[:limit]

    stop_after = cfg["ai"].get("stop_after_consecutive_errors", 3)
    summary = {"shortlist": len(stocks), "completed": 0, "errors": 0,
               "stopped_early": False}
    consecutive = 0
    for i, d in enumerate(stocks, 1):
        tag = f"[{i}/{len(stocks)}] {d['ticker']}"
        try:
            dossier = build_dossier(d)
            specialists = {role: run_specialist(client, role, dossier,
                                                models["specialist"])
                           for role in _ROLES}
            thesis, verdict, confidence = run_judge(
                client, dossier, specialists, models["judge"])

            with connect(db_path) as conn:
                conn.execute("DELETE FROM theses WHERE ticker=? AND run_date=?",
                             (d["ticker"], run_date))
                conn.execute(
                    """INSERT INTO theses
                       (ticker, run_date, bull, bear, value_view, growth_view,
                        thesis, verdict, confidence, total_score)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (d["ticker"], run_date, specialists["bull"],
                     specialists["bear"], specialists["value"],
                     specialists["growth"], thesis, verdict, confidence,
                     d["total_score"]))
            summary["completed"] += 1
            consecutive = 0
            print(f"  {tag}: {verdict} (confidence {confidence})")
        except Exception as e:   # noqa: BLE001 — isolate per-stock failures
            summary["errors"] += 1
            consecutive += 1
            print(f"  {tag}: ERROR - {e}")
            if consecutive >= stop_after:
                summary["stopped_early"] = True
                print(f"\n  Stopping early after {consecutive} consecutive "
                      f"failures (likely the daily free-tier limit). Re-run "
                      f"later — completed stocks are cached and cost nothing.")
                break

    summary.update(api_calls=client.api_calls, cache_hits=client.cache_hits,
                   tokens_used=client.tokens_used)
    return summary
