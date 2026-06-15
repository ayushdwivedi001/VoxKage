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
    def __init__(self, parent, default=False, active_color="#2563eb", bg_color="#15161e", callback=None, **kwargs):
        # Premium thin-pill toggle design: 40px width, 20px height
        super().__init__(parent, width=40, height=20, bg=bg_color, highlightthickness=0, cursor="hand2", **kwargs)
        self.state = default
        self.active_color = active_color
        self.inactive_color = "#2c2d3a"  # Muted, clean dark gray
        self.knob_color = "#ffffff"
        self.callback = callback
        
        self.bind("<Button-1>", self.toggle)
        self.draw()
        
    def draw(self):
        self.delete("all")
        fill = self.active_color if self.state else self.inactive_color
        # Draw sleek rounded track (capsule shape)
        self.create_oval(2, 2, 18, 18, fill=fill, outline="")
        self.create_oval(22, 2, 38, 18, fill=fill, outline="")
        self.create_rectangle(10, 2, 30, 18, fill=fill, outline="")
        
        # Draw knob (white circle) with premium inset padding
        if self.state:
            self.create_oval(22, 4, 36, 18, fill=self.knob_color, outline="")
        else:
            self.create_oval(4, 4, 18, 18, fill=self.knob_color, outline="")
            
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
    
    # ── Geometry & Positioning (Bottom Right, highly optimized compact height)
    w, h = 380, 580
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = sw - w - 20
    y = sh - h - 70
    root.geometry(f"{w}x{h}+{x}+{y}")
    
    root.resizable(False, False)
    
    # ── Modern Minimalist Color Palette (Refined Dark Theme)
    BG = "#0c0d12"       # Pure dark matte background
    PANEL = "#13141c"    # Muted dark gray container panels
    BORDER = "#1f212d"   # Extremely subtle cool border gray
    FG = "#f1f5f9"       # Soft off-white for crisp readability
    SUB = "#64748b"      # Muted slate-400 for secondary text
    ACCENT = "#2563eb"   # Premium Royal Blue accent
    OK_COLOR = "#10b981" # Elegant emerald green
    
    FONT = ("Segoe UI", 9)
    FONT_B = ("Segoe UI", 9, "bold")
    
    root.configure(bg=BG)
    
    # ── Header (Clean and spacious without harsh horizontal lines)
    header = tk.Frame(root, bg=BG, pady=16)
    header.pack(fill="x", padx=16)
    
    tk.Label(header, text="VOXKAGE", fg=FG, bg=BG, font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")
    tk.Label(header, text="CONTROL CENTER", fg=SUB, bg=BG, font=("Segoe UI", 9, "bold"), anchor="e").pack(side="right", pady=(4,0))
    
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
    style.map("Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
    
    style.map('TCombobox', fieldbackground=[('readonly', PANEL)], selectbackground=[('readonly', PANEL)])
    style.configure('TCombobox', background=PANEL, foreground=FG, fieldbackground=PANEL, bordercolor=BORDER, arrowcolor=FG)

    # Initialize Scrollable Viewport
    scroll_container = ScrollableFrame(root, bg_color=BG)
    scroll_container.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    
    content = scroll_container.scrollable_frame

    cfg = _load_config()

    # ── Interface Provider Card ──────────────────────────────────────────────
    _engine_sel = [cfg.get("interface_engine", "antigravity")]

    ip_card = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER, padx=12, pady=12)
    ip_card.pack(fill="x", pady=(0, 10))

    tk.Label(ip_card, text="Interface Provider", fg=ACCENT, bg=PANEL, font=FONT_B, anchor="w").pack(anchor="w", fill="x")
    tk.Label(ip_card, text="Select the CLI engine VoxKage launches as its terminal.",
             fg=SUB, bg=PANEL, font=FONT, anchor="w", justify="left", wraplength=310).pack(anchor="w", fill="x", pady=(2, 10))

    # Claude Model selection frame
    claude_model_frame = tk.Frame(ip_card, bg=PANEL, pady=5)
    
    # Load model selection
    current_model = cfg.get("claude_model", "deepseek-v4-flash-free")
    
    # Parse free models
    free_models = []
    try:
        opencode_config_path = Path.home() / ".opencode-starter" / "config.json"
        if opencode_config_path.exists():
            config_data = json.loads(opencode_config_path.read_text(encoding="utf-8"))
            models_list = config_data.get("modelListCache", {}).get("zen", {}).get("models", [])
            free_models = [m.get("id") for m in models_list if m.get("isFree") is True]
    except Exception:
        pass
        
    if not free_models:
        free_models = ["deepseek-v4-flash-free", "big-pickle", "mimo-v2.5-free", "nemotron-3-ultra-free", "north-mini-code-free"]
        
    if current_model not in free_models:
        free_models.insert(0, current_model)
        
    tk.Label(claude_model_frame, text="Model:", fg=SUB, bg=PANEL, font=FONT_B).pack(side="left", padx=(5, 5))
    model_var = tk.StringVar(value=current_model)
    model_combo = ttk.Combobox(claude_model_frame, textvariable=model_var, values=free_models, state="readonly", width=25)
    model_combo.pack(side="left", padx=5)

    _engine_rows: dict = {}  # key -> (row_frame, accent_bar, label, badge)

    def _build_engine_row(key: str, label: str, subtitle: str):
        row = tk.Frame(ip_card, bg=PANEL, cursor="hand2")
        row.pack(fill="x", pady=3)

        # Left accent bar (3 px)
        bar = tk.Frame(row, bg=ACCENT if _engine_sel[0] == key else BORDER, width=3)
        bar.pack(side="left", fill="y", padx=(0, 10))
        bar.pack_propagate(False)

        # ACTIVE badge (pack side="right" FIRST so expanding left frame doesn't clip it)
        badge = tk.Label(row, text="ACTIVE" if _engine_sel[0] == key else "",
                         fg=OK_COLOR, bg=PANEL, font=FONT_B, anchor="e")
        badge.pack(side="right", padx=(6, 0))

        # Text block
        txt = tk.Frame(row, bg=PANEL)
        txt.pack(side="left", fill="both", expand=True)
        lbl_main = tk.Label(txt, text=label,
                            fg=FG if _engine_sel[0] == key else SUB,
                            bg=PANEL, font=FONT_B, anchor="w")
        lbl_main.pack(anchor="w", fill="x")
        lbl_sub = tk.Label(txt, text=subtitle, fg=SUB, bg=PANEL, font=FONT, anchor="w", justify="left", wraplength=270)
        lbl_sub.pack(anchor="w", fill="x")

        _engine_rows[key] = (row, bar, lbl_main, badge)

        def _select(e=None, _k=key):
            _engine_sel[0] = _k
            for k2, (r2, b2, m2, g2) in _engine_rows.items():
                active = (k2 == _k)
                b2.configure(bg=ACCENT if active else BORDER)
                m2.configure(fg=FG if active else SUB)
                g2.configure(text="ACTIVE" if active else "")
            if _k == "claude":
                claude_model_frame.pack(fill="x", pady=(8, 0))
            else:
                claude_model_frame.pack_forget()

        for widget in (row, bar, txt, lbl_main, lbl_sub, badge):
            widget.bind("<Button-1>", _select)

    _build_engine_row(
        "antigravity",
        "Antigravity CLI  (agy)",
        "Best for Pro users · Latest Google & Claude models"
    )
    _build_engine_row(
        "opencode",
        "OpenCode CLI  (opencode)",
        "Best for free-tier · Connect any API key via /connect"
    )
    _build_engine_row(
        "claude",
        "Claude Code CLI  (claude)",
        "Best for local CLI · Direct launch using OpenCode Zen"
    )

    if _engine_sel[0] == "claude":
        claude_model_frame.pack(fill="x", pady=(8, 0))
    
    # ── Load toggle states ──
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

    toggles = {}
    
    def create_toggle_card(parent, key, title, desc, default_val, active_color=ACCENT):
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER, padx=12, pady=10)
        card.pack(fill="x", pady=(0, 8))
        
        # Left side: labels
        left_col = tk.Frame(card, bg=PANEL)
        left_col.pack(side="left", fill="both", expand=True)
        
        tk.Label(left_col, text=title, fg=FG, bg=PANEL, font=FONT_B, anchor="w").pack(anchor="w", fill="x")
        tk.Label(left_col, text=desc, fg=SUB, bg=PANEL, font=FONT, justify="left", anchor="w", wraplength=260).pack(anchor="w", fill="x", pady=(2, 0))
        
        # Right side: toggle switch
        right_col = tk.Frame(card, bg=PANEL)
        right_col.pack(side="right", padx=(10, 0), anchor="center")
        
        toggle = SlideToggle(right_col, default=default_val, active_color=active_color, bg_color=PANEL)
        toggle.pack()
        
        toggles[key] = toggle

    # 1. Autostart
    create_toggle_card(content, "autostart", "Autostart on Boot", "Launches VoxKage in the system tray when Windows boots.", autostart_val, ACCENT)
    
    # 2. Safe Mode (using a sleek secure red indicator)
    create_toggle_card(content, "safe_mode", "Safe Mode (Shield Protocol)", "Gates potentially destructive actions with confirmation warnings.", safe_mode_val, "#ef4444")
    
    # 3. Telegram watcher
    create_toggle_card(content, "telegram", "Telegram Integration", "Enables the background daemon for receiving remote mobile commands.", telegram_watcher_val, ACCENT)
    
    # 4. Sandbox mode
    create_toggle_card(content, "sandbox", "Sandbox Mode", "Runs untrusted scripts and commands in a restricted terminal.", sandbox_mode_val, ACCENT)
    
    # 5. Audio & Toast Notifications
    create_toggle_card(content, "notifications", "Toast Notifications", "Displays Windows banner notifications on task completions.", notifications_val, ACCENT)

    # ── Footer (Sleek minimalist panel at bottom)
    footer = tk.Frame(root, bg=BG, pady=16, padx=16)
    footer.pack(fill="x", side="bottom")
    
    saved_lbl = tk.Label(footer, text="", fg=OK_COLOR, bg=BG, font=FONT_B, anchor="w")
    saved_lbl.pack(side="left")
    
    def on_apply():
        autostart_state = toggles["autostart"].state
        safe_mode_state = toggles["safe_mode"].state
        telegram_state = toggles["telegram"].state
        sandbox_state = toggles["sandbox"].state
        notif_state = toggles["notifications"].state
        engine_sel = _engine_sel[0]
        claude_model_state = model_var.get()
        
        # 1. Save config settings
        _save_config({
            "autostart": autostart_state,
            "safe_mode": safe_mode_state,
            "telegram_watcher_enabled": telegram_state,
            "sandbox_mode": sandbox_state,
            "notifications_enabled": notif_state,
            "interface_engine": engine_sel,
            "claude_model": claude_model_state,
        })
        
        # 1b. Sync model to ~/.opencode-starter/config.json
        if claude_model_state:
            try:
                opencode_starter_config = Path.home() / ".opencode-starter" / "config.json"
                if opencode_starter_config.exists():
                    os_config = json.loads(opencode_starter_config.read_text(encoding="utf-8"))
                    os_config["lastModel"] = claude_model_state
                    opencode_starter_config.write_text(json.dumps(os_config, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                print(f"[Settings] Error writing to opencode-starter config: {e}", file=sys.stderr)
        
        # 2. Update Autostart Registry
        try:
            from voxkage.autostart import enable_autostart, disable_autostart
            if autostart_state:
                enable_autostart()
            else:
                disable_autostart()
        except Exception as e:
            print(f"[Settings] Registry error: {e}", file=sys.stderr)
            
        # 3. Update C:\Users\AYUSH\.gemini\settings.json for notifications
        try:
            settings_path = Path.home() / ".gemini" / "settings.json"
            if settings_path.exists():
                s_data = json.loads(settings_path.read_text(encoding="utf-8"))
                
                if "general" not in s_data:
                    s_data["general"] = {}
                s_data["general"]["enableNotifications"] = notif_state
                
                settings_path.write_text(json.dumps(s_data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Settings] settings.json config error: {e}", file=sys.stderr)
            
        # 4. Handle active Telegram Watcher process dynamically
        try:
            if not telegram_state:
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
        except Exception:
            pass
            
        saved_lbl.config(text="✓ Saved & Synced")
        root.after(1000, root.destroy)
        
    btn_apply = tk.Button(
        footer, text="Apply", bg=ACCENT, fg="white", font=FONT_B, 
        relief="flat", activebackground="#1d4ed8", activeforeground="white",
        cursor="hand2", padx=20, pady=5, command=on_apply
    )
    btn_apply.pack(side="right")
    
    btn_cancel = tk.Button(
        footer, text="Close", bg="#222530", fg="#94a3b8", font=FONT_B,
        relief="flat", activebackground="#2d313f", activeforeground="white",
        cursor="hand2", padx=16, pady=5, command=root.destroy
    )
    btn_cancel.pack(side="right", padx=(0, 10))
    
    root.mainloop()

if __name__ == "__main__":
    run_gui()
