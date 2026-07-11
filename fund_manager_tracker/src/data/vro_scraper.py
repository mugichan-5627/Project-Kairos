from __future__ import annotations

import re
import time
from dataclasses import dataclass

import pandas as pd
from fuzzywuzzy import fuzz
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from src.config import USER_AGENTS


@dataclass
class VROResult:
    fund_id: str
    managers: list[str]
    url: str


class ValueResearchScraper:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def _driver(self) -> webdriver.Chrome:
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument(f"--user-agent={USER_AGENTS[0]}")
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    def extract_current_managers(self, fund_id: str) -> VROResult:
        url = f"https://www.valueresearchonline.com/funds/{fund_id}/"
        driver = self._driver()
        try:
            driver.get(url)
            time.sleep(5)
            driver.execute_script("window.scrollTo(0, Math.floor(document.body.scrollHeight/2));")
            time.sleep(2)
            text = driver.find_element("tag name", "body").text
        finally:
            driver.quit()
        managers = []
        match = re.search(r"Fund Manager(?:s)?\s+([A-Z][A-Za-z .,&]+)", text)
        if match:
            for name in re.split(r",| and |&", match.group(1)):
                cleaned = " ".join(name.strip().split())
                if cleaned:
                    managers.append(cleaned)
        return VROResult(fund_id=fund_id, managers=managers, url=url)

    @staticmethod
    def fuzzy_map_funds(vro_names: pd.DataFrame, amfi_master: pd.DataFrame, threshold: int = 90) -> pd.DataFrame:
        rows = []
        for _, vro in vro_names.iterrows():
            best_score = -1
            best_scheme = None
            for _, scheme in amfi_master.iterrows():
                score = fuzz.token_set_ratio(str(vro["fund_name"]), str(scheme["scheme_name"]))
                if score > best_score:
                    best_score = score
                    best_scheme = scheme
            if best_scheme is not None and best_score >= threshold:
                rows.append({**vro.to_dict(), "scheme_code": best_scheme["scheme_code"], "match_score": best_score})
        return pd.DataFrame(rows)
