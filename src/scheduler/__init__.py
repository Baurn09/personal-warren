"""Monthly scheduler — wraps the pipeline in an APScheduler job.

`jobs.run_monthly_pipeline()` is the in-process job that runs every step of
the monthly Quality-Momentum workflow; `jobs.start_scheduler()` keeps a
BackgroundScheduler alive against the cron schedule configured in
`config/settings.yaml`.
"""
