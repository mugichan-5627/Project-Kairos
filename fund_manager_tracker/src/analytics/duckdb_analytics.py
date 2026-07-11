from __future__ import annotations

import duckdb

from src.utils.db import read_sql


class DuckDBAnalytics:
    """Columnar sidecar for heavy analytical scans without replacing SQLite writes."""

    def __init__(self) -> None:
        self.conn = duckdb.connect(database=":memory:")

    def register_core_tables(self) -> None:
        self.conn.register("nav_history", read_sql("SELECT * FROM nav_history"))
        self.conn.register("factor_data", read_sql("SELECT * FROM factor_data"))
        self.conn.register("scheme_master", read_sql("SELECT * FROM scheme_master"))

    def factor_coverage_summary(self):
        self.register_core_tables()
        return self.conn.execute(
            """
            SELECT
                COUNT(*) AS factor_months,
                SUM(CASE WHEN factor_is_fallback=1 THEN 1 ELSE 0 END) AS fallback_factor_months,
                SUM(CASE WHEN rfr_is_fallback=1 THEN 1 ELSE 0 END) AS fallback_rfr_months,
                MIN(factor_date) AS first_month,
                MAX(factor_date) AS last_month
            FROM factor_data
            """
        ).fetchdf()

    def nav_month_counts_by_scheme(self):
        self.register_core_tables()
        return self.conn.execute(
            """
            SELECT scheme_code, COUNT(DISTINCT strftime(CAST(nav_date AS DATE), '%Y-%m')) AS nav_months
            FROM nav_history
            GROUP BY scheme_code
            ORDER BY nav_months DESC
            """
        ).fetchdf()
