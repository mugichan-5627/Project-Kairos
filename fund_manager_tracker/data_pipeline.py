from __future__ import annotations

import argparse
import json
from datetime import datetime

from plyer import notification

from db_setup import initialize_database
from src.analytics.pipeline_runner import AttributionPipeline
from src.config import LAST_UPDATED_PATH, PIPELINE_LOG_PATH, VRO_LIMIT_PER_RUN, ensure_dirs
from src.data.amfi_loader import AMFILoader
from src.data.news_monitor import NewsMonitor
from src.data.nse_factors import NSEFactorLoader
from src.detection.change_detector import ManagerChangeDetector
from src.scoring.scorecard import ManagerScorecard
from src.utils.db import read_sql
from src.utils.logging import get_logger


logger = get_logger("pipeline", PIPELINE_LOG_PATH)


def write_last_updated(status: dict) -> None:
    LAST_UPDATED_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")


def run_pipeline(incremental: bool = True, max_nav_schemes: int | None = None) -> dict:
    ensure_dirs()
    initialize_database()
    status: dict = {"started_at": datetime.utcnow().isoformat(), "first_run_complete": False, "sources": {}}
    try:
        logger.info("Step 1: AMFI scheme master")
        amfi = AMFILoader()
        status["sources"]["amfi_scheme_master"] = amfi.refresh_scheme_master()
        schemes = read_sql("SELECT scheme_code FROM scheme_master ORDER BY scheme_code")
        codes = schemes["scheme_code"].astype(str).tolist()
        if max_nav_schemes:
            codes = codes[:max_nav_schemes]
        logger.info("Step 1b: AMFI NAV history for %s schemes", len(codes))
        status["sources"]["amfi_nav_history"] = amfi.refresh_nav_history(codes)
        logger.info("Step 2: factor data")
        status["sources"]["factor_data"] = NSEFactorLoader().refresh()
        logger.info("Step 3: ValueResearch current managers skipped unless fund_id map is available; limit=%s", VRO_LIMIT_PER_RUN)
        status["sources"]["valueresearch"] = "pending_fund_id_mapping"
        logger.info("Step 4: Google News RSS")
        status["sources"]["google_news_rss"] = NewsMonitor().refresh()
        logger.info("Step 5: SID pull is event-triggered; skipped with no mapped SID URLs")
        status["sources"]["sid_parser"] = "pending_sid_url_mapping"
        logger.info("Step 6: change detection")
        status["sources"]["change_events"] = ManagerChangeDetector().refresh_change_events()
        logger.info("Step 7: attribution")
        status["sources"]["attribution"] = AttributionPipeline().refresh_all_events()
        logger.info("Step 8: scorecards")
        status["sources"]["scorecards"] = ManagerScorecard().refresh_all()
        status["first_run_complete"] = True
        status["completed_at"] = datetime.utcnow().isoformat()
        write_last_updated(status)
        notification.notify(title="Project Kairos", message="Data pipeline completed", timeout=5)
        return status
    except Exception as exc:
        logger.exception("Pipeline failed")
        status["error"] = str(exc)
        status["completed_at"] = datetime.utcnow().isoformat()
        write_last_updated(status)
        notification.notify(title="Project Kairos", message=f"Pipeline failed: {exc}", timeout=8)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--max-nav-schemes", type=int, default=100)
    args = parser.parse_args()
    run_pipeline(incremental=args.incremental, max_nav_schemes=args.max_nav_schemes)
