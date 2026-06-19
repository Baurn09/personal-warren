"""Data-quality gate.

For every company this runs a set of checks and records the result per check in
``data_quality`` plus an ``overall`` roll-up row:

* ``price_freshness``        -- latest price is recent enough
* ``fundamentals_complete``  -- all required metrics are present
* ``source_reconciliation``  -- multi-source metrics agree within tolerance

A stock with any ``fail`` should not be scored downstream; a ``warn`` is usable
but flagged. With a single free source, reconciliation mostly passes trivially;
the check is structured so a second source slots straight in.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.config import load_config, resolve
from src.db.schema import connect


def run_quality_gate() -> dict:
    """Run all data-quality checks. Returns ``{pass, warn, fail}`` per stock."""
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    limits = cfg["data_quality"]["staleness_limit_days"]
    tolerance = cfg["data_quality"]["reconciliation_tolerance_pct"]
    required = cfg["data_quality"]["required_fundamentals"]
    expected = cfg["data_quality"]["expected_fundamentals"]
    run_at = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date()

    summary = {"pass": 0, "warn": 0, "fail": 0}
    with connect(db_path) as conn:
        tickers = [r["ticker"] for r in conn.execute("SELECT ticker FROM companies")]

        for ticker in tickers:
            checks: list[tuple[str, str, str]] = []  # (check_name, status, detail)

            # --- price freshness ---
            row = conn.execute(
                "SELECT MAX(date) AS d FROM prices WHERE ticker=?", (ticker,)
            ).fetchone()
            if not row or not row["d"]:
                checks.append(("price_freshness", "fail", "no price data"))
            else:
                age = (today - datetime.strptime(row["d"], "%Y-%m-%d").date()).days
                status = "warn" if age > limits["prices"] else "pass"
                checks.append(("price_freshness", status, f"latest price {age}d old"))

            # --- fundamentals completeness (two tiers) ---
            # A missing REQUIRED metric means the stock is unusable -> fail.
            # A missing EXPECTED metric is flagged -> warn; the stock stays in
            # play and Phase 2 scoring decides if the gap matters for its sector.
            fund = {
                r["metric"] for r in conn.execute(
                    "SELECT DISTINCT metric FROM fundamentals WHERE ticker=?",
                    (ticker,))
            }
            if not fund:
                checks.append(("fundamentals_complete", "fail",
                               "no fundamentals data"))
            else:
                missing_req = [m for m in required if m not in fund]
                missing_exp = [m for m in expected if m not in fund]
                if missing_req:
                    checks.append(("fundamentals_complete", "fail",
                                   "missing required: " + ", ".join(missing_req)))
                elif missing_exp:
                    checks.append(("fundamentals_complete", "warn",
                                   "missing expected: " + ", ".join(missing_exp)))
                else:
                    checks.append(("fundamentals_complete", "pass",
                                   "all present"))

            # --- cross-source reconciliation ---
            recon = conn.execute(
                """SELECT metric, COUNT(DISTINCT source) AS n_src,
                          MIN(value) AS lo, MAX(value) AS hi
                   FROM fundamentals WHERE ticker=?
                   GROUP BY metric, period""", (ticker,)
            ).fetchall()
            disagreements = []
            for m in recon:
                if m["n_src"] > 1 and m["lo"] not in (None, 0):
                    spread = abs(m["hi"] - m["lo"]) / abs(m["lo"]) * 100
                    if spread > tolerance:
                        disagreements.append(f"{m['metric']} {spread:.0f}%")
            if disagreements:
                checks.append(("source_reconciliation", "warn",
                               "disagree: " + "; ".join(disagreements)))
            else:
                checks.append(("source_reconciliation", "pass",
                               "single source or within tolerance"))

            # --- roll-up ---
            statuses = {s for _, s, _ in checks}
            overall = ("fail" if "fail" in statuses
                       else "warn" if "warn" in statuses else "pass")
            checks.append(("overall", overall, f"{len(checks)} checks"))
            summary[overall] += 1

            conn.executemany(
                """INSERT INTO data_quality
                   (ticker, check_name, status, detail, run_at)
                   VALUES (?,?,?,?,?)""",
                [(ticker, name, status, detail, run_at)
                 for name, status, detail in checks],
            )
    return summary
