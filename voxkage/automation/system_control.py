import os
import time
import difflib
import ctypes
import random
import subprocess

# Optional: pycaw for precise volume control (Windows Core Audio API).
# Falls back to keypress automation if not available.
try:
    from ctypes import cast as _ctypes_cast, POINTER as _POINTER
    from comtypes import CLSCTX_ALL as _CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities as _AudioUtilities, IAudioEndpointVolume as _IAudioEndpointVolume
    _PYCAW_AVAILABLE = True
except Exception:
    _PYCAW_AVAILABLE = False


try:
    import pygetwindow as gw
except ImportError:
    gw = None

import pyautogui

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None

def close_app(process_name):
    try:
        # Prevent killing explorer.exe completely
        if process_name.lower() == "explorer.exe":
            # Try to close only the active folder windows instead
            closed_any = False
            for win in gw.getWindowsWithTitle(''):
                if win.title and win.isVisible and "File Explorer" in win.title or os.path.exists(win.title):
                    try:
                        win.close()
                        closed_any = True
                    except Exception:
                        pass
            if closed_any:
                return "Closed folder window safely."
            else:
                return "No folder window found to close."
        
        os.system(f"taskkill /f /im {process_name}")
        return f"Closed {process_name}"
    except Exception as e:
        return f"Failed to close {process_name}: {e}"

def close_folder_window_by_path(folder_path):
    try:
        windows = Desktop(backend="uia").windows()
        for win in windows:
            try:
                if folder_path.lower() in str(win.window_text()).lower() or folder_path.lower() in str(win.element_info.name).lower():
                    win.close()
                    return f"Closed folder window: {folder_path}"
            except:
                continue
        return f"No open folder window found for: {folder_path}"
    except Exception as e:
        return f"Error closing folder window: {e}"

def close_explorer_folder(folder_path):
    import subprocess
    normalized_path = os.path.normpath(folder_path)

    powershell_script = f'''
    $target = "{normalized_path}".TrimEnd('\\').ToLower()

    $shell = New-Object -ComObject Shell.Application
    $shell.Windows() | ForEach-Object {{
        try {{
            $currentPath = $_.Document.Folder.Self.Path.TrimEnd('\\').ToLower()
            if ($currentPath -eq $target) {{
                $_.Quit()
            }}
        }} catch {{
            # skip non-folder windows (like browsers or settings)
        }}
    }}
    '''

    try:
        subprocess.run(["powershell", "-Command", powershell_script], check=True)
        return f"Closed folder: {normalized_path}"
    except subprocess.CalledProcessError as e:
        return f"Failed to close folder '{normalized_path}': {str(e)}"

def switch_to_app(name):
    """
    Uses fuzzy matching to switch to the window whose title best matches 'name'
    """
    name = name.lower()
    time.sleep(0.5)

    # Get all window titles
    all_windows = [w.title for w in gw.getAllWindows() if w.title.strip()]

    # Try to find a close match
    match = difflib.get_close_matches(name, all_windows, n=1, cutoff=0.4)
    if match:
        matched_title = match[0]
        for window in gw.getWindowsWithTitle(matched_title):
            try:
                if window.isMinimized:
                    window.restore()
                window.activate()
                return f"Switched to {matched_title}"
            except Exception as e:
                return f"Error switching to {matched_title}: {e}"

    return f"No open window matched '{name}'"

def switch_to_window_by_title_or_path(keyword):
    try:
        keyword = keyword.lower()
        windows = Desktop(backend="uia").windows()
        for win in windows:
            title = str(win.window_text()).lower()
            path = str(win.element_info.name).lower()
            if keyword in title or keyword in path:
                win.set_focus()
                return f"Switched to window: {win.window_text()}"
        return f"No open window found for: {keyword}"
    except Exception as e:
        return f"Error switching window: {e}"



def set_volume(level_percent):
    """Set system volume precisely using pycaw. level_percent: 0-100"""
    level_percent = max(0, min(100, int(level_percent)))
    if _PYCAW_AVAILABLE:
        try:
            devices = _AudioUtilities.GetSpeakers()
            interface = devices.Activate(_IAudioEndpointVolume._iid_, _CLSCTX_ALL, None)
            vol = _ctypes_cast(interface, _POINTER(_IAudioEndpointVolume))
            vol.SetMute(0, None)
            vol.SetMasterVolumeLevelScalar(level_percent / 100.0, None)
            return f"Volume set to {level_percent}%"
        except Exception:
            pass
    # Fallback: nircmd
    try:
        r = subprocess.run(["nircmd", "setsysvolume", str(int(level_percent * 655.35))],
                          capture_output=True, timeout=5)
        if r.returncode == 0:
            return f"Volume set to {level_percent}% (nircmd)"
    except Exception:
        pass
    # Last resort: key presses from 0
    pyautogui.press("volumemute")
    time.sleep(0.05)
    for _ in range(50):
        pyautogui.press("volumedown")
    for _ in range(int(level_percent / 2)):
        pyautogui.press("volumeup")
    return f"Volume set to approximately {level_percent}%"


def get_volume() -> str:
    """Get current system volume level and mute state."""
    if _PYCAW_AVAILABLE:
        try:
            devices = _AudioUtilities.GetSpeakers()
            interface = devices.Activate(_IAudioEndpointVolume._iid_, _CLSCTX_ALL, None)
            vol = _ctypes_cast(interface, _POINTER(_IAudioEndpointVolume))
            level = round(vol.GetMasterVolumeLevelScalar() * 100)
            muted = bool(vol.GetMute())
            return f"Volume: {level}% | Muted: {'Yes' if muted else 'No'}"
        except Exception as e:
            return f"Could not read volume: {e}"
    return "pycaw not available — volume read unsupported"


def set_brightness(level_percent):
    """Set monitor brightness. Tries WMI then nircmd DDC/CI."""
    level_percent = max(0, min(100, int(level_percent)))
    try:
        script = (f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods "
                  f"-ErrorAction Stop).WmiSetBrightness(1,{level_percent})")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and not r.stderr.strip():
            return f"Brightness set to {level_percent}% (built-in display)"
    except Exception:
        pass
    try:
        r2 = subprocess.run(["nircmd", "setbrightness", str(level_percent)],
                           capture_output=True, timeout=5)
        if r2.returncode == 0:
            return f"Brightness set to {level_percent}% (external monitor DDC/CI)"
    except Exception:
        pass
    return f"Brightness command sent for {level_percent}%. External monitors may not support this."


def get_brightness() -> str:
    """Get current monitor brightness level."""
    try:
        script = "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness -ErrorAction Stop).CurrentBrightness"
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=5)
        val = r.stdout.strip()
        if val.isdigit():
            return f"Current brightness: {val}%"
    except Exception:
        pass
    return "Cannot read brightness on external monitors"

def toggle_wifi(turn_on: bool):
    state = "enable" if turn_on else "disable"
    # assumes your interface name is "Wi-Fi"
    os.system(f'netsh interface set interface "Wi-Fi" {state}')
    return f"Wi-Fi turned {'on' if turn_on else 'off'}"

def toggle_bluetooth(turn_on: bool):
    """
    Toggle Bluetooth using WinRT Radio API (no admin rights needed).
    Falls back to Device Management PowerShell if WinRT unavailable.
    """
    state = "On" if turn_on else "Off"
    label = "on" if turn_on else "off"
    # Method 1: WinRT (Windows 10+, no admin)
    script = f"""
$btType = [Windows.Devices.Radios.RadioKind,Windows.Devices.Radios,ContentType=WindowsRuntime]::Bluetooth
$radios = [Windows.Devices.Radios.Radio,Windows.Devices.Radios,ContentType=WindowsRuntime]::GetRadiosAsync().GetAwaiter().GetResult()
$bt = $radios | Where-Object {{ $_.Kind -eq $btType }}
if ($bt) {{
    $bt.SetStateAsync([Windows.Devices.Radios.RadioState,Windows.Devices.Radios,ContentType=WindowsRuntime]::{state}).GetAwaiter().GetResult() | Out-Null
    Write-Output "Bluetooth turned {label} successfully."
}} else {{ Write-Output "NO_BT_RADIO" }}
"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=10)
        out = r.stdout.strip()
        if out and "NO_BT_RADIO" not in out and "error" not in out.lower():
            return out
    except Exception:
        pass
    # Method 2: DevCon / pnputil fallback
    action = "enable" if turn_on else "disable"
    try:
        r2 = subprocess.run(
            ["powershell", "-Command",
             f"Get-PnpDevice -Class Bluetooth | {action.capitalize()}-PnpDevice -Confirm:$false"],
            capture_output=True, text=True, timeout=10
        )
        return f"Bluetooth {label} (via device manager)."
    except Exception as e:
        return f"Could not toggle Bluetooth: {e}. Try toggling via Action Center."


def bring_console_to_front():
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return "Brought console to front."
    return "Could not find console window to bring to front."

def safe_close_target(target: str) -> str:
    target = target.lower().strip()
    import psutil
    
    # 1. Attempt safe window close (WM_CLOSE) by title matching
    # This safely closes specific files open in VS Code, Notepad, etc.
    closed_count = 0
    for win in gw.getAllWindows():
        try:
            title = win.title.lower()
            if not title: continue
            
            # Match file names or app names in window titles
            if target in title:
                # Protect the main VoxKage terminal window from accidentally killing itself
                if "voxkage" in title and target != "voxkage":
                    continue
                # Use PostMessage for a non-blocking asynchronous close. 
                # win.close() uses SendMessage which blocks if the app shows a "Save changes?" dialog.
                WM_CLOSE = 0x0010
                ctypes.windll.user32.PostMessageW(win._hWnd, WM_CLOSE, 0, 0)
                closed_count += 1
        except Exception:
            pass
            
    if closed_count > 0:
        # If the target is an application (e.g. "chrome", "spotify"), it may leave background processes running.
        # We check if the target exactly matches an executable and kill the lingering processes.
        target_proc = f"{target}.exe" if not target.endswith(".exe") else target
        killed_bg = 0
        
        if target_proc != "explorer.exe":
            time.sleep(0.5)  # Allow graceful window close first
            for proc in psutil.process_iter(['name']):
                try:
                    pname = str(proc.info['name']).lower()
                    if pname == target_proc:
                        proc.kill()
                        killed_bg += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
                    
        if killed_bg > 0:
            return f"Safely closed {closed_count} window(s) and terminated {killed_bg} background process(es) for '{target}'."
        return f"Safely closed {closed_count} window(s) matching '{target}'."
        
    # 2. If it looks like a folder path, try Shell.Application COM object
    if "\\" in target or "/" in target:
        res = close_explorer_folder(target)
        if "Closed folder" in res:
            return res

    # 3. If no windows matched, try safe process kill
    if target.endswith(".exe"):
        target_proc = target
    else:
        target_proc = f"{target}.exe"
        
    if target_proc == "explorer.exe":
        return "Refusing to forcefully taskkill explorer.exe to protect desktop stability. Please specify the exact folder path to close."
        
    killed_count = 0
    for proc in psutil.process_iter(['name']):
        try:
            pinfo = proc.info
            pname = str(pinfo['name']).lower()
            if pname == target_proc or pname == target:
                if "explorer" in pname:
                    continue # Extra protection
                proc.kill()
                killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
            
    if killed_count > 0:
        return f"Forcefully killed {killed_count} background process(es) matching '{target}'."
        
    return f"Could not find any open window, file tab, or safe process matching '{target}' to close."

def toggle_hotspot(turn_on: bool):
    state = "Start" if turn_on else "Stop"
    script = f'''
    [Windows.Networking.NetworkOperators.Tethering.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime] | Out-Null
    [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime] | Out-Null
    $connectionProfile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    $tetheringManager = [Windows.Networking.NetworkOperators.Tethering.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($connectionProfile)
    $tetheringManager.{state}TetheringAsync()
    Start-Sleep -Seconds 1
    '''
    try:
        subprocess.run(["powershell", "-Command", script], capture_output=True)
        return f"Mobile Hotspot turned {'on' if turn_on else 'off'}."
    except Exception as e:
        return f"Failed to toggle Mobile Hotspot: {e}"

def toggle_night_light(turn_on: bool):
    """
    Toggle Night Light via registry (no UI opening needed).
    """
    try:
        import struct, winreg as _wr
        key_path = (
            r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store"
            r"\DefaultAccount\Current\default$windows.data.bluelightreduction"
            r".bluelightreductionstate\windows.data.bluelightreduction.bluelightreductionstate"
        )
        try:
            key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, key_path, 0,
                              _wr.KEY_READ | _wr.KEY_WRITE)
            data, _ = _wr.QueryValueEx(key, "Data")
            # Byte 18 controls the enabled state (0x13 = enabled, 0x10 = disabled)
            ba = bytearray(data)
            if len(ba) > 18:
                ba[18] = 0x13 if turn_on else 0x10
                _wr.SetValueEx(key, "Data", 0, _wr.REG_BINARY, bytes(ba))
            _wr.CloseKey(key)
            state = "enabled" if turn_on else "disabled"
            return f"Night Light {state} via registry. Changes apply within seconds."
        except FileNotFoundError:
            pass
    except Exception:
        pass
    # Fallback: open settings
    subprocess.Popen(["start", "ms-settings:nightlight"], shell=True)
    label = "on" if turn_on else "off"
    return f"Opened Night Light settings — please toggle it {label} manually."

def open_intel_dsa():
    try:
        os.system("start https://www.intel.com/content/www/us/en/support/intel-driver-support-assistant.html")
        return "Opened Intel Driver & Support Assistant website."
    except Exception as e:
        return f"Failed to open Intel DSA: {e}"


def set_audio_output(device_type="speakers"):
    script = f"""
    if (-not (Get-Module -ListAvailable -Name AudioDeviceCmdlets)) {{

        Install-Module -Name AudioDeviceCmdlets -Force -Scope CurrentUser -AllowClobber -ErrorAction SilentlyContinue
    }}
    Import-Module AudioDeviceCmdlets -ErrorAction SilentlyContinue
    if (Get-Command Set-AudioDevice -ErrorAction SilentlyContinue) {{
        $devices = Get-AudioDevice -List
        $target = $devices | Where-Object {{ $_.Type -eq 'Playback' -and $_.Name -match '{device_type}' }}
        if ($target) {{
            Set-AudioDevice -Index $target[0].Index | Out-Null
            Write-Output "Switched audio output to $($target[0].Name)"
        }} else {{
            Write-Output "Audio device matching '{device_type}' not found."
        }}
    }} else {{
        Write-Output "AudioDeviceCmdlets module could not be loaded."
    }}
    """
    try:
        import subprocess
        res = subprocess.run(["powershell", "-Command", script], capture_output=True, text=True)
        return res.stdout.strip() if res.stdout.strip() else "Executed audio output switch."
    except Exception as e:
        return f"Failed to switch audio output: {e}"


def set_power_profile(mode="performance"):
    modes = {
        "performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
        "saver": "a1841308-3541-4fab-bc81-f71556f20b4a"
    }
    guid = modes.get(mode.lower(), modes["balanced"])
    import os
    os.system(f"powercfg -setactive {guid}")
    return f"Power profile set to {mode.capitalize()}."


def update_all_software():
    import subprocess
    try:
        subprocess.Popen(["winget", "update", "--all", "--quiet", "--accept-package-agreements", "--accept-source-agreements"], shell=True)
        return "Triggered global software update via winget in the background. This will silently update all compatible apps."
    except Exception as e:
        return f"Failed to run software update: {e}"


def toggle_focus_mode(turn_on: bool):
    import os
    try:
        val = 0 if turn_on else 1
        os.system(f'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings" /v NOC_GLOBAL_SETTING_TOASTS_ENABLED /t REG_DWORD /d  {val} /f >nul 2>&1')
        state = 'enabled (notifications silenced)' if turn_on else 'disabled'
        return f"Focus mode {state}."
    except Exception as e:
        return f"Failed to toggle Focus mode: {e}"


def check_network_vitality():
    import subprocess
    try:
        res = subprocess.run(["ping", "8.8.8.8", "-n", "4"], capture_output=True, text=True)
        return "Network Vitality Check:\n" + res.stdout.strip()
    except Exception as e:
        return f"Failed to check network: {e}"


def boost_process(process_name: str):
    import subprocess
    try:
        script = f"Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue | ForEach-Object {{ $app_obj = $_; $app_obj.PriorityClass = 'High' }}"
        subprocess.run(["powershell", "-Command", script], check=True)
        return f"Boosted process '{process_name}' to High priority."
    except Exception as e:
        return f"Failed to boost process '{process_name}'. It might not be running or requires admin rights."


def flush_dns():
    import os
    os.system("ipconfig /flushdns >nul 2>&1")
    return "DNS cache flushed successfully."


def clear_temp_files():
    import os
    import shutil
    temp_dir = os.environ.get('TEMP')
    count = 0
    if temp_dir and os.path.exists(temp_dir):
        for item in os.listdir(temp_dir):
            item_path = os.path.join(temp_dir, item)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                count += 1
            except Exception:
                pass
    return f"Cleared {count} temporary files/folders."


def toggle_hidden_files(show: bool):
    import os
    val = 1 if show else 2
    os.system(f'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v Hidden /t REG_DWORD /d {val} /f >nul 2>&1')
    os.system("taskkill /f /im explorer.exe >nul 2>&1 & start explorer.exe")
    return f"Hidden files are now {'visible' if show else 'hidden'}."


def toggle_dark_mode(dark: bool):
    import os
    val = 0 if dark else 1
    os.system(f'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" /v AppsUseLightTheme /t REG_DWORD /d {val} /f >nul 2>&1')
    os.system(f'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" /v SystemUsesLightTheme /t REG_DWORD /d {val} /f >nul 2>&1')
    return f"Windows theme set to {'Dark' if dark else 'Light'} mode. Some apps may require a restart to reflect changes."


def mute_microphone(mute: bool):
    mute_str = "true" if mute else "false"
    state_str = "muted" if mute else "unmuted"
    script = f"""
    if (-not (Get-Module -ListAvailable -Name AudioDeviceCmdlets)) {{

        Install-Module -Name AudioDeviceCmdlets -Force -Scope CurrentUser -AllowClobber -ErrorAction SilentlyContinue
    }}
    Import-Module AudioDeviceCmdlets -ErrorAction SilentlyContinue
    if (Get-Command Set-AudioDevice -ErrorAction SilentlyContinue) {{
        $devices = Get-AudioDevice -List | Where-Object {{ $_.Type -eq 'Recording' }}
        if ($devices) {{
            Set-AudioDevice -Index $devices[0].Index | Out-Null
            Set-AudioDevice -RecordingMute ${mute_str} | Out-Null
            Write-Output "Microphone {state_str}."
        }} else {{
            Write-Output "No recording device found."
        }}
    }} else {{
        Write-Output "AudioDeviceCmdlets module could not be loaded."
    }}
    """
    import subprocess
    try:
        res = subprocess.run(["powershell", "-Command", script], capture_output=True, text=True)
        return res.stdout.strip() if res.stdout.strip() else f"Microphone {state_str}."
    except Exception as e:
        return f"Failed to toggle microphone: {e}"


def system_uptime():
    import subprocess
    try:
        script = "((get-date) - (gcim Win32_OperatingSystem).LastBootUpTime) | Select-Object Days, Hours, Minutes, Seconds | Out-String"
        res = subprocess.run(["powershell", "-Command", script], capture_output=True, text=True)
        return f"System Uptime:\n{res.stdout.strip()}"
    except Exception as e:
        return f"Failed to get uptime: {e}"


# ── NEW FUNCTIONS ──────────────────────────────────────────────────────────────

def get_battery_status() -> str:
    """Get battery level, charging state, and estimated time remaining."""
    try:
        script = (
            "$b = Get-WmiObject -Class Win32_Battery;"
            "if ($b) {"
            "  $pct = $b.EstimatedChargeRemaining;"
            "  $status = switch($b.BatteryStatus){1{'Discharging'} 2{'AC Connected'} 3{'Fully Charged'} default{'Unknown'}};"
            "  $mins = $b.EstimatedRunTime;"
            "  Write-Output \"Battery: $pct% | Status: $status | Est. Runtime: $mins min\""
            "} else { Write-Output 'NO_BATTERY' }"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=8)
        out = r.stdout.strip()
        if "NO_BATTERY" in out:
            return "No battery detected — this appears to be a desktop PC."
        return out or "Could not read battery status."
    except Exception as e:
        return f"Battery check failed: {e}"


def get_system_info() -> str:
    """Return CPU, RAM, disk, GPU, and OS info all in one call."""
    try:
        script = """
$os = Get-WmiObject Win32_OperatingSystem
$cpu = Get-WmiObject Win32_Processor | Select-Object -First 1
$ram_total = [math]::Round($os.TotalVisibleMemorySize/1MB, 1)
$ram_free  = [math]::Round($os.FreePhysicalMemory/1MB, 1)
$ram_used  = [math]::Round($ram_total - $ram_free, 1)
$disks = Get-WmiObject Win32_LogicalDisk -Filter "DriveType=3" |
  ForEach-Object { "$($_.DeviceID) $([math]::Round($_.FreeSpace/1GB,1))GB free / $([math]::Round($_.Size/1GB,1))GB" }
$gpu = (Get-WmiObject Win32_VideoController | Select-Object -First 1).Name
$uptime = ((get-date) - $os.ConvertToDateTime($os.LastBootUpTime))
Write-Output "OS      : $($os.Caption) Build $($os.BuildNumber)"
Write-Output "CPU     : $($cpu.Name.Trim())"
Write-Output "RAM     : ${ram_used}GB used / ${ram_total}GB total ($([math]::Round(($ram_used/$ram_total)*100))%)"
Write-Output "GPU     : $gpu"
$disks | ForEach-Object { Write-Output "Disk    : $_" }
Write-Output "Uptime  : $($uptime.Days)d $($uptime.Hours)h $($uptime.Minutes)m"
"""
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=15)
        return r.stdout.strip() or "Could not fetch system info."
    except Exception as e:
        return f"System info failed: {e}"


def get_running_processes(sort_by: str = "cpu", top_n: int = 15) -> str:
    """List top N processes by CPU or RAM usage."""
    sort_prop = "CPU" if sort_by.lower() == "cpu" else "WorkingSet"
    try:
        script = (
            f"Get-Process | Sort-Object {sort_prop} -Descending | Select-Object -First {top_n} "
            "Name,Id,@{N='CPU(s)';E={[math]::Round($_.CPU,1)}},@{N='RAM(MB)';E={[math]::Round($_.WorkingSet/1MB,1)}} | "
            "Format-Table -AutoSize | Out-String"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=10)
        return f"Top {top_n} processes (by {sort_by.upper()}):\n{r.stdout.strip()}"
    except Exception as e:
        return f"Process list failed: {e}"


def kill_process_by_name(name: str) -> str:
    """Kill a process by name or PID."""
    try:
        import psutil
        killed = []
        for proc in psutil.process_iter(['name', 'pid']):
            pname = proc.info['name'] or ""
            if name.lower() in pname.lower() or str(proc.info['pid']) == name:
                if "explorer" in pname.lower() or "system" in pname.lower():
                    return f"Refused to kill protected process '{pname}'."
                proc.kill()
                killed.append(f"{pname}(PID {proc.info['pid']})")
        return f"Killed: {', '.join(killed)}" if killed else f"No process matching '{name}' found."
    except Exception as e:
        return f"Kill failed: {e}"


def suspend_process(name: str) -> str:
    """Suspend (pause) a running process without killing it."""
    try:
        import psutil
        for proc in psutil.process_iter(['name', 'pid', 'status']):
            if name.lower() in (proc.info['name'] or "").lower():
                proc.suspend()
                return f"Suspended '{proc.info['name']}' (PID {proc.info['pid']})."
        return f"Process '{name}' not found."
    except Exception as e:
        return f"Suspend failed: {e}"


def resume_process(name: str) -> str:
    """Resume a suspended process."""
    try:
        import psutil
        for proc in psutil.process_iter(['name', 'pid', 'status']):
            if name.lower() in (proc.info['name'] or "").lower():
                proc.resume()
                return f"Resumed '{proc.info['name']}' (PID {proc.info['pid']})."
        return f"Process '{name}' not found."
    except Exception as e:
        return f"Resume failed: {e}"


def schedule_shutdown(minutes: int) -> str:
    """Schedule auto-shutdown after N minutes."""
    secs = max(1, int(minutes)) * 60
    r = subprocess.run(["shutdown", "/s", "/t", str(secs)],
                      capture_output=True, text=True)
    if r.returncode == 0:
        return f"Shutdown scheduled in {minutes} minute(s)."
    return f"Shutdown scheduling failed: {r.stderr.strip()}"


def cancel_scheduled_shutdown() -> str:
    """Cancel any pending shutdown or restart timer."""
    r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
    if r.returncode == 0:
        return "Scheduled shutdown cancelled."
    return "No shutdown was scheduled (or cancellation failed)."


def get_startup_programs() -> str:
    """List programs set to run at startup."""
    script = (
        "Get-CimInstance -Class Win32_StartupCommand | "
        "Select-Object Name,Command,Location | Format-Table -AutoSize | Out-String"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=10)
        return "Startup Programs:\n" + (r.stdout.strip() or "None found.")
    except Exception as e:
        return f"Failed to list startup programs: {e}"


def get_network_status() -> str:
    """Get IP address, gateway, DNS, and connection type."""
    script = (
        "Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway} | "
        "Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer | "
        "Format-List | Out-String"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=8)
        out = r.stdout.strip()
        # Also get public IP quickly
        try:
            import urllib.request
            pub_ip = urllib.request.urlopen("https://api.ipify.org", timeout=3).read().decode()
            out += f"\nPublic IP: {pub_ip}"
        except Exception:
            pass
        return out or "No active network connection found."
    except Exception as e:
        return f"Network status failed: {e}"


def get_wifi_networks() -> str:
    """Scan and list available Wi-Fi networks with signal strength."""
    try:
        r = subprocess.run(["netsh", "wlan", "show", "networks", "mode=Bssid"],
                          capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "No Wi-Fi networks found."
    except Exception as e:
        return f"Wi-Fi scan failed: {e}"


def connect_wifi(ssid: str, password: str = "") -> str:
    """Connect to a Wi-Fi network by SSID."""
    try:
        if password:
            # Create a temporary profile XML
            profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM><security>
    <authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption></authEncryption>
    <sharedKey><keyType>passPhrase</keyType><protected>false</protected><keyMaterial>{password}</keyMaterial></sharedKey>
  </security></MSM>
</WLANProfile>"""
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False, mode="w") as f:
                f.write(profile_xml)
                tmp = f.name
            subprocess.run(["netsh", "wlan", "add", "profile", f"filename={tmp}"], capture_output=True)
            os.unlink(tmp)
        r = subprocess.run(["netsh", "wlan", "connect", f"name={ssid}"],
                          capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or f"Connecting to '{ssid}'..."
    except Exception as e:
        return f"Wi-Fi connect failed: {e}"


def toggle_airplane_mode(enable: bool) -> str:
    """Toggle airplane mode (disables all radios)."""
    state = 1 if enable else 0
    label = "enabled" if enable else "disabled"
    script = (
        f"$key = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\RadioManagement\\SystemRadioState';"
        f"Set-ItemProperty -Path $key -Name '(Default)' -Value {state} -Type DWord -ErrorAction SilentlyContinue"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                      capture_output=True, timeout=5)
        return f"Airplane mode {label}. Radios will toggle within a few seconds."
    except Exception as e:
        return f"Airplane mode toggle failed: {e}"


def ping_host(host: str = "8.8.8.8", count: int = 4) -> str:
    """Ping a host and return latency and packet loss."""
    try:
        r = subprocess.run(["ping", host, "-n", str(count)],
                          capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception as e:
        return f"Ping failed: {e}"


def get_open_ports() -> str:
    """List all open TCP ports and which processes own them."""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
        lines = [l for l in r.stdout.splitlines() if "LISTENING" in l]
        return "Open Listening Ports:\n" + "\n".join(lines[:30]) if lines else "No listening ports found."
    except Exception as e:
        return f"Port check failed: {e}"


def run_speed_test() -> str:
    """Run a network speed test using speedtest-cli."""
    try:
        r = subprocess.run(["speedtest", "--simple"],
                          capture_output=True, text=True, timeout=60)
        out = r.stdout.strip()
        return out if out else "Speed test completed (no output)."
    except FileNotFoundError:
        try:
            r2 = subprocess.run(["python", "-m", "speedtest", "--simple"],
                               capture_output=True, text=True, timeout=60)
            return r2.stdout.strip() or "Speed test module not found. Install with: pip install speedtest-cli"
        except Exception:
            pass
    except Exception as e:
        return f"Speed test failed: {e}"
    return "Install speedtest: pip install speedtest-cli"


def list_open_windows() -> str:
    """List all visible open windows with titles and process names."""
    try:
        if gw:
            wins = [(w.title, w.width, w.height) for w in gw.getAllWindows()
                    if w.title.strip() and w.visible]
            if wins:
                return "Open Windows:\n" + "\n".join(f"  {t} [{w}x{h}]" for t, w, h in wins[:30])
        script = "Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object Name,MainWindowTitle | Format-Table | Out-String"
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=8)
        return r.stdout.strip() or "No windows found."
    except Exception as e:
        return f"Window list failed: {e}"


def minimize_window(title: str) -> str:
    """Minimize a window by partial title match."""
    try:
        if gw:
            import difflib
            all_titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
            match = difflib.get_close_matches(title.lower(),
                                              [t.lower() for t in all_titles], n=1, cutoff=0.4)
            if match:
                idx = [t.lower() for t in all_titles].index(match[0])
                for w in gw.getWindowsWithTitle(all_titles[idx]):
                    w.minimize()
                    return f"Minimized: {all_titles[idx]}"
    except Exception:
        pass
    import ctypes
    ctypes.windll.user32.ShowWindow(ctypes.windll.user32.FindWindowW(None, title), 6)
    return f"Minimize sent to '{title}'"


def maximize_window(title: str) -> str:
    """Maximize a window by partial title match."""
    try:
        if gw:
            import difflib
            all_titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
            match = difflib.get_close_matches(title.lower(),
                                              [t.lower() for t in all_titles], n=1, cutoff=0.4)
            if match:
                idx = [t.lower() for t in all_titles].index(match[0])
                for w in gw.getWindowsWithTitle(all_titles[idx]):
                    w.maximize()
                    return f"Maximized: {all_titles[idx]}"
    except Exception:
        pass
    return f"Window '{title}' not found."


def tile_windows(layout: str = "side_by_side") -> str:
    """
    Snap two windows side-by-side, to quadrants, or stacked.
    layout: 'side_by_side' | 'quad' | 'stack'
    """
    try:
        if layout == "side_by_side":
            subprocess.run(["powershell", "-Command",
                           "(New-Object -ComObject Shell.Application).TileVertically()"],
                          capture_output=True, timeout=5)
        elif layout == "stack":
            subprocess.run(["powershell", "-Command",
                           "(New-Object -ComObject Shell.Application).TileHorizontally()"],
                          capture_output=True, timeout=5)
        elif layout == "quad":
            subprocess.run(["powershell", "-Command",
                           "(New-Object -ComObject Shell.Application).CascadeWindows()"],
                          capture_output=True, timeout=5)
        return f"Windows arranged: {layout}"
    except Exception as e:
        return f"Window tiling failed: {e}"


def take_screenshot(save_path: str = "") -> str:
    """Take a full-screen screenshot and save it."""
    import datetime
    if not save_path:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(os.path.expanduser("~"), "Desktop", f"screenshot_{ts}.png")
    try:
        import PIL.ImageGrab
        img = PIL.ImageGrab.grab()
        img.save(save_path)
        return f"Screenshot saved: {save_path}"
    except ImportError:
        pass
    try:
        pyautogui.screenshot(save_path)
        return f"Screenshot saved: {save_path}"
    except Exception as e:
        return f"Screenshot failed: {e}"


def get_clipboard_content() -> str:
    """Read current clipboard text content."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        content = root.clipboard_get()
        root.destroy()
        return f"Clipboard: {content[:500]}" if content else "Clipboard is empty."
    except Exception:
        pass
    try:
        r = subprocess.run(["powershell", "-Command", "Get-Clipboard"],
                          capture_output=True, text=True, timeout=5)
        return f"Clipboard: {r.stdout.strip()}" if r.stdout.strip() else "Clipboard is empty."
    except Exception as e:
        return f"Clipboard read failed: {e}"


def set_clipboard_content(text: str) -> str:
    """Write text to the clipboard."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return f"Clipboard set ({len(text)} chars)."
    except Exception:
        pass
    try:
        subprocess.run(["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
                      capture_output=True, timeout=5)
        return f"Clipboard set ({len(text)} chars)."
    except Exception as e:
        return f"Clipboard write failed: {e}"


def type_text_input(text: str, delay_ms: int = 30) -> str:
    """Type text into the currently focused window."""
    try:
        pyautogui.write(text, interval=delay_ms / 1000.0)
        return f"Typed {len(text)} characters."
    except Exception as e:
        return f"Type failed: {e}"


def press_keyboard_hotkey(keys: str) -> str:
    """Press a keyboard shortcut. keys: 'ctrl+c', 'alt+f4', 'win+d', etc."""
    try:
        parts = [k.strip() for k in keys.split("+")]
        pyautogui.hotkey(*parts)
        return f"Pressed: {keys}"
    except Exception as e:
        return f"Hotkey failed: {e}"


def get_installed_apps(search: str = "") -> str:
    """List installed applications, optionally filtered by name."""
    try:
        script = (
            "Get-Package | Select-Object Name,Version | Sort-Object Name | "
            "Format-Table -AutoSize | Out-String"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=20)
        out = r.stdout.strip()
        if search:
            lines = [l for l in out.splitlines() if search.lower() in l.lower()]
            return "\n".join(lines) if lines else f"No apps matching '{search}' found."
        return out[:3000] if out else "Could not list installed apps."
    except Exception as e:
        return f"App list failed: {e}"


def get_disk_usage() -> str:
    """Get usage for all drives: total, used, free, and percent full."""
    try:
        import shutil as _sh
        lines = ["Disk Usage:"]
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            path = f"{letter}:\\"
            if os.path.exists(path):
                try:
                    total, used, free = _sh.disk_usage(path)
                    pct = round(used / total * 100)
                    lines.append(
                        f"  {letter}: {free//1024//1024//1024}GB free / "
                        f"{total//1024//1024//1024}GB total ({pct}% used)"
                    )
                except Exception:
                    pass
        return "\n".join(lines) if len(lines) > 1 else "No drives found."
    except Exception as e:
        return f"Disk usage failed: {e}"


def get_largest_files(directory: str, count: int = 10) -> str:
    """Find the top N largest files in a directory tree."""
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        return f"Directory not found: {directory}"
    files = []
    try:
        for root, _, fnames in os.walk(directory):
            for fn in fnames:
                fp = os.path.join(root, fn)
                try:
                    files.append((os.path.getsize(fp), fp))
                except Exception:
                    pass
    except Exception as e:
        return f"Scan failed: {e}"
    files.sort(reverse=True)
    lines = [f"Top {count} largest files in {directory}:"]
    for sz, fp in files[:count]:
        sz_s = f"{sz//1024//1024}MB" if sz > 1024*1024 else f"{sz//1024}KB"
        lines.append(f"  {sz_s:>8}  {fp}")
    return "\n".join(lines)


def get_folder_size(path: str) -> str:
    """Recursively calculate total size of a folder."""
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return f"Folder not found: {path}"
    total = 0
    count = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except Exception:
                pass
    gb = total / 1024 / 1024 / 1024
    mb = total / 1024 / 1024
    sz_s = f"{gb:.2f} GB" if gb >= 1 else f"{mb:.1f} MB"
    return f"Folder size: {sz_s} ({count} files)\nPath: {path}"