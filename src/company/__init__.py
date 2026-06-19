"""Company Analyzer — name-driven single-company deep-dive.

A standalone tool (independent of the monthly funnel): take a company *name*,
resolve it to a listed symbol, fetch that one company's data on demand plus a
few live sector peers, and estimate the expected return a small investor can
earn over 1m / 6m / 12m / 5y as Bear/Base/Bull scenario bands — for both swing
trading and buy-and-hold. Python computes every number; the AI only narrates.
"""
