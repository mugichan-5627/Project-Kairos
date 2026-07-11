from __future__ import annotations

from src.utils.db import get_connection


def mark_source(source_name: str, status: str, rows_loaded: int = 0, error_message: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO source_status(source_name,last_attempt,last_success,status,rows_loaded,error_message)
            VALUES(?, CURRENT_TIMESTAMP, CASE WHEN ?='ok' THEN CURRENT_TIMESTAMP ELSE NULL END, ?, ?, ?)
            """,
            (source_name, status, status, rows_loaded, error_message),
        )
