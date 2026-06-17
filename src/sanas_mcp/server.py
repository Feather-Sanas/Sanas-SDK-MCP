"""FastMCP server exposing the Sanas Speech AI SDK as MCP tools.

Offline tools (no native connector required): sanas_get_sdk_info,
sanas_list_models, sanas_validate_audio_config, sanas_generate_integration_snippet.

Live tools (require the `sanas_remote_sdk` connector + credentials):
sanas_verify_connection, sanas_get_active_processor_count, sanas_process_wav,
sanas_benchmark.
"""

from __future__ import annotations

import atexit
import json
import os
from contextlib import asynccontextmanager

import anyio
from mcp.server.fastmcp import FastMCP

from . import audio_jobs, models as catalog, snippets
from .config import SanasConfig
from .sdk_adapter import (
    ConnectorUnavailable,
    SanasError,
    connector_status,
    host_info,
    manager,
)
from .wav_utils import WavError

INSTRUCTIONS = """\
Tools for the Sanas Speech AI SDK — a server-hosted, real-time audio *noise
cancellation / voice processing* SDK (it cleans audio; it does NOT transcribe
to text). Start with sanas_get_sdk_info to see whether the native connector is
available on this host and whether credentials are configured. Use
sanas_list_models / sanas_validate_audio_config / sanas_generate_integration_snippet
without any credentials. The live tools talk to the Sanas AI Engine and need the
connector plus SANAS_REMOTE_ENDPOINT / SANAS_ACCOUNT_ID / SANAS_ACCOUNT_SECRET.\
"""


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    try:
        yield {}
    finally:
        manager.shutdown()


mcp = FastMCP("sanas-sdk", instructions=INSTRUCTIONS, lifespan=_lifespan)
atexit.register(manager.shutdown)


def _config() -> SanasConfig:
    return SanasConfig.from_env()


def _error(exc: Exception) -> dict:
    """Convert an expected failure into a structured, model-friendly result."""
    return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}


# ---------------------------------------------------------------------------
# Offline tools
# ---------------------------------------------------------------------------


@mcp.tool()
def sanas_get_sdk_info() -> dict:
    """Report connector availability, host platform, and configured defaults.

    Safe to call anywhere — never contacts the network and never returns secrets.
    Use this first to learn whether live tools can run on this host.
    """
    cfg = _config()
    return {
        "connector": connector_status(),
        "host": host_info(),
        "config": cfg.redacted(),
        "credentials_complete": not cfg.missing_credentials(),
        "missing_env_vars": cfg.missing_credentials(),
        "supported_sample_rates": list(catalog.SUPPORTED_SAMPLE_RATES),
        "chunk_ms": catalog.CHUNK_MS,
        "sdk_initialized": manager.is_initialized,
    }


@mcp.tool()
def sanas_list_models() -> dict:
    """List the documented Sanas audio models with descriptions and sample rates.

    The list is the set documented in the SDK quickstart/examples and may not be
    exhaustive for your account; confirm a model with sanas_verify_connection.
    """
    return {"models": catalog.KNOWN_MODELS, "note": catalog.CATALOG_NOTE}


@mcp.tool()
def sanas_validate_audio_config(model: str, sample_rate: int) -> dict:
    """Validate a model name + sample rate and compute the 20 ms frame size.

    Unsupported sample rates are errors; unknown model names are warnings (your
    account may provide models beyond the documented catalog).
    """
    errors: list[str] = []
    warnings: list[str] = []
    if sample_rate not in catalog.SUPPORTED_SAMPLE_RATES:
        errors.append(
            f"sample_rate {sample_rate} Hz is not supported "
            f"(supported: {list(catalog.SUPPORTED_SAMPLE_RATES)})"
        )
    known = catalog.is_known_model(model)
    if not known:
        warnings.append(
            f"model '{model}' is not in the documented catalog; it may still be "
            "valid for your account — verify with sanas_verify_connection"
        )
    return {
        "valid": not errors,
        "model": model,
        "model_known": known,
        "sample_rate": sample_rate,
        "chunk_size_samples": catalog.chunk_size_samples(sample_rate)
        if sample_rate in catalog.SUPPORTED_SAMPLE_RATES
        else None,
        "chunk_ms": catalog.CHUNK_MS,
        "errors": errors,
        "warnings": warnings,
    }


@mcp.tool()
def sanas_generate_integration_snippet(
    model: str,
    sample_rate: int = 16000,
    mode: str = "single",
    secure_media: bool = True,
) -> dict:
    """Generate runnable Python for the Sanas Remote SDK.

    mode: 'single' (real-time stream loop), 'wav_file' (batch denoise a WAV), or
    'multi' (concurrent streams on one shared SDK). Credentials are read from
    environment variables in the generated code, never hard-coded.
    """
    try:
        code = snippets.generate(model, sample_rate, mode, secure_media)
    except ValueError as exc:
        return _error(exc)
    return {"ok": True, "language": "python", "mode": mode, "snippet": code}


# ---------------------------------------------------------------------------
# Live tools (require the native connector + credentials)
# ---------------------------------------------------------------------------


@mcp.tool()
async def sanas_verify_connection(
    model: str | None = None,
    sample_rate: int | None = None,
    endpoint: str | None = None,
    account_id: str | None = None,
    account_secret: str | None = None,
    secure_media: bool | None = None,
    timeout_s: float = 10.0,
) -> dict:
    """Verify credentials + endpoint by loading a model to READY, then tear down.

    Uses env defaults unless overridden per call. This is the definitive check
    that an account/endpoint/model actually works end-to-end. Returns timing on
    success or the failing stage/result code on failure.
    """
    cfg = _config().merged(
        endpoint=endpoint,
        account_id=account_id,
        account_secret=account_secret,
        secure_media=secure_media,
    )
    missing = cfg.missing_credentials()
    if missing:
        return {"ok": False, "error_type": "ConfigError",
                "error": "missing credentials: " + ", ".join(missing)}
    model = model or cfg.default_model
    sample_rate = sample_rate or cfg.default_sample_rate
    try:
        from . import sdk_adapter
        return await anyio.to_thread.run_sync(
            lambda: sdk_adapter.verify_connection(cfg, model, sample_rate, timeout_s)
        )
    except (ConnectorUnavailable, SanasError) as exc:
        result = {**_error(exc)}
        if isinstance(exc, SanasError):
            result.update(stage=exc.stage, result=exc.result, reason=exc.reason)
        return result


@mcp.tool()
def sanas_get_active_processor_count() -> dict:
    """Return the number of active audio processors on the shared SDK session.

    Does not initialize the SDK; if no live session exists yet, the count is 0.
    """
    return {
        "ok": True,
        "sdk_initialized": manager.is_initialized,
        "active_processor_count": manager.active_processor_count(),
    }


@mcp.tool()
async def sanas_process_wav(
    input_path: str,
    output_path: str | None = None,
    model: str | None = None,
    expected_sample_rate: int | None = None,
    simulate_realtime: bool = False,
) -> dict:
    """Denoise a WAV file through the Sanas AI Engine and write a cleaned WAV.

    Input must be 8 / 16 / 48 kHz (mono or multi-channel; multi-channel is
    downmixed). Returns throughput and per-frame latency metrics. Set
    simulate_realtime=True to pace at 20 ms/frame (e.g. to mimic a live call);
    leave False to process as fast as possible.
    """
    cfg = _config()
    missing = cfg.missing_credentials()
    if missing:
        return {"ok": False, "error_type": "ConfigError",
                "error": "missing credentials: " + ", ".join(missing)}
    model = model or cfg.default_model
    if not os.path.isfile(input_path):
        return {"ok": False, "error_type": "FileNotFoundError",
                "error": f"input_path not found: {input_path}"}
    if output_path is None:
        root, _ext = os.path.splitext(input_path)
        output_path = f"{root}_sanas_clean.wav"
    try:
        return await anyio.to_thread.run_sync(
            lambda: audio_jobs.process_wav(
                cfg, input_path, output_path, model,
                expected_sample_rate=expected_sample_rate,
                simulate_realtime=simulate_realtime,
            )
        )
    except (ConnectorUnavailable, SanasError, WavError) as exc:
        return _error(exc)


@mcp.tool()
async def sanas_benchmark(
    model: str | None = None,
    sample_rate: int = 8000,
    max_calls: int = 10,
    concurrent_limit: int = 3,
    call_rate: float = 5.0,
    rate_period_ms: int = 1000,
    duration_s: float = 10.0,
    input_wav: str | None = None,
) -> dict:
    """Load-test the AI Engine: run concurrent calls at a controlled rate.

    Reports success rate, peak concurrency, time-to-ready stats, and error
    breakdown. If input_wav is omitted a sine tone is synthesized. max_calls and
    concurrent_limit are capped for safety (1000 / 64).
    """
    cfg = _config()
    missing = cfg.missing_credentials()
    if missing:
        return {"ok": False, "error_type": "ConfigError",
                "error": "missing credentials: " + ", ".join(missing)}
    model = model or cfg.default_model
    try:
        return await anyio.to_thread.run_sync(
            lambda: audio_jobs.benchmark(
                cfg, model, sample_rate, max_calls, concurrent_limit,
                call_rate, rate_period_ms, duration_s, input_wav=input_wav,
            )
        )
    except (ConnectorUnavailable, SanasError, WavError) as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("sanas://models")
def models_resource() -> str:
    """The documented Sanas model catalog as JSON."""
    return json.dumps({"models": catalog.KNOWN_MODELS, "note": catalog.CATALOG_NOTE}, indent=2)


@mcp.resource("sanas://status")
def status_resource() -> str:
    """Live server/connector/config status as JSON (no secrets)."""
    cfg = _config()
    return json.dumps(
        {
            "connector": connector_status(),
            "host": host_info(),
            "config": cfg.redacted(),
            "sdk_initialized": manager.is_initialized,
        },
        indent=2,
    )


def run() -> None:
    """Entry point: serve over stdio."""
    mcp.run()
