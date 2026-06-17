# Sanas SDK MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the
**Sanas Speech AI SDK** (`sanas_remote_sdk`) to MCP clients such as Claude Desktop,
Claude Code, and the Agent SDK.

The Sanas Speech AI SDK is a **server-hosted, real-time audio noise-cancellation /
voice-processing** SDK. A native *SDK Connector* streams audio to a hosted *AI Engine*
that runs the selected voice model. **It cleans audio — it does not transcribe speech
to text.**

## Tools

| Tool | What it does | Needs connector + creds? |
|------|--------------|:---:|
| `sanas_get_sdk_info` | Connector availability, host platform, configured defaults | no |
| `sanas_list_models` | Documented model catalog (e.g. `ST_NC3.0`, `ASR_NC3.0_VAD0.6`) | no |
| `sanas_validate_audio_config` | Validate model + sample rate, compute the 20 ms frame size | no |
| `sanas_generate_integration_snippet` | Generate runnable Python (`single` / `wav_file` / `multi`) | no |
| `sanas_verify_connection` | Init + load a model to READY end-to-end, then tear down | **yes** |
| `sanas_get_active_processor_count` | Active processors on the shared session | yes |
| `sanas_process_wav` | Denoise a WAV file → cleaned WAV + latency/RTF metrics | **yes** |
| `sanas_benchmark` | Concurrent load test → success rate, peak concurrency, latency | **yes** |

Resources: `sanas://models`, `sanas://status`.

## Install

Requires **Python ≥ 3.10**.

```bash
cd sanas-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -e ".[dev]" for tests
```

### Install the Sanas connector (required for the live tools)

The connector (`sanas_remote_sdk`) is a **private, platform-specific native wheel**
delivered in your Sanas onboarding package — it is not on PyPI. Install the wheel
matching your OS/arch and Python version into the **same** virtualenv:

```bash
pip install /path/to/sanas_remote_sdk-<ver>-<py>-<platform>.whl
```

> Supported platforms (per the SDK quickstart): **Ubuntu 22.04 x86-64** and
> **macOS arm64**, Python ≥ 3.10. The offline tools work without the connector;
> the live tools require the wheel for the host platform. `sanas_get_sdk_info`
> tells you whether the connector loaded on the current host.

## Configure

Credentials are read from environment variables (never passed through the model).
Copy `.env.example` to `.env` and fill in the values from your onboarding email,
or set them in your MCP client config (see `examples/claude_desktop_config.example.json`).

| Variable | Required | Default |
|----------|:---:|---------|
| `SANAS_REMOTE_ENDPOINT` | live tools | — |
| `SANAS_ACCOUNT_ID` | live tools | — |
| `SANAS_ACCOUNT_SECRET` | live tools | — |
| `SANAS_SECURE_MEDIA` | no | `true` |
| `SANAS_DEFAULT_MODEL` | no | `ST_NC3.0` |
| `SANAS_DEFAULT_SAMPLE_RATE` | no | `16000` |

## Run

```bash
sanas-mcp                 # serves over stdio
# or
python -m sanas_mcp
```

Then register it with your MCP client. Claude Code:

```bash
claude mcp add sanas-sdk -- sanas-mcp
```

## Notes & limitations

- **Audio only.** Output is cleaned audio; there is no speech-to-text. "Jobs" =
  batch WAV denoising; "usage" metrics = processing latency / real-time factor /
  concurrency from runs (the SDK has no billing API).
- **Sample rates:** 8 / 16 / 48 kHz, streamed in 20 ms frames.
- **Real-time streaming** isn't an MCP-native interaction, so the processing tools
  are batch (`sanas_process_wav`) and load-test (`sanas_benchmark`) shaped. For
  live mic/stream integration, use `sanas_generate_integration_snippet`.
- The model catalog is the documented set and may not match every account; confirm
  with `sanas_verify_connection`.

## Develop / test

```bash
pip install -e ".[dev]"
pytest            # offline tests — no connector or credentials required
```
