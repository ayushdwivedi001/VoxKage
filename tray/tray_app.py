import sys
import os
import subprocess

# Prevent PySide6/COM DPI overlapping crashes on initialization
os.environ["QT_GQL_NO_DPI_AWARENESS"] = "1"

if "--settings" in sys.argv:
    from settings_gui import launch_settings_gui
    launch_settings_gui()
    sys.exit(0)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import main

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QFileDialog
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QObject, Signal, Slot, Qt

import threading
import queue

class TrayBridge(QObject):
    request_file_picker = Signal()

tray_bridge = TrayBridge()
picker_queue = queue.Queue()

@Slot()
def handle_file_picker():
    # Runs on Main UI Thread!
    dialog = QFileDialog(None, "Select a Document for VoxKage to Read", "")
    dialog.setNameFilter("Documents (*.pdf *.docx *.txt *.csv *.log *.py);;PDF Files (*.pdf);;Word Documents (*.docx);;Text Files (*.txt);;CSV Files (*.csv);;Log Files (*.log);;Python Scripts (*.py);;All Files (*.*)")
    
    # Strictly force dialog to stay permanently above all other windows
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
    dialog.raise_()
    dialog.activateWindow()
    
    if dialog.exec():
        selected = dialog.selectedFiles()
        path = selected[0] if selected else ""
    else:
        path = ""
        
    picker_queue.put(path)

tray_bridge.request_file_picker.connect(handle_file_picker)

# Import your assistant start function
from main import start_assistant  # adjust to your main startup function

settings_process = None 
assistant_thread = None

def run_assistant_thread():
    try:
        start_assistant()
    except Exception as e:
        import traceback
        with open("crash_log.txt", "w", encoding="utf-8") as f:
            f.write("Crash occurred during assistant startup or execution:\n")
            f.write(traceback.format_exc())

def run_assistant(icon=None, item=None):
    global assistant_thread
    # Prevent multiple assistant threads from starting
    if assistant_thread and assistant_thread.is_alive():
        print("Assistant is already running.")
        return
    assistant_thread = threading.Thread(target=run_assistant_thread, daemon=True)
    assistant_thread.start()

def open_settings(icon=None, item=None):
    global settings_process
    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle("VoxKage Enterprise")
        if windows:
            win = windows[0]
            if win.isMinimized:
                win.restore()
            win.activate()
            return
    except Exception as e:
        pass

    try:
        if settings_process is None or settings_process.poll() is not None:
            if getattr(sys, 'frozen', False):
                settings_process = subprocess.Popen([sys.executable, "--settings"])
            else:
                settings_process = subprocess.Popen([sys.executable, "settings_gui.py"])
        else:
            print("Settings already open.")
    except Exception as e:
        print("Failed to open settings:", e)

def quit_app(tray_icon):
    global settings_process
    if settings_process and settings_process.poll() is None:  # still running
        settings_process.terminate()  # kill settings
    tray_icon.hide()
    
    # Nuke the process forcefully since whisper/LLM daemons can become zombies
    os._exit(0)

def setup_tray():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    
    app.setQuitOnLastWindowClosed(False)

    from config_loader import get_resource_path
    icon_path = get_resource_path(os.path.join("icons", "icon.png"))
    
    tray_icon = QSystemTrayIcon(QIcon(icon_path), app)
    
    menu = QMenu()
    
    start_action = QAction("Start VoxKage Assistant")
    start_action.triggered.connect(lambda: run_assistant())
    menu.addAction(start_action)
    
    settings_action = QAction("Settings")
    settings_action.triggered.connect(lambda: open_settings())
    menu.addAction(settings_action)
    
    exit_action = QAction("Exit")
    exit_action.triggered.connect(lambda: quit_app(tray_icon))
    menu.addAction(exit_action)
    
    tray_icon.setContextMenu(menu)
    tray_icon.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    setup_tray()
