"""Build the deterministic 'dossier' handed to every agent.

Every number here is computed by Python and passed verbatim. The agents must
reason from these facts and never invent or recall figures of their own — this
is the AI / logic separation from AGENTS.md, enforced at the prompt boundary.
"""
from __future__ import annotations

_CRORE = {"revenue", "net_profit", "market_cap", "fcf", "total_debt",
          "operating_cashflow", "ebitda"}
_PERCENT = {"roe", "roa", "net_margin", "operating_margin", "gross_margin",
            "revenue_growth", "earnings_growth", "promoter_holding",
            "payout_ratio"}

CATEGORIES = ["quality", "moat", "financial_strength",
              "management", "valuation", "growth"]


def _fmt(metric: str, v) -> str:
    """Format a metric value for human/LLM reading."""
    if v is None or (isinstance(v, float) and v != v):   # None or NaN
        return "n/a"
    if metric in _CRORE:
        return f"Rs {v / 1e7:,.0f} cr"
    if metric in _PERCENT:
        return f"{v * 100:.1f}%"
    if metric == "debt_to_equity":            # yfinance reports a percentage
        return f"{v / 100:.2f}x"
    return f"{v:.2f}"


def build_dossier(d: dict) -> str:
    """Render a stock's deterministic facts as the shared agent dossier."""
    m = d["metrics"]
    s = d["scores"]

    def metric(k: str) -> str:
        return _fmt(k, m.get(k))

    def cat(k: str) -> float:
        return s.get(k) or 0.0

    flags = d.get("penalty_flags") or []
    flags_line = ", ".join(flags) if flags else "none"

    return f"""COMPANY: {d['name']}  [{d['ticker']}]
Sector: {d.get('sector') or 'n/a'}   |   Scoring template: {d['template']}

DETERMINISTIC SCORE  (0-100 sector-relative percentiles; computed by Python, not AI)
  Quality {cat('quality'):.0f}  Moat {cat('moat'):.0f}  Financial-Strength {cat('financial_strength'):.0f}  \
Management {cat('management'):.0f}  Valuation {cat('valuation'):.0f}  Growth {cat('growth'):.0f}
  TOTAL {d['total_score']:.1f}   (rank {d['rank']} of {d['n_scored']})

KEY METRICS (trailing twelve months)
  Profitability : ROE {metric('roe')}  ROA {metric('roa')}  Net margin {metric('net_margin')}  \
Operating margin {metric('operating_margin')}  Gross margin {metric('gross_margin')}
  Valuation     : PE {metric('pe')}  P/B {metric('price_to_book')}  EV/EBITDA {metric('ev_ebitda')}
  Balance sheet : Debt/Equity {metric('debt_to_equity')}  Total debt {metric('total_debt')}  \
FCF {metric('fcf')}  Cash conversion {metric('cash_conversion')}
  Scale         : Revenue {metric('revenue')}  Net profit {metric('net_profit')}  \
Market cap {metric('market_cap')}
  Growth        : Revenue growth {metric('revenue_growth')}  Earnings growth {metric('earnings_growth')}
  Ownership     : Promoter/insider holding {metric('promoter_holding')}

RED FLAGS (deterministic checks): {flags_line}"""
