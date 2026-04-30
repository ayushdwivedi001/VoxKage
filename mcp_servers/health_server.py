"""
MCP Server: VoxKage System Health Monitor

VoxKage acts as the doctor of its own hardware — monitoring, diagnosing, and cleaning
the system it lives on.

Tools:
  - health_check()         : Full system vitals (CPU, RAM, GPU, battery, temps, disk)
  - get_processes()        : Running processes by CPU or memory usage
  - get_startup_items()    : Programs that run at Windows startup
  - scan_junk_files()      : Find temp/cache/junk files and their sizes
  - clean_junk_files()     : Delete junk files by category (with confirmation)
  - get_security_status()  : Windows Defender / security state

Run standalone: python mcp_servers/health_server.py
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import winreg
from datetime import datetime
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-health")

import psutil


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"

def _ps(cmd: str) -> str:
    """Run a PowerShell command and return stdout."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=15
        )
        return r.stdout.strip()
    except Exception as e:
        return f"[PS Error: {e}]"

def _bar(pct: float, width: int = 20) -> str:
    filled = int(width * pct / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:.1f}%"


# ── GPU info via PowerShell WMI ───────────────────────────────────────────────

def _gpu_info() -> dict:
    raw = _ps("Get-CimInstance Win32_VideoController | ConvertTo-Json -Depth 1")
    info = {"name": "Unknown GPU", "vram": "?", "driver": "?"}
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0]
        info["name"] = data.get("Name", "Unknown GPU")
        vram = data.get("AdapterRAM", 0)
        info["vram"] = _fmt_bytes(vram) if vram else "?"
        info["driver"] = data.get("DriverVersion", "?")
    except Exception:
        pass
    return info

def _gpu_usage() -> str:
    """Get GPU utilization via DirectX Performance Counters."""
    raw = _ps(
        "try { "
        "$c = (Get-Counter '\\GPU Engine(*engtype_3D*)\\Utilization Percentage' -ErrorAction Stop).CounterSamples; "
        "$u = ($c | Measure-Object CookedValue -Sum).Sum; "
        "Write-Output ([math]::Round($u,1)) "
        "} catch { Write-Output 'N/A' }"
    )
    return raw.strip() if raw.strip() else "N/A"

def _temperatures() -> dict[str, str]:
    """Get CPU/motherboard temps via WMI thermal zones."""
    temps = {}
    try:
        raw = _ps(
            "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
            "-ErrorAction SilentlyContinue | "
            "Select-Object InstanceName, CurrentTemperature | ConvertTo-Json -Depth 1"
        )
        if raw and "[PS Error" not in raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("InstanceName", "Zone")
                k = item.get("CurrentTemperature", 0)
                c = (k / 10) - 273.15
                temps[name.split("\\")[-1]] = f"{c:.1f}°C"
    except Exception:
        pass
    if not temps:
        temps["status"] = "Temperature sensors not accessible via WMI (normal on some hardware)"
    return temps


# ── Junk file categories ──────────────────────────────────────────────────────

_user = os.path.expanduser("~")
_local = os.path.join(_user, "AppData", "Local")

JUNK_CATEGORIES = {
    "user_temp": {
        "label": "User Temp Files",
        "paths": [os.environ.get("TEMP", os.path.join(_local, "Temp"))],
        "recursive": True,
    },
    "system_temp": {
        "label": "System Temp Files",
        "paths": ["C:\\Windows\\Temp"],
        "recursive": True,
    },
    "windows_update_cache": {
        "label": "Windows Update Download Cache",
        "paths": ["C:\\Windows\\SoftwareDistribution\\Download"],
        "recursive": True,
    },
    "prefetch": {
        "label": "Windows Prefetch Files",
        "paths": ["C:\\Windows\\Prefetch"],
        "recursive": False,
    },
    "thumbnail_cache": {
        "label": "Thumbnail Cache",
        "paths": [
            os.path.join(_local, "Microsoft", "Windows", "Explorer"),
        ],
        "recursive": False,
        "pattern": "thumbcache_*.db",
    },
    "chrome_cache": {
        "label": "Chrome Browser Cache",
        "paths": [
            os.path.join(_local, "Google", "Chrome", "User Data", "Default", "Cache"),
            os.path.join(_local, "Google", "Chrome", "User Data", "Default", "Code Cache"),
        ],
        "recursive": True,
    },
    "edge_cache": {
        "label": "Microsoft Edge Cache",
        "paths": [
            os.path.join(_local, "Microsoft", "Edge", "User Data", "Default", "Cache"),
        ],
        "recursive": True,
    },
    "recycle_bin": {
        "label": "Recycle Bin",
        "special": "recycle_bin",
    },
}

def _scan_category(key: str) -> tuple[int, int]:
    """Returns (total_bytes, file_count) for a junk category."""
    cat = JUNK_CATEGORIES.get(key, {})
    total_bytes = 0
    file_count = 0

    if cat.get("special") == "recycle_bin":
        raw = _ps(
            "(New-Object -ComObject Shell.Application).Namespace(0xA).Items() | "
            "ForEach-Object { $_.Size } | Measure-Object -Sum | "
            "Select-Object -ExpandProperty Sum"
        )
        try:
            total_bytes = int(raw.strip())
        except Exception:
            total_bytes = 0
        cnt_raw = _ps(
            "(New-Object -ComObject Shell.Application).Namespace(0xA).Items().Count"
        )
        try:
            file_count = int(cnt_raw.strip())
        except Exception:
            file_count = 0
        return total_bytes, file_count

    pattern = cat.get("pattern", "*")
    recursive = cat.get("recursive", True)

    for base_path in cat.get("paths", []):
        if not os.path.exists(base_path):
            continue
        try:
            if recursive:
                for root, _, files in os.walk(base_path):
                    for f in files:
                        if glob.fnmatch.fnmatch(f, pattern):
                            try:
                                sz = os.path.getsize(os.path.join(root, f))
                                total_bytes += sz
                                file_count += 1
                            except Exception:
                                pass
            else:
                for f in os.listdir(base_path):
                    if glob.fnmatch.fnmatch(f, pattern):
                        fpath = os.path.join(base_path, f)
                        if os.path.isfile(fpath):
                            try:
                                total_bytes += os.path.getsize(fpath)
                                file_count += 1
                            except Exception:
                                pass
        except PermissionError:
            pass

    return total_bytes, file_count


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def health_check() -> str:
    """
    Full system health report — CPU, RAM, GPU, battery, disk, temperatures, uptime.

    Use when user says: 'health check', 'how am i doing', 'check system', 
    'how's the laptop', 'system status', 'check my specs', 'diagnose the pc'.

    VoxKage should interpret this data as checking its own hardware body.
    """
    lines = ["═" * 52, "  🔬 VOXKAGE SYSTEM HEALTH REPORT", "═" * 52, ""]

    # ── CPU ──
    cpu_pct = psutil.cpu_percent(interval=1)
    cpu_freq = psutil.cpu_freq()
    cpu_count = psutil.cpu_count(logical=True)
    cpu_phys = psutil.cpu_count(logical=False)
    freq_str = f"{cpu_freq.current:.0f} MHz" if cpu_freq else "?"
    lines += [
        "💻 CPU",
        f"  Usage   : {_bar(cpu_pct)}",
        f"  Cores   : {cpu_phys} physical / {cpu_count} logical",
        f"  Clock   : {freq_str}",
        "",
    ]

    # ── RAM ──
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    lines += [
        "🧠 MEMORY (RAM)",
        f"  Usage   : {_bar(ram.percent)}",
        f"  Used    : {_fmt_bytes(ram.used)} / {_fmt_bytes(ram.total)}",
        f"  Free    : {_fmt_bytes(ram.available)}",
        f"  Swap    : {_fmt_bytes(swap.used)} used / {_fmt_bytes(swap.total)} total",
        "",
    ]

    # ── GPU ──
    gpu = _gpu_info()
    gpu_use = _gpu_usage()
    lines += [
        "🎮 GPU",
        f"  Name    : {gpu['name']}",
        f"  VRAM    : {gpu['vram']}",
        f"  Usage   : {gpu_use}%" if gpu_use != "N/A" else "  Usage   : N/A (counters not available)",
        f"  Driver  : {gpu['driver']}",
        "",
    ]

    # ── Battery ──
    try:
        bat = psutil.sensors_battery()
        if bat:
            status = "Charging ⚡" if bat.power_plugged else "On Battery 🔋"
            mins = bat.secsleft // 60 if bat.secsleft and bat.secsleft > 0 else None
            time_str = f"{mins} min remaining" if mins else "Calculating..."
            lines += [
                "🔋 BATTERY",
                f"  Charge  : {_bar(bat.percent)}",
                f"  Status  : {status}",
                f"  Time    : {time_str if not bat.power_plugged else 'N/A (plugged in)'}",
                "",
            ]
        else:
            lines += ["🔋 BATTERY : Not detected (desktop/no battery)\n"]
    except Exception:
        lines += ["🔋 BATTERY : N/A\n"]

    # ── Disk ──
    lines.append("💾 STORAGE")
    try:
        for part in psutil.disk_partitions():
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                lines.append(
                    f"  {part.device:6} : {_bar(usage.percent)} "
                    f"({_fmt_bytes(usage.used)} / {_fmt_bytes(usage.total)})"
                )
            except PermissionError:
                pass
    except Exception as e:
        lines.append(f"  Error: {e}")
    lines.append("")

    # ── Temperatures ──
    temps = _temperatures()
    lines.append("🌡 TEMPERATURES")
    for zone, temp in temps.items():
        lines.append(f"  {zone[:30]:30} : {temp}")
    lines.append("")

    # ── Uptime ──
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    mins = rem // 60
    lines += [
        "⏱ UPTIME",
        f"  Running : {hours}h {mins}m (since {boot.strftime('%Y-%m-%d %H:%M')})",
        "",
        "═" * 52,
    ]

    return "\n".join(lines)


@mcp.tool()
def get_processes(sort_by: str = "memory", top_n: int = 15) -> str:
    """
    List the top running processes sorted by resource usage.

    Use when user asks: 'what's eating my memory', 'which app is using the most CPU',
    'what's running', 'show me processes', 'task manager', 'what's slowing down my pc'.

    Parameters:
      sort_by : "memory" (default) or "cpu"
      top_n   : number of processes to show (default 15)
    """
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "status"]):
        try:
            info = p.info
            mem = info["memory_info"].rss if info["memory_info"] else 0
            cpu = info["cpu_percent"] or 0
            procs.append({
                "pid": info["pid"],
                "name": info["name"],
                "mem": mem,
                "cpu": cpu,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    key = "mem" if sort_by == "memory" else "cpu"
    procs.sort(key=lambda x: x[key], reverse=True)
    top = procs[:top_n]

    label = "Memory" if sort_by == "memory" else "CPU"
    lines = [
        f"{'PID':>7}  {'Process Name':<35}  {'Memory':>10}  {'CPU%':>6}",
        "─" * 65,
    ]
    for p in top:
        lines.append(
            f"{p['pid']:>7}  {p['name'][:35]:<35}  "
            f"{_fmt_bytes(p['mem']):>10}  {p['cpu']:>5.1f}%"
        )

    total_ram = psutil.virtual_memory()
    lines += [
        "─" * 65,
        f"Total RAM in use: {_fmt_bytes(total_ram.used)} / {_fmt_bytes(total_ram.total)} "
        f"({total_ram.percent:.1f}%)",
    ]
    return f"Top {top_n} Processes by {label}:\n\n" + "\n".join(lines)


@mcp.tool()
def get_startup_items() -> str:
    """
    List all programs configured to run at Windows startup.

    Use when user asks: 'what's in my startup', 'what runs when i boot',
    'startup programs', 'disable startup', 'why is my pc slow to start'.

    Checks both registry startup keys and the Startup folder.
    """
    items = []

    def _reg_items(hive, path, source):
        try:
            key = winreg.OpenKey(hive, path)
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, i)
                    items.append({"name": name, "command": val, "source": source})
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass

    run_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    _reg_items(winreg.HKEY_CURRENT_USER, run_path, "HKCU Registry")
    _reg_items(winreg.HKEY_LOCAL_MACHINE, run_path, "HKLM Registry")
    _reg_items(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
        "HKLM 32-bit Registry",
    )

    # Startup folder
    startup_folder = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    if os.path.exists(startup_folder):
        for f in os.listdir(startup_folder):
            items.append({
                "name": f,
                "command": os.path.join(startup_folder, f),
                "source": "Startup Folder",
            })

    if not items:
        return "No startup items found (or access denied)."

    lines = [f"{'Name':<35}  {'Source':<22}  Command", "─" * 100]
    for it in items:
        cmd = it["command"][:55] + "..." if len(it["command"]) > 55 else it["command"]
        lines.append(f"{it['name'][:35]:<35}  {it['source']:<22}  {cmd}")

    return f"Startup Programs ({len(items)} items):\n\n" + "\n".join(lines)


@mcp.tool()
def scan_junk_files() -> str:
    """
    Scans the system for junk files that are safe to delete.

    Categories: User/System Temp, Windows Update Cache, Prefetch, 
    Thumbnail Cache, Chrome/Edge cache, Recycle Bin.

    Use when user asks: 'scan for junk', 'clean my pc', 'what can I delete',
    'free up space', 'find temp files', 'disk cleanup'.

    Returns sizes per category and the total space recoverable.
    After scanning, use clean_junk_files() to delete specific categories.
    """
    lines = ["🔍 JUNK FILE SCAN REPORT", "═" * 50, ""]
    total_bytes = 0
    results = {}

    for key, cat in JUNK_CATEGORIES.items():
        size, count = _scan_category(key)
        results[key] = (size, count)
        total_bytes += size
        status = "✅ Clean" if size == 0 else f"⚠️  {_fmt_bytes(size)} ({count} files)"
        lines.append(f"  [{key}]")
        lines.append(f"    {cat['label']:<35} : {status}")
        lines.append("")

    lines += [
        "═" * 50,
        f"  Total Recoverable Space : {_fmt_bytes(total_bytes)}",
        "",
        "To clean specific categories, call:",
        "  clean_junk_files(categories=[...], confirmed=False)",
        "Available categories: " + ", ".join(JUNK_CATEGORIES.keys()),
    ]
    return "\n".join(lines)


@mcp.tool()
def clean_junk_files(
    categories: list,
    confirmed: bool = False,
) -> str:
    """
    Deletes junk files in the specified categories.

    WORKFLOW (ALWAYS FOLLOW THIS):
      1. First call scan_junk_files() to see what's available.
      2. Call clean_junk_files with confirmed=False → shows preview → ask "Agreed?"
      3. Call again with confirmed=True to actually delete.

    Parameters:
      categories : list of category keys to clean (from scan_junk_files output).
                   Use ["all"] to clean all categories.
      confirmed  : False = preview only. True = execute deletion.

    Safe to delete — these are all temp/cache files, NOT personal files.
    """
    if "all" in categories:
        categories = list(JUNK_CATEGORIES.keys())

    # Validate
    invalid = [c for c in categories if c not in JUNK_CATEGORIES]
    if invalid:
        return f"Unknown categories: {invalid}. Valid: {list(JUNK_CATEGORIES.keys())}"

    # Preview
    preview_lines = ["[CONFIRM] Ready to clean the following:\n"]
    total_preview = 0
    for key in categories:
        size, count = _scan_category(key)
        total_preview += size
        cat = JUNK_CATEGORIES[key]
        preview_lines.append(f"  • {cat['label']}: {_fmt_bytes(size)} ({count} files)")

    preview_lines += [
        "",
        f"  Total to free: {_fmt_bytes(total_preview)}",
        "",
        "These are temp/cache files. No personal data will be affected.",
        "\nAgreed?",
    ]

    if not confirmed:
        return "\n".join(preview_lines)

    # Execute
    freed = 0
    errors = []
    cleaned = []

    for key in categories:
        cat = JUNK_CATEGORIES[key]

        if cat.get("special") == "recycle_bin":
            try:
                _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
                cleaned.append(f"✓ {cat['label']} cleared")
            except Exception as e:
                errors.append(f"✗ {cat['label']}: {e}")
            continue

        pattern = cat.get("pattern", "*")
        recursive = cat.get("recursive", True)

        for base_path in cat.get("paths", []):
            if not os.path.exists(base_path):
                continue
            try:
                if recursive:
                    for root, dirs, files in os.walk(base_path, topdown=False):
                        for f in files:
                            if glob.fnmatch.fnmatch(f, pattern):
                                fpath = os.path.join(root, f)
                                try:
                                    sz = os.path.getsize(fpath)
                                    os.remove(fpath)
                                    freed += sz
                                except Exception:
                                    pass
                        for d in dirs:
                            try:
                                dpath = os.path.join(root, d)
                                if not os.listdir(dpath):
                                    os.rmdir(dpath)
                            except Exception:
                                pass
                else:
                    for f in os.listdir(base_path):
                        if glob.fnmatch.fnmatch(f, pattern):
                            fpath = os.path.join(base_path, f)
                            if os.path.isfile(fpath):
                                try:
                                    sz = os.path.getsize(fpath)
                                    os.remove(fpath)
                                    freed += sz
                                except Exception:
                                    pass
                cleaned.append(f"✓ {cat['label']} cleaned")
            except PermissionError as e:
                errors.append(f"✗ {cat['label']}: Permission denied (run as admin for system files)")
            except Exception as e:
                errors.append(f"✗ {cat['label']}: {e}")

    result = [f"🧹 CLEANING COMPLETE — Freed {_fmt_bytes(freed)}\n"]
    result.extend(cleaned)
    if errors:
        result.append("\nErrors:")
        result.extend(errors)
    return "\n".join(result)


@mcp.tool()
def get_security_status() -> str:
    """
    Returns Windows Defender / Security status and last scan info.

    Use when user asks: 'scan for viruses', 'check for malware', 'am i safe',
    'antivirus status', 'security check', 'defender status', 'is my pc protected'.

    Note: Running a full scan can take minutes. This returns the current protection
    status and last scan results instantly. To trigger a new scan, it launches
    Windows Security in the background.
    """
    lines = ["🛡 WINDOWS SECURITY STATUS", "═" * 50, ""]

    status_raw = _ps(
        "Get-MpComputerStatus | Select-Object "
        "AMServiceEnabled, RealTimeProtectionEnabled, "
        "AntivirusEnabled, AntispywareEnabled, "
        "QuickScanAge, FullScanAge, "
        "AntivirusSignatureLastUpdated, "
        "NISEnabled, BehaviorMonitorEnabled "
        "| ConvertTo-Json"
    )

    try:
        s = json.loads(status_raw)
        def _icon(v): return "✅" if v else "❌"

        lines += [
            f"  Real-Time Protection  : {_icon(s.get('RealTimeProtectionEnabled'))}",
            f"  Antivirus             : {_icon(s.get('AntivirusEnabled'))}",
            f"  Antispyware           : {_icon(s.get('AntispywareEnabled'))}",
            f"  Behavior Monitor      : {_icon(s.get('BehaviorMonitorEnabled'))}",
            f"  Network Protection    : {_icon(s.get('NISEnabled'))}",
            "",
        ]

        sig_date = s.get("AntivirusSignatureLastUpdated", "?")
        if sig_date and sig_date != "?":
            try:
                sig_date = str(sig_date)[:10]
            except Exception:
                pass
        lines.append(f"  Definitions Updated   : {sig_date}")

        quick_age = s.get("QuickScanAge")
        full_age = s.get("FullScanAge")
        lines += [
            f"  Last Quick Scan       : {quick_age} day(s) ago" if quick_age is not None else "  Last Quick Scan       : Never",
            f"  Last Full Scan        : {full_age} day(s) ago" if full_age is not None else "  Last Full Scan        : Never",
            "",
        ]

        # Threat summary
        threats_raw = _ps(
            "Get-MpThreatDetection | Select-Object ThreatID, ActionSuccess, "
            "DetectionSourceTypeID | ConvertTo-Json"
        )
        try:
            threats = json.loads(threats_raw)
            if isinstance(threats, dict):
                threats = [threats]
            if threats:
                lines.append(f"  ⚠️  Past Threats Found  : {len(threats)} detection(s) in history")
            else:
                lines.append("  Past Threats          : None detected ✅")
        except Exception:
            lines.append("  Past Threats          : No threat history (clean!)")

    except Exception:
        lines.append(
            "  Could not retrieve Defender status via PowerShell.\n"
            "  Ensure Windows Defender is active and not replaced by 3rd-party AV."
        )

    lines += [
        "",
        "═" * 50,
        "  To open Windows Security: smart_open('windows security')",
        "  To run a quick scan    : Run 'Start-MpScan -ScanType QuickScan' in PowerShell",
    ]
    return "\n".join(lines)


@mcp.tool()
def get_disk_analysis(path: str = "C:\\") -> str:
    """
    Shows what's eating up the most space in a directory.
    Scans top-level folders and ranks by size.

    Use when user asks: 'what's taking up space', 'my SSD is full', 
    'what's the biggest folder', 'storage analysis', 'disk usage breakdown'.

    Parameters:
      path : root directory to analyze (default: C:\\)
    """
    if not os.path.exists(path):
        return f"Path not found: {path}"

    lines = [f"📊 DISK USAGE ANALYSIS — {path}", "═" * 52, ""]

    disk = psutil.disk_usage(path)
    lines += [
        f"  Total   : {_fmt_bytes(disk.total)}",
        f"  Used    : {_fmt_bytes(disk.used)} ({disk.percent:.1f}%)",
        f"  Free    : {_fmt_bytes(disk.free)}",
        "",
        "  Top folders by size:",
        "─" * 52,
    ]

    folder_sizes = []
    try:
        for entry in os.scandir(path):
            if not entry.is_dir():
                continue
            try:
                raw = _ps(
                    f"(Get-ChildItem -Path '{entry.path}' -Recurse -ErrorAction SilentlyContinue "
                    f"| Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum"
                )
                sz = int(raw.strip()) if raw.strip().isdigit() else 0
                folder_sizes.append((sz, entry.name))
            except Exception:
                folder_sizes.append((0, entry.name))
    except PermissionError:
        lines.append("  (Some folders require admin access)")

    folder_sizes.sort(reverse=True)
    total_shown = disk.total or 1
    for sz, name in folder_sizes[:15]:
        pct = (sz / total_shown) * 100
        bar = "█" * min(int(pct / 2), 25)
        lines.append(f"  {name[:28]:<28} {_fmt_bytes(sz):>10}  {bar}")

    lines += ["─" * 52, ""]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
