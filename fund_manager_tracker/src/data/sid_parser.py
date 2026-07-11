from __future__ import annotations

import re
from pathlib import Path

import fitz
import pandas as pd

from src.config import RAW_SID_DIR
from src.utils.rate_limiter import RateLimiter


MANAGER_PATTERNS = [
    re.compile(r"Fund Manager(?:\(s\))?\s*[:\-]\s*([A-Z][A-Za-z .,&]+)", re.I),
    re.compile(r"managed by\s+([A-Z][A-Za-z .,&]+)", re.I),
]


class SIDParser:
    def __init__(self, limiter: RateLimiter | None = None) -> None:
        self.limiter = limiter or RateLimiter()
        RAW_SID_DIR.mkdir(parents=True, exist_ok=True)

    def download_pdf(self, url: str, scheme_code: str, suffix: str = "current") -> Path:
        response = self.limiter.get(url)
        path = RAW_SID_DIR / f"{scheme_code}_{suffix}.pdf"
        path.write_bytes(response.content)
        return path

    def extract_text(self, pdf_path: Path) -> str:
        with fitz.open(pdf_path) as doc:
            return "\n".join(page.get_text("text") for page in doc)

    def parse_managers_from_text(self, text: str) -> list[str]:
        managers: list[str] = []
        for pattern in MANAGER_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(1).split("\n")[0]
                for name in re.split(r",| and |&", raw):
                    cleaned = " ".join(name.strip().split())
                    if 3 <= len(cleaned) <= 80 and cleaned not in managers:
                        managers.append(cleaned)
        return managers

    def parse_pdf(self, pdf_path: Path, scheme_code: str, scheme_name: str | None = None, amc_name: str | None = None) -> pd.DataFrame:
        text = self.extract_text(pdf_path)
        managers = self.parse_managers_from_text(text)
        return pd.DataFrame(
            [
                {
                    "scheme_code": scheme_code,
                    "scheme_name": scheme_name,
                    "amc_name": amc_name,
                    "manager_name": manager,
                    "source": "sid_pdf",
                    "confidence_score": 1.0,
                    "raw_evidence": str(pdf_path),
                    "is_lead_manager": int(i == 0),
                }
                for i, manager in enumerate(managers)
            ]
        )
