"""Personal Warren — Quality scoring + Monthly Quality-Momentum pipeline.

Drives the full deterministic pipeline (no API calls):

  1. Quality scoring + red flags (`src.screen.funnel`) — still produces the
     6-category scores; under the 1-month pivot these serve as the **quality
     filter**, not the primary rank.
  2. Momentum signals (`src.momentum.signals`) — 12-1 momentum, RSI, ATR,
     trend filter, etc., computed from the prices table.
  3. Monthly funnel (`src.screen.monthly_funnel`) — quality filter + momentum
     rank → ~15-stock monthly shortlist.
  4. Monthly advisor (`src.advisor.monthly`) — per-pick entry, stop-loss,
     target and position-size hint.

Run the data pipeline (run_pipeline.py) first. Usage:
    python run_scoring.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.advisor.monthly import run_monthly_advisor      # noqa: E402
from src.config import load_config, resolve              # noqa: E402
from src.db.schema import init_db                        # noqa: E402
from src.momentum.signals import compute_and_store       # noqa: E402
from src.screen.funnel import run_funnel                 # noqa: E402
from src.screen.monthly_funnel import run_monthly_funnel  # noqa: E402


def main() -> None:
    db_path = resolve(load_config()["paths"]["database"])
    if not Path(db_path).exists():
        print("No database found. Run the data pipeline first:")
        print("    python run_pipeline.py")
        return

    init_db(db_path)   # ensure all tables exist

    print("[1/4] Quality scoring + red flags (becomes the quality filter) ...")
    s = run_funnel()
    print(f"      universe        : {s['universe']}")
    print(f"      disqualified    : {s['disqualified']}  (loss-makers, "
          f"negative equity, data-quality fails)")
    print(f"      scored          : {s['scored']}")

    print("[2/4] Momentum signals (12-1, RSI, ATR, trend, vol) ...")
    m = compute_and_store()
    print(f"      computed        : {m['computed']}")
    print(f"      skipped         : {m['skipped']}  (insufficient history)")

    print("[3/4] Monthly funnel (quality filter + momentum rank) ...")
    f = run_monthly_funnel()
    if "error" in f:
        print(f"      ERROR: {f['error']}")
        return
    qt = f.get("quality_threshold")
    print(f"      universe        : {f['universe']}")
    print(f"      quality passed  : {f['quality_passed']}"
          f"  (threshold {qt:.1f}/100)" if qt is not None else "")
    print(f"      eligible        : {f['eligible']}  (passed quality + trend + RSI)")
    print(f"      shortlist       : {f['shortlist']}")

    print("[4/4] Monthly advisor (entry / stop / target / size) ...")
    a = run_monthly_advisor()
    if "error" in a:
        print(f"      ERROR: {a['error']}")
        return
    print(f"      advised         : {a['advised']}")
    print(f"      total allocated : {a.get('total_allocated_pct', 0.0)}%")

    print("\nDone. View results:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
