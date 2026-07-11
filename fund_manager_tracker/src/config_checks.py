from __future__ import annotations

import os
from dataclasses import dataclass

from src.config import DB_IS_EPHEMERAL, DB_PATH


# Nothing is strictly required for the read-only demo path: analytics + UI work
# off the bundled SQLite file. The agent/alert layers run in dry-run when their
# keys are absent so the UI never errors. Treat every external dependency as
# optional and surface its state in /api/status.
REQUIRED_ENV_VARS: list[str] = []

OPTIONAL_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "NVIDIA_API_KEY",
    "TAVILY_API_KEY",
    "RESEND_API_KEY",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
]

AGENT_GROUPS = {
    "evidence_llm": ["ANTHROPIC_API_KEY", "NVIDIA_API_KEY"],
    "evidence_search": ["TAVILY_API_KEY"],
    "email_delivery": ["RESEND_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"],
    "whatsapp_delivery": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
}

ALIASES = {
    "ANTHROPIC_API_KEY": ["ANTHROPIC_API_KEY", "KAIROS_ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
    "NVIDIA_API_KEY": ["NVIDIA_API_KEY", "KAIROS_NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
    "TAVILY_API_KEY": ["TAVILY_API_KEY", "KAIROS_TAVILY_API_KEY"],
    "RESEND_API_KEY": ["RESEND_API_KEY", "KAIROS_RESEND_API_KEY"],
    "SMTP_HOST": ["SMTP_HOST", "KAIROS_SMTP_SERVER", "KAIROS_SMTP_HOST"],
    "SMTP_PORT": ["SMTP_PORT", "KAIROS_SMTP_PORT"],
    "SMTP_USER": ["SMTP_USER", "KAIROS_SMTP_USER"],
    "SMTP_PASSWORD": ["SMTP_PASSWORD", "KAIROS_SMTP_PASSWORD"],
    "TWILIO_ACCOUNT_SID": ["TWILIO_ACCOUNT_SID", "KAIROS_TWILIO_ACCOUNT_SID"],
    "TWILIO_AUTH_TOKEN": ["TWILIO_AUTH_TOKEN", "KAIROS_TWILIO_AUTH_TOKEN"],
}


@dataclass(frozen=True)
class EnvStatus:
    all_required_present: bool
    missing_required: list[str]
    missing_optional: list[str]
    configured_required: list[str]
    warnings: list[str]
    db_path: str
    db_is_ephemeral: bool
    agent_groups: dict[str, dict]

    def to_dict(self) -> dict:
        return {
            "all_required_present": self.all_required_present,
            "missing_required": self.missing_required,
            "missing_optional": self.missing_optional,
            "configured_required": self.configured_required,
            "warnings": self.warnings,
            "db_path": self.db_path,
            "db_is_ephemeral": self.db_is_ephemeral,
            "agent_groups": self.agent_groups,
        }


def _present(name: str) -> bool:
    candidates = ALIASES.get(name, [name])
    return any(bool(os.getenv(candidate)) for candidate in candidates)


def validate_environment() -> EnvStatus:
    missing_required = [name for name in REQUIRED_ENV_VARS if not _present(name)]
    configured_required = [name for name in REQUIRED_ENV_VARS if _present(name)]
    missing_optional = [name for name in OPTIONAL_ENV_VARS if not _present(name)]
    warnings: list[str] = []
    groups: dict[str, dict] = {}
    for group_name, var_names in AGENT_GROUPS.items():
        present = [name for name in var_names if _present(name)]
        missing = [name for name in var_names if not _present(name)]
        if group_name == "evidence_llm":
            # Either LLM provider is enough to consider the group live
            status = "live" if present else "dormant"
        elif group_name == "email_delivery":
            # Resend alone OR a complete SMTP triple keeps email delivery live
            smtp_vars = {"SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"}
            smtp_live = smtp_vars.issubset(set(present))
            status = "live" if ("RESEND_API_KEY" in present or smtp_live) else "dormant"
        else:
            status = "live" if not missing else "dormant"
        groups[group_name] = {
            "status": status,
            "configured": present,
            "missing": missing,
        }
    if groups["evidence_llm"]["status"] == "dormant":
        warnings.append("No LLM provider configured; Evidence Agent will fall back to heuristic judging.")
    if groups["email_delivery"]["status"] == "dormant":
        warnings.append("Neither Resend nor SMTP configured; investor alerts will render to disk only (dry-run mode).")
    if DB_IS_EPHEMERAL:
        warnings.append("KAIROS_DB_PATH resolves under /tmp; data will not persist across cold starts.")
    return EnvStatus(
        all_required_present=not missing_required,
        missing_required=missing_required,
        missing_optional=missing_optional,
        configured_required=configured_required,
        warnings=warnings,
        db_path=str(DB_PATH),
        db_is_ephemeral=DB_IS_EPHEMERAL,
        agent_groups=groups,
    )
