"""
MCP Server: VoxKage Dev Server Manager

Smart local development server lifecycle management.
Detects project type, starts the correct server, waits for readiness,
and exposes the screenshot path for Gemini vision analysis.

Tools:
  detect_and_start_server(project_dir, port) -- Auto-detect + start correct server
  stop_server(port)                          -- Kill whatever is on a port
  get_server_status(port)                    -- Check if server is running
  wait_for_server(url, timeout_secs)         -- Poll until server responds
  get_latest_screenshot_path()               -- Return the absolute path of the
                                                last browser screenshot (for vision)
  capture_and_score_ui(url, label)           -- Navigate + screenshot + return path
                                                ready for Gemini vision analysis

Run standalone: python mcp_servers/devserver_server.py
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Optional

# -- Path setup ----------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-devserver")

# -- Constants -----------------------------------------------------------------
# Where voxkage-browser saves its browser state screenshots
_VOXKAGE_BRAIN_DIR = r"C:\VoxKage\Brain"
_SCREENSHOT_FILENAME = "latest_browser_state.jpg"
_SCREENSHOT_PATH = os.path.join(_VOXKAGE_BRAIN_DIR, _SCREENSHOT_FILENAME)
_FALLBACK_SCREENSHOT = os.path.join(os.environ.get("TEMP", "C:\\Windows\\Temp"), "voxkage_browser_state.jpg")

# Track running servers: {port: {"pid": int, "command": str, "url": str, "proc": Popen}}
_running_servers: dict = {}

# -- Helpers -------------------------------------------------------------------

def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int) -> str:
    """Kill whatever process is listening on the given port."""
    killed = []

    # Method 1: kill from our own tracking dict
    if port in _running_servers:
        info = _running_servers.pop(port)
        proc = info.get("proc")
        pid  = info.get("pid")
        if proc:
            try:
                proc.kill()
                killed.append(f"tracked process (pid {pid})")
            except Exception:
                pass

    # Method 2: use netstat + taskkill to catch anything we didn't spawn
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) > 4:   # never kill System (4)
                    subprocess.run(["taskkill", "/f", "/pid", pid],
                                   capture_output=True, timeout=5)
                    killed.append(f"pid {pid} via netstat")
    except Exception as e:
        pass

    time.sleep(0.5)   # give OS time to release the port
    return f"Killed port {port}: {', '.join(killed) if killed else 'nothing was running'}"


def _read_package_json(project_dir: str) -> dict:
    pkg = os.path.join(project_dir, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _has_file(project_dir: str, *names) -> bool:
    for name in names:
        if os.path.isfile(os.path.join(project_dir, name)):
            return True
    return False


def _has_dep(pkg: dict, *names) -> bool:
    all_deps = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))
    for name in names:
        if name in all_deps:
            return True
    return False


def _detect_project_type(project_dir: str) -> dict:
    """
    Scans a project directory and returns:
      {
        "type":    str,   # "nextjs" | "vite" | "react" | "vue" | "angular" | "svelte"
                          # | "django" | "flask" | "fastapi" | "html" | "unknown"
        "command": list,  # e.g. ["npm", "run", "dev"]
        "port":    int,   # default port for this framework
        "cwd":     str,   # where to run the command
        "note":    str,   # human-readable explanation
      }
    """
    pkg = _read_package_json(project_dir)

    # -- Next.js ---------------------------------------------------------------
    if _has_dep(pkg, "next"):
        return {
            "type": "nextjs",
            "command": ["npm", "run", "dev"],
            "port": 3000,
            "cwd": project_dir,
            "note": "Next.js detected (next in dependencies)",
        }

    # -- Vite (React/Vue/Svelte via Vite) -------------------------------------
    if _has_dep(pkg, "vite", "@vitejs/plugin-react", "@vitejs/plugin-vue"):
        port = 5173
        return {
            "type": "vite",
            "command": ["npm", "run", "dev"],
            "port": port,
            "cwd": project_dir,
            "note": "Vite project detected",
        }

    # -- Create React App ------------------------------------------------------
    if _has_dep(pkg, "react-scripts"):
        return {
            "type": "react-cra",
            "command": ["npm", "start"],
            "port": 3000,
            "cwd": project_dir,
            "note": "Create React App detected (react-scripts)",
        }

    # -- Angular ---------------------------------------------------------------
    if _has_dep(pkg, "@angular/core"):
        return {
            "type": "angular",
            "command": ["npx", "ng", "serve", "--port", "4200"],
            "port": 4200,
            "cwd": project_dir,
            "note": "Angular detected (@angular/core)",
        }

    # -- Svelte Kit ------------------------------------------------------------
    if _has_dep(pkg, "@sveltejs/kit"):
        return {
            "type": "svelte-kit",
            "command": ["npm", "run", "dev"],
            "port": 5173,
            "cwd": project_dir,
            "note": "SvelteKit detected",
        }

    # -- Vue (Nuxt) ------------------------------------------------------------
    if _has_dep(pkg, "nuxt"):
        return {
            "type": "nuxt",
            "command": ["npm", "run", "dev"],
            "port": 3000,
            "cwd": project_dir,
            "note": "Nuxt.js detected",
        }

    # -- Generic npm project (has package.json + dev script) ------------------
    scripts = pkg.get("scripts", {})
    if pkg and "dev" in scripts:
        return {
            "type": "npm-generic",
            "command": ["npm", "run", "dev"],
            "port": 3000,
            "cwd": project_dir,
            "note": f"Generic npm project (dev script: {scripts['dev'][:60]})",
        }
    if pkg and "start" in scripts:
        return {
            "type": "npm-generic",
            "command": ["npm", "start"],
            "port": 3000,
            "cwd": project_dir,
            "note": f"Generic npm project (start script: {scripts['start'][:60]})",
        }

    # -- Django ----------------------------------------------------------------
    if _has_file(project_dir, "manage.py"):
        return {
            "type": "django",
            "command": ["python", "manage.py", "runserver", "8000"],
            "port": 8000,
            "cwd": project_dir,
            "note": "Django detected (manage.py)",
        }

    # -- Flask / FastAPI -------------------------------------------------------
    for app_file in ["app.py", "main.py", "run.py", "server.py"]:
        fpath = os.path.join(project_dir, app_file)
        if os.path.isfile(fpath):
            try:
                content = open(fpath, encoding="utf-8", errors="ignore").read()
            except Exception:
                content = ""
            if "flask" in content.lower():
                return {
                    "type": "flask",
                    "command": ["python", app_file],
                    "port": 5000,
                    "cwd": project_dir,
                    "note": f"Flask detected in {app_file}",
                }
            if "fastapi" in content.lower() or "uvicorn" in content.lower():
                return {
                    "type": "fastapi",
                    "command": ["uvicorn", app_file.replace(".py", "") + ":app", "--reload", "--port", "8000"],
                    "port": 8000,
                    "cwd": project_dir,
                    "note": f"FastAPI detected in {app_file}",
                }

    # -- Plain HTML/CSS/JS (static site) --------------------------------------
    html_files = [f for f in os.listdir(project_dir) if f.endswith(".html")]
    if html_files:
        return {
            "type": "html-static",
            "command": ["python", "-m", "http.server", "8080"],
            "port": 8080,
            "cwd": project_dir,
            "note": f"Static HTML site detected ({len(html_files)} HTML file(s): {', '.join(html_files[:3])})",
        }

    return {
        "type": "unknown",
        "command": [],
        "port": 8080,
        "cwd": project_dir,
        "note": "Could not detect project type. Please specify the start command manually.",
    }


def _get_screenshot_path() -> str:
    """Return the absolute path of the latest browser screenshot."""
    if os.path.isfile(_SCREENSHOT_PATH):
        return _SCREENSHOT_PATH
    if os.path.isfile(_FALLBACK_SCREENSHOT):
        return _FALLBACK_SCREENSHOT
    return ""


# -- MCP Tools -----------------------------------------------------------------

@mcp.tool()
def detect_project_type(project_dir: str = "") -> str:
    """
    Scan a project directory and determine what kind of server to run.
    Returns the detected framework, recommended command, and default port.

    Use BEFORE start_dev_server if you want to understand the project first
    without actually starting anything.

    Parameters:
      project_dir : Absolute path to the project directory.
                    Leave blank to use the current working directory.
    """
    cwd = project_dir.strip() or os.getcwd()
    if not os.path.isdir(cwd):
        return f"[DEVSERVER] Directory not found: {cwd}"

    info = _detect_project_type(cwd)
    cmd_str = " ".join(info["command"]) if info["command"] else "(unknown -- please specify)"
    return (
        f"[DEVSERVER] Project type detected: {info['type']}\n"
        f"  Directory : {cwd}\n"
        f"  Command   : {cmd_str}\n"
        f"  Port      : {info['port']}\n"
        f"  Note      : {info['note']}\n\n"
        f"Call start_dev_server(project_dir='{cwd}') to launch it."
    )


@mcp.tool()
def start_dev_server(
    project_dir: str = "",
    port: Optional[int] = None,
    command_override: str = "",
) -> str:
    """
    AUTO-DETECT the project type and start the correct local dev server.

    This is the ONLY correct way to serve a local web project for visual testing.
    NEVER use file:// paths in the browser -- ALWAYS use this tool first.

    Detection logic:
      Next.js              -> npm run dev      (port 3000)
      Vite / React / Vue   -> npm run dev      (port 5173)
      Create React App     -> npm start        (port 3000)
      Angular              -> ng serve         (port 4200)
      Django               -> python manage.py runserver (port 8000)
      Flask / FastAPI      -> python app.py    (port 5000/8000)
      Plain HTML/CSS/JS    -> python -m http.server (port 8080)

    Port conflict policy: ALWAYS kills the existing process on the port and
    restarts fresh. The same URL is preserved across restarts.

    Parameters:
      project_dir      : Absolute path to project. Blank = current working dir.
      port             : Override the default port. Leave blank to use framework default.
      command_override : Force a specific command, e.g. "npm run start:dev"
                         Leave blank for auto-detection.

    Returns: {url, port, server_type, command, pid}
    """
    cwd = project_dir.strip() or os.getcwd()
    if not os.path.isdir(cwd):
        return f"[DEVSERVER ERROR] Directory not found: {cwd}"

    info = _detect_project_type(cwd)

    if info["type"] == "unknown" and not command_override:
        return (
            f"[DEVSERVER] Could not auto-detect project type in: {cwd}\n"
            f"  Please specify: start_dev_server(project_dir='{cwd}', command_override='npm run dev')"
        )

    # Resolve final command and port
    if command_override:
        cmd_parts = command_override.split()
        final_cmd = cmd_parts
        final_type = "custom"
    else:
        final_cmd = info["command"]
        final_type = info["type"]

    final_port = port or info["port"]
    server_url = f"http://localhost:{final_port}"

    # Kill whatever is on this port to keep URL stable
    kill_msg = _kill_port(final_port)

    # For html-static, serve from the file's directory, inject port
    if final_type == "html-static" and not command_override:
        final_cmd = ["python", "-m", "http.server", str(final_port)]

    # Start the server process
    log_path = os.path.join(os.environ.get("TEMP", "C:\\Windows\\Temp"),
                            f"voxkage_devserver_{final_port}.log")
    try:
        with open(log_path, "w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                final_cmd,
                cwd=cwd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

        _running_servers[final_port] = {
            "pid": proc.pid,
            "command": " ".join(final_cmd),
            "url": server_url,
            "type": final_type,
            "cwd": cwd,
            "proc": proc,
            "log": log_path,
        }

        # Brief wait then verify it started
        time.sleep(2)
        if proc.poll() is not None:
            # Process already exited -- read log for error
            try:
                log_content = open(log_path, encoding="utf-8", errors="ignore").read()[-500:]
            except Exception:
                log_content = "(could not read log)"
            return (
                f"[DEVSERVER ERROR] Server process exited immediately (likely a startup error).\n"
                f"  Command : {' '.join(final_cmd)}\n"
                f"  Log     : {log_path}\n"
                f"  Output  : {log_content}\n\n"
                f"Check the command and try again."
            )

        return (
            f"[DEVSERVER] Server started successfully.\n"
            f"  Type      : {final_type}\n"
            f"  URL       : {server_url}\n"
            f"  Port      : {final_port}\n"
            f"  PID       : {proc.pid}\n"
            f"  Command   : {' '.join(final_cmd)}\n"
            f"  Directory : {cwd}\n"
            f"  Log file  : {log_path}\n\n"
            f"Next step: Call wait_for_server(url='{server_url}') to confirm it is ready,\n"
            f"then open_url('{server_url}') and get_browser_state() for visual inspection."
        )

    except FileNotFoundError as e:
        return (
            f"[DEVSERVER ERROR] Command not found: {final_cmd[0]}\n"
            f"  Make sure {final_cmd[0]} is installed and in PATH.\n"
            f"  Error: {e}"
        )
    except Exception as e:
        return f"[DEVSERVER ERROR] Failed to start server: {e}"


@mcp.tool()
def stop_server(port: int = 8080) -> str:
    """
    Stop the dev server running on the given port.
    Kills the process cleanly and frees the port.

    Use when: done testing, switching projects, or restarting.

    Parameters:
      port : The port the server is running on (e.g. 3000, 8080, 5173)
    """
    return _kill_port(port)


@mcp.tool()
def get_server_status(port: int = 8080) -> str:
    """
    Check if a dev server is currently running on a given port.

    Returns:
      - Whether the port is in use
      - The URL
      - The PID (if tracked)
      - The command that started it (if tracked)

    Parameters:
      port : Port to check (e.g. 3000, 8080, 5173)
    """
    in_use = _is_port_in_use(port)
    tracked = _running_servers.get(port)

    if not in_use and not tracked:
        return (
            f"[DEVSERVER] Port {port} is FREE -- no server running.\n"
            f"  Call start_dev_server() to launch one."
        )

    lines = [f"[DEVSERVER] Port {port}: {'IN USE' if in_use else 'was running (may have exited)'}"]
    if tracked:
        lines.append(f"  URL      : {tracked.get('url', f'http://localhost:{port}')}")
        lines.append(f"  Type     : {tracked.get('type', '?')}")
        lines.append(f"  PID      : {tracked.get('pid', '?')}")
        lines.append(f"  Command  : {tracked.get('command', '?')}")
        lines.append(f"  CWD      : {tracked.get('cwd', '?')}")
        proc = tracked.get("proc")
        if proc:
            alive = proc.poll() is None
            lines.append(f"  Process  : {'ALIVE' if alive else 'EXITED'}")
    else:
        lines.append(f"  URL      : http://localhost:{port}")
        lines.append(f"  (started externally -- not tracked by VoxKage)")

    return "\n".join(lines)


@mcp.tool()
def wait_for_server(
    url: str = "http://localhost:8080",
    timeout_secs: int = 30,
) -> str:
    """
    Wait until the local server at `url` responds with HTTP 200.
    ALWAYS call this after start_dev_server() before taking a screenshot.

    Why: Dev servers (especially Next.js, Vite) take a few seconds to compile.
    Opening the browser too early shows a blank page or error.

    Parameters:
      url          : Full URL to poll (e.g. "http://localhost:3000")
      timeout_secs : Max seconds to wait before giving up (default 30)

    Returns: "READY" | "TIMEOUT" | "ERROR: ..."
    """
    import urllib.request
    deadline = time.time() + timeout_secs
    last_err = ""
    attempts = 0

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 400:
                    return (
                        f"[DEVSERVER] Server READY at {url} (HTTP {resp.status})\n"
                        f"  Waited: {attempts} attempts\n"
                        f"  Next: Call open_url('{url}') then get_browser_state() for screenshot."
                    )
        except Exception as e:
            last_err = str(e)[:100]
        time.sleep(1)
        attempts += 1

    return (
        f"[DEVSERVER] TIMEOUT: Server at {url} did not respond within {timeout_secs}s.\n"
        f"  Last error : {last_err}\n"
        f"  Attempts   : {attempts}\n"
        f"  Suggestion : Check if the server started correctly via get_server_status().\n"
        f"               Look at the log file for startup errors."
    )


@mcp.tool()
def get_latest_screenshot_path() -> str:
    """
    Returns the absolute file path of the most recent browser screenshot
    taken by VoxKage's browser tools (get_browser_state / take_screenshot).

    Use this to get the path for Gemini vision analysis:
      1. Call get_browser_state() -- this takes the screenshot
      2. Call get_latest_screenshot_path() -- get the path
      3. Pass the path to analyze_specific_file(file_path=..., query="score this UI")

    This is the bridge between the browser tool and Gemini's vision capability.
    """
    path = _get_screenshot_path()
    if not path:
        return (
            "[DEVSERVER] No screenshot found yet.\n"
            "  Call get_browser_state() or take_screenshot() first to capture one.\n"
            f"  Expected location: {_SCREENSHOT_PATH}"
        )

    stat = os.stat(path)
    age_secs = time.time() - stat.st_mtime
    return (
        f"[SCREENSHOT PATH]\n"
        f"  Path     : {path}\n"
        f"  Size     : {stat.st_size:,} bytes\n"
        f"  Age      : {age_secs:.0f} seconds ago\n\n"
        f"To score the UI, call:\n"
        f"  analyze_specific_file(\n"
        f"    file_path='{path}',\n"
        f"    query='Score this UI design 0-100. Evaluate: visual hierarchy, "
        f"color harmony, spacing, typography, consistency. "
        f"Return: SCORE: X/100 | ISSUES: [list] | PASS or FAIL'\n"
        f"  )"
    )


@mcp.tool()
def get_devserver_qa_guide() -> str:
    """
    Returns the step-by-step Visual QA workflow guide for VoxKage.
    Call this when unsure how to run the visual self-correction loop.

    This explains:
    - How to start a server for any project type
    - How to take a screenshot and score the UI
    - How to do the self-correction loop (up to 10 tries)
    - How to compare against a reference image
    """
    return """
[VOXKAGE VISUAL QA GUIDE]

=== WORKFLOW A: Basic Visual Check ===
(Triggered by: "check it", "verify the UI", "run and inspect")

  Step 1: start_dev_server(project_dir='<path>')
  Step 2: wait_for_server(url='http://localhost:<port>')
  Step 3: open_url('http://localhost:<port>')
  Step 4: get_browser_state()              -- takes + saves screenshot
  Step 5: get_latest_screenshot_path()     -- get the saved path
  Step 6: analyze_specific_file(file_path='<path from step 5>',
            query='Score this UI 0-100. Visual hierarchy, color harmony,
                   spacing, typography. Return: SCORE: X/100 | ISSUES: [list]')
  Step 7: If SCORE >= 85 -> report and stop.
          If SCORE < 85  -> note issues, edit files, loop back to Step 4.
          Max 10 tries.


=== WORKFLOW B: Reference Image Comparison ===
(Triggered by: "match this design", "compare with reference image",
               "does it look like the image", "build it to look like X")

  Step 1: Get reference image path from user.
  Step 2: start_dev_server(project_dir='<path>')
  Step 3: wait_for_server(url='http://localhost:<port>')
  Step 4: open_url('http://localhost:<port>')
  Step 5: get_browser_state()
  Step 6: get_latest_screenshot_path()     -- live screenshot path
  Step 7: analyze_specific_file(file_path='<screenshot>',
            query='Compare this screenshot to the reference image at <ref_path>.
                   Score visual similarity 0-100. List layout differences,
                   color mismatches, missing elements.
                   Return: SIMILARITY: X/100 | DIFFERENCES: [list]')
  Step 8: If SIMILARITY >= 85 -> stop, report match.
          If SIMILARITY < 85  -> apply targeted fixes, retry. Max 10 tries.
  Step 9: Final report after 10 tries: "Achieved X/100. Remaining: [list]"


=== SERVER DETECTION CHEAT SHEET ===
  Next.js          -> npm run dev      -> http://localhost:3000
  Vite             -> npm run dev      -> http://localhost:5173
  Create React App -> npm start        -> http://localhost:3000
  Angular          -> ng serve         -> http://localhost:4200
  Django           -> manage.py run    -> http://localhost:8000
  Flask            -> python app.py    -> http://localhost:5000
  Plain HTML       -> python -m http.server -> http://localhost:8080


=== ABSOLUTE RULES ===
  1. NEVER open a local file via file:// URL in the browser.
  2. ALWAYS call start_dev_server() first, then wait_for_server().
  3. ALWAYS use get_browser_state() (not gui_step) to screenshot a webpage.
  4. ALWAYS use get_latest_screenshot_path() to get the image for vision analysis.
  5. DO NOT call gui_step to interact with webpage buttons -- use agent_step.
"""


if __name__ == "__main__":
    mcp.run()
