# core/sensory.py
"""
Acoustic & Dialogue Sensory Engine.
Equipped with Center-Channel vocal focus, Silero VAD,
and anti-hallucination decoding parameters for noisy cinematic audio.
"""

import os
import sys
import time
from typing import Tuple
import numpy as np
from faster_whisper import WhisperModel

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


class SensoryEngine:
    """
    Multimodal audio analysis engine combining acoustic physics (NumPy RMS)
    and robust speech transcription (Faster-Whisper small.en on CPU).
    """

    def __init__(self, model_size: str = "small.en", device: str = "cpu", compute_type: str = "int8"):
        print(f"[SensoryEngine] Loading Whisper ({model_size}) on {device} ({compute_type})...")
        t0 = time.perf_counter()
        
        # 'small.en' provides dramatically superior acoustic separation for English dialogue
        self.whisper = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=4
        )
        print(f"[SensoryEngine] Whisper loaded in {time.perf_counter() - t0:.2f}s.")

    def detect_acoustic_spike(self, samples: np.ndarray, window_ms: int = 100) -> Tuple[bool, float]:
        """
        Analyzes audio power using sliding-window Root Mean Square (RMS).
        Flags loud_spike=True if peak volume exceeds median volume by >15.0 dB.
        """
        if len(samples) == 0:
            return False, 0.0

        w = int((window_ms / 1000.0) * config.AUDIO_SAMPLE_RATE)
        num_windows = len(samples) // w

        if num_windows == 0:
            return False, 0.0

        trimmed = samples[: num_windows * w]
        reshaped = trimmed.reshape((num_windows, w))
        
        rms_values = np.sqrt(np.mean(reshaped ** 2, axis=1))
        db_values = 20.0 * np.log10(rms_values + 1e-9)

        median_db = float(np.median(db_values))
        peak_db = float(np.max(db_values))
        delta_db = peak_db - median_db

        loud_spike = delta_db >= config.ACOUSTIC_SPIKE_DB
        return loud_spike, delta_db

    def transcribe(self, samples: np.ndarray) -> str:
        """
        Transcribes audio with strict anti-hallucination and noise-rejection filters.
        """
        if len(samples) == 0:
            return ""

        segments, _ = self.whisper.transcribe(
            samples,
            beam_size=1,                         # Fast greedy search
            temperature=0.0,                     # Zero random guessing
            condition_on_previous_text=False,    # Kills repetitive hallucination loops!
            no_speech_threshold=0.6,             # Drops instrumental/score hallucination
            compression_ratio_threshold=2.4,     # Discards repetitive gibberish
            vad_filter=True,                     # Silero VAD: processes human speech only
            vad_parameters=dict(
                min_silence_duration_ms=250,
                threshold=0.5
            ),
            language="en"
        )

        transcript = " ".join(seg.text.strip() for seg in segments if seg.text)
        return transcript.strip()

    def process(self, raw_audio_bytes: bytes, fallback_subtitles: str = "") -> Tuple[bool, float, str, str]:
        """
        Dual-Engine Sensory Pipeline:
        1. Evaluates acoustic volume spike (NumPy RMS physics).
        2. Priority 1: If clean embedded subtitles exist, use them (100% human ground truth).
        3. Priority 2: If no subtitles exist, run noise-hardened Whisper model.
        """
        # Step 1: Acoustic Physics
        if raw_audio_bytes:
            audio_int16 = np.frombuffer(raw_audio_bytes, dtype=np.int16)
            samples = audio_int16.astype(np.float32) / 32768.0
            loud_spike, delta_db = self.detect_acoustic_spike(samples)
        else:
            samples = np.array([], dtype=np.float32)
            loud_spike, delta_db = False, 0.0

        # Step 2: Speech Dialogue (Subtitles first, Whisper fallback)
        if fallback_subtitles.strip():
            final_dialogue = fallback_subtitles.strip()
            source = "Embedded Subtitles"
        else:
            final_dialogue = self.transcribe(samples)
            source = "Whisper Neural Audio"

        return loud_spike, delta_db, final_dialogue, source


if __name__ == "__main__":
    from core.vlc_client import VLCClient
    from core.demuxer import demux_slice

    print("=" * 70)
    print("TESTING HARDENED SENSORY ENGINE (Whisper small.en + VAD)")
    print("=" * 70)

    vlc = VLCClient()
    status = vlc.get_status()

    if not status.file_path or status.state == "stopped":
        print("[!] VLC is not playing. Start video playback first.")
        sys.exit(1)

    target_sec = status.time + config.LOOKAHEAD_SEC
    print(f"[VLC Ground Truth]:  {status.time:.2f}s | {os.path.basename(status.file_path)}")
    print(f"[Look-Ahead Target]: {target_sec:.2f}s (T + {config.LOOKAHEAD_SEC}s)")
    print("-" * 70)

    # 1. Demux Slice (Frames, Audio, Subtitles)
    t0 = time.perf_counter()
    frames, audio_bytes, subs = demux_slice(status.file_path, target_sec)
    
    # 2. Process through Sensory Engine
    engine = SensoryEngine(model_size="small.en")
    
    # Force Whisper test by testing BOTH subtitles and pure audio:
    audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
    samples = audio_int16.astype(np.float32) / 32768.0
    
    t_whisper_start = time.perf_counter()
    pure_whisper_dialogue = engine.transcribe(samples)
    t_whisper = time.perf_counter() - t_whisper_start
    
    loud_spike, delta_db = engine.detect_acoustic_spike(samples)

    print(f"-> [Acoustic Spike]:   +{delta_db:.1f} dB (Spike: {loud_spike})")
    print(f"-> [Subtitles Track]:  \"{subs if subs else '[No subtitles]'}\"")
    print(f"-> [Pure Whisper AI]:  \"{pure_whisper_dialogue if pure_whisper_dialogue else '[No speech detected]'}\"")
    print(f"-> [Whisper Latency]:  {t_whisper:.3f} seconds")
    print("=" * 70)