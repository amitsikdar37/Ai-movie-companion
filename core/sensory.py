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

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        print(f"[SensoryEngine] Loading Faster-Whisper ({model_size}) on {device} ({compute_type})...")
        t0 = time.perf_counter()
        
        # Whisper runs on CPU in int8 to keep 100% of GPU VRAM free for Ollama VLM
        self.whisper = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=4
        )
        print(f"[SensoryEngine] Whisper loaded in {time.perf_counter() - t0:.2f}s.")

    def detect_acoustic_spike(self, samples: np.ndarray, window_ms: int = 100) -> Tuple[bool, float]:
        if len(samples) == 0:
            return False, 0.0

        # Window size in samples: 100ms at 16,000 Hz = 1,600 samples
        w = int((window_ms / 1000.0) * config.AUDIO_SAMPLE_RATE)
        num_windows = len(samples) // w

        if num_windows == 0:
            return False, 0.0

        # Reshape into windows and compute RMS per window
        trimmed = samples[: num_windows * w]
        reshaped = trimmed.reshape((num_windows, w))
        
        # RMS = sqrt(mean(x^2))
        rms_values = np.sqrt(np.mean(reshaped ** 2, axis=1))

        # Convert RMS to Decibels (dB = 20 * log10(RMS + epsilon))
        # Epsilon (1e-9) prevents log(0) runtime errors during dead silence
        db_values = 20.0 * np.log10(rms_values + 1e-9)

        median_db = float(np.median(db_values))
        peak_db = float(np.max(db_values))
        delta_db = peak_db - median_db

        loud_spike = delta_db >= config.ACOUSTIC_SPIKE_DB
        return loud_spike, delta_db

    def transcribe(self, samples: np.ndarray) -> str:
        if len(samples) == 0:
            return ""

        segments, _ = self.whisper.transcribe(
            samples,
            beam_size=1,            # Greedy search: ultra-fast
            vad_filter=True,        # Drops non-speech silence instantly
            vad_parameters=dict(min_silence_duration_ms=250),
            language="en"           # Set language directly to bypass detection latency
        )

        transcript = " ".join(seg.text.strip() for seg in segments if seg.text)
        return transcript.strip()

    def process_audio(self, raw_audio_bytes: bytes) -> Tuple[bool, float, str]:
        if not raw_audio_bytes:
            return False, 0.0, ""

        # Step 1: Cast raw binary buffer to 16-bit signed int, then normalize to float32
        audio_int16 = np.frombuffer(raw_audio_bytes, dtype=np.int16)
        samples = audio_int16.astype(np.float32) / 32768.0

        # Step 2: Physics check (Acoustics)
        loud_spike, delta_db = self.detect_acoustic_spike(samples)

        # Step 3: Semantic check (Whisper)
        transcript = self.transcribe(samples)

        return loud_spike, delta_db, transcript


if __name__ == "__main__":
    from core.vlc_client import VLCClient
    from core.demuxer import demux_slice

    print("=" * 70)
    print("DAY 1 MILESTONE TEST: Full Time-Traveler Ingestion Pipeline")
    print("=" * 70)

    # 1. Connect to VLC
    vlc = VLCClient()
    status = vlc.get_status()

    if not status.file_path or status.state == "stopped":
        print("[!] VLC is not playing. Open your movie in VLC and start playback first.")
        sys.exit(1)

    target_sec = status.time + config.LOOKAHEAD_SEC
    print(f"[VLC Ground Truth]:  {status.time:.2f}s | State: {status.state} | {os.path.basename(status.file_path)}")
    print(f"[Look-Ahead Target]: {target_sec:.2f}s (T + {config.LOOKAHEAD_SEC}s into the future)")
    print("-" * 70)

    # 2. Extract future slice in RAM
    t_start = time.perf_counter()
    frames, audio_bytes = demux_slice(status.file_path, target_sec)
    t_demux = time.perf_counter() - t_start

    # 3. Sensory Analysis (NumPy + Whisper)
    engine = SensoryEngine(model_size="base")
    
    t_sensory_start = time.perf_counter()
    loud_spike, delta_db, transcript = engine.process_audio(audio_bytes)
    t_sensory = time.perf_counter() - t_sensory_start
    t_total = time.perf_counter() - t_start

    # 4. Report Day 1 Milestone Results
    print(f"-> [T+{config.LOOKAHEAD_SEC:.0f}s Extracted]:  {len(frames)} JPEGs in RAM ({sum(len(f) for f in frames):,} bytes)")
    spike_label = "LOUD SPIKE DETECTED!" if loud_spike else "Normal Volume"
    print(f"-> [Audio dB Delta]:    +{delta_db:.1f} dB ({spike_label})")
    print(f"-> [Whisper Dialogue]:  \"{transcript if transcript else '[No dialogue detected]'}\"")
    print("-" * 70)
    print(f"[Latency Breakdown]:")
    print(f"   * Demuxer Slicing:   {t_demux:.3f}s")
    print(f"   * Sensory (dB + AI): {t_sensory:.3f}s")
    print(f"   * Total Pipeline:    {t_total:.3f}s")
    print("=" * 70)

    if len(frames) == 3 and len(audio_bytes) == 160000:
        print("[SUCCESS] DAY 1 MILESTONE ACHIEVED! All invariants satisfied.")
    else:
        print("[WARNING] Pipeline completed with partial data.")