"""Sanas audio model catalog and audio-format constants.

The authoritative list of models available to an account is provisioned by
Sanas (delivered with your onboarding) and is *not* queryable through the SDK.
The entries below are the models documented in the SDK quickstart and examples.
``sanas_validate_audio_config`` treats unknown model names as a warning (not an
error) because your account may expose additional models. Use
``sanas_verify_connection`` to confirm a given model loads on the AI Engine.
"""

from __future__ import annotations

# The AI Engine accepts these input sample rates directly (see SDK examples:
# "works seamlessly with 8kHz, 16kHz, or 48kHz input files").
SUPPORTED_SAMPLE_RATES: tuple[int, ...] = (8000, 16000, 48000)

# Audio is streamed to the engine in 20 ms frames.
CHUNK_MS: int = 20

CATALOG_NOTE = (
    "Models are provisioned per account by Sanas; this catalog lists the models "
    "documented in the SDK quickstart/examples and may not be exhaustive for your "
    "account. Confirm availability with sanas_verify_connection."
)

KNOWN_MODELS: list[dict] = [
    {
        "name": "ST_NC3.0",
        "family": "Noise Cancellation 3.0",
        "vad": False,
        "recommended_sample_rates": [8000, 16000],
        "description": (
            "General-purpose real-time noise cancellation (generation 3.0). "
            "Default model used in the single-/multi-stream SDK examples."
        ),
    },
    {
        "name": "ASR_NC3.0_VAD0.6",
        "family": "Noise Cancellation 3.0 + VAD",
        "vad": True,
        "recommended_sample_rates": [16000],
        "description": (
            "Noise cancellation (3.0) with integrated voice-activity detection "
            "(VAD 0.6), used in the agentic/ASR-oriented examples. Output is "
            "cleaned audio (this SDK does not return transcripts)."
        ),
    },
]

_MODELS_BY_NAME = {m["name"]: m for m in KNOWN_MODELS}


def is_known_model(name: str) -> bool:
    return name in _MODELS_BY_NAME


def get_model(name: str) -> dict | None:
    return _MODELS_BY_NAME.get(name)


def chunk_size_samples(sample_rate: int) -> int:
    """Number of samples in one 20 ms frame at ``sample_rate``."""
    return int(sample_rate * CHUNK_MS / 1000)
