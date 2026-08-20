# AntNest

> An AI coding agent with a queen/worker architecture, desktop GUI, and process-level sandboxing.

AntNest is a desktop AI assistant that runs shell commands through isolated worker ants instead of executing them directly. The queen (LLM) thinks and plans; the workers execute in throwaway environments and are destroyed after each task.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-brightgreen.svg)](https://www.python.org/)

---

## Why Queen/Worker?

Traditional AI agents call the shell directly from the LLM process. One bad command poisons the environment. AntNest adds an isolation layer:

```
LLM reasoning -> spawn worker ant -> worker runs shell -> result returned -> worker destroyed
```

| | Traditional Agent | AntNest |
|---|---|---|
| Execution | In the agent process | In a throwaway subprocess |
| Environment | Commands pollute cwd | Worker cwd is a temp directory, deleted after use |
| Concurrency | Serial | Multiple workers can run in parallel |
| Security | LLM controls shell directly | LLM -> command filter -> process isolation -> destroy |

> **Disclaimer**: Process-level isolation, not a container sandbox. Safety is defense-in-depth: LLM instruction following -> command regex filter -> process isolation + destroy.

---

## Quick Start

### Option A: Windows Installer

Download `AntNest-Setup.exe` from [Releases](https://github.com/llxpy/AntNest/releases). It bundles Python, uv, and all dependencies.

### Option B: Portable (uv)

```bash
git clone https://github.com/llxpy/AntNest.git
cd AntNest
uv run python prototype_antnest.py
```

`uv` handles the virtual environment and dependencies automatically.

### Option C: Compiled Launcher

`AntNest.exe` in the repo root is a self-contained launcher (ps2exe). It auto-installs dependencies on first run and starts the desktop UI with zero console windows.

---

## Configuration

Copy `config.json.example` to `config.json` and fill in your API key:

```json
{
  "api": {
    "base_url": "https://api.deepseek.com/v1",
    "model_name": "deepseek-chat",
    "api_key": "sk-your-key-here"
  },
  "agent": {
    "max_depth": 2,
    "max_clones": { "0": 10, "1": 5, "2": 3 }
  }
}
```

No real keys are shipped in the repo. The installer provides an empty template.

### Multi-Model Support

AntNest auto-detects your API provider and adapts parameters:

| Provider | Behavior |
|---|---|
| DeepSeek | thinking_mode=auto, keeps reasoning_content |
| Kimi / Moonshot | temperature fixed to 1.0, thinking disabled, strict message keys |
| MiniMax | thinking disabled, /models check skipped |
| OpenAI | Default parameters |
| Local (Ollama etc.) | openai_compat profile |

---

## Architecture

```
        Queen (LLM)
  Reasoning / Memory / Decision
  Never touches Shell directly
     |        |        |
  spawn    spawn    spawn
     |        |        |
  Worker-1  Worker-2  Worker-3
  Run cmd   Run cmd   Run cmd
  Return    Return    Return
  Destroy   Destroy   Destroy
```

- **Queen** - LLM-powered core. Reasons, manages memory, spawns workers. Never executes commands.
- **Workers** - Isolated subprocess copies. Temporary directory, one command, result returned, destroyed.
- **Bridge** - Python <-> WebView2 GUI bridge. Settings, model detection, UI updates.
- **Skills** - Pluggable knowledge modules (.baim files or SKILL.md directories).
- **MCP** - Model Context Protocol client for external tool integrations.

---

## Project Structure

```
AntNest/
  AntNest.py                 # Full agent (standalone)
  prototype_antnest.py       # GUI entry point (pywebview)
  antnest_bridge.py          # Python <-> WebView2 bridge
  antnest_clone_worker.py    # Worker ant spawn/isolation
  antnest_session.py         # Conversation session management
  code_tools.py              # File read/write/patch tools
  mcp_client.py              # MCP stdio client
  api_compat.py              # Multi-provider API compatibility
  skills_loader.py           # Skills plugin loader
  phtmlwin.py                # Minimal pywebview GUI framework
  installer/                 # Inno Setup + PowerShell bootstrapper
  tests/                     # pytest test suite
  AntNest.exe                # Compiled launcher (ps2exe)
  config.json.example        # Template (no real keys)
```

---

## Security Model

1. **LLM instruction following** - The model is prompted to use safe commands only.
2. **Dangerous command regex filter** - Hardcoded blocks for destructive patterns.
3. **Process isolation** - Workers run in isolated subprocesses with temporary directories.
4. **Instant destroy** - Output captured, process killed, temp directory deleted.

Defense-in-depth, not a guarantee. See `antnest_clone_worker.py` for the exact filter list.

---

## Running Tests

```bash
python -m pytest tests/ -q
```

---

## License

[MIT](LICENSE) - Copyright (c) 2026 LLXPY
