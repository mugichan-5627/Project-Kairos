from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from src.utils.db import get_connection


def _log_tavily_failure(error: str, query: str | None = None) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO data_quality_log(check_name, status, details)
                VALUES('tavily', 'api_failure', ?)
                """,
                (f"{query or ''} :: {error}"[:2000],),
            )
    except Exception:
        pass


@dataclass
class TavilySearchClient:
    api_key: str | None = None
    base_url: str | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("TAVILY_API_KEY") or os.getenv("KAIROS_TAVILY_API_KEY")
        self.base_url = self.base_url or os.getenv("TAVILY_BASE_URL", "https://api.tavily.com/search")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 8, include_answer: bool = False, topic: str = "news") -> dict[str, Any]:
        if not self.enabled:
            error = "TAVILY_API_KEY is not configured"
            _log_tavily_failure(error, query)
            return {"query": query, "results": [], "error": error}
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": include_answer,
            "search_depth": "advanced",
            "topic": topic,
        }
        try:
            response = requests.post(self.base_url, json=payload, timeout=45)
            if response.status_code == 429:
                error = "Tavily rate limit reached"
                _log_tavily_failure(error, query)
                return {"query": query, "results": [], "error": error}
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            _log_tavily_failure(str(exc), query)
            return {"query": query, "results": [], "error": str(exc)}
