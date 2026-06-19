"""Personal Warren — review past monthly picks against realised prices.

Walks every row in ``monthly_advice`` and persists the outcome
(stop_hit / target_hit / held / not_yet) plus the realised return vs
Nifty 50 TRI over the same window.

Usage:
    python run_review.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config, resolve     # noqa: E402
from src.db.schema import init_db               # noqa: E402
from src.learning.loop import review_outcomes, summarise   # noqa: E402


def main() -> None:
    db_path = resolve(load_config()["paths"]["database"])
    if not Path(db_path).exists():
        print("No database found. Run:  python run_pipeline.py  and  "
              "python run_scoring.py")
        return
    init_db(db_path)

    print("Reviewing past monthly picks against realised prices ...")
    r = review_outcomes()
    if r.get("error"):
        print(f"ERROR: {r['error']}")
        return
    print(f"  reviewed     : {r['reviewed']}")
    print(f"    stop hit   : {r['stop_hit']}")
    print(f"    target hit : {r['target_hit']}")
    print(f"    held to MTM: {r['held']}")
    print(f"    not yet    : {r['not_yet']}  (holding window not elapsed)")

    s = summarise()
    if s.get("closed", 0) > 0:
        print()
        print("Closed picks summary")
        print(f"  total closed     : {s['closed']}")
        print(f"  winners          : {s['winners']}  "
              f"({s['winners'] / s['closed'] * 100:.0f}%)")
        if s.get("beat_nifty") is not None:
            print(f"  beat Nifty       : {s['beat_nifty']}  "
                  f"({s['beat_nifty'] / s['closed'] * 100:.0f}%)")
        print(f"  avg return /pick : {s['avg_return_pct']:+.2f}%")
        if s.get("avg_excess_pct") is not None:
            print(f"  avg excess /pick : {s['avg_excess_pct']:+.2f}%")
    print()
    print("View results:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
