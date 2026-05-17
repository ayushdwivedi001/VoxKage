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
import importlib
import tkinter as tk
from tkinter import ttk

# ── Config & Env ──────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        from voxkage.paths import config_path
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_config(data: dict):
    try:
        from voxkage.paths import config_path
        existing = _load_config()
        existing.update(data)
        config_path().write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Settings] Save error: {e}", file=sys.stderr)

def _has_module(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except ImportError:
        return False

def _get_integrations() -> dict:
    from voxkage._env import load_voxkage_env
    from voxkage.paths import data_dir
    load_voxkage_env()
    
    gmail_ok = (data_dir() / "credentials.json").exists() or \
               (data_dir() / "gmail_token.json").exists()
               
    return {
        "Telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()),
        "Spotify":  bool(os.environ.get("SPOTIFY_CLIENT_ID", "").strip()),
        "GitHub":   bool(os.environ.get("GITHUB_PAT", "").strip()),
        "Gmail":    gmail_ok,
    }

# ── GUI Application ───────────────────────────────────────────────────────────

def run_gui():
    # Attempt to apply DPI awareness on Windows so it doesn't look blurry
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title("VoxKage Settings")
    
    # ── Geometry & Positioning (Bottom Right)
    w, h = 380, 560
    
    # Get actual screen width/height (with DPI awareness if applicable)
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    
    # Taskbar is usually at the bottom, subtract roughly 60px for it, plus a small margin
    x = sw - w - 20
    y = sh - h - 70
    
    root.geometry(f"{w}x{h}+{x}+{y}")
    
    # Use toolwindow style on Windows (no maximize/minimize buttons, thinner frame)
    if sys.platform == "win32":
        root.attributes("-toolwindow", True)
    
    root.resizable(False, False)
    
    # ── Styling Constants
    BG = "#0f1117"
    PANEL = "#161b27"
    FG = "#e2e8f0"
    SUB = "#64748b"
    ACCENT = "#38bdf8"
    ACCENT2 = "#a78bfa"
    OK_COLOR = "#22c55e"
    ERR_COLOR = "#f87171"
    BORDER = "#1e2535"
    FONT = ("Segoe UI", 9)
    FONT_B = ("Segoe UI", 9, "bold")
    
    root.configure(bg=BG)
    
    # ── Header
    header = tk.Frame(root, bg=BG, pady=12)
    header.pack(fill="x", padx=16)
    
    tk.Label(header, text="VOXKAGE", fg=FG, bg=BG, font=("Segoe UI", 12, "bold")).pack(side="left")
    tk.Label(header, text="SETTINGS", fg=SUB, bg=BG, font=("Segoe UI", 9, "bold")).pack(side="right", pady=(4,0))
    
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
    
    # ── Content Container
    content = tk.Frame(root, bg=BG, padx=16, pady=12)
    content.pack(fill="both", expand=True)
    
    cfg = _load_config()
    
    # Correct Gemini CLI supported models
    # gemini-3.1-pro-preview and gemini-2.5-pro require a Pro subscription
    models = [
        "gemini-3.1-pro-preview  (Pro users)",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro  (Pro users)",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    # Canonical IDs stored in config (without the display suffix)
    model_ids = [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    def _model_to_display(model_id: str) -> str:
        for mid, mdisp in zip(model_ids, models):
            if mid == model_id:
                return mdisp
        return model_id
    def _display_to_model(display: str) -> str:
        for mid, mdisp in zip(model_ids, models):
            if mdisp == display:
                return mid
        # Fallback: strip the suffix if someone saved a display string
        return display.split("  (")[0].strip()

    # ── Combobox styling ──
    # Force the dropdown Listbox to use our dark colours — ttk alone doesn't reach it
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", "#2c3a52")
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.font", "{{Segoe UI}} 9")

    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(
        "TCombobox",
        fieldbackground=PANEL,
        background=PANEL,
        foreground=FG,
        selectbackground=PANEL,
        selectforeground=FG,
        bordercolor=BORDER,
        arrowcolor=SUB,
        lightcolor=PANEL,
        darkcolor=PANEL,
        insertcolor=FG,
    )
    style.map("TCombobox",
        fieldbackground=[("readonly", PANEL)],
        foreground=[("readonly", FG)],
        background=[("active", PANEL)],
    )
    
    # Helper to create a setting field
    def create_field(parent, title, desc, color):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", pady=(0, 14))
        
        # Title with colored dot
        th = tk.Frame(f, bg=BG)
        th.pack(fill="x", anchor="w")
        
        # Draw a small circle using Canvas
        c = tk.Canvas(th, width=10, height=10, bg=BG, highlightthickness=0)
        c.create_oval(2, 2, 8, 8, fill=color, outline="")
        c.pack(side="left", pady=(2,0))
        
        tk.Label(th, text=title, fg=color, bg=BG, font=FONT_B).pack(side="left", padx=(4,0))
        
        # Description
        tk.Label(f, text=desc, fg=SUB, bg=BG, font=FONT, justify="left", wraplength=340).pack(fill="x", anchor="w", pady=(2, 6))
        
        return f

    # Main Agent Model
    f1 = create_field(content, "Main Agent Model", "VoxKage will use this as its default model every time it wakes up.", ACCENT)
    cb_main = ttk.Combobox(f1, values=models, state="readonly", font=FONT)
    cb_main.set(_model_to_display(cfg.get("main_model", "gemini-2.5-flash")))
    cb_main.pack(fill="x")

    # Sub-Agent Model
    f2 = create_field(content, "Sub-Agent Model", "Used for all background tasks. Lighter for speed, heavier for accuracy.", ACCENT2)
    cb_sub = ttk.Combobox(f2, values=models, state="readonly", font=FONT)
    cb_sub.set(_model_to_display(cfg.get("subagent_model", "gemini-2.5-flash-lite")))
    cb_sub.pack(fill="x")
    
    tk.Frame(content, bg=BORDER, height=1).pack(fill="x", pady=(4, 14))
    
    # ── Capability Packs ──
    tk.Label(content, text="Capability Packs", fg="#f59e0b", bg=BG, font=FONT_B).pack(anchor="w", pady=(0,6))
    
    packs = {
        "RAG Memory": _has_module("chromadb"),
        "Vision & OCR": _has_module("cv2"),
        "PDF Conversion": _has_module("docx2pdf")
    }
    
    for name, is_ok in packs.items():
        pf = tk.Frame(content, bg=PANEL, padx=12, pady=6)
        pf.pack(fill="x", pady=(0,4))
        
        # Fake border radius using a colored background
        tk.Label(pf, text=name, fg=FG, bg=PANEL, font=FONT_B).pack(side="left")
        
        status_text = "Installed" if is_ok else "Not installed"
        status_color = OK_COLOR if is_ok else ERR_COLOR
        tk.Label(pf, text=status_text, fg=status_color, bg=PANEL, font=FONT).pack(side="right")
        
    tk.Frame(content, bg=BORDER, height=1).pack(fill="x", pady=(10, 14))
    
    # ── Integrations ──
    tk.Label(content, text="Integrations", fg=OK_COLOR, bg=BG, font=FONT_B).pack(anchor="w", pady=(0,6))
    
    ints = _get_integrations()
    for name, is_ok in ints.items():
        inf = tk.Frame(content, bg=PANEL, padx=12, pady=6)
        inf.pack(fill="x", pady=(0,4))
        
        tk.Label(inf, text=name, fg=FG, bg=PANEL, font=FONT_B).pack(side="left")
        
        # Status dot
        c = tk.Canvas(inf, width=12, height=12, bg=PANEL, highlightthickness=0)
        c.create_oval(2, 2, 10, 10, fill=(OK_COLOR if is_ok else SUB), outline="")
        c.pack(side="right")
        
    # ── Footer ──
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
    footer = tk.Frame(root, bg=BG, pady=12, padx=16)
    footer.pack(fill="x", side="bottom")
    
    # Status label for "Saved"
    saved_lbl = tk.Label(footer, text="", fg=OK_COLOR, bg=BG, font=FONT_B)
    saved_lbl.pack(side="left")
    
    def on_apply():
        _save_config({
            "main_model": _display_to_model(cb_main.get()),
            "subagent_model": _display_to_model(cb_sub.get()),
        })
        saved_lbl.config(text="✓ Saved")
        # Automatically close after 1 second
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

# ── Entry Point (called via python -m) ─────────────────────────────────────────

if __name__ == "__main__":
    run_gui()
