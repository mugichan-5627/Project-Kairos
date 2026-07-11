from __future__ import annotations

import re

from bs4 import BeautifulSoup
import pandas as pd

from src.utils.rate_limiter import RateLimiter


SEBI_URLS = [
    "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFbo=yes",
    "https://www.sebi.gov.in/legal/circulars.html",
]


class SEBIScraper:
    def __init__(self, limiter: RateLimiter | None = None) -> None:
        self.limiter = limiter or RateLimiter()

    def scan(self) -> pd.DataFrame:
        rows = []
        pattern = re.compile(r"(change in fund manager|key personnel|fund manager)", re.I)
        for url in SEBI_URLS:
            response = self.limiter.get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a"):
                text = " ".join(link.get_text(" ").split())
                href = link.get("href")
                if text and pattern.search(text):
                    rows.append({"title": text, "url": href, "source_page": url, "source": "sebi"})
        return pd.DataFrame(rows)
