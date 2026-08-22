<p align="center">
  <strong>🐜</strong>
</p>

<h1 align="center">AntNest</h1>

<p align="center">
  <strong>Local AI Operations Assistant</strong>
</p>

<p align="center">
  File management · System automation · Process isolation · Persistent memory
</p>

<p align="center">
  <a href="https://github.com/llxpy/AntNest/releases"><img src="https://img.shields.io/badge/Windows-v1.2.3-blue?logo=windows" alt="Windows"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12+-yellow?logo=python" alt="Python"></a>
</p>

---

## What is AntNest?

AntNest is a desktop AI assistant that operates directly on your local machine. Unlike cloud-based tools, it runs entirely on your hardware, manages your files, executes system commands, and automates routine tasks — all with process-level security.

**The queen thinks. The workers execute. Your system stays safe.**

---

## Capabilities

<table>
<tr>
<td width="50%">

### 📁 File Operations
- Read, write, search, and patch any local file
- Batch file processing across directories
- Content-aware file analysis
- Real-time file monitoring

</td>
<td width="50%">

### ⚡ System Automation
- Execute commands via isolated worker processes
- Automated system diagnostics
- Batch operations with task tracking
- Scheduled maintenance tasks

</td>
</tr>
<tr>
<td>

### 🧠 Persistent Memory
- Conversation history preserved across sessions
- Knowledge accumulation over time
- Task state tracking (pending → running → done → verified)
- Session auto-recovery

</td>
<td>

### 🔒 Security Architecture
- Process-level isolation for all operations
- Dangerous command filtering
- Temporary execution environments
- Instant resource cleanup

</td>
</tr>
</table>

---

## Architecture

```
              ┌─────────────────────┐
              │     Queen (LLM)     │
              │   Reasoning Layer   │
              │   Never Executes    │
              └──────────┬──────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
    ┌─────────┐    ┌─────────┐    ┌─────────┐
    │ Worker  │    │ Worker  │    │ Worker  │
    │   #1    │    │   #2    │    │   #3    │
    │ (temp)  │    │ (temp)  │    │ (temp)  │
    └─────────┘    └─────────┘    └─────────┘
         │               │               │
         └───────────────┼───────────────┘
                         │
                    ┌────▼────┐
                    │ Result  │
                    │ Return  │
                    └─────────┘
```

**Design Principles:**
- **Isolation** — Each operation runs in a disposable subprocess
- **Defense-in-depth** — Multiple security layers protect your system
- **State machine** — Every task is tracked from creation to completion
- **Memory persistence** — Knowledge accumulates across sessions

---

## Installation

### Windows Installer (Recommended)

Download `AntNest-Setup.exe` from [**Releases**](https://github.com/llxpy/AntNest/releases) — includes Python, uv, and all dependencies.

### Portable

```bash
git clone https://github.com/llxpy/AntNest.git
cd AntNest
uv run python prototype_antnest.py
```

### Direct Launch

Run `AntNest.exe` from the repository root — zero configuration required.

---

## Supported Providers

| Provider | Integration |
|----------|-------------|
| DeepSeek | Full support with reasoning |
| Kimi / Moonshot | Optimized parameters |
| MiniMax | Fast inference |
| OpenAI | Standard API |
| Ollama / Local | Full compatibility |

---

## How It Works

1. **You ask** — Natural language instruction via the desktop GUI
2. **Queen reasons** — LLM analyzes the request and plans execution
3. **Workers execute** — Isolated subprocesses handle the actual operations
4. **Results return** — Output captured and displayed
5. **Cleanup** — Temporary environments destroyed, system state preserved

---

## Security

AntNest implements defense-in-depth security:

| Layer | Protection |
|-------|------------|
| Instruction | LLM prompted to use safe commands only |
| Filter | Regex blocks destructive patterns |
| Isolation | Workers run in temporary directories |
| Cleanup | Processes killed, temp files deleted after each task |

> ⚠️ **Note**: Process-level isolation, not container sandboxing. See `antnest_clone_worker.py` for the complete filter list.

---

## Testing

```bash
python -m pytest tests/ -q
```

---

## Project Structure

```
AntNest/
├── AntNest.py                 # Core agent engine
├── prototype_antnest.py       # Desktop GUI entry
├── antnest_bridge.py          # GUI ↔ Python bridge
├── antnest_clone_worker.py    # Worker isolation system
├── antnest_session.py         # Session management
├── task_manager.py            # Task state machine
├── code_tools.py              # File operations toolkit
├── api_compat.py              # Multi-provider API layer
├── mcp_client.py              # MCP tool integration
├── phtmlwin.py                # Minimal GUI framework
├── installer/                 # Windows installer
├── tests/                     # Test suite
└── android/                   # Mobile companion (experimental)
```

---

## License

[MIT](LICENSE) — Copyright © 2026 LLXPY
