"""Thin adapter over the native ``sanas_remote_sdk`` connector.

Responsibilities:
* lazily import the platform-specific native connector and report status,
* translate SDK result/state integer codes into readable names,
* drive the create-processor / wait-for-READY lifecycle used by every live tool,
* provide a process-wide, thread-safe SDK singleton (``SdkManager``) shared by the
  batch/benchmark tools, plus an ephemeral path for credential verification.

The connector is a SWIG-wrapped C++ library; it is thread-safe for sharing one
SDK instance across worker threads (the SDK's own multistream example does this).
"""

from __future__ import annotations

import platform
import sys
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# Connector import + status
# ---------------------------------------------------------------------------


class ConnectorUnavailable(RuntimeError):
    """The native ``sanas_remote_sdk`` package cannot be imported on this host."""


class SanasError(RuntimeError):
    """A Sanas SDK call returned a non-success result or timed out."""

    def __init__(self, stage: str, result: str | None = None, reason: str | None = None):
        self.stage = stage
        self.result = result
        self.reason = reason
        parts = [f"stage={stage}"]
        if result:
            parts.append(f"result={result}")
        if reason:
            parts.append(f"reason={reason}")
        super().__init__("Sanas SDK error (" + ", ".join(parts) + ")")


_connector: Any = None
_connector_error: str | None = None
_import_lock = threading.Lock()


def host_info() -> dict:
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


def import_connector() -> Any:
    """Import and cache the native connector, or raise ``ConnectorUnavailable``."""
    global _connector, _connector_error
    if _connector is not None:
        return _connector
    with _import_lock:
        if _connector is not None:
            return _connector
        try:
            import sanas_remote_sdk  # type: ignore

            _connector = sanas_remote_sdk
            _connector_error = None
            return _connector
        except Exception as exc:  # ImportError, OSError (wrong-arch .so), etc.
            info = host_info()
            _connector_error = str(exc)
            raise ConnectorUnavailable(
                "The Sanas SDK connector (`sanas_remote_sdk`) is not importable in this "
                f"environment ({info['platform']}/{info['machine']}, Python {info['python']}). "
                "Install the connector wheel that matches this platform and Python version "
                "(from your Sanas onboarding package) into this interpreter, e.g. "
                "`pip install sanas_remote_sdk-<ver>-<py>-<platform>.whl`. "
                f"Underlying import error: {exc}"
            ) from exc


def connector_status() -> dict:
    """Non-raising probe used by ``sanas_get_sdk_info``."""
    try:
        mod = import_connector()
    except ConnectorUnavailable as exc:
        return {"available": False, "version": None, "error": str(exc)}
    return {
        "available": True,
        "version": getattr(mod, "__version__", "unknown"),
        "error": None,
    }


def _result_name(result_cls: Any, value: Any) -> str:
    """Reverse-map an SDK result/state integer back to its symbolic name."""
    for key, val in vars(result_cls).items():
        if not key.startswith("_") and val == value:
            return key
    return str(value)


# ---------------------------------------------------------------------------
# Processor lifecycle
# ---------------------------------------------------------------------------


def _create_ready_processor(sdk: Any, mod: Any, model: str, sample_rate: int,
                            timeout_s: float) -> tuple[Any, float]:
    """Create an audio processor and block until it reaches READY.

    Returns ``(processor, time_to_ready_seconds)`` or raises ``SanasError``.
    """
    ready = threading.Event()
    failed = threading.Event()
    last = {"state": None, "reason": ""}

    def state_callback(state: int, reason: str) -> None:
        last["state"] = state
        last["reason"] = reason
        if state == mod.ProcessorState.READY:
            ready.set()
        elif state in (mod.ProcessorState.FAILED, mod.ProcessorState.DISCONNECTED):
            failed.set()

    audio_params = mod.AudioParams()
    audio_params.modelName = model
    audio_params.sampleRate = sample_rate

    started = time.time()
    processor, create_result = sdk.CreateAudioProcessor(audio_params, state_callback)
    if not processor or create_result != mod.CreateProcessorResult.SUCCESS:
        raise SanasError(
            stage="create_processor",
            result=_result_name(mod.CreateProcessorResult, create_result),
        )

    if not ready.wait(timeout=timeout_s):
        try:
            sdk.DestroyAudioProcessor(processor)
        except Exception:
            pass
        if failed.is_set():
            raise SanasError(
                stage="processor_state",
                result=_result_name(mod.ProcessorState, last["state"]),
                reason=last["reason"] or None,
            )
        raise SanasError(stage="processor_ready_timeout",
                         reason=f"processor did not reach READY within {timeout_s}s")

    return processor, time.time() - started


def initialize_sdk(mod: Any, config) -> Any:
    """Create + Initialize a fresh SDK instance from ``config``; raise on failure."""
    sdk = mod.CreateRemoteSDK()
    if not sdk:
        raise SanasError(stage="create_sdk", reason="CreateRemoteSDK returned null")
    params = mod.InitParams()
    params.remoteEndpoint = config.endpoint
    params.accountId = config.account_id
    params.accountSecret = config.account_secret
    params.secureMedia = config.secure_media
    result = sdk.Initialize(params)
    if result != mod.InitSDKResult.SUCCESS:
        raise SanasError(stage="initialize",
                         result=_result_name(mod.InitSDKResult, result))
    return sdk


def verify_connection(config, model: str, sample_rate: int, timeout_s: float) -> dict:
    """Ephemeral end-to-end check: init, load a processor to READY, tear down.

    Proves credentials + endpoint reachability + that ``model`` loads on the AI
    Engine at ``sample_rate``. Uses its own SDK instance so it can test arbitrary
    credentials without disturbing the shared session.
    """
    mod = import_connector()
    sdk = initialize_sdk(mod, config)
    try:
        processor, time_to_ready = _create_ready_processor(
            sdk, mod, model, sample_rate, timeout_s)
        sdk.DestroyAudioProcessor(processor)
        return {
            "ok": True,
            "sdk_version": getattr(mod, "__version__", "unknown"),
            "endpoint": config.endpoint,
            "model": model,
            "sample_rate": sample_rate,
            "secure_media": config.secure_media,
            "time_to_ready_seconds": round(time_to_ready, 3),
        }
    finally:
        try:
            sdk.Shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shared SDK singleton (used by batch + benchmark + active-count tools)
# ---------------------------------------------------------------------------


class SdkManager:
    """Process-wide SDK instance, lazily initialized from the resolved config."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sdk: Any = None
        self._mod: Any = None
        self._config = None

    def ensure(self, config) -> tuple[Any, Any]:
        """Return ``(sdk, connector_module)``, initializing on first use."""
        missing = config.missing_credentials()
        if missing:
            raise SanasError(
                stage="config",
                reason="missing required environment variables: " + ", ".join(missing),
            )
        with self._lock:
            if self._sdk is None:
                mod = import_connector()
                sdk = initialize_sdk(mod, config)
                self._sdk, self._mod, self._config = sdk, mod, config
            return self._sdk, self._mod

    @property
    def is_initialized(self) -> bool:
        if self._sdk is None:
            return False
        try:
            return bool(self._sdk.IsInitialized())
        except Exception:
            return False

    def active_processor_count(self) -> int:
        if self._sdk is None:
            return 0
        return int(self._sdk.GetActiveProcessorCount())

    def shutdown(self) -> None:
        with self._lock:
            if self._sdk is not None:
                try:
                    self._sdk.Shutdown()
                finally:
                    self._sdk = None
                    self._mod = None


# Single shared manager instance for the server process.
manager = SdkManager()
