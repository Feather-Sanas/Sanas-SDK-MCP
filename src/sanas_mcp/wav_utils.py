"""Minimal, dependency-free WAV I/O for the Sanas MCP server.

Reads 16-bit PCM and 32-bit float WAV files (mono or multi-channel, downmixed
to mono) and writes mono 16-bit PCM. Logic mirrors the helpers shipped with the
Sanas SDK examples but walks RIFF chunks properly so files with extra metadata
chunks (LIST/fact/...) are handled.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


@dataclass
class WavData:
    samples: list[float]  # mono, normalized to [-1.0, 1.0]
    sample_rate: int
    channels: int
    bits_per_sample: int

    @property
    def num_samples(self) -> int:
        return len(self.samples)

    @property
    def duration_seconds(self) -> float:
        return self.num_samples / self.sample_rate if self.sample_rate else 0.0


class WavError(ValueError):
    """Raised when a WAV file cannot be parsed or is unsupported."""


def read_wav(path: str) -> WavData:
    """Read a WAV file into mono float samples in ``[-1.0, 1.0]``."""
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise WavError(f"Not a valid RIFF/WAVE file: {path}")

        fmt_chunk: bytes | None = None
        data_chunk: bytes | None = None
        while True:
            chunk_header = fh.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
            payload = fh.read(chunk_size)
            if chunk_id == b"fmt ":
                fmt_chunk = payload
            elif chunk_id == b"data":
                data_chunk = payload
            # RIFF chunks are word-aligned: skip a pad byte after odd sizes.
            if chunk_size % 2 == 1:
                fh.read(1)
            if fmt_chunk is not None and data_chunk is not None:
                break

    if fmt_chunk is None or len(fmt_chunk) < 16:
        raise WavError(f"Missing/short 'fmt ' chunk in {path}")
    if data_chunk is None:
        raise WavError(f"Missing 'data' chunk in {path}")

    (audio_format, channels, sample_rate, _byte_rate,
     _block_align, bits_per_sample) = struct.unpack("<HHIIHH", fmt_chunk[:16])

    if channels < 1:
        raise WavError(f"Invalid channel count ({channels}) in {path}")

    if audio_format == 1 and bits_per_sample == 16:
        count = len(data_chunk) // 2
        interleaved = struct.unpack(f"<{count}h", data_chunk[: count * 2])
        floats = [s / 32768.0 for s in interleaved]
    elif audio_format in (3,) and bits_per_sample == 32:
        count = len(data_chunk) // 4
        floats = list(struct.unpack(f"<{count}f", data_chunk[: count * 4]))
    elif audio_format == 1 and bits_per_sample == 32:
        count = len(data_chunk) // 4
        interleaved = struct.unpack(f"<{count}i", data_chunk[: count * 4])
        floats = [s / 2147483648.0 for s in interleaved]
    else:
        raise WavError(
            f"Unsupported WAV format in {path}: audio_format={audio_format}, "
            f"bits_per_sample={bits_per_sample} (supported: 16-bit PCM, 32-bit float)"
        )

    mono = _downmix(floats, channels)
    return WavData(samples=mono, sample_rate=sample_rate,
                   channels=channels, bits_per_sample=bits_per_sample)


def _downmix(interleaved: list[float], channels: int) -> list[float]:
    if channels == 1:
        return interleaved
    frames = len(interleaved) // channels
    out = [0.0] * frames
    for i in range(frames):
        base = i * channels
        out[i] = sum(interleaved[base: base + channels]) / channels
    return out


def save_wav(path: str, samples: list[float], sample_rate: int) -> None:
    """Write mono float samples as a 16-bit PCM WAV file."""
    pcm = bytearray()
    clamp = max
    for sample in samples:
        clamped = clamp(-1.0, min(1.0, sample))
        pcm += struct.pack("<h", int(clamped * 32767.0))

    bits_per_sample = 16
    byte_rate = sample_rate * bits_per_sample // 8
    block_align = bits_per_sample // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16,
        1, 1, sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(pcm)


def synth_tone(sample_rate: int, duration_seconds: float,
               frequency: float = 220.0, amplitude: float = 0.2) -> list[float]:
    """Generate a sine tone — used by the benchmark tool when no WAV is given."""
    n = int(sample_rate * duration_seconds)
    two_pi_f = 2.0 * math.pi * frequency
    return [amplitude * math.sin(two_pi_f * (i / sample_rate)) for i in range(n)]
