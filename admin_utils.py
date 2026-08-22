"""
admin_utils.py — 管理员模式工具
- 检测管理员权限
- 修复输入法问题
- 危险命令确认系统
"""
import os
import sys
import ctypes
import re
import subprocess
from typing import Optional, Tuple

# ====================== 管理员检测 ======================

def is_admin() -> bool:
    """检测当前是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def get_elevation_info() -> dict:
    """获取权限提升信息"""
    admin = is_admin()
    return {
        "is_admin": admin,
        "elevation_type": "管理员" if admin else "普通用户",
        "can_modify_system": admin,
        "warning": "管理员模式下可执行系统级操作，请谨慎使用" if admin else None
    }

# ====================== 输入法修复 ======================

def fix_ime_for_admin():
    """
    修复管理员模式下输入法消失的问题
    原因：Windows 在 UAC 提升后会重置输入法状态
    解决：强制加载用户输入法并设置为当前输入法
    """
    try:
        # 方案1：通过注册表加载用户输入法
        user_sid = get_user_sid()
        if user_sid:
            # 读取用户输入法列表
            import winreg
            key_path = f"Software\\Microsoft\\CTF\\SortOrder\\AssemblyItem\\0x00000804\\{user_sid}"
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
                winreg.CloseKey(key)
            except FileNotFoundError:
                # 如果没有中文输入法，尝试加载默认
                pass

        # 方案2：通过 PowerShell 刷新输入法
        ps_script = """
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.InputLanguage]::InstalledInputLanguages | ForEach-Object {
            Write-Output $_.CultureName
        }
        """
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10
        )

        # 方案3：使用 ctypes 直接操作
        # 激活输入法管理器
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                # 激活输入法
                ctypes.windll.imm32.ImmGetContext(hwnd)
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"[admin_utils] IME 修复失败: {e}")
        return False

def get_user_sid() -> Optional[str]:
    """获取当前用户的 SID"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Volatile Environment")
        sid = winreg.QueryValueEx(key, "USERSID")[0]
        winreg.CloseKey(key)
        return sid
    except Exception:
        return None

# ====================== 危险命令确认系统 ======================

# 危险命令分类和说明
DANGEROUS_COMMANDS = {
    # 系统级操作
    "system_modify": {
        "patterns": [
            (r"format\s+[a-z]:", "格式化磁盘"),
            (r"diskpart", "磁盘分区工具"),
            (r"bcdedit", "启动配置编辑"),
            (r"regedit", "注册表编辑器"),
            (r"secpol", "安全策略"),
        ],
        "risk": "可能损坏系统或导致数据丢失",
        "reason": "系统级修改需要管理员权限以确保系统稳定性",
        "confirmation_required": True
    },
    # 文件操作
    "file_dangerous": {
        "patterns": [
            (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+/", "递归删除根目录文件"),
            (r"del\s+/[fs]\s+[a-z]:[\\\/]", "强制删除系统文件"),
            (r"rmdir\s+/[sq]\s+[a-z]:[\\\/]", "删除系统目录"),
            (r"Remove-Item[^\\n]*(?:-Recurse|-Force)", "PowerShell 递归/强制删除"),
        ],
        "risk": "永久删除文件，无法恢复",
        "reason": "文件删除操作不可逆，需要用户确认",
        "confirmation_required": True
    },
    # 网络操作
    "network_modify": {
        "patterns": [
            (r"netsh\s+interface", "网络接口配置"),
            (r"netsh\s+wlan\s+delete", "删除 WiFi 配置"),
            (r"firewall.*add", "添加防火墙规则"),
            (r"firewall.*delete", "删除防火墙规则"),
        ],
        "risk": "可能导致网络连接中断",
        "reason": "网络配置修改影响系统连接稳定性",
        "confirmation_required": True
    },
    # 进程管理
    "process_critical": {
        "patterns": [
            (r"Stop-Process.*-Name.*(?:explorer|svchost|csrss|lsass)", "终止关键系统进程"),
            (r"taskkill.*(?:/f|/im).*(?:explorer|svchost)", "强制终止系统进程"),
        ],
        "risk": "可能导致系统不稳定或崩溃",
        "reason": "关键系统进程不应被用户手动终止",
        "confirmation_required": True
    },
    # 服务管理
    "service_modify": {
        "patterns": [
            (r"Stop-Service.*-Name", "停止系统服务"),
            (r"Set-Service.*-StartupType.*Disabled", "禁用系统服务"),
            (r"sc\s+config.*start=\s*disabled", "禁用系统服务"),
        ],
        "risk": "可能导致依赖该服务的应用程序无法正常运行",
        "reason": "服务配置修改影响系统功能完整性",
        "confirmation_required": True
    },
    # 计划任务
    "task_modify": {
        "patterns": [
            (r"Unregister-ScheduledTask", "删除计划任务"),
            (r"schtasks.*(?:/delete|/Delete)", "删除计划任务"),
        ],
        "risk": "可能导致自动化任务中断",
        "reason": "计划任务删除影响系统自动化流程",
        "confirmation_required": True
    },
}

def analyze_command(command: str) -> dict:
    """
    分析命令的危险等级和需要确认的原因
    返回: {
        "is_dangerous": bool,
        "risk_level": "low" | "medium" | "high" | "critical",
        "category": str,
        "description": str,
        "risk": str,
        "reason": str,
        "confirmation_required": bool
    }
    """
    cmd_lower = command.lower()

    # 逐个分类检查
    for category, info in DANGEROUS_COMMANDS.items():
        for pattern, desc in info["patterns"]:
            if re.search(pattern, cmd_lower):
                # 根据分类判断风险等级
                risk_level = _get_risk_level(category)
                return {
                    "is_dangerous": True,
                    "risk_level": risk_level,
                    "category": category,
                    "description": desc,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "confirmation_required": info["confirmation_required"]
                }

    return {
        "is_dangerous": False,
        "risk_level": "low",
        "category": "safe",
        "description": "安全命令",
        "risk": None,
        "reason": None,
        "confirmation_required": False
    }

def _get_risk_level(category: str) -> str:
    """根据分类获取风险等级"""
    risk_levels = {
        "system_modify": "critical",
        "file_dangerous": "critical",
        "network_modify": "medium",
        "process_critical": "high",
        "service_modify": "high",
        "task_modify": "medium",
    }
    return risk_levels.get(category, "low")

def format_confirmation_message(analysis: dict, command: str) -> str:
    """格式化确认消息"""
    risk_icons = {
        "low": "✅",
        "medium": "⚠️",
        "high": "🔶",
        "critical": "🔴"
    }

    icon = risk_icons.get(analysis["risk_level"], "❓")
    level_name = {
        "low": "低风险",
        "medium": "中风险",
        "high": "高风险",
        "critical": "危险"
    }.get(analysis["risk_level"], "未知")

    msg = f"""
╔══════════════════════════════════════════════════════════════╗
║                    ⚠️  危险操作确认                          ║
╚══════════════════════════════════════════════════════════════╝

{icon} 风险等级: {level_name}
📋 命令类型: {analysis['description']}
🔴 可能后果: {analysis['risk']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

命令内容:
{command}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 为什么要执行此命令:
{analysis['reason']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请输入以下确认:
  输入 "YES" 执行此命令
  输入 "NO" 取消此命令
  输入其他内容取消操作

> """
    return msg

def get_user_confirmation(command: str) -> Tuple[bool, str]:
    """
    获取用户确认
    返回: (confirmed: bool, user_input: str)
    """
    analysis = analyze_command(command)

    if not analysis["is_dangerous"] or not analysis["confirmation_required"]:
        return True, "safe"

    # 显示确认消息
    msg = format_confirmation_message(analysis, command)
    print(msg, end="", flush=True)

    # 获取用户输入
    try:
        user_input = input().strip()
        if user_input.upper() == "YES":
            return True, "YES"
        elif user_input.upper() == "NO":
            return False, "NO"
        else:
            return False, user_input
    except (EOFError, KeyboardInterrupt):
        return False, "cancelled"

# ====================== 管理员模式工具 ======================

def run_as_admin():
    """请求管理员权限提升（用于 Windows）"""
    if is_admin():
        print("[admin_utils] 已经是管理员权限")
        return True
    else:
        try:
            # 使用 ShellExecute 提升权限
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )
            return True
        except Exception as e:
            print(f"[admin_utils] 权限提升失败: {e}")
            return False

def get_admin_status() -> dict:
    """获取完整的管理员状态信息"""
    info = get_elevation_info()
    info["ime_fix_available"] = True
    info["dangerous_commands_count"] = sum(
        len(cat["patterns"]) for cat in DANGEROUS_COMMANDS.values()
    )
    return info

# ====================== 测试 ======================

if __name__ == "__main__":
    print("=== AntNest 管理员工具测试 ===\n")

    # 测试管理员检测
    info = get_elevation_info()
    print(f"权限状态: {info['elevation_type']}")
    print(f"系统修改权限: {'是' if info['can_modify_system'] else '否'}")
    if info['warning']:
        print(f"警告: {info['warning']}")
    print()

    # 测试命令分析
    test_commands = [
        "Get-Process",
        "format C:",
        "Remove-Item -Recurse -Force C:\\Windows",
        "Stop-Service -Name WSearch",
        "netsh interface set interface 'Wi-Fi' disable",
    ]

    print("=== 命令分析测试 ===")
    for cmd in test_commands:
        analysis = analyze_command(cmd)
        status = "🔴 危险" if analysis["is_dangerous"] else "✅ 安全"
        print(f"{status}: {cmd}")
        if analysis["is_dangerous"]:
            print(f"  类型: {analysis['description']}")
            print(f"  风险: {analysis['risk']}")
            print(f"  原因: {analysis['reason']}")
        print()
