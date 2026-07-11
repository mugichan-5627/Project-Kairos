from __future__ import annotations

from urllib.parse import quote_plus

import feedparser
import pandas as pd

from src.config import MAJOR_AMCS
from src.utils.db import get_connection
from src.utils.rate_limiter import RateLimiter


class NewsMonitor:
    def __init__(self, limiter: RateLimiter | None = None) -> None:
        self.limiter = limiter or RateLimiter()

    def query_amc(self, amc_name: str) -> pd.DataFrame:
        query = quote_plus(f'"{amc_name} fund manager change"')
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        response = self.limiter.get(url)
        feed = feedparser.parse(response.text)
        rows = []
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            if not any(term in title.lower() for term in ["manager", "key personnel", "change", "exit", "joins"]):
                continue
            rows.append(
                {
                    "amc_name": amc_name,
                    "title": title,
                    "url": getattr(entry, "link", None),
                    "published": getattr(entry, "published", None),
                    "source": "google_news_rss",
                    "confidence_score": 0.7,
                }
            )
        return pd.DataFrame(rows)

    def refresh(self, amcs: list[str] | None = None) -> int:
        amcs = amcs or MAJOR_AMCS
        total = 0
        with get_connection() as conn:
            for amc in amcs:
                df = self.query_amc(amc)
                for _, row in df.iterrows():
                    conn.execute(
                        """
                        INSERT INTO manager_changes(scheme_code, manager_name, manager_key, change_date, source, confidence_score, evidence_url, notes)
                        VALUES(NULL, NULL, NULL, ?, ?, ?, ?, ?)
                        """,
                        (row["published"], row["source"], row["confidence_score"], row["url"], f"{row['amc_name']} | {row['title']}"),
                    )
                total += len(df)
            conn.execute(
                "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded) VALUES('google_news_rss',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'ok',?)",
                (total,),
            )
        return total
