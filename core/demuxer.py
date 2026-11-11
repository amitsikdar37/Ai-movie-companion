import os
import sys
import subprocess
import time
from typing import List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


def extract_frames(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> List[bytes]:
    vf_filter = (
        f"fps=3/{duration},"
        "scale=384:384:force_original_aspect_ratio=decrease,"
        "pad=384:384:(ow-iw)/2:(oh-ih)/2"
    )

    cmd = [
        "ffmpeg",
        "-ss", f"{start_sec:.2f}",   # Fast seek before input
        "-i", file_path,
        "-t", f"{duration:.2f}",
        "-vf", vf_filter,
        "-f", "image2pipe",          # Stream output continuously
        "-vcodec", "mjpeg",          # JPEG compression
        "-"                          # Pipe directly to stdout (RAM)
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        raw_bytes, _ = process.communicate(timeout=3.0)
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        return []

    frames: List[bytes] = []
    start_idx = 0

    while True:
        soi = raw_bytes.find(b"\xff\xd8", start_idx)
        if soi == -1:
            break

        eoi = raw_bytes.find(b"\xff\xd9", soi)
        if eoi == -1:
            break

        # Slice the complete JPEG byte buffer
        jpeg_buf = raw_bytes[soi : eoi + 2]
        frames.append(jpeg_buf)
        start_idx = eoi + 2

    return frames


def extract_audio(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> bytes:

    cmd = [
        "ffmpeg",
        "-ss", f"{start_sec:.2f}",   # Fast seek
        "-i", file_path,
        "-t", f"{duration:.2f}",
        "-vn",                       # Drop video stream
        "-ac", "1",                  # Mono channel
        "-ar", str(config.AUDIO_SAMPLE_RATE),  # 16,000 Hz
        "-f", "s16le",               # Signed 16-bit Little Endian PCM
        "-"                          # Pipe directly to stdout (RAM)
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        raw_audio, _ = process.communicate(timeout=3.0)
        return raw_audio
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        return b""


def demux_slice(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> Tuple[List[bytes], bytes]:
    frames = extract_frames(file_path, start_sec, duration)
    audio = extract_audio(file_path, start_sec, duration)
    return frames, audio


if __name__ == "__main__":
    from core.vlc_client import VLCClient

    print("=" * 70)
    print("Testing In-Memory Demuxer against current VLC playback...")
    print("=" * 70)

    client = VLCClient()
    status = client.get_status()

    if not status.file_path or status.state == "stopped":
        print("[!] VLC is not currently playing any file.")
        print("[!] Open a video in VLC and play it, then run this test again.")
        sys.exit(1)

    lookahead_target = status.time + config.LOOKAHEAD_SEC
    print(f"Current VLC Time:    {status.time:.2f}s")
    print(f"Look-Ahead Target:   {lookahead_target:.2f}s (T + {config.LOOKAHEAD_SEC}s)")
    print(f"Target Media File:   {status.file_path}")
    print("\nExecuting In-Memory Demuxing (Zero Disk I/O)...")

    t_start = time.perf_counter()
    frames, audio = demux_slice(status.file_path, lookahead_target)
    t_elapsed = time.perf_counter() - t_start

    print("-" * 70)
    print(f"-> Extracted Frames:  {len(frames)} (Expected: 3)")
    for i, f in enumerate(frames):
        print(f"   * Frame {i + 1}: {len(f):,} bytes | SOI: {f[:2].hex().upper()} | EOI: {f[-2:].hex().upper()}")

    expected_audio_len = int(config.SLICE_DURATION_SEC * config.AUDIO_SAMPLE_RATE * 2)
    print(f"-> Extracted Audio:   {len(audio):,} bytes (Expected: {expected_audio_len:,} bytes)")
    print(f"-> Pipeline Latency:  {t_elapsed:.3f} seconds")
    print("=" * 70)

    if len(frames) == 3 and len(audio) == expected_audio_len:
        print("[SUCCESS] Phase 1.2 In-Memory Demuxer is verified and fully functional!")
    else:
        print("[WARNING] Extraction completed with partial or missing data.")