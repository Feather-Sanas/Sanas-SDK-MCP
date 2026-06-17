"""Ready-to-run Python integration snippets for the Sanas Remote SDK.

Generated from the real SDK API so users can copy/paste a working starting point
for a given model + sample rate. Credentials are read from environment variables
in the generated code (never hard-coded).
"""

from __future__ import annotations

MODES = ("single", "wav_file", "multi")

_HEADER = '''"""Sanas Remote SDK example ({mode}) — model={model}, sample_rate={sample_rate} Hz.

Requires the `sanas_remote_sdk` connector installed for your platform, plus the
environment variables SANAS_REMOTE_ENDPOINT, SANAS_ACCOUNT_ID, SANAS_ACCOUNT_SECRET.
"""
import os
import threading
import sanas_remote_sdk

ENDPOINT = os.environ["SANAS_REMOTE_ENDPOINT"]
ACCOUNT_ID = os.environ["SANAS_ACCOUNT_ID"]
ACCOUNT_SECRET = os.environ["SANAS_ACCOUNT_SECRET"]
MODEL = "{model}"
SAMPLE_RATE = {sample_rate}
SECURE_MEDIA = {secure_media}


def make_sdk():
    sdk = sanas_remote_sdk.CreateRemoteSDK()
    params = sanas_remote_sdk.InitParams()
    params.remoteEndpoint = ENDPOINT
    params.accountId = ACCOUNT_ID
    params.accountSecret = ACCOUNT_SECRET
    params.secureMedia = SECURE_MEDIA
    if sdk.Initialize(params) != sanas_remote_sdk.InitSDKResult.SUCCESS:
        raise RuntimeError("SDK Initialize failed")
    return sdk


def create_ready_processor(sdk):
    ready = threading.Event()
    failed = threading.Event()

    def on_state(state, reason):
        if state == sanas_remote_sdk.ProcessorState.READY:
            ready.set()
        elif state in (sanas_remote_sdk.ProcessorState.FAILED,
                       sanas_remote_sdk.ProcessorState.DISCONNECTED):
            failed.set()

    params = sanas_remote_sdk.AudioParams()
    params.modelName = MODEL
    params.sampleRate = SAMPLE_RATE
    processor, result = sdk.CreateAudioProcessor(params, on_state)
    if result != sanas_remote_sdk.CreateProcessorResult.SUCCESS:
        raise RuntimeError(f"CreateAudioProcessor failed: {{result}}")
    if not ready.wait(timeout=15):
        raise RuntimeError("processor did not reach READY")
    return processor
'''

_SINGLE = '''

def main():
    sdk = make_sdk()
    processor = create_ready_processor(sdk)
    chunk = int(SAMPLE_RATE * 0.020)  # 20 ms frames
    try:
        # Replace this loop with your real-time audio source.
        for _ in range(50):
            frame = [0.0] * chunk          # <- feed mic/stream samples here
            cleaned = processor.ProcessSamples(frame)
            # ... consume `cleaned` (list of float samples) ...
    finally:
        sdk.DestroyAudioProcessor(processor)
        sdk.Shutdown()


if __name__ == "__main__":
    main()
'''

_WAV_FILE = '''
import wave
import struct


def read_mono_floats(path):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
        ints = struct.unpack(f"<{{len(frames)//2}}h", frames)
        chans = w.getnchannels()
        if chans > 1:
            ints = [sum(ints[i:i+chans]) / chans for i in range(0, len(ints), chans)]
        return [s / 32768.0 for s in ints], rate


def write_mono_floats(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767))
                                for s in samples))


def main(input_path="input.wav", output_path="output_clean.wav"):
    samples, rate = read_mono_floats(input_path)
    assert rate == SAMPLE_RATE, f"file is {{rate}} Hz, expected {{SAMPLE_RATE}}"
    sdk = make_sdk()
    processor = create_ready_processor(sdk)
    chunk = int(SAMPLE_RATE * 0.020)
    out = []
    try:
        for i in range(0, len(samples), chunk):
            frame = samples[i:i + chunk]
            if len(frame) < chunk:
                frame += [0.0] * (chunk - len(frame))
            out.extend(processor.ProcessSamples(frame))
    finally:
        sdk.DestroyAudioProcessor(processor)
        sdk.Shutdown()
    write_mono_floats(output_path, out, SAMPLE_RATE)
    print(f"wrote {{output_path}} ({{len(out)}} samples)")


if __name__ == "__main__":
    main()
'''

_MULTI = '''
from concurrent.futures import ThreadPoolExecutor


def process_one_stream(sdk, call_id):
    processor = create_ready_processor(sdk)
    chunk = int(SAMPLE_RATE * 0.020)
    try:
        for _ in range(50):
            frame = [0.0] * chunk          # <- feed this stream's samples here
            processor.ProcessSamples(frame)
    finally:
        sdk.DestroyAudioProcessor(processor)


def main(num_streams=4):
    sdk = make_sdk()  # one SDK instance is shared across all streams
    try:
        with ThreadPoolExecutor(max_workers=num_streams) as pool:
            for future in [pool.submit(process_one_stream, sdk, i)
                           for i in range(num_streams)]:
                future.result()
        print("active processors:", sdk.GetActiveProcessorCount())
    finally:
        sdk.Shutdown()


if __name__ == "__main__":
    main()
'''

_BODIES = {"single": _SINGLE, "wav_file": _WAV_FILE, "multi": _MULTI}


def generate(model: str, sample_rate: int, mode: str = "single",
             secure_media: bool = True) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {MODES}")
    header = _HEADER.format(mode=mode, model=model, sample_rate=sample_rate,
                            secure_media=secure_media)
    return header + _BODIES[mode]
