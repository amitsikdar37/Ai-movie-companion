# core/demuxer.py
"""
In-Memory Look-Ahead Demuxer.
Uses FFmpeg via stdout pipes to extract video frames, raw PCM audio buffers,
and embedded subtitles directly into RAM with Zero Disk I/O.
"""

import os
import sys
import re
import subprocess
import time
from typing import List, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


def extract_frames(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> List[bytes]:
    """
    Extracts exactly 3 downscaled JPEG frames (384x384) over a duration-second window
    using an in-memory FFmpeg stdout pipe.
    """
    vf_filter = (
        f"fps=3/{duration},"
        "scale=384:384:force_original_aspect_ratio=decrease,"
        "pad=384:384:(ow-iw)/2:(oh-ih)/2"
    )

    cmd = [
        "ffmpeg",
        "-ss", f"{start_sec:.2f}",
        "-i", file_path,
        "-t", f"{duration:.2f}",
        "-vf", vf_filter,
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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

        frames.append(raw_bytes[soi : eoi + 2])
        start_idx = eoi + 2

    return frames


def extract_subtitles(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> str:
    cmd = [
        "ffmpeg",
        "-ss", f"{start_sec:.2f}",
        "-i", file_path,
        "-t", f"{duration:.2f}",
        "-map", "0:s:0?",          # Try mapping first subtitle stream
        "-f", "srt",
        "-"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_srt, _ = process.communicate(timeout=2.0)
        srt_text = raw_srt.decode("utf-8", errors="ignore").strip()
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        return ""

    if not srt_text:
        return ""

    # Parse and clean SRT text
    dialogue_lines = []
    for line in srt_text.splitlines():
        line = line.strip()
        # Skip empty lines, sequence numbers (e.g. '1', '2'), and timestamps (contains '-->')
        if not line or line.isdigit() or "-->" in line:
            continue
        # Remove formatting tags like <i>, </i>, <font...>, etc.
        cleaned = re.sub(r"<[^>]+>", "", line)
        # Remove SDH brackets like [gunfire], (chuckles)
        cleaned = re.sub(r"\[.*?\]|\(.*?\)", "", cleaned).strip()
        if cleaned:
            dialogue_lines.append(cleaned)

    return " ".join(dialogue_lines)


def extract_audio(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> bytes:
    """
    Extracts raw 16kHz mono 16-bit PCM audio bytes over a duration-second window.
    Tries English audio track first; falls back to default audio track if unavailable.
    """
    # Attempt 1: English audio track specifically
    cmd_english = [
        "ffmpeg",
        "-ss", f"{start_sec:.2f}",
        "-i", file_path,
        "-t", f"{duration:.2f}",
        "-map", "0:a:m:language:eng",   # Clean syntax (no trailing ?)
        "-vn",
        "-ac", "1",
        "-ar", str(config.AUDIO_SAMPLE_RATE),
        "-f", "s16le",
        "-"
    ]

    try:
        process = subprocess.Popen(cmd_english, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_audio, _ = process.communicate(timeout=3.0)
        if len(raw_audio) >= 160000:
            return raw_audio
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass

    # Attempt 2: Fallback to default audio track (for movies with single track or no tags)
    cmd_fallback = [
        "ffmpeg",
        "-ss", f"{start_sec:.2f}",
        "-i", file_path,
        "-t", f"{duration:.2f}",
        "-vn",
        "-ac", "1",
        "-ar", str(config.AUDIO_SAMPLE_RATE),
        "-f", "s16le",
        "-"
    ]

    try:
        process = subprocess.Popen(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_audio, _ = process.communicate(timeout=3.0)
        return raw_audio
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        return b""


def demux_slice(file_path: str, start_sec: float, duration: float = config.SLICE_DURATION_SEC) -> Tuple[List[bytes], bytes, str]:
    """
    Simultaneously extracts video frames, audio buffers, and embedded subtitles.
    Returns:
        (frames, audio_bytes, subtitles_text)
    """
    frames = extract_frames(file_path, start_sec, duration)
    audio = extract_audio(file_path, start_sec, duration)
    subs = extract_subtitles(file_path, start_sec, duration)
    return frames, audio, subs


if __name__ == "__main__":
    from core.vlc_client import VLCClient

    print("=" * 70)
    print("Testing In-Memory Demuxer with SUBTITLE EXTRACTION...")
    print("=" * 70)

    client = VLCClient()
    status = client.get_status()

    if not status.file_path or status.state == "stopped":
        print("[!] VLC is not currently playing any file.")
        sys.exit(1)

    target_sec = status.time + config.LOOKAHEAD_SEC
    print(f"Current VLC Time:    {status.time:.2f}s")
    print(f"Look-Ahead Target:   {target_sec:.2f}s")
    print(f"Target Media File:   {os.path.basename(status.file_path)}")
    print("\nExecuting In-Memory Demuxing (Frames + Audio + Subtitles)...")

    t_start = time.perf_counter()
    frames, audio, subtitles = demux_slice(status.file_path, target_sec)
    t_elapsed = time.perf_counter() - t_start

    print("-" * 70)
    print(f"-> Extracted Frames:     {len(frames)} JPEGs in RAM")
    print(f"-> Extracted Audio:      {len(audio):,} bytes")
    print(f"-> Extracted Subtitles:  \"{subtitles if subtitles else '[No subtitles in this 5s window]'}\"")
    print(f"-> Latency:              {t_elapsed:.3f} seconds")
    print("=" * 70)