# This module previously hosted the standalone daemon entrypoint (run_daemon).
# Scheduling now lives in `scheduler_ctl.SchedulerCtl`, driven by the FastAPI
# app lifespan in `web.app`. Kept as an empty placeholder for backward import
# compatibility; safe to delete once nothing imports it.
