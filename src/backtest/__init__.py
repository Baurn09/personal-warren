"""Rolling 1-month backtest of the Quality-Momentum strategy.

`engine.run_backtest()` is the entry point; `benchmark` loads Nifty 50 prices
and approximates TRI returns. See `engine.py` for the look-ahead-bias caveat
on the (current-snapshot-only) quality filter.
"""
