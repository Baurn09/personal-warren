"""Personal Warren — monthly scheduler.

Runs the in-process Quality-Momentum pipeline on the cron schedule defined
in ``config/settings.yaml`` (default: 1st of every month at 06:30 IST).

Usage:
    python run_scheduler.py                 # start scheduler (blocks)
    python run_scheduler.py --run-now       # start + run the job once now
    python run_scheduler.py --once          # run the job once and exit
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config, resolve              # noqa: E402
from src.db.schema import init_db                        # noqa: E402
from src.scheduler.jobs import (                         # noqa: E402
    run_monthly_pipeline, start_scheduler,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the Personal Warren monthly pipeline on a schedule.")
    ap.add_argument("--once", action="store_true",
                    help="Run the pipeline once and exit (no scheduler).")
    ap.add_argument("--run-now", action="store_true",
                    help="Start the scheduler AND fire the job once on startup.")
    args = ap.parse_args()

    db_path = resolve(load_config()["paths"]["database"])
    if not Path(db_path).exists():
        print("No database found. Run:  python run_pipeline.py")
        sys.exit(1)
    init_db(db_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s")

    if args.once:
        result = run_monthly_pipeline()
        print()
        ok = "OK" if result["ok"] else "FAILED"
        print(f"Monthly pipeline {ok}.  "
              f"Started {result['started_at']}, "
              f"finished {result['finished_at']}.")
        for s in result["steps"]:
            mark = "v" if s.get("ok") else "x"
            extra = (f"  ({s.get('elapsed_seconds')}s)"
                     if s.get("elapsed_seconds") else "")
            err = f"  -> {s.get('error')}" if not s.get("ok") else ""
            print(f"  [{mark}] {s['step']}{extra}{err}")
        sys.exit(0 if result["ok"] else 1)

    start_scheduler(run_now=args.run_now)


if __name__ == "__main__":
    main()
