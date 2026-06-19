"""Personal Warren — rolling 1-month backtest.

Validates the Quality-Momentum strategy against Nifty 50 TRI over the
available price history. Persists results to the ``backtest_runs`` and
``backtest_monthly`` tables; the dashboard's Backtest tab renders them.

Run the data pipeline and `run_scoring.py` first. Usage:
    python run_backtest.py
    python run_backtest.py --start 2022-01-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.backtest.engine import run_backtest      # noqa: E402
from src.config import load_config, resolve       # noqa: E402
from src.db.schema import init_db                 # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Rolling 1-month backtest "
                                             "vs Nifty 50.")
    ap.add_argument("--start", default=None,
                    help="ISO start date (default: earliest + 1y warmup)")
    ap.add_argument("--end", default=None,
                    help="ISO end date (default: latest available)")
    args = ap.parse_args()

    db_path = resolve(load_config()["paths"]["database"])
    if not Path(db_path).exists():
        print("No database found. Run the data pipeline first:")
        print("    python run_pipeline.py")
        return
    init_db(db_path)

    print("Running rolling 1-month backtest (Quality-Momentum vs Nifty 50 TRI)")
    print("CAVEAT: quality filter uses the *current* fundamentals snapshot —")
    print("        hit rate is an upper bound until point-in-time fundamentals")
    print("        are wired in.")
    print()

    result = run_backtest(start=args.start, end=args.end)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    print(f"Window           : {result['start_date']}  ->  {result['end_date']}")
    print(f"Months           : {result['months_total']}")
    print(f"Beat Nifty       : {result['months_beating_nifty']}  "
          f"({result['hit_rate_pct']}%)")
    print(f"Avg excess /mo   : {result['avg_excess_pct']:+.3f}%")
    print(f"Total return     : {result['total_return_pct']:+.2f}%   "
          f"(Nifty TRI: {result['nifty_return_pct']:+.2f}%)")
    print(f"Max drawdown     : {result['max_drawdown_pct']:.2f}%")
    print()
    print(f"Saved as backtest_runs.run_id = {result['run_id']}.")
    print("View results:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
