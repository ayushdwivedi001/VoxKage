"""
VoxKage IPC Bridge — Windows Named Pipe Communication

Provides a lightweight, non-blocking IPC mechanism between the Telegram Watcher
(or any external process) and the running VoxKage/Gemini CLI session.

Architecture:
    ┌──────────────┐  Named Pipe   ┌─────────────────┐
    │ Tg Watcher / │ ────────────► │ VoxKage CLI /    │
    │ External App │ \\.\pipe\      │ Gemini Session   │
    └──────────────┘  voxkage_ipc  └─────────────────┘

Two modes:
    1. Server (CLI side) — listens for incoming messages on the pipe
    2. Client (Watcher/external) — sends messages to the pipe

Fallback: If Named Pipes are unavailable (non-Windows), uses a simple
file-based inbox at ~/.voxkage/telegram_inbox.jsonl.
"""

import json
import os
import sys
import time
import logging
import threading
from pathlib import Path

log = logging.getLogger("vk.ipc")

_PIPE_NAME = r"\\.\pipe\voxkage_ipc"
_VOXKAGE_DIR = Path(os.path.expanduser("~")) / ".voxkage"
_INBOX_FILE = _VOXKAGE_DIR / "telegram_inbox.jsonl"


# ── Client Side (Telegram Watcher → VoxKage) ─────────────────────────────────

def send_message(message: str, source: str = "telegram") -> bool:
    """
    Send a message to the running VoxKage session via Named Pipe.

    Args:
        message: The text message to inject.
        source:  Origin tag ("telegram", "external", etc.)

    Returns:
        True if the message was delivered successfully.
    """
    payload = json.dumps({
        "source": source,
        "text": message,
        "timestamp": time.time(),
    }, ensure_ascii=False)

    # Try Named Pipe first (Windows only)
    if sys.platform == "win32":
        try:
            import win32file
            import pywintypes

            handle = win32file.CreateFile(
                _PIPE_NAME,
                win32file.GENERIC_WRITE,
                0,     # no sharing
                None,  # default security
                win32file.OPEN_EXISTING,
                0,     # default attributes
                None,  # no template
            )
            try:
                data = (payload + "\n").encode("utf-8")
                win32file.WriteFile(handle, data)
                log.info(f"[IPC] Message sent via Named Pipe ({len(message)} chars)")
                return True
            finally:
                win32file.CloseHandle(handle)

        except pywintypes.error as e:
            if e.winerror == 2:  # ERROR_FILE_NOT_FOUND — pipe server not running
                log.debug("[IPC] Named Pipe server not found — falling back to inbox file")
            else:
                log.warning(f"[IPC] Named Pipe write error: {e}")
        except ImportError:
            log.debug("[IPC] win32file not available — falling back to inbox file")
        except Exception as e:
            log.warning(f"[IPC] Unexpected pipe error: {e}")

    # Fallback: write to inbox file
    return _write_to_inbox(payload)


def _write_to_inbox(payload: str) -> bool:
    """Write a message to the file-based inbox (cross-platform fallback)."""
    try:
        _VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_INBOX_FILE, "a", encoding="utf-8") as f:
            f.write(payload + "\n")
        log.info(f"[IPC] Message saved to inbox file: {_INBOX_FILE}")
        return True
    except Exception as e:
        log.error(f"[IPC] Inbox file write failed: {e}")
        return False


# ── Server Side (VoxKage CLI listens) ─────────────────────────────────────────

class IPCServer:
    """
    Named Pipe server that runs in a daemon thread.
    Receives messages from the Telegram Watcher and invokes a callback.
    """

    def __init__(self, on_message=None):
        """
        Args:
            on_message: Callable(source: str, text: str) — called when a message arrives.
        """
        self.on_message = on_message
        self._running = False
        self._thread = None

    def start(self):
        """Start the IPC server in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        log.info("[IPC] Named Pipe server started")

    def stop(self):
        """Stop the IPC server."""
        self._running = False

    def _listen_loop(self):
        """Main loop — creates a pipe instance and waits for connections."""
        if sys.platform != "win32":
            log.info("[IPC] Named Pipes not supported on this platform — using inbox file polling")
            self._poll_inbox_loop()
            return

        try:
            import win32pipe
            import win32file
            import pywintypes
        except ImportError:
            log.warning("[IPC] win32pipe not available — falling back to inbox file polling")
            self._poll_inbox_loop()
            return

        while self._running:
            try:
                # Create a new pipe instance for each connection
                pipe = win32pipe.CreateNamedPipe(
                    _PIPE_NAME,
                    win32pipe.PIPE_ACCESS_INBOUND,
                    (
                        win32pipe.PIPE_TYPE_MESSAGE
                        | win32pipe.PIPE_READMODE_MESSAGE
                        | win32pipe.PIPE_WAIT
                    ),
                    1,      # max instances
                    4096,   # out buffer
                    4096,   # in buffer
                    1000,   # timeout ms
                    None,   # default security
                )

                # Wait for a client to connect (blocking)
                win32pipe.ConnectNamedPipe(pipe, None)

                # Read the message
                try:
                    result, data = win32file.ReadFile(pipe, 8192)
                    if result == 0:  # success
                        text = data.decode("utf-8").strip()
                        for line in text.split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                                source = msg.get("source", "unknown")
                                content = msg.get("text", "")
                                if self.on_message and content:
                                    self.on_message(source, content)
                            except json.JSONDecodeError:
                                # Raw text — treat as telegram message
                                if self.on_message:
                                    self.on_message("raw", line)
                except Exception as e:
                    log.debug(f"[IPC] Read error: {e}")
                finally:
                    win32file.CloseHandle(pipe)

            except pywintypes.error as e:
                if self._running:
                    log.debug(f"[IPC] Pipe error: {e}")
                    time.sleep(1)
            except Exception as e:
                if self._running:
                    log.error(f"[IPC] Server error: {e}")
                    time.sleep(2)

    def _poll_inbox_loop(self):
        """Fallback: poll the inbox file for new messages."""
        last_pos = 0
        if _INBOX_FILE.exists():
            last_pos = _INBOX_FILE.stat().st_size

        while self._running:
            try:
                if _INBOX_FILE.exists():
                    current_size = _INBOX_FILE.stat().st_size
                    if current_size > last_pos:
                        with open(_INBOX_FILE, "r", encoding="utf-8") as f:
                            f.seek(last_pos)
                            new_lines = f.readlines()
                        last_pos = current_size

                        for line in new_lines:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                                source = msg.get("source", "unknown")
                                content = msg.get("text", "")
                                if self.on_message and content:
                                    self.on_message(source, content)
                            except json.JSONDecodeError:
                                pass
            except Exception as e:
                log.debug(f"[IPC] Inbox poll error: {e}")

            time.sleep(2)
