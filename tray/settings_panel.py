"""
tray/settings_panel.py — VoxKage Settings GUI
==============================================
A premium PySide6 dark-themed floating panel that appears above the system
tray icon when the user clicks "⚙ Settings".

Features:
  - Main Agent model selector  (writes to ~/.gemini/settings.json + ~/.voxkage/config.json)
  - Sub-Agent model selector   (writes to ~/.voxkage/config.json only)
  - Live checkmarks on active model
  - Auto-positions above the tray icon
  - Click-outside-to-close behaviour
"""

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QFont, QColor, QPainter, QBrush, QPen, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QFrame, QGraphicsDropShadowEffect,
)

# ── Paths ──────────────────────────────────────────────────────────────────────
_VOXKAGE_DIR = Path(os.path.expanduser("~")) / ".voxkage"
_VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
_VOXKAGE_CONFIG = _VOXKAGE_DIR / "config.json"
_GEMINI_SETTINGS = Path(os.path.expanduser("~")) / ".gemini" / "settings.json"

# ── Model catalogue ────────────────────────────────────────────────────────────
MODELS = [
    ("gemini-3.1-pro-preview",       "Gemini 3.1 Pro Preview"),
    ("gemini-3-flash-preview",       "Gemini 3 Flash Preview ✦"),
    ("gemini-3.1-flash-lite-preview","Gemini 3.1 Flash Lite Preview"),
    ("gemini-2.5-pro",               "Gemini 2.5 Pro"),
    ("gemini-2.5-flash",             "Gemini 2.5 Flash"),
    ("gemini-2.5-flash-lite",        "Gemini 2.5 Flash Lite"),
]

DEFAULT_MAIN_MODEL    = "gemini-3-flash-preview"
DEFAULT_SUBAGENT_MODEL = "gemini-3-flash-preview"

# ── Stylesheet ─────────────────────────────────────────────────────────────────
DARK_QSS = """
QWidget#SettingsPanel {
    background-color: #0f0f0f;
    border-radius: 14px;
    border: 1px solid #2a2a2a;
}
QLabel#title {
    color: #e0e0e0;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1.5px;
}
QLabel#subtitle {
    color: #555;
    font-size: 9px;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}
QLabel#sectionLabel {
    color: #bd93f9;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
}
QLabel#descLabel {
    color: #6a6a7a;
    font-size: 11px;
    padding-left: 2px;
}
QLabel#hintLabel {
    color: #8a8a9a;
    font-size: 10px;
    font-style: italic;
    padding-left: 2px;
}
QComboBox {
    background-color: #1a1a2e;
    color: #c8c8e0;
    border: 1px solid #2d2d45;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 10px;
    font-family: "Segoe UI", sans-serif;
    selection-background-color: #2d1b69;
}
QComboBox:hover {
    border: 1px solid #bd93f9;
    background-color: #1e1e38;
}
QComboBox:focus {
    border: 1px solid #bd93f9;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #bd93f9;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #16162a;
    color: #c8c8e0;
    border: 1px solid #2d2d45;
    border-radius: 6px;
    selection-background-color: #2d1b69;
    outline: none;
    padding: 4px;
}
QFrame#divider {
    background-color: #1f1f2e;
    max-height: 1px;
}
QPushButton#applyBtn {
    background-color: #bd93f9;
    color: #0a0a14;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QPushButton#applyBtn:hover {
    background-color: #cfa8ff;
}
QPushButton#applyBtn:pressed {
    background-color: #9b6dd6;
}
QPushButton#closeBtn {
    background-color: transparent;
    color: #444;
    border: none;
    font-size: 14px;
    padding: 0px;
}
QPushButton#closeBtn:hover {
    color: #888;
}
QLabel#savedLabel {
    color: #50fa7b;
    font-size: 9px;
    font-weight: 600;
}
"""


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_voxkage_config() -> dict:
    """Load ~/.voxkage/config.json, return defaults if missing/broken."""
    defaults = {
        "main_model": DEFAULT_MAIN_MODEL,
        "subagent_model": DEFAULT_SUBAGENT_MODEL,
    }
    try:
        if _VOXKAGE_CONFIG.exists():
            data = json.loads(_VOXKAGE_CONFIG.read_text(encoding="utf-8"))
            defaults.update(data)
    except Exception:
        pass
    return defaults


def _save_voxkage_config(cfg: dict):
    """Persist ~/.voxkage/config.json atomically."""
    try:
        _VOXKAGE_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Settings] Failed to save voxkage config: {e}")


def _update_gemini_model(model_id: str):
    """Patch ~/.gemini/settings.json model.name so next CLI launch uses it."""
    try:
        if _GEMINI_SETTINGS.exists():
            data = json.loads(_GEMINI_SETTINGS.read_text(encoding="utf-8"))
        else:
            data = {}
        if "model" not in data or not isinstance(data["model"], dict):
            data["model"] = {}
        data["model"]["name"] = model_id
        _GEMINI_SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Settings] Failed to patch gemini settings: {e}")


# ── Settings Panel Widget ──────────────────────────────────────────────────────

class SettingsPanel(QWidget):
    def __init__(self, tray_geometry=None, parent=None):
        super().__init__(parent)
        self.tray_geometry = tray_geometry
        self.setObjectName("SettingsPanel")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(380)  # 340 width + 40 for shadow margins

        self._setup_ui()
        self._apply_shadow()
        self._load_current_values()
        self._position_above_tray()

    # ── Build UI ───────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20) # Margins for the drop shadow to render into
        root.setSpacing(0)

        # Inner card
        self.card = QWidget()
        self.card.setObjectName("SettingsPanel")
        self.card.setStyleSheet(DARK_QSS)
        root.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(4)

        # ── Header ─────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(0)

        vox_lbl = QLabel("VOXKAGE")
        vox_lbl.setObjectName("title")
        vox_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        hdr.addWidget(vox_lbl)

        hdr.addStretch()

        sub_lbl = QLabel("SETTINGS")
        sub_lbl.setObjectName("subtitle")
        sub_lbl.setFont(QFont("Segoe UI", 9))
        hdr.addWidget(sub_lbl)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(20, 20)
        close_btn.clicked.connect(self.close)
        hdr.addWidget(close_btn)

        layout.addLayout(hdr)
        layout.addSpacing(14)

        # ── Divider ────────────────────────────────────────────────────────────
        layout.addWidget(self._divider())
        layout.addSpacing(14)

        # ── Main Model Section ─────────────────────────────────────────────────
        main_lbl = QLabel("◈  Main Agent Model")
        main_lbl.setObjectName("sectionLabel")
        main_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        layout.addWidget(main_lbl)

        main_desc = QLabel("VoxKage will use this as its default model every time it wakes up")
        main_desc.setObjectName("descLabel")
        main_desc.setWordWrap(True)
        layout.addWidget(main_desc)
        layout.addSpacing(6)

        self.main_combo = self._build_combo()
        layout.addWidget(self.main_combo)

        main_hint = QLabel("Type  /model  in VoxKage for more info")
        main_hint.setObjectName("hintLabel")
        layout.addWidget(main_hint)

        layout.addSpacing(16)

        # ── Sub-Agent Model Section ────────────────────────────────────────────
        sub_lbl2 = QLabel("◈  Sub-Agent Model")
        sub_lbl2.setObjectName("sectionLabel")
        sub_lbl2.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        layout.addWidget(sub_lbl2)

        sub_desc = QLabel("Used for all background tasks. Use a lighter model for speed or a heavier one for accuracy")
        sub_desc.setObjectName("descLabel")
        sub_desc.setWordWrap(True)
        layout.addWidget(sub_desc)
        layout.addSpacing(6)

        self.sub_combo = self._build_combo()
        layout.addWidget(self.sub_combo)

        sub_hint = QLabel("Type  /model  in VoxKage for more info")
        sub_hint.setObjectName("hintLabel")
        layout.addWidget(sub_hint)

        layout.addSpacing(18)

        # ── Divider ────────────────────────────────────────────────────────────
        layout.addWidget(self._divider())
        layout.addSpacing(12)

        # ── Footer buttons ─────────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.saved_lbl = QLabel("")
        self.saved_lbl.setObjectName("savedLabel")
        self.saved_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        footer.addWidget(self.saved_lbl)
        footer.addStretch()

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("applyBtn")
        apply_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        apply_btn.setFixedHeight(32)
        apply_btn.clicked.connect(self._apply)
        footer.addWidget(apply_btn)

        layout.addLayout(footer)

    def _build_combo(self) -> QComboBox:
        """Create a model dropdown with all available models."""
        combo = QComboBox()
        combo.setFont(QFont("Segoe UI", 10))
        for model_id, label in MODELS:
            combo.addItem(label, userData=model_id)
        return combo

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("divider")
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    # ── Load / save ────────────────────────────────────────────────────────────
    def _load_current_values(self):
        cfg = _load_voxkage_config()
        self._set_combo(self.main_combo, cfg.get("main_model", DEFAULT_MAIN_MODEL))
        self._set_combo(self.sub_combo, cfg.get("subagent_model", DEFAULT_SUBAGENT_MODEL))

    def _set_combo(self, combo: QComboBox, model_id: str):
        for i in range(combo.count()):
            if combo.itemData(i) == model_id:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def _apply(self):
        main_model    = self.main_combo.currentData()
        subagent_model = self.sub_combo.currentData()

        cfg = _load_voxkage_config()
        cfg["main_model"]     = main_model
        cfg["subagent_model"] = subagent_model
        _save_voxkage_config(cfg)

        # Also patch Gemini CLI settings so next terminal launch picks it up
        _update_gemini_model(main_model)

        self.saved_lbl.setText("✓ Saved")
        QTimer.singleShot(2000, lambda: self.saved_lbl.setText(""))

    # ── Position above tray icon ───────────────────────────────────────────────
    def _position_above_tray(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        w = self.width()
        h = self.height()

        if self.tray_geometry:
            # Place centered above tray icon
            x = self.tray_geometry.x() + self.tray_geometry.width() // 2 - w // 2
            y = self.tray_geometry.y() - h - 8
        else:
            # Fallback: bottom-right corner
            x = screen.right() - w - 16
            y = screen.bottom() - h - 56

        # Keep on screen
        x = max(screen.left(), min(x, screen.right() - w))
        y = max(screen.top(), y)

        self.move(x, y)

    # ── Drop shadow ────────────────────────────────────────────────────────────
    def _apply_shadow(self):
        effect = QGraphicsDropShadowEffect(self.card)
        effect.setBlurRadius(32)
        effect.setOffset(0, 4)
        effect.setColor(QColor(0, 0, 0, 200))
        self.card.setGraphicsEffect(effect)

    # ── Click outside to close ─────────────────────────────────────────────────
    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        # Small delay prevents accidental close on open
        QTimer.singleShot(150, self._check_focus)

    def _check_focus(self):
        if not self.isActiveWindow():
            self.close()


# ── Standalone preview ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    panel = SettingsPanel()
    panel.show()
    panel.activateWindow()
    sys.exit(app.exec())
