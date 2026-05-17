"""
MCP Server: VoxKage Notifications
Sends Windows toast notifications so background sub-agents can alert the user
when tasks complete without requiring the user to poll check_tasks().

Requirements:
    pip install winotify

Standalone — run directly:
    python mcp_servers/notify_server.py
"""

import os
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-notify")

# ── Icon path ─────────────────────────────────────────────────────────────────
_ICON_PATH = os.path.join(_ROOT, "assets", "voxkage_icon.ico")
if not os.path.isfile(_ICON_PATH):
    _ICON_PATH = ""  # winotify works without an icon


def _toast(title: str, message: str, duration: str = "short") -> str:
    """
    Fire a Windows toast notification using winotify.
    Falls back to a visible MessageBox if winotify is unavailable.
    duration: 'short' (5s) | 'long' (25s)
    """
    try:
        from winotify import Notification, audio

        toast = Notification(
            app_id   = "VoxKage",
            title    = title,
            msg      = message,
            duration = duration,
            icon     = _ICON_PATH if _ICON_PATH else None,
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return "ok"
    except ImportError:
        # winotify not installed — fallback: PowerShell balloon
        import subprocess
        ps_cmd = (
            f"[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
            f"$n = New-Object System.Windows.Forms.NotifyIcon;"
            f"$n.Icon = [System.Drawing.SystemIcons]::Information;"
            f"$n.Visible = $true;"
            f"$n.ShowBalloonTip(5000, '{title.replace(chr(39), '')}', '{message[:200].replace(chr(39), '')}', [System.Windows.Forms.ToolTipIcon]::Info);"
            f"Start-Sleep -s 6; $n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return "ok-fallback"
    except Exception as e:
        return f"error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def notify_task_done(
    task_id: str,
    title: str,
    message: str,
) -> str:
    """
    *** CALLED BY SUB-AGENTS ONLY — after complete_task() ***

    Sends a Windows desktop toast notification to alert the user that a
    background task has finished. Call this IMMEDIATELY after calling
    complete_task() so the user sees a notification without having to ask.

    Parameters:
      task_id : The task ID that just completed
      title   : Short notification title (e.g. "Research Complete")
      message : 1-2 sentence summary of what was done
                (e.g. "Your PDF on NCERT Math books is ready in Documents.")
    """
    full_title = f"VoxKage \u2014 {title}"
    full_msg   = f"[Task {task_id}] {message}"
    result     = _toast(full_title, full_msg, duration="long")
    return f"Notification fired (status={result}): {full_title} | {full_msg}"


@mcp.tool()
def notify(
    title: str,
    message: str,
    urgent: bool = False,
) -> str:
    """
    Send a Windows desktop toast notification from the MAIN VoxKage session.

    Use for:
    - Reminders: "Remind me in 5 minutes to check the file"
    - Proactive alerts: "I noticed your disk is almost full"
    - Any time you want to push info to the user outside of the chat

    Parameters:
      title   : Short notification title
      message : Notification body (max ~200 chars for readability)
      urgent  : If True, uses 'long' duration (25s) instead of 'short' (5s)
    """
    duration = "long" if urgent else "short"
    result   = _toast(f"VoxKage \u2014 {title}", message, duration=duration)
    return f"Notification sent (status={result})"


if __name__ == "__main__":
    mcp.run()
