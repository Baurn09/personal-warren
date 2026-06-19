# Personal Warren — Build Roadmap

A phased plan to build the agent specified in [AGENTS.md](AGENTS.md). Each
phase has explicit exit criteria. Universe is **Nifty 500** throughout.

> **Pivot note (current):** the project was originally framed for a 1–5 year
> Buffett strategy (quality scoring + AI debate + margin-of-safety advisor).
> It has been **pivoted to a 1-month Quality + Momentum strategy**. The
> existing quality scoring is repurposed as a **filter**; new momentum signals
> drive a monthly shortlist; the long-term advisor and the 5-agent debate are
> kept in tree but dormant. Phases 1–2 are unchanged; Phases 3–6 reflect the
> pivot.

## Guiding principles

- **Data before intelligence.** Nothing downstream is trustworthy until
  ingestion and the quality gate work.
- **Deterministic before AI.** Quality scoring, momentum signals and the
  monthly advisor are all pure Python. The AI Monthly Brief *explains* the
  deterministic output — it never sets numbers.
- **Funnel discipline.** Each stage cuts the candidate set:
  `Nifty 500 → quality gate → quality filter → momentum rank → monthly
  shortlist (~15) → portfolio`.
- **Everything auditable.** Raw source snapshots; every signal and score
  reproducible from stored data.

## Project skeleton

```
personal-warren/
├── AGENTS.md            # master spec
├── ROADMAP.md           # this file
├── README.md
├── requirements.txt
├── config/
│   ├── settings.yaml    # universe, weights, thresholds, momentum & advisor cfg
│   └── sectors.yaml     # sector classification + per-sector metric maps
├── data/
│   ├── raw/             # immutable source snapshots (audit)
│   └── warren.db        # SQLite
├── src/
│   ├── ingest/          # prices, fundamentals, news, reconcile
│   ├── db/              # schema.py
│   ├── scoring/         # engine.py (quality filter), sector_models.py, flags.py
│   ├── momentum/        # signals.py, earnings_momentum.py        (Phase 4)
│   ├── screen/          # funnel.py (quality filter + momentum rank)
│   ├── advisor/         # monthly.py  (active); valuation.py  (legacy, dormant)
│   ├── agents/          # monthly_brief.py  (active);
│   │                    # specialists.py + judge.py  (legacy, dormant)
│   ├── backtest/        # engine.py, benchmark.py                 (Phase 5)
│   └── learning/        # loop.py                                  (Phase 6)
├── dashboard/           # app.py (Streamlit)
└── scheduler/           # jobs.py (APScheduler)                   (Phase 6)
```

---

## Phase 1 — Data Pipeline  ✅

Goal: a reliable, auditable Nifty 500 dataset.

Done:
- SQLite schema (`companies`, `prices`, `fundamentals`, `snapshots`,
  `data_quality`, …).
- Nifty 500 constituents from NSE; prices via yfinance; fundamentals via
  yfinance `.info` + balance-sheet fallback + derived metrics.
- News ingestion via RSS.
- Cross-source reconciliation + data-quality gate.

**Exit criteria met:** all Nifty 500 stocks ingested, reconciled,
quality-flagged, every record reproducible from a stored raw snapshot.

---

## Phase 2 — Quality Scoring  ✅  (now the *filter*, not the rank)

Goal: a deterministic, sector-aware quality score for every gated stock.

Done:
- Sector classification + `General` / `Financials` templates.
- 6-category weighted scoring (Quality 20, Moat 20, FS 20, Mgmt 15, Val 15,
  Growth 10), sector-relative percentiles.
- Quality-of-earnings red flags with penalties / disqualifiers.
- Streamlit ranking + score-breakdown view.

**Role under the pivot:** the score is now a **quality filter** — stocks
below a minimum threshold (default: top 50% on combined Quality + Moat +
Financial Strength) are not eligible for momentum ranking. The 6-category
breakdown is preserved and shown for transparency, but is no longer the
primary selector.

---

## Phase 3 — AI Agents  💤  (legacy, dormant under the pivot)

The long-term 5-agent debate (Bull / Bear / Value / Growth + Judge) lives in
[src/agents/specialists.py](src/agents/specialists.py) and
[src/agents/judge.py](src/agents/judge.py) but is **not invoked** by the
1-month workflow.

Under the pivot, AI work is replaced by a single **Monthly Brief** (Phase 4
below) — a ~100-word per-stock summary running only on the monthly shortlist.

The Phase 3 deliverables that **are still used** are the OpenRouter client
with caching + token budget ([src/agents/llm.py](src/agents/llm.py)), the
`ai_cache` table, and the dashboard plumbing.

---

## Phase 4 — Momentum Layer  (the primary pivot work)

Goal: rank quality-eligible stocks by momentum and produce monthly action
calls.

Tasks:
- `src/momentum/signals.py` — 12-1 momentum, 6-month return, 52-week-high
  distance, volume trend, trend filter, RSI(14), ATR(14), realised volatility.
- `src/momentum/earnings_momentum.py` — earnings-growth direction and
  forward/trailing PE gap as soft tilts.
- Extend `src/screen/funnel.py` with the monthly pipeline:
  eligibility (gate-pass + not loss-making + quality floor) → momentum rank
  (sector-relative) → trend filter → top-N shortlist.
- `src/advisor/monthly.py` — per shortlist pick: Action (Take / Hold / Skip),
  entry price band, stop-loss (tighter of -7% or entry − 1.5 × ATR),
  1-month target, ATR-aware position-size hint, templated rationale.
- `src/agents/monthly_brief.py` — single-agent ~100-word brief per pick
  (catalyst + 30-day risk), reusing the existing `LLMClient` with caching.
- Schema additions: `momentum_signals`, `monthly_rankings`, `monthly_advice`.
- Dashboard: **Monthly Picks** tab — shortlist with action, entry, stop,
  target, position size, key momentum signals.
- Unit tests: momentum-signal math, monthly-advisor decision tables.

**Exit criteria:** the monthly pipeline produces a ~15-name shortlist with
action / entry / stop / target for each name plus a Monthly Brief; the
Streamlit dashboard renders the Monthly Picks tab.

---

## Phase 5 — Backtest

Goal: validate the 1-month strategy against Nifty 50 TRI.

Tasks:
- `src/backtest/benchmark.py` — Nifty 50 (and Nifty 500) TRI loader. yfinance
  `^NSEI` gives price return only; approximate TRI as price return + ~1.3%
  dividend yield until a proper source is wired in. Document the
  approximation.
- `src/backtest/engine.py` — point-in-time rolling 1-month backtest: for each
  historical month, snapshot the pipeline as it would have been then, build
  the monthly portfolio, hold 1 month, measure return. Track CAGR, monthly
  hit-rate, average monthly excess return, max drawdown.
- Schema: `backtest_runs`, `backtest_monthly`.
- Dashboard: **Backtest** tab — equity curve vs Nifty 50, monthly hit-rate,
  drawdown.
- Unit tests for backtest accounting.

**Honest caveat:** price-based momentum has a clean history; fundamentals
only have a current snapshot, so the initial backtest carries look-ahead bias
on the quality-filter layer. Point-in-time fundamentals are deferred.

**Exit criteria:** a working backtest over the available price window, with
hit rate > 50%, average monthly excess return positive, drawdown documented;
honest acknowledgment of the fundamentals look-ahead bias.

---

## Phase 6 — Live Monthly Loop & Learning

Goal: run unattended and learn from its own history.

Tasks:
- `scheduler/jobs.py` — APScheduler: data refresh (weekly), monthly pipeline
  on the 1st of each month, performance log.
- `src/learning/loop.py` — log expected (predicted) vs actual monthly
  outcomes; surface patterns (which momentum factor is working, sector tilts).
- Dashboard: monthly performance vs Nifty, attribution, learning-loop review.

**Exit criteria:** the full monthly cycle runs on a schedule; per-month
outcomes are logged and reviewable; the dashboard surfaces portfolio,
performance, and lessons.

---

## Explicitly deferred

- **Point-in-time fundamentals** for a fundamentals-aware backtest.
- **Earnings-date catalysts** (NSE corporate-action calendars).
- **FII/DII flow signals**, **sector rotation models** — outside free-data
  scope on day one; revisit if monthly performance is weak.
- **FAISS embeddings** — reintroduced once annual reports are ingested.
- **Cyclicals / Commodities sector template** — added when needed.
- **Live broker integration** — out of scope; the system remains a research
  assistant, not an order router.
