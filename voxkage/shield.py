"""
VoxKage Shield Protocol — Three-Layer Safety System

Layer 1: Hard-blocked system paths (never overridable)
Layer 2: Safe Mode confirmation gate (default ON, user can disable)
Layer 3: Audit log (every destructive action logged with timestamp)

Used by: os_control_server.py, file_ops_server.py, system_server.py
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path


# ── Load blocklist ────────────────────────────────────────────────────────────

_SHIELD_FILE = os.path.join(os.path.dirname(__file__), "data", "shield_blocklist.json")
_AUDIT_LOG   = os.path.join(os.path.expandvars(r"C:\VoxKage\Brain"), "audit.log")


def _load_blocklist() -> dict:
    """Load the shield blocklist, returning defaults if file is missing."""
    try:
        with open(_SHIELD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "protected_paths": [
                "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
            ],
            "protected_commands": [
                "format ", "diskpart", "rm -rf /",
            ],
            "protected_extensions_from_delete": [".sys", ".dll", ".exe"],
            "safe_mode_default": True,
        }


_BLOCKLIST = _load_blocklist()


def _expand_path(p: str) -> str:
    """Expand ~, %ENV_VARS%, and normalize separators."""
    p = os.path.expanduser(p)
    p = os.path.expandvars(p)
    return p.replace("/", "\\").rstrip("\\").lower()


# Pre-compute expanded protected paths for fast matching
_PROTECTED_PATHS = [_expand_path(p) for p in _BLOCKLIST.get("protected_paths", [])]
_PROTECTED_CMDS  = [c.lower() for c in _BLOCKLIST.get("protected_commands", [])]
_PROTECTED_EXTS  = [e.lower() for e in _BLOCKLIST.get("protected_extensions_from_delete", [])]


# ── Layer 1: Hard-Block Check ─────────────────────────────────────────────────

def shield_check_path(path: str) -> str | None:
    """
    Check if a path is protected by the Shield Protocol.

    Returns:
        None if the path is safe to operate on.
        A human-readable error string if the path is BLOCKED.
    """
    normalized = _expand_path(path)
    for blocked in _PROTECTED_PATHS:
        if normalized.startswith(blocked) or normalized == blocked:
            return (
                f"[VOXKAGE SHIELD] BLOCKED: '{path}' is a protected system path.\n"
                f"VoxKage cannot modify files inside {blocked} for safety.\n"
                f"This restriction cannot be overridden."
            )
    return None


def shield_check_command(command: str) -> str | None:
    """
    Check if a shell command matches a dangerous pattern.

    Returns:
        None if the command is safe to run.
        A human-readable error string if the command is BLOCKED.
    """
    cmd_lower = command.lower().strip()
    for blocked in _PROTECTED_CMDS:
        if blocked in cmd_lower:
            return (
                f"[VOXKAGE SHIELD] BLOCKED: This command matches a dangerous pattern.\n"
                f"Pattern matched: '{blocked}'\n"
                f"VoxKage refuses to execute commands that could damage the system.\n"
                f"This restriction cannot be overridden."
            )
    return None


def shield_check_delete(path: str) -> str | None:
    """
    Check if a file should be protected from deletion based on its extension.

    Returns:
        None if the file can be deleted.
        A human-readable error string if the file is BLOCKED.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _PROTECTED_EXTS:
        # Only block if the file is in a system directory
        normalized = _expand_path(path)
        for sys_path in _PROTECTED_PATHS:
            if normalized.startswith(sys_path):
                return (
                    f"[VOXKAGE SHIELD] BLOCKED: Cannot delete '{path}'.\n"
                    f"System {ext} files in protected directories are never deletable."
                )
    # Also block any delete inside protected paths
    return shield_check_path(path)


# ── Layer 2: Safe Mode Confirmation ───────────────────────────────────────────

def is_safe_mode_enabled() -> bool:
    """Check if Safe Mode is enabled (default: True)."""
    env_val = os.environ.get("VOXKAGE_SAFE_MODE", "").lower().strip()
    if env_val in ("0", "false", "off", "no"):
        return False
    # Check config.json
    try:
        config_path = os.path.join(os.path.expanduser("~"), ".voxkage", "config.json")
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
                if cfg.get("safe_mode") is False:
                    return False
    except Exception:
        pass
    return _BLOCKLIST.get("safe_mode_default", True)


# ── Layer 3: Audit Log ────────────────────────────────────────────────────────

def audit_log(action: str, target: str, allowed: bool):
    """
    Log a destructive action to the audit log.

    Args:
        action:  Short verb — "shell", "delete", "move", "kill_process"
        target:  The path or command being acted upon
        allowed: True if the action was permitted, False if blocked
    """
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        status = "ALLOWED" if allowed else "BLOCKED"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {status:8s} | {action}: {target}\n"
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # Audit logging is best-effort — never crash VoxKage


# ── Convenience: full shield gate ─────────────────────────────────────────────

def shield_gate_path(path: str, action: str = "access") -> str | None:
    """
    Combined shield check + audit for path operations.
    Returns None if allowed, error string if blocked.
    """
    result = shield_check_path(path)
    if result:
        audit_log(action, path, allowed=False)
        return result
    audit_log(action, path, allowed=True)
    return None


def shield_gate_command(command: str) -> str | None:
    """
    Combined shield check + audit for shell commands.
    Returns None if allowed, error string if blocked.
    """
    result = shield_check_command(command)
    if result:
        audit_log("shell", command, allowed=False)
        return result
    audit_log("shell", command, allowed=True)
    return None
