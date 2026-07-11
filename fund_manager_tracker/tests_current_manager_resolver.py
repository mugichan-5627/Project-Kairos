from __future__ import annotations

import tempfile
import gc
from pathlib import Path

from db_setup import initialize_database
from src.data.current_manager_resolver import CurrentManagerResolver
from src.utils.db import get_connection, read_sql


class FakeSearchClient:
    enabled = True

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5, include_answer: bool = True, topic: str = "general") -> dict:
        self.queries.append(query)
        return {
            "answer": "SBI PSU Fund is managed by Rohit Shimpi since 01-Jun-2024.",
            "results": [
                {
                    "title": "SBI PSU Fund",
                    "url": "https://example.com/sbi-psu-fund",
                    "content": "Fund Manager Rohit Shimpi since 01-Jun-2024. The fund is from SBI Mutual Fund.",
                }
            ],
        }


def test_resolver_uses_cached_current_manager_before_search() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "kairos.db"
        initialize_database(db_path)
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO scheme_master(scheme_code, scheme_name, amc_name, category, sub_category, source)
                VALUES('SBI1', 'SBI PSU Fund - Regular Plan - Growth', 'SBI Mutual Fund', 'Equity', 'Thematic', 'test')
                """
            )
            conn.execute(
                """
                INSERT INTO current_manager_snapshot
                (scheme_code, scheme_name, amc_name, manager_name, manager_key, confirmed_date, source, source_url, confidence_score)
                VALUES('SBI1', 'SBI PSU Fund - Regular Plan - Growth', 'SBI Mutual Fund',
                       'Rohit Shimpi', 'Rohit Shimpi | SBI Mutual Fund', '2024-06-01',
                       'test_cache', 'https://example.com/cache', 0.95)
                """
            )

        fake = FakeSearchClient()
        result = CurrentManagerResolver(search_client=fake, db_path=db_path).resolve(
            scheme_code="SBI1",
            scheme_name="SBI PSU Fund - Regular Plan - Growth",
            amc_name="SBI Mutual Fund",
        )

        assert result["manager_name"] == "Rohit Shimpi"
        assert result["source"] == "test_cache"
        assert fake.queries == []
        gc.collect()


def test_resolver_extracts_and_caches_manager_from_general_search() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "kairos.db"
        initialize_database(db_path)
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO scheme_master(scheme_code, scheme_name, amc_name, category, sub_category, source)
                VALUES('SBI1', 'SBI PSU Fund - Regular Plan - Growth', 'SBI Mutual Fund', 'Equity', 'Thematic', 'test')
                """
            )

        fake = FakeSearchClient()
        result = CurrentManagerResolver(search_client=fake, db_path=db_path).resolve(
            scheme_code="SBI1",
            scheme_name="SBI PSU Fund - Regular Plan - Growth",
            amc_name="SBI Mutual Fund",
        )
        cached = read_sql("SELECT manager_name, source, source_url FROM current_manager_snapshot", db_path=db_path)

        assert result["manager_name"] == "Rohit Shimpi"
        assert result["source"] == "live_search"
        assert result["source_url"] == "https://example.com/sbi-psu-fund"
        assert "current fund manager" in fake.queries[0].lower()
        assert cached.iloc[0]["manager_name"] == "Rohit Shimpi"
        gc.collect()
