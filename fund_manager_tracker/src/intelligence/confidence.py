from __future__ import annotations

from collections import defaultdict


SOURCE_WEIGHTS = {
    "amfi_sid": 1.00,
    "sid_pdf": 1.00,
    "sebi_circular": 0.90,
    "sebi": 0.90,
    "valueresearch": 0.85,
    "value_research": 0.85,
    "reputable_news": 0.70,
    "et_article": 0.70,
    "mint_article": 0.70,
    "business_standard_article": 0.70,
    "tavily": 0.55,
    "tavily_search": 0.55,
    "llm_extraction": 0.35,
}


def base_weight_for_source(source_type: str | None, source_name: str | None = None, source_url: str | None = None) -> float:
    key = (source_type or source_name or "").strip().lower().replace(" ", "_")
    if key in SOURCE_WEIGHTS:
        return SOURCE_WEIGHTS[key]
    url = (source_url or "").lower()
    if "economictimes" in url or "indiatimes.com" in url:
        return SOURCE_WEIGHTS["et_article"]
    if "livemint.com" in url:
        return SOURCE_WEIGHTS["mint_article"]
    if "business-standard.com" in url:
        return SOURCE_WEIGHTS["business_standard_article"]
    if "valueresearchonline.com" in url:
        return SOURCE_WEIGHTS["valueresearch"]
    if "sebi.gov.in" in url:
        return SOURCE_WEIGHTS["sebi_circular"]
    if "amfiindia.com" in url:
        return SOURCE_WEIGHTS["amfi_sid"]
    return 0.50


def corroboration_multiplier(agreeing_sources: int) -> float:
    if agreeing_sources >= 3:
        return 1.25
    if agreeing_sources == 2:
        return 1.15
    return 1.00


def system_confidence(evidence_items: list[dict]) -> float:
    if not evidence_items:
        return SOURCE_WEIGHTS["llm_extraction"]
    weights = [
        base_weight_for_source(item.get("source_type"), item.get("source_name"), item.get("source_url"))
        for item in evidence_items
    ]
    source_keys = {
        (item.get("source_url") or item.get("source_name") or item.get("source_type") or "").lower()
        for item in evidence_items
    }
    return min(max(weights) * corroboration_multiplier(len(source_keys)), 1.0)
