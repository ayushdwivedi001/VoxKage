# Hard offline mode - MUST be set before any HuggingFace-related imports
import os
os.environ["HF_HUB_OFFLINE"] = "1"

import re
import queue
import threading
import tempfile
import time
import traceback
import subprocess
import numpy as np
import soundfile as sf
import torch
from kokoro import KPipeline
import logging

logger = logging.getLogger(__name__)

import json
from datetime import datetime

from config_loader import get_resource_path

def log_to_hud(sender: str, text: str):
    """Universal helper to append chat strings to Phase 11 Settings GUI HUD log."""
    try:
        log_path = os.path.join(os.path.abspath("."), ".hud_log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"sender": sender, "text": text, "timestamp": datetime.now().isoformat()}) + "\n")
    except Exception as e:
        logger.error(f"Failed to write to HUD log: {e}")

# Local model paths - 100% offline operation
KOKORO_MODEL_PATH = get_resource_path(os.path.join('models', 'tts', 'kokoro', 'kokoro-v1_0.pth'))
KOKORO_VOICE_PATH = get_resource_path(os.path.join('models', 'tts', 'kokoro', 'voices', 'af_heart.pt'))
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"  # Used for metadata only, no downloads when model path is local

# TTS Configuration
DEFAULT_VOICE = "af_heart"
KOKORO_LANG_CODE = "a"  # American English
KOKORO_SAMPLE_RATE = 24000  # Kokoro native sample rate
TTS_SPEED = 1.0

class VoiceManager:
    def __init__(self):
        self.pipeline = None
        self.voice_pack = None
        self.voice_name = DEFAULT_VOICE
        self.device = "cpu"  # Kokoro is fast on CPU; change to "cuda" if NVIDIA GPU available
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.playback_thread = None
        self._current_ps_process = None
        self.was_interrupted = False
        self._temp_wav_files = []  # Track temp files for cleanup

    def load_voice(self, voice: str = DEFAULT_VOICE):
        """Loads Kokoro Voice pipeline from local files (100% offline)."""
        if self.pipeline is not None:
            logger.info(f"Kokoro pipeline already loaded with voice: {self.voice_name}")
            return
            
        try:
            # 1. Load the Voice Pack tensor from local .pt file
            self.voice_pack = torch.load(KOKORO_VOICE_PATH, map_location=self.device, weights_only=True)
            
            # 2. Initialize Pipeline with local .pth file path
            # repo_id is for metadata only; model= path ensures no downloads
            self.pipeline = KPipeline(
                lang_code=KOKORO_LANG_CODE, 
                model=KOKORO_MODEL_PATH, 
                device=self.device,
                repo_id=KOKORO_REPO_ID
            )
            
            self.voice_name = voice
            logger.info(f"Kokoro initialized offline: model={KOKORO_MODEL_PATH}, voice={KOKORO_VOICE_PATH}")
        except Exception as e:
            logger.error(f"Failed to load Kokoro: {e}")
            import traceback
            traceback.print_exc()

    def _synthesize_to_wav(self, text: str, temp_wav: str) -> bool:
        """Synthesize text using local Kokoro model and save as WAV file.
        
        Includes aggressive silence trimming to remove the 1-2 second 
        pauses between sentences caused by the TTS engine's natural padding.
        """
        try:
            generator = self.pipeline(
                text, 
                voice=self.voice_pack, 
                speed=TTS_SPEED, 
                split_pattern=r'\n+'
            )

            audio_chunks = []
            for _, _, audio in generator:
                if audio is not None:
                    audio_chunks.append(audio)
            
            if not audio_chunks:
                logger.error(f"No audio generated for text: {text}")
                return False
            
            # Combine whatever chunks Kokoro spit out
            full_audio = np.concatenate(audio_chunks)
            
            # --- THE FIX: NUMPY SILENCE TRIMMING ---
            # 1. Find all indices where the audio volume is above a tiny threshold (0.01)
            threshold = 0.01
            non_silent_indices = np.where(np.abs(full_audio) > threshold)[0]
            
            # 2. If we found actual speech, crop the array
            if non_silent_indices.size > 0:
                # Keep a 500-sample padding at the start (~0.02s)
                start_idx = max(0, non_silent_indices[0] - 500)
                
                # Keep a 2400-sample buffer at the end (~0.1s at 24kHz) 
                # This prevents it from sounding aggressively chopped off.
                end_idx = min(len(full_audio), non_silent_indices[-1] + 2400)
                
                full_audio = full_audio[start_idx:end_idx]
            # ---------------------------------------
            
            # Write WAV file at 24kHz
            sf.write(temp_wav, full_audio, KOKORO_SAMPLE_RATE)
            return True
            
        except Exception as e:
            logger.error(f"Error synthesizing text '{text}': {e}")
            traceback.print_exc()
            return False

    def _play_audio_worker(self):
        """Background worker that pulls pre-synthesized WAV file paths from the queue and plays them via PowerShell."""
        try:
            while True:
                item = self.audio_queue.get()
                if item is None:  # Shutdown signal
                    break
                    
                self.is_playing = True
                
                # item is now a WAV file path (pre-synthesized)
                if isinstance(item, str) and item.endswith('.wav') and os.path.exists(item):
                    try:
                        # OS-Level Playback via PowerShell (with required escapes for Windows spaces)
                        print(f"--> [Kokoro TTS Local] Playing {item} via PowerShell")
                        cmd = ["powershell", "-c", f'(New-Object Media.SoundPlayer -ArgumentList "{item}").PlaySync()']
                        try:
                            self._current_ps_process = subprocess.Popen(cmd, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
                            # Dynamic timeout: estimate from WAV size (16-bit 22kHz mono ≈ 44000 bytes/sec)
                            # Add a 20s safety buffer so even large files never get cut short.
                            try:
                                file_size = os.path.getsize(item)
                                estimated_duration = max(15, (file_size / 44000) + 20)
                            except Exception:
                                estimated_duration = 60
                            try:
                                self._current_ps_process.wait(timeout=estimated_duration)
                            except subprocess.TimeoutExpired:
                                logger.warning(f"Audio playback timed out for {item}. Killing process.")
                                self._current_ps_process.kill()
                        except Exception as p_err:
                            logger.error(f"PowerShell Popen failed: {p_err}")
                        finally:
                            self._current_ps_process = None
                            
                        # Cleanup after playback
                        try:
                            os.remove(item)
                        except Exception as ex:
                            logger.error(f"Failed to remove temp wav {item}: {ex}")
                        
                    except Exception as e:
                        logger.error(f"Error playing wav file: {e}")
                        traceback.print_exc()
                        
                self.is_playing = False
                self.audio_queue.task_done()
        except Exception as e:
            logger.error(f"Error in audio playback thread: {e}")
            traceback.print_exc()
        finally:
            self.is_playing = False

    def start_playback_thread(self):
        if self.playback_thread is None or not self.playback_thread.is_alive():
            self.playback_thread = threading.Thread(target=self._play_audio_worker, daemon=True)
            self.playback_thread.start()

    def clean_text(self, text: str) -> str:
        """Removes markdown, emojis, and formatting artifacts from text before synthesis."""
        if not text:
            return ""
        
        # Remove markdown bold/italic markers (**text**, *text*, __text__)
        text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
        text = re.sub(r'_{2,3}([^_]+)_{2,3}', r'\1', text)
        
        # Remove heading markers (#)
        text = re.sub(r'#{1,6}\s+', '', text)
        
        # Remove code blocks and inline code
        text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
        
        # Remove remaining markdown artifacts
        text = re.sub(r'[*_#`~>\[\]\(\)]', '', text)
        
        # Remove Unicode emojis (supplementary planes U+10000+)
        text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
        
        # Also remove common emoji in BMP range (U+2600-U+27BF) and variation selectors
        text = re.sub(r'[\u2600-\u27BF\uFE00-\uFE0F]', '', text)
        
        # Remove zero-width joiner used in emoji sequences
        text = text.replace('\u200d', '')
        
        return text.strip()

    def speak_sentence(self, text: str):
        """Pre-synthesizes text to WAV and queues the file path for playback."""
        clean = self.clean_text(text)
        if not clean or self.pipeline is None:
            return
            
        try:
            # Pre-synthesize to WAV file before queuing for playback
            temp_dir = tempfile.gettempdir()
            temp_wav = os.path.join(temp_dir, f"temp_speech_{time.time()}.wav")
            
            if self._synthesize_to_wav(clean, temp_wav):
                # Queue the WAV file path for playback
                self.audio_queue.put(temp_wav)
            else:
                logger.error(f"Synthesis failed for text: {text}")
        except Exception as e:
            logger.error(f"Error queueing speech for text '{text}': {e}")
            traceback.print_exc()

    def stop_audio(self):
        """Hard Kill Switch: Empty queue and slaughter the native Windows audio stream."""
        self.is_playing = False
        self.was_interrupted = True
        logger.info("Voice TTS explicitly interrupted by user!")
        
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except queue.Empty:
                break
                
        if self._current_ps_process:
            try:
                self._current_ps_process.kill()
                logger.info("Killed PowerShell playback task.")
            except Exception as e:
                logger.error(f"Failed to kill audio process: {e}")

    def stop_and_clear(self):
        """Alias for standard shutdown routines if needed."""
        self.stop_audio()
                
    def wait_to_finish(self):
        """Blocks until all queued audio has finished playing."""
        self.audio_queue.join()

# Global singleton instance
manager = VoiceManager()

def init_voice():
    manager.load_voice()
    manager.start_playback_thread()
    
    # Bind Ctrl+Space global hotkey to stop audio
    try:
        if not getattr(init_voice, "hotkey_bound", False):
            import keyboard
            keyboard.add_hotkey('ctrl+space', manager.stop_audio, suppress=False)
            init_voice.hotkey_bound = True
            logger.info("Registered ctrl+space hotkey for audio interruption.")
    except Exception as e:
        logger.error(f"Failed to bind hotkey: {e}")

def speak(text: str):
    """
    Standard block-speak function for backward compatibility.
    Senses sentence boundaries and queues them for Kokoro TTS.
    """
    if not text:
        return
        
    log_to_hud("VoxKage", text)
        
    if not manager.pipeline:
        init_voice()
        
    sentences = re.split(r'(?<=[.!?])\s*', text.replace('\n', ' '))
    for sentence in sentences:
        if sentence.strip():
            manager.speak_sentence(sentence.strip())
            
    # Block until playback completes so the script doesn't start listening too early
    manager.wait_to_finish()

class SentenceStreamer:
    """
    Utility class to buffer streaming LLM tokens into full sentences,
    then sending them to the VoiceManager for parallel playback.
    """
    def __init__(self):
        self.buffer = ""
        # Common sentence terminating punctuation
        self.terminators = {'.', '!', '?'}
        if not manager.pipeline:
            init_voice()

    def add_token(self, token: str):
        # If user interrupted, discard all new tokens immediately
        if manager.was_interrupted:
            self.buffer = ""
            return
        
        self.buffer += token
        
        # Suppress the "IGNORE" token so it doesn't get spoken
        if self.buffer.strip().upper().startswith("IGNORE"):
            return
        
        # We look for terminating punctuation.
        if any(t in self.buffer for t in self.terminators):
            # Try to find the last index of a terminator
            match = re.search(r'([.!?])\s+', self.buffer)
            if match:
                split_idx = match.end()
                sentence = self.buffer[:split_idx].strip()
                self.buffer = self.buffer[split_idx:]
                
                if sentence:
                    from voice.voice_manager import log_to_hud
                    log_to_hud("VoxKage", sentence)
                    manager.speak_sentence(sentence)

    def flush(self):
        """Speak any remaining text in the buffer."""
        # Don't flush if user interrupted
        if manager.was_interrupted:
            self.buffer = ""
            return
        buf = self.buffer.strip()
        if buf and not buf.upper().startswith("IGNORE"):
            from voice.voice_manager import log_to_hud
            log_to_hud("VoxKage", buf)
            manager.speak_sentence(buf)
            self.buffer = ""
