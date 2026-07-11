from __future__ import annotations

import re


CHANGE_TERMS = {
    "exit": ["left", "exits", "resigned", "quit", "moves out", "steps down"],
    "join": ["joined", "joins", "appointed", "takes over", "assumes charge"],
    "switch": ["joins", "moved to", "appointed at", "switches to"],
}


def classify_evidence_text(text: str) -> tuple[str, float]:
    lowered = text.lower()
    if any(term in lowered for term in CHANGE_TERMS["switch"]) and any(term in lowered for term in ["amc", "mutual fund", "asset management"]):
        return "amc_switch", 0.65
    if any(term in lowered for term in CHANGE_TERMS["exit"]):
        return "manager_exit", 0.6
    if any(term in lowered for term in CHANGE_TERMS["join"]):
        return "manager_join", 0.55
    if "fund manager" in lowered or "key personnel" in lowered:
        return "manager_related", 0.45
    return "unknown", 0.2


def extract_possible_names(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", text)
    blocked = {"Mutual Fund", "Asset Management", "Fund Manager", "Google News", "India Fund"}
    return sorted({c for c in candidates if c not in blocked and len(c) <= 60})
