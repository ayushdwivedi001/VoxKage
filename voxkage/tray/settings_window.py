"""
voxkage/tray/settings_window.py
================================
Native, lightweight settings panel using standard tkinter.
Zero extra dependencies (no PySide6, no Chrome).

Runs in its own process, positioned near the system tray.
"""
from __future__ import annotations

import os
import sys
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path

# ── Config & Env ──────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        from voxkage.paths import config_path
        if config_path().exists():
            return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_config(data: dict):
    try:
        from voxkage.paths import config_path
        existing = _load_config()
        existing.update(data)
        config_path().write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Settings] Save error: {e}", file=sys.stderr)

# ── Slide Toggle Widget ───────────────────────────────────────────────────────

class SlideToggle(tk.Canvas):
    def __init__(self, parent, default=False, active_color="#06b6d4", bg_color="#161b27", callback=None, **kwargs):
        super().__init__(parent, width=42, height=22, bg=bg_color, highlightthickness=0, cursor="hand2", **kwargs)
        self.state = default
        self.active_color = active_color
        self.inactive_color = "#334155"  # slate-700
        self.knob_color = "#ffffff"
        self.callback = callback
        
        self.bind("<Button-1>", self.toggle)
        self.draw()
        
    def draw(self):
        self.delete("all")
        fill = self.active_color if self.state else self.inactive_color
        # Draw track (rounded capsule)
        self.create_oval(2, 2, 20, 20, fill=fill, outline="")
        self.create_oval(22, 2, 40, 20, fill=fill, outline="")
        self.create_rectangle(11, 2, 31, 20, fill=fill, outline="")
        
        # Draw knob (white circle)
        if self.state:
            self.create_oval(22, 4, 38, 20, fill=self.knob_color, outline="")
        else:
            self.create_oval(4, 4, 20, 20, fill=self.knob_color, outline="")
            
    def toggle(self, event=None):
        self.state = not self.state
        self.draw()
        if self.callback:
            self.callback(self.state)

    def set_state(self, new_state: bool):
        if self.state != new_state:
            self.state = new_state
            self.draw()

# ── GUI Application ───────────────────────────────────────────────────────────

def run_gui():
    # ── Single Instance Check (Windows) ──
    if sys.platform == "win32":
        try:
            import win32gui
            import win32con
            import ctypes
            
            hwnd = win32gui.FindWindow(None, "VoxKage Control Center")
            if hwnd:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
                return
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title("VoxKage Control Center")
    
    # ── Geometry & Positioning (Bottom Right)
    w, h = 380, 620
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = sw - w - 20
    y = sh - h - 70
    root.geometry(f"{w}x{h}+{x}+{y}")
    
    root.resizable(False, False)
    
    # ── Styling Constants
    BG = "#0f1117"
    PANEL = "#161b27"
    FG = "#e2e8f0"
    SUB = "#64748b"
    ACCENT = "#06b6d4"   # Electric cyan
    ACCENT2 = "#a78bfa"  # Purple
    OK_COLOR = "#10b981" # Green
    BORDER = "#1e2535"
    FONT = ("Segoe UI", 9)
    FONT_B = ("Segoe UI", 9, "bold")
    
    root.configure(bg=BG)
    
    # ── Header
    header = tk.Frame(root, bg=BG, pady=12)
    header.pack(fill="x", padx=16)
    
    tk.Label(header, text="VOXKAGE", fg=FG, bg=BG, font=("Segoe UI", 12, "bold")).pack(side="left")
    tk.Label(header, text="CONTROL CENTER", fg=SUB, bg=BG, font=("Segoe UI", 9, "bold")).pack(side="right", pady=(4,0))
    
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
    
    # ── Scrollable Frame Class ───────────────────────────────────────────────
    class ScrollableFrame(tk.Frame):
        def __init__(self, container, bg_color, *args, **kwargs):
            super().__init__(container, bg=bg_color, *args, **kwargs)
            self.canvas = tk.Canvas(self, bg=bg_color, highlightthickness=0)
            self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar")
            self.scrollable_frame = tk.Frame(self.canvas, bg=bg_color)

            self.scrollable_frame.bind(
                "<Configure>",
                lambda e: self.canvas.configure(
                    scrollregion=self.canvas.bbox("all")
                )
            )

            self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
            self.canvas.bind('<Configure>', self._on_canvas_configure)
            self.canvas.configure(yscrollcommand=self.scrollbar.set)

            self.canvas.pack(side="left", fill="both", expand=True)
            self.scrollbar.pack(side="right", fill="y", padx=(4, 0))

            # Bind mouse wheel when hovering
            self.canvas.bind('<Enter>', self._bind_mousewheel)
            self.canvas.bind('<Leave>', self._unbind_mousewheel)

        def _on_canvas_configure(self, event):
            self.canvas.itemconfig(self.canvas_window, width=event.width)

        def _bind_mousewheel(self, event):
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        def _unbind_mousewheel(self, event):
            self.canvas.unbind_all("<MouseWheel>")

        def _on_mousewheel(self, event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # Configure custom scrollbar style
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    
    style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BORDER, arrowcolor=SUB)
    style.map("Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT2)])

    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG, bordercolor=BORDER, arrowcolor=SUB)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)], background=[("active", PANEL)])

    # Add Popdown Listbox option database styles
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", "#2c3a52")
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.font", "Segoe UI 9")

    # Initialize Scrollable Viewport
    scroll_container = ScrollableFrame(root, bg_color=BG)
    scroll_container.pack(fill="both", expand=True, padx=16, pady=12)
    
    content = scroll_container.scrollable_frame
    
    cfg = _load_config()
    
    # ── Load current state ──
    # 1. Autostart
    try:
        from voxkage.autostart import is_autostart_enabled
        autostart_val = is_autostart_enabled()
    except Exception:
        autostart_val = cfg.get("autostart", False)
        
    # 2. Safe Mode
    safe_mode_val = cfg.get("safe_mode", True)
    
    # 3. Telegram Watcher
    telegram_watcher_val = cfg.get("telegram_watcher_enabled", True)
    
    # 4. Sandbox Tasks
    sandbox_mode_val = cfg.get("sandbox_mode", True)
    
    # 5. Toast Notifications
    try:
        settings_json_path = Path.home() / ".gemini" / "settings.json"
        if settings_json_path.exists():
            settings_data = json.loads(settings_json_path.read_text(encoding="utf-8"))
            notifications_val = settings_data.get("general", {}).get("enableNotifications", True)
        else:
            notifications_val = cfg.get("notifications_enabled", True)
    except Exception:
        notifications_val = cfg.get("notifications_enabled", True)

    # 6. Themes Sync
    themes = [
        "Default Dark",
        "Atom One Dark",
        "Ayu Dark",
        "Dracula Dark",
        "GitHub Dark",
        "GitHub Dark Colorblind Dark",
        "Holiday Dark",
        "Shades Of Purple Dark",
        "Solarized Dark",
        "Tokyo Night Dark",
        "ANSI Dark"
    ]
    current_theme = cfg.get("theme", "Default Dark")
    if current_theme not in themes:
        current_theme = "Default Dark"

    # Theme picker dropdown frame
    theme_frame = tk.Frame(content, bg=PANEL, bd=1, relief="solid", highlightbackground=BORDER, highlightcolor=BORDER, padx=10, pady=8)
    theme_frame.configure(highlightthickness=1)
    theme_frame.pack(fill="x", pady=(0, 14))
    
    tk.Label(theme_frame, text="Unified Color Theme", fg=ACCENT2, bg=PANEL, font=FONT_B).pack(anchor="w")
    tk.Label(theme_frame, text="Syncs logo & agy interface styling", fg=SUB, bg=PANEL, font=FONT).pack(anchor="w", pady=(0, 6))
    
    cb_theme = ttk.Combobox(theme_frame, values=themes, state="readonly", font=FONT)
    cb_theme.set(current_theme)
    cb_theme.pack(fill="x", pady=(4, 0))

    # Prevent mouse wheel from changing values, scroll canvas instead
    def _combobox_scroll(event):
        scroll_container._on_mousewheel(event)
        return "break"
    cb_theme.bind("<MouseWheel>", _combobox_scroll)

    # Allow clicking anywhere on the combobox to drop it down
    def _combobox_click(event):
        cb_theme.focus_set()
        cb_theme.event_generate('<Down>')
        return "break"
    cb_theme.bind("<Button-1>", _combobox_click)

    # Helper for premium toggles
    toggles = {}
    
    def create_toggle_card(parent, key, title, desc, default_val, active_color=ACCENT):
        card = tk.Frame(parent, bg=PANEL, bd=1, relief="solid", highlightbackground=BORDER, highlightcolor=BORDER, padx=10, pady=8)
        card.configure(highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))
        
        # Left side: labels
        left_col = tk.Frame(card, bg=PANEL)
        left_col.pack(side="left", fill="both", expand=True)
        
        tk.Label(left_col, text=title, fg=FG, bg=PANEL, font=FONT_B).pack(anchor="w")
        tk.Label(left_col, text=desc, fg=SUB, bg=PANEL, font=FONT, justify="left", wraplength=280).pack(anchor="w", pady=(2, 0))
        
        # Right side: toggle switch
        right_col = tk.Frame(card, bg=PANEL)
        right_col.pack(side="right", padx=(10, 0), anchor="center")
        
        toggle = SlideToggle(right_col, default=default_val, active_color=active_color, bg_color=PANEL)
        toggle.pack()
        
        toggles[key] = toggle

    # 1. Autostart
    create_toggle_card(content, "autostart", "Autostart on Boot", "Launches VoxKage in the system tray when Windows boots.", autostart_val, ACCENT)
    
    # 2. Safe Mode
    create_toggle_card(content, "safe_mode", "Safe Mode (Shield Protocol)", "Gates potentially destructive actions with confirmation warnings.", safe_mode_val, "#ef4444")
    
    # 3. Telegram watcher
    create_toggle_card(content, "telegram", "Telegram Integration", "Enables the background daemon for receiving remote mobile commands.", telegram_watcher_val, ACCENT)
    
    # 4. Sandbox mode
    create_toggle_card(content, "sandbox", "Sandbox Mode", "Runs untrusted scripts and commands in a restricted terminal.", sandbox_mode_val, ACCENT)
    
    # 5. Audio & Toast Notifications
    create_toggle_card(content, "notifications", "Toast Notifications", "Displays Windows banner notifications on task completions.", notifications_val, ACCENT)

    # ── Footer ──
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
    footer = tk.Frame(root, bg=BG, pady=12, padx=16)
    footer.pack(fill="x", side="bottom")
    
    saved_lbl = tk.Label(footer, text="", fg=OK_COLOR, bg=BG, font=FONT_B)
    saved_lbl.pack(side="left")
    
    def on_apply():
        # Read GUI values
        theme_sel = cb_theme.get()
        autostart_state = toggles["autostart"].state
        safe_mode_state = toggles["safe_mode"].state
        telegram_state = toggles["telegram"].state
        sandbox_state = toggles["sandbox"].state
        notif_state = toggles["notifications"].state
        
        # 1. Save to ~/.voxkage/config.json
        _save_config({
            "theme": theme_sel,
            "autostart": autostart_state,
            "safe_mode": safe_mode_state,
            "telegram_watcher_enabled": telegram_state,
            "sandbox_mode": sandbox_state,
            "notifications_enabled": notif_state
        })
        
        # 2. Update Autostart Registry
        try:
            from voxkage.autostart import enable_autostart, disable_autostart
            if autostart_state:
                enable_autostart()
            else:
                disable_autostart()
        except Exception as e:
            print(f"[Settings] Registry error: {e}", file=sys.stderr)
            
        # 3. Update C:\Users\AYUSH\.gemini\settings.json for themes & notifications
        try:
            # Map theme to agy CLI counterpart
            theme_map = {
                "Default Dark": "Default",
                "ANSI Dark": "ANSI Dark",
                "Atom One Dark": "Atom One Dark",
                "Ayu Dark": "Ayu Dark",
                "Dracula Dark": "Dracula",
                "GitHub Dark": "GitHub Dark",
                "GitHub Dark Colorblind Dark": "GitHub Dark Colorblind",
                "Holiday Dark": "Holiday",
                "Shades Of Purple Dark": "Shades Of Purple",
                "Solarized Dark": "Solarized Dark",
                "Tokyo Night Dark": "Tokyo Night"
            }
            agy_theme = theme_map.get(theme_sel, "Default")
            
            settings_path = Path.home() / ".gemini" / "settings.json"
            if settings_path.exists():
                s_data = json.loads(settings_path.read_text(encoding="utf-8"))
                
                # Update theme in UI settings
                if "ui" not in s_data:
                    s_data["ui"] = {}
                s_data["ui"]["theme"] = agy_theme
                
                # Update notifications in General settings
                if "general" not in s_data:
                    s_data["general"] = {}
                s_data["general"]["enableNotifications"] = notif_state
                
                settings_path.write_text(json.dumps(s_data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Settings] Agy config error: {e}", file=sys.stderr)
            
        # 4. Handle active Telegram Watcher process dynamically
        try:
            if not telegram_state:
                # Stop Telegram watcher if running
                lock_file = Path(os.path.expanduser("~")) / ".voxkage" / "telegram_watcher.lock"
                if lock_file.exists():
                    pid = int(lock_file.read_text().strip())
                    try:
                        import psutil
                        if psutil.pid_exists(pid):
                            psutil.Process(pid).terminate()
                    except ImportError:
                        try:
                            import signal
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            pass
                    try:
                        lock_file.unlink(missing_ok=True)
                    except Exception:
                        pass
            else:
                # If enabled, ensure running (though next startup will pick it up, we can spawn it now)
                # But let's let cli.py or the watcher handle starting to avoid duplicate locks
                pass
        except Exception:
            pass
            
        saved_lbl.config(text="✓ Saved & Synced")
        root.after(1000, root.destroy)
        
    btn_apply = tk.Button(
        footer, text="Apply", bg=ACCENT2, fg="white", font=FONT_B, 
        relief="flat", activebackground="#8b5cf6", activeforeground="white",
        cursor="hand2", padx=20, pady=4, command=on_apply
    )
    btn_apply.pack(side="right")
    
    btn_cancel = tk.Button(
        footer, text="Close", bg=BORDER, fg=FG, font=FONT_B,
        relief="flat", activebackground="#2c3447", activeforeground="white",
        cursor="hand2", padx=16, pady=4, command=root.destroy
    )
    btn_cancel.pack(side="right", padx=(0, 10))
    
    root.mainloop()

if __name__ == "__main__":
    run_gui()
