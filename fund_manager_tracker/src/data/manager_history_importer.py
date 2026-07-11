from __future__ import annotations

import pandas as pd

from src.data.canonical_manager import REQUIRED_SEED_COLUMNS, import_seed_dataframe
from src.detection.change_detector import ManagerChangeDetector


REQUIRED_COLUMNS = {"scheme_code", "scheme_name", "amc_name", "manager_name", "start_date", "source"}


def normalize_import(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [c.strip().lower().replace(" ", "_") for c in normalized.columns]
    missing = REQUIRED_COLUMNS - set(normalized.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    if "confidence_score" not in normalized.columns:
        normalized["confidence_score"] = 0.9
    if "end_date" not in normalized.columns:
        normalized["end_date"] = None
    if "is_lead_manager" not in normalized.columns:
        normalized["is_lead_manager"] = 0
    if "raw_evidence" not in normalized.columns:
        normalized["raw_evidence"] = "csv_import"
    return normalized


def import_manager_history(df: pd.DataFrame, run_detection: bool = True) -> dict:
    normalized = normalize_import(df)
    if REQUIRED_SEED_COLUMNS.issubset(set(normalized.columns)):
        seed_result = import_seed_dataframe(normalized, verified=True)
    else:
        seed_result = {"seed_rows": 0, "verified": False}
    detector = ManagerChangeDetector()
    rows = detector.persist_history(normalized)
    events = detector.refresh_change_events() if run_detection else 0
    return {"history_rows": rows, "change_events": events, **seed_result}
