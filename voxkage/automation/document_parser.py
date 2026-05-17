import os

try:
    import fitz  # PyMuPDF — requires pip install voxkage[docs_plus]
except ImportError:
    fitz = None

try:
    from docx import Document  # python-docx — ships in core
except ImportError:
    Document = None
import logging
import threading

logger = logging.getLogger(__name__)

def extract_text(file_path: str) -> str:
    """
    Extracts text from a given file path based on its extension.
    Supports .pdf, .docx, and .txt files.
    """
    if not os.path.exists(file_path):
        return f"File not found: {file_path}"
        
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext == '.pdf':
            if fitz is None:
                return "[VoxKage] PDF reading requires PyMuPDF. Run: pip install voxkage[docs_plus]"
            text = ""
            with fitz.open(file_path) as doc:
                for page in doc:
                    page_text = page.get_text().strip()
                    if len(page_text) < 20:
                        try:
                            from rapidocr_onnxruntime import RapidOCR
                            import numpy as np
                            ocr = RapidOCR()
                            pix = page.get_pixmap(dpi=150, alpha=False)
                            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                            result, _ = ocr(img)
                            if result:
                                page_text = "\n".join([line[1] for line in result])
                        except Exception as e:
                            logger.error(f"OCR failed on PDF page: {e}")
                    text += page_text + "\n"
            return text.strip()
            
        elif ext in ('.png', '.jpg', '.jpeg', '.bmp'):
            try:
                from rapidocr_onnxruntime import RapidOCR
                import cv2
                ocr = RapidOCR()
                img = cv2.imread(file_path)
                result, _ = ocr(img)
                if result:
                    return "\n".join([line[1] for line in result])
                return ""
            except Exception as e:
                logger.error(f"OCR failed on image {file_path}: {e}")
                return ""
            
        elif ext == '.docx':
            if Document is None:
                return "[VoxKage] DOCX reading requires python-docx. Run: pip install python-docx"
            doc = Document(file_path)
            return "\n".join([paragraph.text for paragraph in doc.paragraphs]).strip()
            
        elif ext in ['.txt', '.csv', '.log', '.py']:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read().strip()
                
        else:
            return f"Unsupported file type: {ext}"
    except Exception as e:
        logger.error(f"Error parsing {file_path}: {e}")
        return f"Error reading file {os.path.basename(file_path)}: {str(e)}"

def chunk_text(text: str, chunk_size: int = 4000) -> list:
    """
    Splits text into chunks of roughly `chunk_size` characters, 
    preferring to break at newlines or spaces.
    """
    chunks = []
    # If the text is small enough, return it directly
    if len(text) <= chunk_size:
        return [text]
        
    start = 0
    while start < len(text):
        end = start + chunk_size
        
        # If we're at the end of the text, append the rest
        if end >= len(text):
            chunks.append(text[start:])
            break
            
        # Try to find a good breaking point (newline or space) searching backwards
        break_point = end
        for i in range(end, max(start, end - 500), -1):
            if text[i] in ['\n', ' ']:
                break_point = i
                break
                
        chunks.append(text[start:break_point].strip())
        start = break_point + 1
        
    return chunks

def find_file(keyword: str, search_dirs: list = None) -> str:
    """
    Searches common user directories recursively for a file containing the keyword in its name.
    Returns the absolute path of the first match, or None.
    """
    if search_dirs is None:
        user_home = os.path.expanduser('~')
        search_dirs = [
            os.path.join(user_home, "Documents"),
            os.path.join(user_home, "Downloads"),
            os.path.join(user_home, "Desktop")
        ]
    keyword_lower = keyword.lower()
    # Split keyword to handle spaces vs underscores in filenames
    search_terms = keyword_lower.split()
    
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
            
        for root, dirs, files in os.walk(search_dir):
            for file in files:
                file_lower = file.lower()
                if file_lower.endswith(('.pdf', '.docx', '.txt')):
                    # Check if all search terms are in the filename
                    if all(term in file_lower for term in search_terms):
                        return os.path.join(root, file)
    return None

def analyze_specific_file_sync(file_path: str, query: str) -> str:
    """
    Synchronous function to extract text, chunk it if necessary, and query the LLM.
    Since long documents take time to read, this is kept simple for the LLM tool call.
    """
    logger.info(f"Analyzing file: {file_path} for query: {query}")
    
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif'):
        import base64
        try:
            with open(file_path, "rb") as img_file:
                b64_img = base64.b64encode(img_file.read()).decode("utf-8")
            import json
            return json.dumps({
                "__vision__": True,
                "text": f"User query: {query}\n[Vision analysis of image: {file_path}]",
                "screenshot_b64": b64_img
            })
        except Exception as e:
            return f"Error reading image for vision analysis: {e}"

    text = extract_text(file_path)
    if "Error" in text or "Unsupported" in text or "File not found" in text:
        return text
        
    # Standard Qwen context length handles several thousand tokens safely
    # We will grab up to 6000 chars right now for simplicity.
    # Advanced logic: if asking to search for a topic throughout the document,
    # we would iterate through chunks.
    
    chunks = chunk_text(text, 6000)
    
    # Simple logic: If it's a short question ("summarize"), do the first chunk
    # Or, feed the first chunk containing relevant keywords.
    # To keep response times low, we just use Chunk 1
    # unless we do a multi-chunk summarize (which takes a long time).
    
    used_chunk = chunks[0]
    if len(chunks) > 1:
        prefix = f"[Note: This is a large file ({len(chunks)} pages/chunks). The following is the BEGINNING of the file.]\n\n"
        used_chunk = prefix + used_chunk
    else:
        prefix = "[Entire File Content]\n\n"
        used_chunk = prefix + used_chunk
        
    # We don't ask the LLM to recursively call itself here, we just return the text
    # so the LLM context has the file data to answer the user!
    
    return used_chunk

def open_file_picker_sync() -> str:
    """Opens a native Windows file dialog to pick a file via Qt Main Thread Signal."""
    try:
        from voxkage.tray.tray_app import tray_bridge, picker_queue
        
        # Clear queue just in case
        while not picker_queue.empty():
            picker_queue.get()
            
        tray_bridge.request_file_picker.emit()
        
        # Block until the UI thread passes the result back
        file_path = picker_queue.get(timeout=120)
        
        if file_path:
            try:
                from voxkage.llm.helpers import log_to_hud
                log_to_hud("VoxKage", f"[System] File selected: {file_path}")
            except Exception as e:
                pass
                
        return file_path
    except Exception as e:
        print(f"Error launching file picker: {e}")
        return ""
