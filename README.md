# Personal Warren

A personal AI investing **research agent** for the Indian market (Nifty 500). It
is a research assistant — not a trading bot, and not financial advice.

The system runs a **1-month Quality + Momentum** strategy: only own quality
businesses (a Buffett-style filter), rank them by momentum, hold ~1 month, then
rotate. Alongside it, a standalone **Company Analyzer** can deep-dive any one
company you name. Every number is computed deterministically in Python; the AI
only explains the results — it never predicts prices or invents figures.

- Master spec: [AGENTS.md](AGENTS.md)
- Build roadmap: [ROADMAP.md](ROADMAP.md)

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

AI features (the Monthly Brief and the Company Analyzer's narrative) use
OpenRouter. Copy `.env.example` to `.env` and add your key from
https://openrouter.ai/keys. Everything else — including all earnings estimates —
runs without an API key.

## The monthly pipeline

Run these in order for a monthly refresh:

```powershell
python run_pipeline.py        # ingest universe, fundamentals, prices, news + quality gate
python run_scoring.py         # quality filter -> momentum rank -> shortlist -> advisor
python run_briefs.py          # one ~100-word AI brief per shortlist pick (cached)
python run_review.py          # grade past picks vs realised prices + Nifty
python run_backtest.py        # rolling 1-month backtest vs Nifty 50 TRI
```

Useful `run_pipeline.py` flags: `--limit N`, `--skip-prices`, `--skip-news`,
`--universe-file path\to\ind_nifty500list.csv` (if NSE blocks the download, get
the CSV from nseindia.com → Indices → NIFTY 500).

## Company Analyzer — analyse any one company by name

A standalone deep-dive, independent of the funnel. Give it a company **name**;
it resolves the listed symbol, fetches that company's data on demand plus a few
sector peers, and estimates the expected return a small investor can earn over
**1 month / 6 months / 12 months / 5 years** as **Bear / Base / Bull** scenario
bands — for both swing trading and buy-and-hold (no day-trading) — with a
1-month swing-vs-hold comparison, a quality/MOAT read vs peers, recent news, and
an optional AI narrative.

```powershell
python run_company.py "Reliance"            # resolve by name (asks if ambiguous)
python run_company.py "Tata Motors" --yes   # auto-pick the best name match
python run_company.py INFY --symbol         # treat the input as a ticker
python run_company.py "Infosys" --no-ai     # estimates only, no API key needed
python run_company.py "ITC" --deep          # Bull/Bear/Value/Growth AI debate
```

Estimates are **probabilistic scenarios** from the company's own historical
behaviour plus its fundamentals — not predictions. The 5-year view is flagged
lower-confidence (it leans on a current fundamentals snapshot).

## Dashboard

```powershell
streamlit run dashboard/app.py
```

Tabs: Data Pipeline · **Company Analyzer** (search a name and view the
analysis) · Monthly Picks · Backtest · Learning Log · Scoring & Ranking ·
Advisor (legacy) · AI Theses (legacy).

## Tests

```powershell
python -m pytest tests/        # 80 tests; each also runs standalone: python tests/test_*.py
```

## Project layout

```
config/        settings.yaml (config) + sectors.yaml (sector templates)
data/          warren.db (SQLite) + raw/ (immutable source snapshots)
src/ingest/    universe / prices / fundamentals / news / reconcile / snapshots
src/scoring/   engine.py (quality filter) + sector_models.py + flags.py
src/momentum/  signals.py (12-1, RSI, ATR, volatility, trend) + earnings_momentum
src/screen/    monthly_funnel.py (quality floor + momentum rank -> shortlist)
src/advisor/   monthly.py (entry / stop / target / position size)
src/agents/    monthly_brief.py + llm.py (OpenRouter, cached); legacy debate
src/backtest/  engine.py + benchmark.py (Nifty 50 TRI)
src/learning/  loop.py (realised outcomes of past picks)
src/company/   Company Analyzer: resolve / profile / peers / quality / estimate /
               swing / news / dossier / analyst / report
dashboard/     app.py (Streamlit)
run_*.py       entry points (pipeline, scoring, briefs, review, backtest,
               scheduler, company)
```

Phases and design rationale are in [ROADMAP.md](ROADMAP.md); the full
specification, including the Company Analyzer methodology, is in
[AGENTS.md](AGENTS.md).
