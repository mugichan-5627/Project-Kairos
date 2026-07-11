from __future__ import annotations

import os

from src.llm.nvidia_client import NvidiaLLMClient
from src.utils.db import get_connection


def log_llm_error(provider: str, error: str, attempt: int) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO data_quality_log(check_name, status, details)
                VALUES(?, 'api_failure', ?)
                """,
                (provider, f"attempt={attempt}; {error}"[:2000]),
            )
    except Exception:
        pass


def call_claude(prompt: str, system_prompt: str, max_tokens: int = 700) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("KAIROS_ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0,
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
    except Exception:
        raise


def call_llm_with_fallback(prompt: str, system_prompt: str, max_retries: int = 2, max_tokens: int = 700) -> tuple[str | None, str]:
    for attempt in range(max_retries):
        try:
            response = call_claude(prompt, system_prompt, max_tokens=max_tokens)
            if response and response.strip():
                return response, "claude"
        except Exception as exc:
            log_llm_error("claude_api", str(exc), attempt)

    nvidia = NvidiaLLMClient()
    for attempt in range(max_retries):
        try:
            if not nvidia.enabled:
                break
            response = nvidia.complete(system_prompt, prompt, temperature=0, max_tokens=max_tokens)
            if response and response.strip():
                return response, "nvidia"
        except Exception as exc:
            log_llm_error("nvidia_api", str(exc), attempt)

    return None, "all_failed"
