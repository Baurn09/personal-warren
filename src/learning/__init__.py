"""Learning log — review past monthly picks against realised prices.

`loop.review_outcomes()` walks every `monthly_advice` row, looks up the price
history that followed it, classifies the outcome (stop_hit / target_hit /
held / not_yet) and persists to `pick_outcomes`. Aggregates feed the
dashboard's Learning Log tab.
"""
