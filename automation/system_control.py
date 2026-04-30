import os
import time
import difflib
import ctypes
import random
import pygetwindow as gw
import pyautogui
from pywinauto import Desktop
import subprocess

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
    # approximate 50 presses to go 0→100
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
def change_wallpaper_from_folder(folder_path="C:\\wallpapers"):
    if not os.path.exists(folder_path):
        return "Wallpaper folder not found."

    wallpapers = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    if not wallpapers:
        return "No wallpapers found in the folder."

    selected = os.path.join(folder_path, random.choice(wallpapers))
    ctypes.windll.user32.SystemParametersInfoW(20, 0, selected, 3)
    return f"Wallpaper changed to {os.path.basename(selected)}."

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
                win.close()
                closed_count += 1
        except Exception:
            pass
            
    if closed_count > 0:
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
