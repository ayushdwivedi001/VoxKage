"""
voxkage/_env.py — Shared .env loader for all MCP servers and modules.

Loads environment variables from the user's ~/.voxkage/.env file,
with a fallback to the repo root .env for dev mode.

override=True is intentional: the user's ~/.voxkage/.env MUST win over
any stale tokens already in the shell environment. This prevents the
'token from old session' bug where a new bot token is ignored.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


_loaded = False


def load_voxkage_env(force: bool = False) -> None:
    """
    Load .env from the user's VoxKage data directory.
    Falls back to repo-root .env in dev mode.

    override=True ensures ~/.voxkage/.env always wins over any stale
    shell-level env vars (prevents token name mismatch bugs on reinstalls).

    Args:
        force: If True, re-load even if already loaded. Use after writing
               new tokens via plugin setup so they take effect immediately.
    """
    global _loaded
    if _loaded and not force:
        return
    _loaded = True

    # Priority 1: ~/.voxkage/.env
    user_env = Path.home() / ".voxkage" / ".env"
    if user_env.exists():
        load_dotenv(str(user_env), override=True)
        return

    # Priority 2: Repo root .env (dev mode — .env next to pyproject.toml)
    # Walk up from this file's location to find the repo root
    current = Path(__file__).parent
    for _ in range(5):  # max 5 levels up
        candidate = current / ".env"
        if candidate.exists():
            load_dotenv(str(candidate), override=True)
            _monkey_patch_fastmcp()
            return
        current = current.parent

    _monkey_patch_fastmcp()


def _check_cognitive_gate_status(tool_name: str) -> str | None:
    import time
    import json
    from pathlib import Path
    
    session_file = Path.home() / ".voxkage" / "cognitive" / "session_state.json"
    if not session_file.exists():
        return f"PROTOCOL VIOLATION: You must call start_turn(user_message) before calling '{tool_name}'."
        
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            session = json.load(f)
    except Exception:
        return None
        
    last_start_turn = session.get("last_start_turn_ts", 0.0)
    last_action = session.get("last_action_ts", 0.0)
    now = time.time()
    
    # Idle threshold: 30 seconds. If time since last tool execution exceeds 30s,
    # it indicates a new turn. If start_turn was not called in this new turn, block it.
    if (now - last_action > 30.0) and (last_start_turn < last_action):
        return (
            f"\n⚠⚠⚠ PROTOCOL VIOLATION ⚠⚠⚠\n"
            f"You called tool '{tool_name}' WITHOUT calling start_turn() for the current turn!\n"
            f"RULE ZERO: start_turn(user_message) MUST be your FIRST action every turn.\n"
            f"Please call start_turn() now to unlock other tools.\n"
            f"⚠⚠⚠ END VIOLATION ⚠⚠⚠\n\n"
        )
        
    # Update last_action_ts in the session file so subsequent checks know we are active in this turn
    session["last_action_ts"] = now
    try:
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
        
    return None


def _monkey_patch_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
        import functools
        import inspect
        
        original_tool = FastMCP.tool
        
        def custom_tool(self, name: str = None, description: str = None):
            decorator = original_tool(self, name=name, description=description)
            
            def wrap_decorator(func):
                tool_name = name or func.__name__
                
                # Exclude cognitive core registration and diagnostic/feedback tools from gate checks
                if tool_name in ("start_turn", "get_profile", "user_corrected", "learn"):
                    return decorator(func)
                
                if inspect.iscoroutinefunction(func):
                    @functools.wraps(func)
                    async def async_wrapper(*args, **kwargs):
                        err = _check_cognitive_gate_status(tool_name)
                        if err:
                            return err
                        return await func(*args, **kwargs)
                    return decorator(async_wrapper)
                else:
                    @functools.wraps(func)
                    def sync_wrapper(*args, **kwargs):
                        err = _check_cognitive_gate_status(tool_name)
                        if err:
                            return err
                        return func(*args, **kwargs)
                    return decorator(sync_wrapper)
                    
            return wrap_decorator
            
        FastMCP.tool = custom_tool
    except Exception:
        pass

