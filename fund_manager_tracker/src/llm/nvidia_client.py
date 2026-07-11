from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class NvidiaLLMClient:
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    min_interval_seconds: float = 1.6

    _last_call: float = 0.0

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("NVIDIA_API_KEY") or os.getenv("KAIROS_NVIDIA_API_KEY")
        self.base_url = self.base_url or os.getenv("KAIROS_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
        self.model = self.model or os.getenv("KAIROS_NVIDIA_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 500) -> str:
        if not self.enabled:
            return "LLM is not configured. Add KAIROS_NVIDIA_API_KEY to .env or enter it in the setup wizard."
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        self._last_call = time.time()
        if response.status_code == 429:
            return "NVIDIA API rate limit reached. Wait a minute and try again."
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


def transition_brief(context: str, api_key: str | None = None) -> str:
    client = NvidiaLLMClient(api_key=api_key)
    return client.complete(
        "You are an Indian mutual fund transition-risk analyst. Be concise, evidence-led, and avoid investment advice guarantees.",
        f"Write a short transition risk brief from this Project Kairos data:\n\n{context}",
        max_tokens=450,
    )


def portfolio_brief(context: str, api_key: str | None = None) -> str:
    client = NvidiaLLMClient(api_key=api_key)
    return client.complete(
        "You are an Indian mutual fund portfolio risk analyst. Explain manager-transition risk in plain language. Do not recommend buying or selling; use Hold, Monitor, or Review as soft action labels.",
        f"Summarize this portfolio manager-risk scan:\n\n{context}",
        max_tokens=550,
    )
