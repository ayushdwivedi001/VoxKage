# 🛡️ VoxKage: The Living OS Agentic AI

<p align="center">
  <img src="icons/icon.png" width="200" alt="VoxKage Logo">
</p>

VoxKage is not just another AI assistant; it is a **Living OS Agentic AI**. While most AI agents today are confined to a single directory or a browser tab, VoxKage lives directly on your operating system, commanding your PC with the authority of a digital sovereign. 

Built on a foundation of **Gemini 2.0 Flash** and orchestrated with **LangGraph**, VoxKage bridges the gap between raw LLM reasoning and real-world OS automation.

---

## 🚀 Why VoxKage?

### Traditional Agents vs. VoxKage
| Feature | Single-Directory Agents | VoxKage (OS Agentic AI) |
| :--- | :--- | :--- |
| **Scope** | Limited to one folder/project | **Entire Filesystem (C:/, Desktop, Downloads, etc.)** |
| **Interface** | Text-only or web UI | **Voice-First + GUI HUD + Telegram Remote** |
| **OS Control** | None | **Hardware Control (Volume, Brightness, Wifi, Shutdown)** |
| **App Control** | Only what's in the sandbox | **Native Apps (VS Code, Word, Spotify, Chrome, etc.)** |
| **Offline First** | Rarely | **Local STT (Whisper), Local TTS (Kokoro), Local RAG** |

---

## ✨ Key Features

- **🎙️ Voice-First Sovereignty:** Always-listening wake word detection ("VoxKage" or "Vision") with high-quality local TTS.
- **🖥️ System-Wide Automation:** Control hardware, launch apps, and manage files anywhere on your PC via natural language.
- **🌐 Browser Agent:** Deep research, price comparisons, and multi-site workflows using advanced browser automation.
- **📚 Local Knowledge RAG:** Index your personal documents (PDF, Word, TXT, CSV) and codebase for instant semantic retrieval.
- **📱 Telegram Bridge:** Command your PC from anywhere in the world. Send files, get reports, or shutdown your PC from your phone.
- **🎵 Multimedia King:** Integrated Spotify and YouTube controls. "Play my lofi beats" just works.
- **🤖 Agentic Reasoning:** Uses LangGraph to perform multi-step tasks, self-correcting when it encounters errors.

---

## 🛠️ Tech Stack

- **Core:** Python 3.10+
- **LLM:** Gemini 2.0 Flash (Reasoning Engine)
- **Orchestration:** LangGraph (Stateful Agentic Loops)
- **Voice:** `faster-whisper` (STT), `Kokoro` (Premium Local TTS)
- **GUI:** PySide6 (Material Design HUD)
- **MCP:** Model Context Protocol for tool modularity
- **Automation:** Playwright, PyAutoGUI, Keyboard
- **Database:** Local Vector DB for RAG

---

## 📦 Installation Guide

### Prerequisites
- **Python 3.10 or 3.11** (Recommended)
- **FFmpeg** (Required for audio processing)
- **Git**

### 1. Clone the Repository
```bash
git clone https://github.com/ayushdwivedi001/VoxKage.git
cd VoxKage
```

### 2. Set Up Virtual Environment
```bash
python -m venv venv
venv\Scripts\activate  # Windows
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Configure Environment
Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
TELEGRAM_BOT_TOKEN=your_bot_token_here (optional)
TELEGRAM_CHAT_ID=your_chat_id_here (optional)
SPOTIPY_CLIENT_ID=your_id
SPOTIPY_CLIENT_SECRET=your_secret
```

---

## 🎮 Usage

### Launching VoxKage
Simply run the main entry point:
```bash
python main.py
```
This will launch the **VoxKage HUD** and start the voice listener.

### Commands to Try:
- **Voice:** "VoxKage, open my resume and summarize it."
- **Voice:** "VoxKage, search for the best mechanical keyboards under $100 and compare them."
- **Voice:** "VoxKage, play some dark academia music on Spotify."
- **Telegram:** "Is my PC running?" or "Shutdown in 10 minutes."

---

## 📂 Project Structure

- `automation/`: Core logic for browser, system, and app control.
- `llm/`: The "Brain" - Gemini integration and LangGraph loops.
- `mcp_servers/`: Modular tool servers following the MCP standard.
- `tg_bridge/`: Telegram bot integration for remote access.
- `voice/`: Local STT/TTS and intent routing.
- `icons/`: High-quality assets for the UI.

---

## 🤝 Contributing
VoxKage is an evolving entity. If you'd like to contribute to its growth, feel free to fork the repo and submit a PR!

---

### 👑 Built by [Ayush Dwivedi](https://github.com/ayushdwivedi001)

---
