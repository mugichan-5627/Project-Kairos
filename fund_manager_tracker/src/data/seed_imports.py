from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.detection.change_detector import manager_key
from src.utils.db import get_connection


SEED_DIR = Path(__file__).resolve().parents[2] / "seed_data"
CURRENT_MANAGER_SEED = SEED_DIR / "current_manager_seed.csv"
SCHEME_LINEAGE_SEED = SEED_DIR / "scheme_lineage_seed.csv"


def import_current_manager_seed(path: Path = CURRENT_MANAGER_SEED) -> int:
    df = pd.read_csv(path)
    rows = 0
    with get_connection() as conn:
        for _, row in df.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO current_manager_snapshot
                (scheme_code, scheme_name, amc_name, manager_name, manager_key, role, rank, confirmed_date,
                 source, source_url, confidence_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["scheme_code"]),
                    row["scheme_name"],
                    row["amc_name"],
                    row["manager_name"],
                    manager_key(row["manager_name"], row["amc_name"]),
                    row.get("role", "manager"),
                    int(row.get("rank", 1)),
                    row.get("confirmed_date"),
                    row.get("source"),
                    row.get("source_url"),
                    float(row.get("confidence_score", 0.5)),
                ),
            )
            rows += 1
    return rows


def import_scheme_lineage_seed(path: Path = SCHEME_LINEAGE_SEED) -> int:
    df = pd.read_csv(path)
    rows = 0
    with get_connection() as conn:
        for _, row in df.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO scheme_lineage(old_scheme_code, new_scheme_code, event_date, event_type, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(row["old_scheme_code"]),
                    str(row["new_scheme_code"]),
                    row["event_date"],
                    row["event_type"],
                    f"{row.get('old_scheme_name')} -> {row.get('new_scheme_name')}; {row.get('notes')}; source={row.get('source_url')}",
                ),
            )
            rows += 1
    return rows
