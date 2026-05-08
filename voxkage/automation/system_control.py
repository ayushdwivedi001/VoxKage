import os
import time
import difflib
import ctypes
import random
import subprocess

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
    """
    level_percent: 0-100
    """
    # First mute then raise
    pyautogui.press("volumemute")
    # approximate 50 presses to go 0â†’100
    steps = int(level_percent / 2)
    for _ in range(steps):
        pyautogui.press("volumeup")
    return f"Volume set to {level_percent}%"

def set_brightness(level_percent):
    """
    Uses PowerShell via os.system to set monitor brightness.
    """
    # Requires Win10+ and that WmiMonitorBrightnessMethods is available
    cmd = (
        f"powershell (Get-WmiObject -Namespace root/WMI "
        f"-Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level_percent})"
    )
    os.system(cmd)
    return f"Brightness set to {level_percent}%"

def toggle_wifi(turn_on: bool):
    state = "enable" if turn_on else "disable"
    # assumes your interface name is "Wi-Fi"
    os.system(f'netsh interface set interface "Wi-Fi" {state}')
    return f"Wi-Fi turned {'on' if turn_on else 'off'}"

def toggle_bluetooth(turn_on: bool):
    # simple start/stop of bluetooth support service
    svc = "bthserv"
    if turn_on:
        os.system(f"net start {svc}")
        return "Bluetooth turned on"
    else:
        os.system(f"net stop {svc}")
        return "Bluetooth turned off"


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
    try:
        os.system("start ms-settings:nightlight")
        return "Opened Night Light settings. You can toggle it manually from the settings window."
    except Exception as e:
        return f"Failed to open Night Light settings: {e}"

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