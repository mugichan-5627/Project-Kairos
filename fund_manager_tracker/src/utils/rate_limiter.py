from __future__ import annotations

import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from src.config import REQUEST_LOG_PATH, USER_AGENTS, ensure_dirs
from src.utils.logging import get_logger


DOMAIN_INTERVALS = {
    "api.mfapi.in": 2.0,
    "www.amfiindia.com": 0.5,
    "amfiindia.com": 0.5,
    "www.valueresearchonline.com": 5.0,
    "valueresearchonline.com": 5.0,
    "news.google.com": 3.0,
    "archive.org": 2.0,
    "web.archive.org": 2.0,
    "www.sebi.gov.in": 3.0,
    "sebi.gov.in": 3.0,
}


@dataclass
class RateLimiter:
    min_intervals: dict[str, float] = field(default_factory=lambda: DOMAIN_INTERVALS.copy())
    last_request: dict[str, float] = field(default_factory=dict)
    request_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        ensure_dirs()
        self.logger = get_logger("requests", REQUEST_LOG_PATH)

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    def _wait(self, domain: str) -> None:
        interval = self.min_intervals.get(domain, 1.0)
        if "valueresearchonline.com" in domain:
            interval += random.uniform(0, 3)
        elapsed = time.time() - self.last_request.get(domain, 0)
        if elapsed < interval:
            time.sleep(interval - elapsed)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }

    def request(self, method: str, url: str, max_retries: int = 5, **kwargs) -> requests.Response:
        domain = self._domain(url)
        for attempt in range(max_retries):
            self._wait(domain)
            if "valueresearchonline.com" in domain and self.request_counts[domain] and self.request_counts[domain] % 50 == 0:
                self.session.close()
                self.session = requests.Session()
            headers = self._headers()
            headers.update(kwargs.pop("headers", {}) or {})
            self.logger.info("%s %s attempt=%s", method.upper(), url, attempt + 1)
            response = self.session.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)
            self.last_request[domain] = time.time()
            self.request_counts[domain] += 1
            if response.status_code not in (429, 503):
                response.raise_for_status()
                return response
            wait = max(60, (2**attempt) * random.uniform(1, 3))
            self.logger.warning("Backoff status=%s url=%s wait=%.1fs", response.status_code, url, wait)
            time.sleep(wait)
        response.raise_for_status()
        return response

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)
