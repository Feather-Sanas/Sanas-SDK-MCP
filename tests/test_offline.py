"""Tests for the parts of the server that do not need the native connector.

These run anywhere (no `sanas_remote_sdk`, no credentials, no network).
"""

from __future__ import annotations

import os
import tempfile

import pytest

from sanas_mcp import models as catalog
from sanas_mcp import snippets, wav_utils
from sanas_mcp.config import SanasConfig
from sanas_mcp.sdk_adapter import _result_name


# --- config ---------------------------------------------------------------

def test_config_from_env_and_missing():
    cfg = SanasConfig.from_env({})
    assert cfg.missing_credentials() == [
        "SANAS_REMOTE_ENDPOINT", "SANAS_ACCOUNT_ID", "SANAS_ACCOUNT_SECRET",
    ]
    assert cfg.secure_media is True
    assert "account_secret" not in cfg.redacted()  # secret never exposed
    assert cfg.redacted()["account_secret_set"] is False


def test_config_secure_media_parsing_and_override():
    cfg = SanasConfig.from_env({
        "SANAS_REMOTE_ENDPOINT": "e", "SANAS_ACCOUNT_ID": "a",
        "SANAS_ACCOUNT_SECRET": "s", "SANAS_SECURE_MEDIA": "false",
    })
    assert cfg.missing_credentials() == []
    assert cfg.secure_media is False
    merged = cfg.merged(secure_media=None, default_model="ST_NC3.0", endpoint="other")
    assert merged.endpoint == "other"          # non-None override applied
    assert merged.secure_media is False         # None override ignored


# --- model catalog / validation -------------------------------------------

def test_chunk_size_for_supported_rates():
    assert catalog.chunk_size_samples(8000) == 160
    assert catalog.chunk_size_samples(16000) == 320
    assert catalog.chunk_size_samples(48000) == 960


def test_known_models():
    assert catalog.is_known_model("ST_NC3.0")
    assert not catalog.is_known_model("NOPE")


# --- wav round trip --------------------------------------------------------

@pytest.mark.parametrize("rate", [8000, 16000, 48000])
def test_wav_roundtrip(rate):
    tone = wav_utils.synth_tone(rate, 0.1, frequency=200.0, amplitude=0.5)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.wav")
        wav_utils.save_wav(path, tone, rate)
        wav = wav_utils.read_wav(path)
        assert wav.sample_rate == rate
        assert wav.channels == 1
        assert wav.num_samples == len(tone)
        # 16-bit quantization tolerance
        assert max(abs(a - b) for a, b in zip(wav.samples, tone)) < 1e-3


def test_read_wav_rejects_garbage():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bad.wav")
        with open(path, "wb") as f:
            f.write(b"NOTAWAVE0000")
        with pytest.raises(wav_utils.WavError):
            wav_utils.read_wav(path)


# --- snippet generation ----------------------------------------------------

@pytest.mark.parametrize("mode", list(snippets.MODES))
def test_snippet_compiles(mode):
    code = snippets.generate("ST_NC3.0", 16000, mode=mode)
    # generated code must be syntactically valid Python
    compile(code, f"<snippet-{mode}>", "exec")
    assert "ST_NC3.0" in code
    assert "ACCOUNT_SECRET" in code  # creds come from env in generated code
    assert 'os.environ["SANAS_ACCOUNT_SECRET"]' in code


def test_snippet_rejects_bad_mode():
    with pytest.raises(ValueError):
        snippets.generate("ST_NC3.0", 16000, mode="nope")


# --- result-name reverse mapping (no connector needed) ---------------------

def test_result_name_reverse_lookup():
    class Fake:
        SUCCESS = 0
        FAILED = 3
    assert _result_name(Fake, 0) == "SUCCESS"
    assert _result_name(Fake, 3) == "FAILED"
    assert _result_name(Fake, 99) == "99"
