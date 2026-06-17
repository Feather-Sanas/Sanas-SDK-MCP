"""Runtime configuration for the Sanas MCP server.

Credentials and defaults are read from environment variables so secrets are
never passed through the model. A local ``.env`` file is loaded if present
(see ``.env.example``). Individual tool calls may override non-secret defaults.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

_TRUE = {"1", "true", "yes", "on", "y", "t"}

# Default model used when a tool call does not specify one. ``ST_NC3.0`` is the
# general-purpose noise-cancellation model shown in the SDK examples/docs.
DEFAULT_MODEL = "ST_NC3.0"
DEFAULT_SAMPLE_RATE = 16000


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE


@dataclass
class SanasConfig:
    """Resolved configuration for talking to the Sanas AI Engine."""

    endpoint: str | None = None
    account_id: str | None = None
    account_secret: str | None = None
    secure_media: bool = True
    default_model: str = DEFAULT_MODEL
    default_sample_rate: int = DEFAULT_SAMPLE_RATE

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "SanasConfig":
        env = environ if environ is not None else os.environ
        try:
            sample_rate = int(env.get("SANAS_DEFAULT_SAMPLE_RATE") or DEFAULT_SAMPLE_RATE)
        except (TypeError, ValueError):
            sample_rate = DEFAULT_SAMPLE_RATE
        return cls(
            endpoint=env.get("SANAS_REMOTE_ENDPOINT") or None,
            account_id=env.get("SANAS_ACCOUNT_ID") or None,
            account_secret=env.get("SANAS_ACCOUNT_SECRET") or None,
            secure_media=_as_bool(env.get("SANAS_SECURE_MEDIA"), True),
            default_model=env.get("SANAS_DEFAULT_MODEL") or DEFAULT_MODEL,
            default_sample_rate=sample_rate,
        )

    def merged(self, **overrides: object) -> "SanasConfig":
        """Return a copy with any non-``None`` overrides applied."""
        data = asdict(self)
        for key, value in overrides.items():
            if value is not None and key in data:
                data[key] = value
        return SanasConfig(**data)

    def missing_credentials(self) -> list[str]:
        """Names of required env vars that are not set."""
        missing = []
        if not self.endpoint:
            missing.append("SANAS_REMOTE_ENDPOINT")
        if not self.account_id:
            missing.append("SANAS_ACCOUNT_ID")
        if not self.account_secret:
            missing.append("SANAS_ACCOUNT_SECRET")
        return missing

    def redacted(self) -> dict:
        """Config safe to return to the model (secret is never exposed)."""
        return {
            "endpoint": self.endpoint,
            "account_id": self.account_id,
            "account_secret_set": bool(self.account_secret),
            "secure_media": self.secure_media,
            "default_model": self.default_model,
            "default_sample_rate": self.default_sample_rate,
        }
