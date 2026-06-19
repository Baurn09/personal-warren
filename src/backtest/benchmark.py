"""Nifty 50 benchmark loader for the rolling 1-month backtest.

yfinance ^NSEI returns *price-only* history (no dividends). Total Return Index
(TRI) is approximated by adding a daily dividend-yield drip — ~1.3% annual,
the historical Nifty 50 average. The engine applies the *same* drip to the
portfolio side so the comparison remains apples-to-apples; only the absolute
returns shift. This is documented in the backtest output. A proper TRI source
can replace this without changing the engine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from src.config import resolve

NIFTY_TICKER = "^NSEI"
ASSUMED_ANNUAL_DIV_YIELD = 0.013       # ~1.3% — historical Nifty 50 average
TRADING_DAYS_PER_YEAR = 252
CACHE_PATH = resolve("data/raw/nifty50_prices.csv")


def load_nifty_prices(start: Optional[str] = None,
                      end: Optional[str] = None,
                      use_cache: bool = True) -> pd.DataFrame:
    """Load ^NSEI daily prices. Cached to ``data/raw/nifty50_prices.csv``.

    Returns columns: ``date, open, high, low, close, volume``.
    """
    cache = Path(CACHE_PATH)
    if use_cache and cache.exists():
        df = pd.read_csv(cache, parse_dates=["date"])
    else:
        hist = yf.Ticker(NIFTY_TICKER).history(period="max", auto_adjust=False)
        if hist is None or hist.empty:
            raise RuntimeError("yfinance returned no data for ^NSEI")
        df = (hist.reset_index()
              .rename(columns={"Date": "date", "Open": "open", "High": "high",
                               "Low": "low", "Close": "close",
                               "Volume": "volume"})
              [["date", "open", "high", "low", "close", "volume"]])
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if start:
        df = df[df["date"] >= pd.to_datetime(start)]
    if end:
        df = df[df["date"] <= pd.to_datetime(end)]
    return df.reset_index(drop=True)


def period_return(prices: pd.DataFrame, start_date: pd.Timestamp,
                  end_date: pd.Timestamp,
                  annual_yield: float = ASSUMED_ANNUAL_DIV_YIELD) -> float:
    """TRI return between ``start_date`` and ``end_date`` (inclusive).

    Price return is exact; dividend yield is added as a daily linear drip
    proportional to the number of trading days actually traversed.
    """
    window = prices[(prices["date"] >= start_date)
                    & (prices["date"] <= end_date)]
    if len(window) < 2:
        return 0.0
    start_close = float(window["close"].iloc[0])
    end_close = float(window["close"].iloc[-1])
    if start_close <= 0:
        return 0.0
    price_ret = end_close / start_close - 1.0
    trading_days = len(window) - 1
    div_drip = annual_yield * (trading_days / TRADING_DAYS_PER_YEAR)
    return float(price_ret + div_drip)


def trading_days_between(prices: pd.DataFrame, start_date: pd.Timestamp,
                         end_date: pd.Timestamp) -> int:
    """Number of trading days between two timestamps in the Nifty calendar."""
    window = prices[(prices["date"] >= start_date)
                    & (prices["date"] <= end_date)]
    return max(0, len(window) - 1)
