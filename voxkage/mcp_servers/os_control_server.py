"""
MCP Server: OS Control — Deep File/Folder Operations

Covers: copy, cut/move, rename, create_folder, sort_directory,
        find_duplicates, recycle bin, image compress/resize/wallpaper,
        kill_process, check_windows_updates.

All destructive operations require confirmed=True after user approval.
Run standalone: python mcp_servers/os_control_server.py
"""

import os
import sys
import re
import shutil
import hashlib
import subprocess
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-oscontrol")


# ── Directory/file resolution helpers ────────────────────────────────────────

_NOISE = {"the","a","an","my","is","in","on","at","file","folder","app","application",
          "of","to","open","go","find","show","me","please","can","you","it","i",
          "want","that","with","inside","into","under","new","create","make"}

def _tokens(text: str) -> set:
    return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()) - _NOISE

def _fscore(query: str, name: str) -> float:
    q, n = _tokens(query), _tokens(name)
    if not q: return 0.0
    if query.lower() in name.lower() or name.lower() in query.lower(): return 1.0
    if not n: return 0.0
    return len(q & n) / len(q)

def _resolve_path(description: str) -> str | None:
    """Resolve a natural-language path to an absolute OS path."""
    if os.path.exists(description):
        return os.path.abspath(description)
    user_home = os.path.expanduser("~")
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
        path = os.path.join(user_home, name)
        if _fscore(description, name) >= 0.5 and os.path.exists(path):
            return path
    # Search common roots
    for root in (user_home, "C:\\"):
        try:
            for entry in os.scandir(root):
                if _fscore(description, entry.name) >= 0.6 and entry.is_dir():
                    return entry.path
        except Exception:
            pass
    return None

def _find_file(description: str) -> str | None:
    """Find a file by description in common user folders."""
    user_home = os.path.expanduser("~")
    search_dirs = [
        os.path.join(user_home, "Desktop"),
        os.path.join(user_home, "Downloads"),
        os.path.join(user_home, "Documents"),
        os.path.join(user_home, "Pictures"),
    ]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for entry in os.scandir(d):
            if entry.is_file() and _fscore(description, entry.name) >= 0.45:
                return entry.path
    return None


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def copy_item(
    source: str,
    destination: str,
    confirmed: bool = False,
) -> str:
    """
    Copy a file or folder to a destination location.

    HARD STOP CONFIRMATION GATE (main session only):
      1. Call with confirmed=False → preview shown → ask user "Agreed?"
      2. END YOUR TURN. Wait for user's next message.
      3. Only call with confirmed=True after user says yes.

    Parameters:
      source      : absolute path OR natural language (e.g. "my resume in Downloads")
      destination : target folder — absolute path OR natural language (e.g. "Desktop")
      confirmed   : False = preview. True = execute.
    """
    src = source if os.path.exists(source) else _find_file(source) or _resolve_path(source)
    if not src or not os.path.exists(src):
        return f"Could not find: '{source}'. Please provide an absolute path."

    dst_dir = destination if os.path.isdir(destination) else _resolve_path(destination)
    if not dst_dir:
        return f"Destination not found: '{destination}'."

    dst_path = os.path.join(dst_dir, os.path.basename(src))
    kind = "folder" if os.path.isdir(src) else "file"

    if not confirmed:
        return (
            f"[CONFIRM] Copy {kind}:\n"
            f"  📄 From: {src}\n"
            f"  📁 To:   {dst_path}\n\n"
            f"Agreed?"
        )
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst_path, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst_path)
        return f"✓ Copied '{os.path.basename(src)}' → {dst_dir}"
    except Exception as e:
        return f"Copy failed: {e}"


@mcp.tool()
def cut_item(
    source: str,
    destination: str,
    confirmed: bool = False,
) -> str:
    """
    Move (cut + paste) a file or folder to a new location.

    HARD STOP CONFIRMATION GATE: call confirmed=False first, show preview, wait for user approval.

    Parameters:
      source      : absolute path OR natural language description
      destination : target folder — absolute path OR natural language
      confirmed   : False = preview. True = execute (irreversible move).
    """
    src = source if os.path.exists(source) else _find_file(source) or _resolve_path(source)
    if not src or not os.path.exists(src):
        return f"Could not find: '{source}'."

    dst_dir = destination if os.path.isdir(destination) else _resolve_path(destination)
    if not dst_dir:
        return f"Destination not found: '{destination}'."

    dst_path = os.path.join(dst_dir, os.path.basename(src))
    kind = "folder" if os.path.isdir(src) else "file"

    if not confirmed:
        return (
            f"[CONFIRM] Move (cut) {kind}:\n"
            f"  📄 From: {src}\n"
            f"  📁 To:   {dst_path}\n\n"
            f"⚠️ The original will be removed from its current location. Agreed?"
        )
    try:
        shutil.move(src, dst_path)
        return f"✓ Moved '{os.path.basename(src)}' → {dst_dir}"
    except Exception as e:
        return f"Move failed: {e}"


@mcp.tool()
def rename_item(
    item_path: str,
    new_name: str,
    confirmed: bool = False,
) -> str:
    """
    Rename a file or folder.

    HARD STOP CONFIRMATION GATE: confirmed=False → preview → wait → confirmed=True.

    Parameters:
      item_path : absolute path OR natural language description of the file/folder
      new_name  : the new name (just the name, not the full path)
      confirmed : False = preview. True = execute rename.
    """
    item = item_path if os.path.exists(item_path) else _find_file(item_path) or _resolve_path(item_path)
    if not item or not os.path.exists(item):
        return f"Could not find: '{item_path}'."

    parent = os.path.dirname(item)
    # Preserve extension if new_name has none and item is a file
    if os.path.isfile(item) and "." not in new_name:
        ext = os.path.splitext(item)[1]
        new_name = new_name + ext

    new_path = os.path.join(parent, new_name)

    if not confirmed:
        return (
            f"[CONFIRM] Rename:\n"
            f"  Old: {os.path.basename(item)}\n"
            f"  New: {new_name}\n"
            f"  In:  {parent}\n\n"
            f"Agreed?"
        )
    try:
        os.rename(item, new_path)
        return f"✓ Renamed to '{new_name}' at {parent}"
    except Exception as e:
        return f"Rename failed: {e}"


@mcp.tool()
def create_folder(
    parent_directory: str,
    folder_name: str,
    confirmed: bool = False,
) -> str:
    """
    Create a new folder inside a parent directory.

    HARD STOP CONFIRMATION GATE: confirmed=False → preview → wait → confirmed=True.

    Parameters:
      parent_directory : where to create the folder — absolute path or natural language
      folder_name      : name of the new folder
      confirmed        : False = preview. True = create.
    """
    parent = parent_directory if os.path.isdir(parent_directory) else _resolve_path(parent_directory)
    if not parent:
        return f"Parent directory not found: '{parent_directory}'."

    new_path = os.path.join(parent, folder_name)

    if not confirmed:
        return (
            f"[CONFIRM] Create folder:\n"
            f"  📁 {new_path}\n\n"
            f"Agreed?"
        )
    try:
        os.makedirs(new_path, exist_ok=True)
        return f"✓ Created folder: {new_path}"
    except Exception as e:
        return f"Failed to create folder: {e}"


@mcp.tool()
def sort_directory(
    directory: str,
    sort_by: str = "name",
) -> str:
    """
    List files in a directory sorted by the specified criteria and return the sorted listing.
    Does NOT physically rename or move files — it shows you a sorted view so you can decide
    what to do next.

    Parameters:
      directory : absolute path OR natural language (e.g. "Downloads")
      sort_by   : "name" | "size" | "date" | "type"
                  name = alphabetical
                  size = largest first
                  date = newest first
                  type = grouped by extension
    """
    dir_path = directory if os.path.isdir(directory) else _resolve_path(directory)
    if not dir_path:
        return f"Directory not found: '{directory}'."

    try:
        entries = [e for e in os.scandir(dir_path) if e.is_file()]
    except Exception as e:
        return f"Cannot scan directory: {e}"

    if sort_by == "size":
        entries.sort(key=lambda e: e.stat().st_size, reverse=True)
        label = "Largest first"
    elif sort_by == "date":
        entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        label = "Newest first"
    elif sort_by == "type":
        entries.sort(key=lambda e: os.path.splitext(e.name)[1].lower())
        label = "Grouped by extension"
    else:
        entries.sort(key=lambda e: e.name.lower())
        label = "Alphabetical"

    lines = [f"📁 {dir_path} — sorted by: {sort_by} ({label})\n"]
    for e in entries:
        try:
            sz = e.stat().st_size
            sz_s = f"{sz // 1024}KB" if sz > 1024 else f"{sz}B"
        except Exception:
            sz_s = "?"
        ext = os.path.splitext(e.name)[1] or "(no ext)"
        lines.append(f"  {e.name}  [{sz_s}]  {ext}")
    lines.append(f"\nTotal: {len(entries)} files")
    return "\n".join(lines)


@mcp.tool()
def find_duplicates(
    search_path: str,
    file_types: str = "",
) -> str:
    """
    Scan a directory (recursively) to find duplicate files based on content hash.
    Groups duplicates together and shows total wasted space.

    Parameters:
      search_path : directory to scan — absolute path OR natural language (e.g. "Downloads")
      file_types  : optional comma-separated extensions to scan (e.g. ".jpg,.pdf,.docx").
                    Leave empty to scan ALL files.
    """
    dir_path = search_path if os.path.isdir(search_path) else _resolve_path(search_path)
    if not dir_path:
        return f"Directory not found: '{search_path}'."

    allowed_exts = set()
    if file_types.strip():
        allowed_exts = {e.strip().lower().lstrip(".") for e in file_types.split(",")}
        allowed_exts = {"." + e for e in allowed_exts}

    def file_hash(path: str) -> str | None:
        try:
            h = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    hash_map: dict[str, list[str]] = {}
    scanned = 0
    for root, _, files in os.walk(dir_path):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if allowed_exts and ext not in allowed_exts:
                continue
            fpath = os.path.join(root, fname)
            h = file_hash(fpath)
            if h:
                hash_map.setdefault(h, []).append(fpath)
            scanned += 1

    dupes = {h: paths for h, paths in hash_map.items() if len(paths) > 1}
    if not dupes:
        return f"No duplicates found in '{dir_path}'. Scanned {scanned} files."

    lines = [f"Found {len(dupes)} duplicate groups in '{dir_path}' (scanned {scanned} files):\n"]
    total_waste = 0
    for i, (h, paths) in enumerate(dupes.items(), 1):
        try:
            sz = os.path.getsize(paths[0])
        except Exception:
            sz = 0
        waste = sz * (len(paths) - 1)
        total_waste += waste
        sz_s = f"{sz // 1024}KB" if sz > 1024 else f"{sz}B"
        lines.append(f"Group {i} ({sz_s} each, {len(paths)} copies, wastes {waste // 1024}KB):")
        for p in paths:
            lines.append(f"  • {p}")
    lines.append(f"\nTotal wasted space: {total_waste // 1024 // 1024}MB ({total_waste // 1024}KB)")
    lines.append("\nTo delete duplicates, use delete_file() on the paths you want to remove.")
    return "\n".join(lines)


@mcp.tool()
def view_recycle_bin() -> str:
    """
    List files currently in the Windows Recycle Bin with their original paths and sizes.
    """
    try:
        import winshell
        items = list(winshell.recycle_bin())
        if not items:
            return "Recycle Bin is empty."
        lines = [f"Recycle Bin contents ({len(items)} items):\n"]
        total = 0
        for item in items[:50]:
            try:
                sz = item.recycle_date()
                orig = item.original_filename()
                lines.append(f"  📄 {os.path.basename(orig)}")
                lines.append(f"     Original: {orig}")
            except Exception:
                lines.append(f"  📄 (item details unavailable)")
        if len(items) > 50:
            lines.append(f"\n  ... and {len(items) - 50} more items.")
        return "\n".join(lines)
    except ImportError:
        # Fallback via PowerShell
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "$shell = New-Object -ComObject Shell.Application; "
                 "$rb = $shell.Namespace(10); "
                 "$rb.Items() | ForEach-Object { $_.Path + ' -> ' + $_.Name }"],
                capture_output=True, text=True, timeout=10
            )
            out = result.stdout.strip()
            if not out:
                return "Recycle Bin is empty (or could not read it)."
            return "Recycle Bin contents:\n" + out
        except Exception as e:
            return f"Failed to read Recycle Bin: {e}"


@mcp.tool()
def empty_recycle_bin(confirmed: bool = False) -> str:
    """
    Permanently empty the Windows Recycle Bin.

    HARD STOP CONFIRMATION GATE: confirmed=False → preview → wait → confirmed=True.

    Parameters:
      confirmed : False = show what will be deleted. True = permanently empty.
    """
    if not confirmed:
        # Count items
        try:
            import winshell
            items = list(winshell.recycle_bin())
            count_str = f"{len(items)} items"
        except Exception:
            count_str = "all items"

        return (
            f"[CONFIRM] Permanently empty Recycle Bin ({count_str})?\n\n"
            f"⚠️ This cannot be undone. Agreed?"
        )

    try:
        import winshell
        winshell.recycle_bin().empty(confirm=False, show_progress=False, sound=False)
        return "✓ Recycle Bin emptied successfully."
    except ImportError:
        try:
            result = subprocess.run(
                ["powershell", "-Command", "Clear-RecycleBin -Force"],
                capture_output=True, text=True, timeout=15
            )
            return "✓ Recycle Bin emptied via PowerShell."
        except Exception as e:
            return f"Failed to empty Recycle Bin: {e}"


@mcp.tool()
def compress_image(
    image_path: str,
    quality: int = 75,
    output_path: str = "",
    confirmed: bool = False,
) -> str:
    """
    Compress an image file to reduce its size.

    HARD STOP CONFIRMATION GATE: confirmed=False → preview → wait → confirmed=True.

    Parameters:
      image_path  : path to the image (JPG, PNG, WEBP) or natural language description
      quality     : compression quality 1-95 (default 75 — good balance). Lower = smaller file.
      output_path : where to save the compressed image (default: overwrites original with '_compressed' suffix)
      confirmed   : False = preview. True = compress.
    """
    from PIL import Image

    src = image_path if os.path.isfile(image_path) else _find_file(image_path)
    if not src:
        return f"Image not found: '{image_path}'."

    quality = max(1, min(95, quality))
    ext = os.path.splitext(src)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return f"Unsupported image format: {ext}. Use JPG, PNG, WEBP, or BMP."

    if not output_path:
        stem = os.path.splitext(src)[0]
        output_path = f"{stem}_compressed{ext}"

    orig_size = os.path.getsize(src)
    orig_kb = orig_size // 1024

    if not confirmed:
        return (
            f"[CONFIRM] Compress image:\n"
            f"  📄 Source: {src} ({orig_kb}KB)\n"
            f"  🎯 Quality: {quality}/95\n"
            f"  💾 Output: {output_path}\n\n"
            f"Agreed?"
        )

    try:
        with Image.open(src) as img:
            # Convert RGBA to RGB for JPEG
            if img.mode in ("RGBA", "P") and ext in (".jpg", ".jpeg"):
                img = img.convert("RGB")
            img.save(output_path, quality=quality, optimize=True)
        new_kb = os.path.getsize(output_path) // 1024
        saved = orig_kb - new_kb
        return (
            f"✓ Image compressed:\n"
            f"  Original: {orig_kb}KB → Compressed: {new_kb}KB (saved {saved}KB)\n"
            f"  Saved at: {output_path}"
        )
    except Exception as e:
        return f"Compression failed: {e}"


@mcp.tool()
def resize_image(
    image_path: str,
    width: int = 0,
    height: int = 0,
    scale_percent: int = 0,
    output_path: str = "",
    confirmed: bool = False,
) -> str:
    """
    Resize or upscale an image to specific dimensions or a percentage.

    HARD STOP CONFIRMATION GATE: confirmed=False → preview → wait → confirmed=True.

    Parameters:
      image_path    : path to the image or natural language description
      width         : target width in pixels (0 = auto from height or scale)
      height        : target height in pixels (0 = auto from width or scale)
      scale_percent : resize to X% of original (e.g. 50 = half, 200 = 2x upscale)
      output_path   : where to save (default: adds '_resized' suffix)
      confirmed     : False = preview. True = execute.
    """
    from PIL import Image

    src = image_path if os.path.isfile(image_path) else _find_file(image_path)
    if not src:
        return f"Image not found: '{image_path}'."

    ext = os.path.splitext(src)[1].lower()
    if not output_path:
        stem = os.path.splitext(src)[0]
        output_path = f"{stem}_resized{ext}"

    try:
        with Image.open(src) as img:
            orig_w, orig_h = img.size

        if scale_percent > 0:
            new_w = int(orig_w * scale_percent / 100)
            new_h = int(orig_h * scale_percent / 100)
        elif width > 0 and height > 0:
            new_w, new_h = width, height
        elif width > 0:
            ratio = width / orig_w
            new_w, new_h = width, int(orig_h * ratio)
        elif height > 0:
            ratio = height / orig_h
            new_w, new_h = int(orig_w * ratio), height
        else:
            return "Provide width, height, or scale_percent."

    except Exception as e:
        return f"Failed to open image: {e}"

    if not confirmed:
        return (
            f"[CONFIRM] Resize image:\n"
            f"  📄 Source: {src} ({orig_w}×{orig_h}px)\n"
            f"  🎯 New size: {new_w}×{new_h}px\n"
            f"  💾 Output: {output_path}\n\n"
            f"Agreed?"
        )

    try:
        with Image.open(src) as img:
            resample = Image.LANCZOS if new_w > orig_w or new_h > orig_h else Image.LANCZOS
            resized = img.resize((new_w, new_h), resample)
            resized.save(output_path)
        return (
            f"✓ Image resized:\n"
            f"  {orig_w}×{orig_h} → {new_w}×{new_h}px\n"
            f"  Saved at: {output_path}"
        )
    except Exception as e:
        return f"Resize failed: {e}"


@mcp.tool()
def set_wallpaper(image_path: str) -> str:
    """
    Set an image as the Windows desktop wallpaper immediately.
    No confirmation needed — this is non-destructive and instantly reversible.

    Parameters:
      image_path : absolute path to an image file (JPG, PNG, BMP)
                   OR natural language description (e.g. "moon wallpaper in Downloads")
    """
    src = image_path if os.path.isfile(image_path) else _find_file(image_path)
    if not src:
        return f"Image not found: '{image_path}'."

    try:
        import ctypes
        abs_path = os.path.abspath(src)
        # SPI_SETDESKWALLPAPER = 20, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE = 3
        result = ctypes.windll.user32.SystemParametersInfoW(20, 0, abs_path, 3)
        if result:
            return f"✓ Wallpaper set to: {os.path.basename(src)}"
        return "Failed to set wallpaper (SystemParametersInfoW returned 0)."
    except Exception as e:
        return f"Failed to set wallpaper: {e}"


@mcp.tool()
def kill_process(
    process_name_or_pid: str,
    confirmed: bool = False,
) -> str:
    """
    Kill a running Windows process by name or PID.

    HARD STOP CONFIRMATION GATE: confirmed=False → shows process info → wait → confirmed=True.

    Parameters:
      process_name_or_pid : process name (e.g. "chrome.exe", "notepad") or PID number
      confirmed           : False = show process info. True = terminate.
    """
    import psutil

    # Find matching processes
    targets = []
    try:
        pid_num = int(process_name_or_pid)
        p = psutil.Process(pid_num)
        targets = [p]
    except (ValueError, psutil.NoSuchProcess):
        name_lower = process_name_or_pid.lower().replace(".exe", "")
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                if name_lower in p.info["name"].lower():
                    targets.append(p)
            except Exception:
                pass

    if not targets:
        return f"No running process found matching: '{process_name_or_pid}'"

    if not confirmed:
        lines = [f"[CONFIRM] Terminate {len(targets)} process(es):\n"]
        for p in targets:
            try:
                mem_mb = p.memory_info().rss // 1024 // 1024
                lines.append(f"  PID {p.pid}: {p.name()} — {mem_mb}MB RAM")
            except Exception:
                lines.append(f"  PID {p.pid}: {p.name()}")
        lines.append("\n⚠️ Unsaved work in these processes will be lost. Agreed?")
        return "\n".join(lines)

    killed = []
    failed = []
    for p in targets:
        try:
            pname = p.name()
            p.terminate()
            killed.append(f"{pname} (PID {p.pid})")
        except Exception as e:
            failed.append(f"{p.pid}: {e}")

    result = []
    if killed:
        result.append(f"✓ Terminated: {', '.join(killed)}")
    if failed:
        result.append(f"Failed: {', '.join(failed)}")
    return "\n".join(result)


@mcp.tool()
def check_windows_updates() -> str:
    """
    Check for available Windows updates using PowerShell.
    Returns a list of available updates with titles and sizes.
    Note: This may take 15-30 seconds as it queries Windows Update servers.
    """
    try:
        script = (
            "Import-Module PSWindowsUpdate -ErrorAction SilentlyContinue; "
            "$updates = Get-WUList -AcceptAll 2>$null; "
            "if ($updates) { $updates | Select-Object Title, Size, MsrcSeverity | ConvertTo-Csv -NoTypeInformation } "
            "else { 'NO_MODULE' }"
        )
        result = subprocess.run(
            ["powershell", "-Command", script],
            capture_output=True, text=True, timeout=45
        )
        out = result.stdout.strip()

        if "NO_MODULE" in out or not out:
            # Fallback: use Windows Update COM API
            script2 = (
                "$session = New-Object -ComObject Microsoft.Update.Session; "
                "$searcher = $session.CreateUpdateSearcher(); "
                "$results = $searcher.Search('IsInstalled=0 and Type=Software'); "
                "$results.Updates | ForEach-Object { $_.Title + ' | KB' + ($_.KBArticleIDs -join ',') }; "
                "Write-Output ('TOTAL: ' + $results.Updates.Count + ' updates available')"
            )
            result2 = subprocess.run(
                ["powershell", "-Command", script2],
                capture_output=True, text=True, timeout=45
            )
            out2 = result2.stdout.strip()
            if not out2:
                return "No updates found or Windows Update service is unavailable."
            return f"Windows Update Status:\n{out2}"

        return f"Windows Update Status:\n{out}"

    except subprocess.TimeoutExpired:
        return "Windows Update check timed out (>45s). Try again or check via Windows Settings."
    except Exception as e:
        return f"Failed to check Windows updates: {e}"



@mcp.tool()
def find_files(
    directory: str,
    file_type: str = "",
    name_keyword: str = "",
    sort_by: str = "date",
    recursive: bool = False,
    top_n: int = 20,
) -> str:
    """
    Find files in a directory matching a type/extension and/or name keyword.
    Returns results sorted by date (newest first) by default.

    Use this tool for queries like:
      "latest MKV video in Downloads"     -> directory="Downloads", file_type="mkv"
      "find my Boys episode"              -> directory="Downloads", name_keyword="boys"
      "which PDFs are in Documents?"      -> directory="Documents", file_type="pdf"
      "biggest images in Pictures"        -> directory="Pictures", file_type="image", sort_by="size"
      "all Word files on Desktop"         -> directory="Desktop", file_type="word"

    Parameters:
      directory    : folder to search — natural language ("Downloads", "Desktop") or absolute path
      file_type    : extension or type keyword. Examples:
                     "mkv", "mp4", "avi", "mov"  -> video files
                     "video"                      -> all common video formats
                     "image" or "photo"           -> jpg, jpeg, png, gif, webp, bmp
                     "audio" or "music"           -> mp3, flac, wav, aac, ogg
                     "pdf", "docx", "xlsx", "pptx", "txt"  -> specific types
                     "document"                   -> pdf, docx, doc, txt, xlsx
                     ".mkv"                       -> exact extension (dot prefix OK)
                     ""                           -> all files
      name_keyword : optional string to filter filenames (case-insensitive substring match)
                     e.g. "boys", "season 4", "invoice"
      sort_by      : "date" (newest first), "size" (largest first), "name" (alphabetical)
      recursive    : False = top-level only (default). True = scan all subdirectories.
      top_n        : max results to return (default 20)
    """
    # Resolve directory
    dir_path = directory if os.path.isdir(directory) else _resolve_path(directory)
    if not dir_path:
        return f"Directory not found: '{directory}'. Try an absolute path."

    # Extension set from type keyword
    _TYPE_GROUPS = {
        "video":    {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts"},
        "image":    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".svg", ".ico"},
        "photo":    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic"},
        "audio":    {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".opus"},
        "music":    {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma"},
        "document": {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".odt"},
        "word":     {".docx", ".doc"},
        "excel":    {".xlsx", ".xls", ".csv"},
        "ppt":      {".pptx", ".ppt"},
        "pdf":      {".pdf"},
        "code":     {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".cpp", ".c", ".go"},
    }

    ft = file_type.lower().strip().lstrip(".")
    allowed_exts: set[str] = set()
    if ft:
        if ft in _TYPE_GROUPS:
            allowed_exts = _TYPE_GROUPS[ft]
        else:
            # Treat as direct extension
            allowed_exts = {"." + ft}

    # Collect files
    entries = []
    try:
        if recursive:
            for root, _, files in os.walk(dir_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    ext = os.path.splitext(fname)[1].lower()
                    if allowed_exts and ext not in allowed_exts:
                        continue
                    if name_keyword and name_keyword.lower() not in fname.lower():
                        continue
                    try:
                        stat = os.stat(fpath)
                        entries.append((fpath, fname, stat.st_size, stat.st_mtime))
                    except Exception:
                        pass
        else:
            for entry in os.scandir(dir_path):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if allowed_exts and ext not in allowed_exts:
                    continue
                if name_keyword and name_keyword.lower() not in entry.name.lower():
                    continue
                try:
                    stat = entry.stat()
                    entries.append((entry.path, entry.name, stat.st_size, stat.st_mtime))
                except Exception:
                    pass
    except Exception as e:
        return f"Failed to scan '{dir_path}': {e}"

    if not entries:
        filters = []
        if file_type:
            filters.append(f"type='{file_type}'")
        if name_keyword:
            filters.append(f"name containing '{name_keyword}'")
        filter_str = " and ".join(filters) if filters else "any type"
        return (
            f"No files found in '{dir_path}' matching {filter_str}.\n"
            f"Try recursive=True to search subdirectories, or broaden the filter."
        )

    # Sort
    if sort_by == "size":
        entries.sort(key=lambda e: e[2], reverse=True)
        sort_label = "largest first"
    elif sort_by == "name":
        entries.sort(key=lambda e: e[1].lower())
        sort_label = "alphabetical"
    else:
        entries.sort(key=lambda e: e[3], reverse=True)
        sort_label = "newest first"

    import datetime
    lines = [
        f"Found {len(entries)} file(s) in '{dir_path}'",
        f"Filter: type='{file_type or 'any'}'"
        + (f", name contains '{name_keyword}'" if name_keyword else "")
        + f" | Sorted: {sort_label}\n"
    ]
    shown = entries[:top_n]
    for fpath, fname, sz, mtime in shown:
        sz_s = f"{sz // 1024 // 1024}MB" if sz > 1024*1024 else (f"{sz // 1024}KB" if sz > 1024 else f"{sz}B")
        dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        ext = os.path.splitext(fname)[1].upper()
        lines.append(f"  {fname}  [{sz_s}]  {ext}  {dt}")
        lines.append(f"    Path: {fpath}")
    if len(entries) > top_n:
        lines.append(f"\n  ... and {len(entries) - top_n} more. Use top_n={len(entries)} to see all.")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
