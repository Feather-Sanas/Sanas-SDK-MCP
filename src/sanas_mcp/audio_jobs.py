"""Batch and benchmark jobs built on the shared SDK session.

These wrap the streaming SDK in request/response shapes that fit MCP:
* ``process_wav`` — denoise a whole WAV file and write a cleaned WAV, returning
  throughput/latency metrics.
* ``benchmark`` — drive N concurrent "calls" at a controlled rate against the AI
  Engine and report success rate, peak concurrency, and latency stats.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import wav_utils
from .models import SUPPORTED_SAMPLE_RATES, chunk_size_samples
from .sdk_adapter import SanasError, _create_ready_processor, manager

# Safety caps so a single tool call cannot launch a runaway load test.
MAX_BENCHMARK_CALLS = 1000
MAX_BENCHMARK_CONCURRENCY = 64


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, round((pct / 100.0) * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _latency_stats(latencies_ms: list[float]) -> dict:
    if not latencies_ms:
        return {"count": 0}
    ordered = sorted(latencies_ms)
    return {
        "count": len(ordered),
        "avg_ms": round(sum(ordered) / len(ordered), 3),
        "p50_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def process_wav(config, input_path: str, output_path: str, model: str,
                expected_sample_rate: int | None = None,
                simulate_realtime: bool = False,
                ready_timeout_s: float = 15.0) -> dict:
    """Run ``input_path`` through the AI Engine and write the cleaned WAV."""
    wav = wav_utils.read_wav(input_path)
    if wav.sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise SanasError(
            stage="validate_input",
            reason=(f"input sample rate {wav.sample_rate} Hz is not supported "
                    f"(supported: {list(SUPPORTED_SAMPLE_RATES)})"),
        )
    if expected_sample_rate is not None and wav.sample_rate != expected_sample_rate:
        raise SanasError(
            stage="validate_input",
            reason=(f"input sample rate {wav.sample_rate} Hz does not match "
                    f"expected_sample_rate {expected_sample_rate} Hz"),
        )

    sdk, mod = manager.ensure(config)
    processor, time_to_ready = _create_ready_processor(
        sdk, mod, model, wav.sample_rate, ready_timeout_s)

    chunk = chunk_size_samples(wav.sample_rate)
    output: list[float] = []
    latencies_ms: list[float] = []
    samples = wav.samples

    wall_start = time.time()
    next_chunk_time = wall_start
    try:
        for i in range(0, len(samples), chunk):
            frame = samples[i:i + chunk]
            if len(frame) < chunk:
                frame = frame + [0.0] * (chunk - len(frame))
            t0 = time.time()
            out_frame = processor.ProcessSamples(frame)
            latencies_ms.append((time.time() - t0) * 1000.0)
            output.extend(out_frame)
            if simulate_realtime:
                next_chunk_time += 0.020
                delay = next_chunk_time - time.time()
                if delay > 0:
                    time.sleep(delay)
    finally:
        sdk.DestroyAudioProcessor(processor)

    wall_seconds = time.time() - wall_start
    wav_utils.save_wav(output_path, output, wav.sample_rate)

    input_duration = wav.duration_seconds
    return {
        "ok": True,
        "input_path": input_path,
        "output_path": output_path,
        "model": model,
        "sample_rate": wav.sample_rate,
        "input_channels": wav.channels,
        "input_samples": wav.num_samples,
        "output_samples": len(output),
        "input_duration_seconds": round(input_duration, 3),
        "processing_wall_seconds": round(wall_seconds, 3),
        "real_time_factor": round(input_duration / wall_seconds, 3) if wall_seconds else None,
        "time_to_ready_seconds": round(time_to_ready, 3),
        "chunk_count": len(latencies_ms),
        "per_chunk_latency": _latency_stats(latencies_ms),
        "simulated_realtime": simulate_realtime,
    }


class _Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = 0
        self.completed = 0
        self.failed = 0
        self.active = 0
        self.peak_concurrent = 0
        self.ready_times: list[float] = []
        self.errors: dict[str, int] = {}

    def start(self) -> None:
        with self.lock:
            self.started += 1
            self.active += 1
            self.peak_concurrent = max(self.peak_concurrent, self.active)

    def finish(self, ok: bool, ready_time: float | None = None,
               error: str | None = None) -> None:
        with self.lock:
            self.active -= 1
            if ok:
                self.completed += 1
                if ready_time is not None:
                    self.ready_times.append(ready_time)
            else:
                self.failed += 1
                if error:
                    self.errors[error] = self.errors.get(error, 0) + 1


def benchmark(config, model: str, sample_rate: int, max_calls: int,
              concurrent_limit: int, call_rate: float, rate_period_ms: int,
              duration_s: float, input_wav: str | None = None,
              ready_timeout_s: float = 15.0) -> dict:
    """Drive concurrent calls at a controlled rate and report aggregate stats."""
    if sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise SanasError(stage="validate", reason=f"unsupported sample_rate {sample_rate}")

    capped_note = []
    if max_calls > MAX_BENCHMARK_CALLS:
        capped_note.append(f"max_calls capped at {MAX_BENCHMARK_CALLS}")
        max_calls = MAX_BENCHMARK_CALLS
    if concurrent_limit > MAX_BENCHMARK_CONCURRENCY:
        capped_note.append(f"concurrent_limit capped at {MAX_BENCHMARK_CONCURRENCY}")
        concurrent_limit = MAX_BENCHMARK_CONCURRENCY
    max_calls = max(1, max_calls)
    concurrent_limit = max(1, concurrent_limit)

    # Prepare a looped audio buffer of the requested duration.
    if input_wav:
        wav = wav_utils.read_wav(input_wav)
        base = wav.samples
        src_rate = wav.sample_rate
    else:
        base = wav_utils.synth_tone(sample_rate, min(duration_s, 5.0))
        src_rate = sample_rate
    if src_rate != sample_rate and input_wav:
        raise SanasError(stage="validate",
                         reason=f"input_wav rate {src_rate} != sample_rate {sample_rate}")
    needed = int(sample_rate * duration_s)
    if base:
        audio = [base[i % len(base)] for i in range(needed)]
    else:
        audio = [0.0] * needed

    sdk, mod = manager.ensure(config)
    chunk = chunk_size_samples(sample_rate)
    stats = _Stats()

    def one_call() -> None:
        stats.start()
        try:
            processor, ready = _create_ready_processor(
                sdk, mod, model, sample_rate, ready_timeout_s)
        except SanasError as exc:
            stats.finish(ok=False, error=exc.result or exc.stage)
            return
        try:
            next_time = time.time()
            for i in range(0, len(audio), chunk):
                frame = audio[i:i + chunk]
                if len(frame) < chunk:
                    frame = frame + [0.0] * (chunk - len(frame))
                processor.ProcessSamples(frame)
                next_time += 0.020
                delay = next_time - time.time()
                if delay > 0:
                    time.sleep(delay)
            stats.finish(ok=True, ready_time=ready)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            stats.finish(ok=False, error=type(exc).__name__)
        finally:
            try:
                sdk.DestroyAudioProcessor(processor)
            except Exception:
                pass

    interval = (rate_period_ms / 1000.0) / call_rate if call_rate > 0 else 0.0
    semaphore = threading.Semaphore(concurrent_limit)
    wall_start = time.time()

    with ThreadPoolExecutor(max_workers=concurrent_limit) as executor:
        next_spawn = time.time()
        for _ in range(max_calls):
            now = time.time()
            if now < next_spawn:
                time.sleep(next_spawn - now)
            semaphore.acquire()

            def wrapped() -> None:
                try:
                    one_call()
                finally:
                    semaphore.release()

            executor.submit(wrapped)
            next_spawn += interval
        executor.shutdown(wait=True)

    wall_seconds = time.time() - wall_start
    total_finished = stats.completed + stats.failed
    return {
        "ok": True,
        "model": model,
        "sample_rate": sample_rate,
        "config": {
            "max_calls": max_calls,
            "concurrent_limit": concurrent_limit,
            "call_rate": call_rate,
            "rate_period_ms": rate_period_ms,
            "call_duration_seconds": duration_s,
            "audio_source": input_wav or "synthesized_tone",
        },
        "results": {
            "calls_started": stats.started,
            "completed": stats.completed,
            "failed": stats.failed,
            "success_rate_pct": round(stats.completed / total_finished * 100, 1) if total_finished else 0.0,
            "peak_concurrent": stats.peak_concurrent,
            "errors": stats.errors,
            "time_to_ready": _latency_stats([t * 1000 for t in stats.ready_times]),
            "wall_seconds": round(wall_seconds, 2),
        },
        "notes": capped_note,
    }
