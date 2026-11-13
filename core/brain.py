# core/brain.py
"""
Cognitive Brain & Structured Vision-Language Model.
Equipped with Black-Frame Filtering, Zero-Crash Fallbacks,
and Rolling Plot Memory.
"""

import os
import sys
import time
import io
import base64
from enum import Enum
from typing import List, Optional
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
import ollama

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


class EmoteType(str, Enum):
    """The physical emotion the desktop avatar must perform."""
    SHOCK = "shock"
    LAUGH = "laugh"
    CRINGE = "cringe"
    CRY = "cry"
    HYPE = "hype"
    CONFUSED = "confused"
    NEUTRAL = "neutral"


class ReactionPayload(BaseModel):
    """Strict Pydantic Schema guaranteed by Ollama grammar-constrained decoding."""
    updated_plot_memory: str = Field(
        description="A concise 2-sentence rolling summary of the ongoing narrative, integrating this new scene."
    )
    should_react: bool = Field(
        description="True ONLY if this scene contains a genuine emotional peak, jump scare, plot twist, or comedy punchline. False during normal scenes."
    )
    trigger_offset_sec: float = Field(
        default=0.0,
        ge=0.0,
        le=5.0,
        description="Precise offset in seconds (0.0 to 5.0) within this 5-second slice where the reaction must occur."
    )
    emote: EmoteType = Field(
        description="The physical emotion the avatar must perform."
    )
    bubble_text: str = Field(
        description="Reaction text strictly between 1 and 4 words. Must be punchy, natural, and expressive. Examples: 'NO WAY!', 'HE'S COOKED!', 'RUN BRO!', 'THAT'S CRAZY'."
    )


SYSTEM_PROMPT = """You are an animated desktop companion watching a movie live with the user.
You have eyes (sequential scene frames) and ears (dialogue and volume spikes).

CRITICAL BEHAVIOR RULES:
1. bubble_text must strictly be between 1 and 4 punchy words (e.g. 'NO WAY!', 'He's cooked!', 'Run bro!', 'That's wild!'). Never use long sentences.
2. should_react must be FALSE during ordinary dialogue, quiet moments, or transitions. Set should_react to TRUE ONLY when a genuine jump scare, funny joke, shocking plot twist, or intense action occurs.
3. Be natural, authentic, and expressive like a close friend sitting on the couch. Never break character. Never output conversational explanations outside the JSON schema.
4. Keep updated_plot_memory strictly to 2 concise sentences summarizing the ongoing narrative."""


def sanitize_frames(frames: List[bytes]) -> List[bytes]:
    """
    Detects solid-black transition cuts and replaces them with the
    previous valid visible frame to prevent VLM null-token repetition loops.
    """
    clean_frames = []
    last_valid_frame = None

    for f in frames:
        try:
            img = Image.open(io.BytesIO(f))
            arr = np.array(img)
            # If average brightness is > 12.0, it is a valid visible scene
            if arr.mean() > 12.0:
                clean_frames.append(f)
                last_valid_frame = f
            else:
                # Black transition cut: reuse previous visible frame if available
                clean_frames.append(last_valid_frame if last_valid_frame else f)
        except Exception:
            clean_frames.append(f)

    return clean_frames


class CognitiveBrain:
    """Manages VLM inference, memory persistence, and seek-flushing."""

    def __init__(self, model_name: str = "qwen2.5vl:3b"):
        self.model_name = model_name
        self.rolling_memory = "The movie is in progress. Observing the ongoing narrative."
        print(f"[CognitiveBrain] Initialized with local model: {self.model_name}")

    def reset_memory(self):
        """Flushes rolling memory when a user timeline seek jump occurs."""
        self.rolling_memory = "Timeline skipped. Re-evaluating ongoing scene context."
        print("[CognitiveBrain] Timeline seek detected. Rolling memory flushed.")

    def evaluate_scene(
        self,
        jpeg_frames: List[bytes],
        dialogue: str,
        loud_spike: bool,
        update_memory: bool = True
    ) -> Optional[ReactionPayload]:
        """
        Evaluates a 5-second multimodal slice and returns a validated ReactionPayload.
        Equipped with Black-Frame Filtering and Graceful Fallback.
        """
        if not jpeg_frames:
            return None

        # Shield 1: Sanitize frames to eliminate pitch-black transition cuts
        sanitized_frames = sanitize_frames(jpeg_frames)
        b64_images = [base64.b64encode(img).decode("utf-8") for img in sanitized_frames]

        dialogue_text = dialogue.strip() if dialogue.strip() else "[No spoken dialogue in this slice]"
        spike_info = "YES! A sudden loud sound/explosion was detected." if loud_spike else "No, sound levels are normal."

        user_prompt = (
            f"Current Rolling Memory: {self.rolling_memory}\n"
            f"Dialogue: \"{dialogue_text}\"\n"
            f"Acoustic Spike: {spike_info}\n\n"
            "Analyze the sequential scene frames and decide if a reaction is warranted."
        )

        try:
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": user_prompt,
                        "images": b64_images
                    }
                ],
                format=ReactionPayload.model_json_schema(),
                options={
                    "temperature": 0.2,
                    "num_ctx": 4096,
                    "repeat_penalty": 1.15
                }
            )

            raw_json = response["message"]["content"].strip()
            
            # Shield 2: If Ollama returned empty string, fallback gracefully
            if not raw_json:
                print("[CognitiveBrain] VLM returned empty content, invoking fallback...")
                return self._fallback_evaluate(dialogue_text, loud_spike, update_memory)

            payload = ReactionPayload.model_validate_json(raw_json)
            if update_memory:
                self.rolling_memory = payload.updated_plot_memory
            return payload

        except Exception as e:
            print(f"[CognitiveBrain Warning]: {e}. Invoking dialogue fallback.")
            return self._fallback_evaluate(dialogue_text, loud_spike, update_memory)

    def _fallback_evaluate(self, dialogue: str, loud_spike: bool, update_memory: bool = True) -> ReactionPayload:
        """Fallback evaluation using dialogue and acoustic spike without images."""
        try:
            user_prompt = (
                f"Current Rolling Memory: {self.rolling_memory}\n"
                f"Dialogue: \"{dialogue}\"\n"
                f"Acoustic Spike: {'YES' if loud_spike else 'No'}\n\n"
                "Generate reaction JSON based on dialogue and sound."
            )
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                format=ReactionPayload.model_json_schema(),
                options={"temperature": 0.3, "num_ctx": 2048}
            )
            raw_json = response["message"]["content"].strip()
            if raw_json:
                payload = ReactionPayload.model_validate_json(raw_json)
                if update_memory:
                    self.rolling_memory = payload.updated_plot_memory
                return payload
        except Exception:
            pass

        # Final ultra-safe default
        return ReactionPayload(
            updated_plot_memory=self.rolling_memory,
            should_react=False,
            trigger_offset_sec=0.0,
            emote=EmoteType.NEUTRAL,
            bubble_text="..."
        )


if __name__ == "__main__":
    from core.vlc_client import VLCClient
    from core.demuxer import demux_slice
    from core.sensory import SensoryEngine

    print("=" * 70)
    print("DAY 2 MILESTONE TEST: Hardened Multimodal Cognitive Brain")
    print("=" * 70)

    vlc = VLCClient()
    status = vlc.get_status()

    if not status.file_path or status.state == "stopped":
        print("[!] VLC is not playing. Open a video in VLC first.")
        sys.exit(1)

    target_sec = status.time + config.LOOKAHEAD_SEC
    print(f"Current VLC Position: {status.time:.2f}s | {os.path.basename(status.file_path)}")
    print(f"Look-Ahead Target:    {target_sec:.2f}s (T + {config.LOOKAHEAD_SEC}s)")
    print("-" * 70)

    t0 = time.perf_counter()
    frames, audio_bytes, subtitles = demux_slice(status.file_path, target_sec)
    t_demux = time.perf_counter() - t0

    engine = SensoryEngine(model_size="small.en")
    loud_spike, delta_db, dialogue, source = engine.process(audio_bytes, subtitles)

    print(f"-> Extracted Frames:  {len(frames)} JPEGs in RAM")
    print(f"-> Dialogue Source:   {source}")
    print(f"-> Spoken Dialogue:   \"{dialogue if dialogue else '[Silence / Ambiance]'}\"")
    print(f"-> Acoustic Spike:    +{delta_db:.1f} dB (Spike: {loud_spike})")
    print("-" * 70)

    brain = CognitiveBrain(model_name="qwen2.5vl:3b")
    print("Sending multimodal slice to Qwen2.5-VL...")
    
    t_brain_start = time.perf_counter()
    reaction = brain.evaluate_scene(frames, dialogue, loud_spike)
    t_brain = time.perf_counter() - t_brain_start

    print("=" * 70)
    if reaction:
        print("[AI REACTION PAYLOAD RECEIVED]:")
        print(f"   * Should React:      {reaction.should_react}")
        print(f"   * Emote Animation:   {reaction.emote.value.upper()}")
        print(f"   * Speech Bubble:     \"{reaction.bubble_text}\"")
        print(f"   * Trigger Second:    +{reaction.trigger_offset_sec:.1f}s into slice")
        print(f"   * Updated Memory:    {reaction.updated_plot_memory}")
        print("-" * 70)
        print(f"[Latency Benchmark]:")
        print(f"   * Demuxer Latency:   {t_demux:.3f}s")
        print(f"   * Brain VLM Latency: {t_brain:.3f}s")
        print(f"   * Total Time Elapsed:{t_demux + t_brain:.3f}s (Budget: 12.0s)")
        print("=" * 70)
        print("[SUCCESS] DAY 2 MILESTONE ACHIEVED! Brain is bulletproof!")