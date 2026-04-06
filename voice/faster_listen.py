import os
import numpy as np
import pyaudio
import time
import re
from faster_whisper import WhisperModel

# Load the Whisper model once globally
model = None
# ----------------------------
# Model bootstrap (robust)
# ----------------------------
_model = None

def get_model():
    global _model
    if _model is not None:
        return _model

    print("⚙️ Getting Started...")
    # Try fast CUDA FP16 first, fall back to CPU INT8
    try:
        _model = WhisperModel("small", device="cuda", compute_type="float16")
        print("✅ Loaded Whisper small (CUDA, fp16)")
    except Exception:
        _model = WhisperModel("small", device="cpu", compute_type="int8")
        print("✅ Loaded Whisper small (CPU, int8)")
    return _model

# ----------------------------
# Audio config
# ----------------------------
SAMPLE_RATE = 16000
CHUNK_SIZE = 1024
BASE_THRESHOLD = 100          # fallback if calibration fails
SILENCE_DURATION = 1.0        # seconds of trailing silence to stop
MAX_RECORD_SECONDS = 15       # safety limit

def rms(data_bytes: bytes) -> float:
    """Root-mean-square of int16 audio buffer."""
    if not data_bytes:
        return 0.0
    samples = np.frombuffer(data_bytes, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))

def calibrate_threshold(p, seconds=0.6):
    """Quick ambient noise calibration for dynamic VAD threshold."""
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        frames = int(seconds * SAMPLE_RATE / CHUNK_SIZE)
        vals = []
        for _ in range(max(1, frames)):
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            vals.append(rms(data))
        stream.stop_stream()
        stream.close()
        ambient = np.median(vals) if vals else BASE_THRESHOLD
        # Slight headroom above ambient
        thr = max(BASE_THRESHOLD * 0.6, ambient * 1.8)
        return thr
    except Exception:
        return BASE_THRESHOLD

def trim_trailing_silence(frames, threshold):
    """Trim trailing silent frames by RMS threshold."""
    for i in range(len(frames) - 1, -1, -1):
        if rms(frames[i]) >= threshold:
            return frames[:i + 1]
    return frames

# ----------------------------
# Text normalization
# ----------------------------
def normalize_for_intent(text: str) -> str:
    """
    Keep things important for NLU (numbers, %, quotes, basic punctuation).
    We only lowercase and collapse whitespace.
    """
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

# ----------------------------
# Public API
# ----------------------------
def listen():
    """
    Records a short utterance, translates to English, and returns
    a normalized English string good for intent parsing.
    """
    from voice.voice_manager import manager
    manager.was_interrupted = False
    
    print("🎤 Listening...")
    p = pyaudio.PyAudio()

    # Calibrate VAD threshold from ambient
    threshold = calibrate_threshold(p) or BASE_THRESHOLD
    # print(f"🔧 VAD threshold ~ {int(threshold)}")  # (optional debug)

    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )

    frames = []
    silent_chunks = 0
    speaking_started = False
    start_time = time.time()

    try:
        while True:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            vol = rms(data)

            if vol > threshold:
                frames.append(data)
                silent_chunks = 0
                speaking_started = True
            elif speaking_started:
                frames.append(data)
                silent_chunks += 1

            # stop on trailing silence
            if speaking_started and (silent_chunks * CHUNK_SIZE / SAMPLE_RATE) > SILENCE_DURATION:
                break

            # safety stop
            if (time.time() - start_time) > MAX_RECORD_SECONDS:
                break
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    print("🧠 Transcribing...")
    if not frames:
        print("❌ No speech detected.")
        return None

    frames = trim_trailing_silence(frames, threshold)
    audio = b"".join(frames)
    audio_np = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

    model = get_model()
    # Force English output for all languages
    segments, info = model.transcribe(
        audio_np,
        task="translate",        # << always output English
        language=None,           # auto-detect spoken language
        vad_filter=False,        # disabled for strict catch-all
        beam_size=5,
        best_of=5,
        temperature=0.0
    )

    # We expect a short utterance; take the first segment’s text
    full_text = ""
    for seg in segments:
        full_text += seg.text.strip() + " "
    full_text = full_text.strip()

    if not full_text:
        print("❌ Didn't catch anything.")
        return None

    # Show raw English transcript (debug)
    print(f"🗣️ Raw (EN): {full_text}")

    normalized = normalize_for_intent(full_text)
    print(f"✨ Normalized: {normalized}")
    return normalized or None

def get_model():
    global model
    if model is None:
        print("⚙️ Getting Started...")
        model = WhisperModel("small", compute_type="float16", device="auto")
    return model

# Constants
SAMPLE_RATE = 16000
CHUNK_SIZE = 1024
THRESHOLD = 100  # You can tweak this based on your environment
SILENCE_DURATION = 1  # Seconds of silence to stop recording
MAX_RECORD_SECONDS = 15  # Safety limit

def rms(data):
    """Calculate Root Mean Square (RMS) of audio data."""
    samples = np.frombuffer(data, dtype=np.int16)
    samples = samples.astype(np.float32)
    samples = np.clip(samples, -32768, 32767)  # Avoid invalid sqrt
    if samples.size == 0:
        return 0
    return np.sqrt(np.mean(np.square(samples)))

def clean_transcription(text):
    """Lowercase, strip, and remove all punctuation including full stops, commas, etc."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)  # remove anything that is not word or space
    return text

def trim_trailing_silence(frames, threshold=THRESHOLD):
    """Trim silent frames from the end."""
    for i in range(len(frames) - 1, -1, -1):
        vol = rms(frames[i])
        if vol >= threshold:
            return frames[:i + 1]
    return frames  # In case all are silent

def listen():
    from voice.voice_manager import manager
    manager.was_interrupted = False
    print("🎤 Listening...")
    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )

    frames = []
    silent_chunks = 0
    speaking_started = False
    start_time = time.time()

    try:
        while True:
            # --- IPC Command Injection ---
            # Polling for GUI-injected text (e.g. File Attachments from settings_gui.py)
            if os.path.exists(".ui_command"):
                try:
                    with open(".ui_command", "r", encoding="utf-8") as f:
                        injected_text = f.read().strip()
                    os.remove(".ui_command")
                    if injected_text:
                        print("🗣️ GUI Injected text from Dashboard.")
                        return injected_text
                except Exception:
                    pass
            # -----------------------------

            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            volume = rms(data)

            if volume > THRESHOLD:
                frames.append(data)
                silent_chunks = 0
                speaking_started = True
            elif speaking_started:
                silent_chunks += 1
                frames.append(data)

            if silent_chunks * CHUNK_SIZE / SAMPLE_RATE > SILENCE_DURATION:
                break

            if time.time() - start_time > MAX_RECORD_SECONDS:
                break
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    print("🧠 Transcribing...")
    if not frames:
        # print("❌ No speech detected.") # Suppress spam
        return None
    
    # ✅ Trim silence from the end
    trimmed_frames = trim_trailing_silence(frames)
    audio_data = b"".join(trimmed_frames)
    audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

    model = get_model()
    # Removed strict VAD filter to ensure maximum recognition capability
    segments, info = model.transcribe(audio_np, language="en", vad_filter=False)
    
    for segment in segments:
        cleaned = clean_transcription(segment.text)
        if cleaned:
            print(f"🗣️ You said: {cleaned}")
            return cleaned

    # print("❌ Didn't catch anything.") # Suppress background noise spam
    return None
