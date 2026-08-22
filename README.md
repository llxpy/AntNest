<p align="center">
  <strong>🐜</strong>
</p>

<h1 align="center">AntNest</h1>

<p align="center">
  <strong>Local AI Operations Platform</strong>
</p>

<p align="center">
  Full system access · Complete shell integration · Process-level isolation · Persistent intelligence
</p>

<p align="center">
  <a href="https://github.com/llxpy/AntNest/releases"><img src="https://img.shields.io/badge/Windows-v1.2.3-blue?logo=windows" alt="Windows"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12+-yellow?logo=python" alt="Python"></a>
</p>

---

## What is AntNest?

AntNest is a local AI operations platform that gives you complete control over your Windows environment through natural language. Unlike cloud-based AI tools, it runs entirely on your hardware with **full shell access** — network diagnostics, system monitoring, file operations, process management, and virtually any command-line task.

**Your AI. Your machine. Your rules.**

---

## Core Capabilities

<table>
<tr>
<td width="50%">

### 🌐 Network Operations
- Full network stack access
- `ping`, `traceroute`, `nslookup`, `netstat`
- WiFi/Bluetooth scanning and management
- Network interface configuration
- Bandwidth monitoring
- Connection diagnostics
- Port scanning and analysis

</td>
<td width="50%">

### 🖥 System Administration
- Complete process management
- Service control and monitoring
- System information retrieval
- Disk and memory management
- Registry operations
- Scheduled task management
- Environment variable control

</td>
</tr>
<tr>
<td>

### 📁 File Operations
- Read, write, search, patch any file
- Batch operations across directories
- Content-aware analysis
- Real-time monitoring
- Archive management
- Symbolic link handling
- **Encryption/Decryption** — AES, RSA, base64
- **File integrity** — checksums, hash verification

</td>
<td>

### 🎮 Game Optimization
- Config file modification
- Game settings optimization
- Performance tuning
- Save file management
- Mod installation support
- Launch parameter optimization

</td>
</tr>
<tr>
<td>

### 💾 Disk Management
- Partition analysis and reporting
- Disk space monitoring
- Cleanup and optimization
- Backup verification
- Drive health monitoring
- Storage allocation analysis

</td>
<td>

### 🔧 Hardware Inspection
- CPU temperature and usage
- GPU information and monitoring
- RAM utilization analysis
- Storage health (S.M.A.R.T.)
- Network adapter details
- Peripheral device management

</td>
</tr>
<tr>
<td>

### ⚡ Command Execution
- **Full shell access** — PowerShell, CMD, Git Bash
- Worker isolation for safe execution
- Parallel task processing
- Output capture and parsing
- Error handling and recovery
- Environment variable management

</td>
<td>

### 🧠 Persistent Intelligence
- Cross-session memory
- Knowledge accumulation
- Task state tracking
- Context preservation
- Learning from interactions
- Session recovery

</td>
</tr>
<tr>
<td>

### 🔒 Security Architecture
- Process-level isolation
- Command filtering (configurable)
- Temporary execution environments
- Instant resource cleanup
- Audit logging
- Permission controls

</td>
<td>

### 📊 Monitoring & Diagnostics
- Real-time system monitoring
- Performance baseline tracking
- Anomaly detection
- Resource usage alerts
- Health check automation
- Trend analysis

</td>
</tr>
</table>

---

## Shell Integration

AntNest provides **complete shell access** — not a sandboxed subset, but the full power of your command line:

```powershell
# Network diagnostics
Test-NetConnection -ComputerName google.com -Port 443
Get-NetAdapter | Select-Object Name, Status, LinkSpeed
netstat -an | Select-String "ESTABLISHED"

# System monitoring
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10
Get-WmiObject Win32_OperatingSystem | Select-Object FreePhysicalMemory
Get-Service | Where-Object {$_.Status -eq "Running"}

# File operations with encryption
$plainText = "sensitive data"
$encrypted = $plainText | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString
$encrypted | Out-File "encrypted.txt"
$secureString = Get-Content "encrypted.txt" | ConvertTo-SecureString
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureString)
$plainText = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

# Hardware inspection
Get-WmiObject Win32_Processor | Select-Object Name, LoadPercentage
Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM
Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus

# Disk management
Get-Partition | Select-Object DriveLetter, Size, Type
Get-Volume | Select-Object DriveLetter, FileSystemLabel, SizeRemaining, Size
Get-Disk | Select-Object Number, FriendlyName, PartitionStyle, Size

# Game config optimization
$configPath = "$env:LOCALAPPDATA\Game\Config.ini"
Get-Content $configPath | ForEach-Object {
    if ($_ -match "Quality=(\d+)") {
        $_ -replace "Quality=\d+", "Quality=3"
    } else { $_ }
} | Set-Content $configPath

# Process management
Stop-Process -Name "notepad" -Force
Start-Process "powershell" -ArgumentList "-NoExit", "-Command", "& {Get-Help}"
```

**The queen decides what to run. Workers execute it in isolation. Your system stays clean.**

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         USER INTERFACE          │
                    │      Desktop GUI (pywebview)     │
                    └───────────────┬─────────────────┘
                                    │
                    ┌───────────────▼─────────────────┐
                    │          QUEEN (LLM)            │
                    │    Reasoning · Planning · Memory │
                    │    Never Executes Directly      │
                    └───────────────┬─────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼─────────┐ ┌─────────▼─────────┐ ┌─────────▼─────────┐
    │     WORKER #1     │ │     WORKER #2     │ │     WORKER #3     │
    │  Isolated Process │ │  Isolated Process │ │  Isolated Process │
    │  Temp Directory   │ │  Temp Directory   │ │  Temp Directory   │
    │  Single Command   │ │  Single Command   │ │  Single Command   │
    └─────────┬─────────┘ └─────────┬─────────┘ └─────────┬─────────┘
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    │
                    ┌───────────────▼─────────────────┐
                    │        RESULT AGGREGATION       │
                    │    Capture · Parse · Return      │
                    └───────────────┬─────────────────┘
                                    │
                    ┌───────────────▼─────────────────┐
                    │          CLEANUP                 │
                    │   Kill Process · Delete Temp     │
                    │   Preserve Results Only          │
                    └─────────────────────────────────┘
```

**Design Principles:**

| Principle | Implementation |
|-----------|----------------|
| **Isolation** | Each command runs in a disposable subprocess |
| **Safety** | Multiple security layers protect your system |
| **Tracking** | Every task has a lifecycle: pending → running → done → verified |
| **Memory** | Knowledge persists across sessions |
| **Power** | Full shell access, not a restricted subset |

---

## Installation

### Option 1: Windows Installer (Recommended)

Download `AntNest-Setup.exe` from [**Releases**](https://github.com/llxpy/AntNest/releases).

Includes:
- Python 3.12+ runtime
- All dependencies
- Desktop shortcut
- Start menu integration

### Option 2: Portable

```bash
git clone https://github.com/llxpy/AntNest.git
cd AntNest
uv run python prototype_antnest.py
```

### Option 3: Direct Launch

Run `AntNest.exe` from the repository root — zero configuration required.

---

## Supported AI Providers

| Provider | Status | Notes |
|----------|--------|-------|
| DeepSeek | ✅ Full | Reasoning support, thinking_mode=auto |
| Kimi / Moonshot | ✅ Full | Optimized parameters |
| MiniMax | ✅ Full | Fast inference |
| OpenAI | ✅ Full | Standard API |
| Ollama / Local | ✅ Full | Any OpenAI-compatible endpoint |

---

## How It Works

```
1.  USER ASKS         "Check my network connection and list running services"
         ↓
2.  QUEEN REASONS     Analyzes request → Plans execution → Decides commands
         ↓
3.  WORKERS EXECUTE   Isolated subprocess runs: Test-NetConnection, Get-Service
         ↓
4.  RESULTS RETURN    Output captured, parsed, formatted
         ↓
5.  CLEANUP           Temporary environments destroyed
         ↓
6.  USER SEES         Clean, formatted response with actionable information
```

---

## Security Model

AntNest implements **defense-in-depth** security:

| Layer | Protection | Configurable |
|-------|------------|--------------|
| **LLM Prompt** | Model instructed to use safe commands | ❌ |
| **Command Filter** | Regex blocks destructive patterns | ✅ |
| **Process Isolation** | Workers run in temp directories | ❌ |
| **Resource Cleanup** | Processes killed, temp files deleted | ❌ |
| **Audit Logging** | All commands logged for review | ✅ |

> ⚠️ **Note**: Process-level isolation, not container sandboxing. See `antnest_clone_worker.py` for the complete filter list and configuration options.

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

## Use Cases

| Scenario | Example Commands |
|----------|------------------|
| **Network Diagnostics** | `ping`, `tracert`, `nslookup`, `netstat`, `ipconfig` |
| **System Monitoring** | `Get-Process`, `Get-Service`, `Get-WmiObject` |
| **File Management** | `Get-ChildItem`, `Copy-Item`, `Select-String` |
| **File Encryption** | `ConvertTo-SecureString`, `AES`, `RSA` operations |
| **Process Control** | `Start-Process`, `Stop-Process`, `Get-Process` |
| **Service Management** | `Get-Service`, `Start-Service`, `Restart-Service` |
| **Registry Operations** | `Get-ItemProperty`, `Set-ItemProperty` |
| **Scheduled Tasks** | `Get-ScheduledTask`, `Register-ScheduledTask` |
| **Hardware Inspection** | `Get-WmiObject Win32_Processor`, `Get-PhysicalDisk` |
| **Disk Management** | `Get-Partition`, `Get-Volume`, `Get-Disk` |
| **Game Optimization** | Config modification, performance tuning |
| **Backup & Recovery** | File verification, checksum validation |

---

## License

[MIT](LICENSE) — Copyright © 2026 LLXPY
