# main.py
"""
AI Movie Companion - Master Application Orchestrator.
Coordinates the VLC Poller, In-Memory Look-Ahead Ingestion,
Cognitive VLM Brain, Timestamp Dispatcher, and Transparent PyQt6 Overlay.
Equipped with Monotonic Seek-Epoch Guards, Continuous Rolling Memory Updates,
Video-Switch Resets, Instant Post-Seek Slicing, and QThread Race-Protection.
"""

import os
import sys
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot, QObject, QTimer
from PyQt6.QtWidgets import QApplication

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from core.vlc_client import VLCClient, PlaybackState
from core.demuxer import demux_slice
from core.sensory import SensoryEngine
from core.brain import CognitiveBrain, ReactionPayload
from core.scheduler import ReactionScheduler
from ui.overlay import DesktopCompanionOverlay


class LookaheadWorker(QThread):
    """
    Background worker thread that slices future video/audio in RAM,
    evaluates scenes with Qwen2.5-VL, and signals reactions.
    """
    status_log = pyqtSignal(str)
    reaction_ready = pyqtSignal(int, float, object)  # (epoch, target_sec, ReactionPayload)

    def __init__(self, scheduler: ReactionScheduler):
        super().__init__()
        self.scheduler = scheduler
        self.brain = CognitiveBrain(model_name="qwen2.5vl:3b")
        # Lazy-loaded inside run() to prevent GUI startup freeze
        self.sensory: Optional[SensoryEngine] = None

        self.is_busy = False
        self.current_file = ""
        self.target_sec = 0.0
        self.epoch = 0

    def analyze_future_slice(self, file_path: str, target_sec: float, epoch: int):
        """
        Triggers analysis for a future timestamp with epoch tagging.
        Guards against QThread restart race conditions.
        """
        if self.is_busy or self.isRunning():
            return

        self.current_file = file_path
        self.target_sec = target_sec
        self.epoch = epoch
        self.start()

    def run(self):
        """Runs in-memory demuxing, sensory analysis, and VLM brain inference."""
        self.is_busy = True
        run_epoch = self.epoch
        try:
            # Lazy initialization of Whisper on first background task
            if self.sensory is None:
                self.sensory = SensoryEngine(model_size="small.en")

            # 1. Demux 5-second slice at target_sec
            frames, audio_bytes, subtitles = demux_slice(self.current_file, self.target_sec)
            if not frames:
                return

            # 2. Sensory processing (Physics dB check + Subtitles/Whisper)
            loud_spike, delta_db, dialogue, source = self.sensory.process(audio_bytes, subtitles)

            # 3. Cognitive Brain Evaluation (Ollama Qwen2.5-VL)
            # update_memory=False so stale in-flight results don't corrupt memory
            reaction = self.brain.evaluate_scene(frames, dialogue, loud_spike, update_memory=False)

            # Bug 1 Fix: Always emit valid payloads so rolling_memory updates even when should_react is False
            if reaction is not None:
                self.reaction_ready.emit(run_epoch, self.target_sec, reaction)

        except Exception as e:
            self.status_log.emit(f"[Worker Error]: {e}")
        finally:
            self.is_busy = False


class Orchestrator(QObject):
    """Coordinates VLC polling, seek flushing, and overlay reaction dispatch."""

    def __init__(self, overlay: DesktopCompanionOverlay):
        super().__init__()
        self.overlay = overlay
        self.vlc = VLCClient()
        self.scheduler = ReactionScheduler(cooldown_seconds=20.0)
        self.worker = LookaheadWorker(self.scheduler)

        self.worker.status_log.connect(self._log)
        self.worker.reaction_ready.connect(self.on_reaction_ready)

        self.seek_epoch = 0
        # Bug 2 Fix: Track active video file to prevent stalls when switching media
        self.active_file = ""
        self.last_analyzed_target = -1.0
        self.step_interval = config.SLICE_DURATION_SEC

    def _log(self, message: str):
        print(message)

    def _reset_pipeline(self, current_time: float, reason: str):
        """
        Unified pipeline reset for timeline seeks and media switches.
        Bug 3 Fix: Pre-subtracts step_interval so the post-seek slice triggers immediately.
        """
        self.seek_epoch += 1
        print(f"\n[{reason}] Bumped seek epoch to {self.seek_epoch}. Flushing pipeline...")
        self.scheduler.flush()
        self.brain_reset()
        self.last_analyzed_target = (current_time + config.LOOKAHEAD_SEC) - self.step_interval

    @pyqtSlot(int, float, object)
    def on_reaction_ready(self, epoch: int, target_sec: float, reaction: ReactionPayload):
        """
        Validates epoch freshness, updates rolling narrative memory continuously,
        and queues reaction if emotional peak was flagged.
        """
        if epoch != self.seek_epoch:
            print(f"[Orchestrator] Discarded stale result from epoch {epoch} (current: {self.seek_epoch}).")
            return

        # Bug 1 Fix: Always advance rolling plot memory for narrative continuity
        self.worker.brain.rolling_memory = reaction.updated_plot_memory

        # Only queue overlay popup if VLM flagged a reaction peak
        if reaction.should_react:
            self.scheduler.schedule_reaction(slice_start_time=target_sec, payload=reaction)
            print(f"[VLM Reaction Queued] Emote: {reaction.emote.value.upper()} | Text: \"{reaction.bubble_text}\"")

    @pyqtSlot()
    def poll_tick(self):
        """Called periodically by the VLC poller timer."""
        status = self.vlc.get_status()

        if status.state != "playing" or not status.file_path:
            return

        # 1. Bug 2 Fix: Detect New Video File Loaded in VLC
        if status.file_path != self.active_file:
            self.active_file = status.file_path
            self._reset_pipeline(status.time, f"New Media Loaded: {os.path.basename(status.file_path)}")
            return

        # 2. Detect Timeline Seek
        if status.is_seek:
            self._reset_pipeline(status.time, "Seek Event Detected")
            return

        # 3. Check Priority Queue for Due Reactions
        reaction = self.scheduler.check_and_dispatch(status.time)
        if reaction:
            emote, bubble_text = reaction
            self.overlay.reaction_signal.emit(emote, bubble_text)

        # 4. Look-Ahead Scheduling
        lookahead_target = status.time + config.LOOKAHEAD_SEC
        if lookahead_target - self.last_analyzed_target >= self.step_interval:
            self.last_analyzed_target = lookahead_target
            mins, secs = divmod(int(status.time), 60)
            target_mins, target_secs = divmod(int(lookahead_target), 60)
            print(f"[VLC {mins:02d}:{secs:02d}] Slicing future at {target_mins:02d}:{target_secs:02d} (T+{config.LOOKAHEAD_SEC:.0f}s)...")
            self.worker.analyze_future_slice(status.file_path, lookahead_target, self.seek_epoch)

    def brain_reset(self):
        """Resets the rolling memory inside the brain."""
        self.worker.brain.reset_memory()


def main():
    print("=" * 70)
    print("AI MOVIE DESKTOP COMPANION - PRODUCTION RUNNER")
    print("=" * 70)
    print("1. Open VLC Media Player and start any movie.")
    print("2. The companion will float at the bottom-right corner of your screen.")
    print("3. Press Ctrl+C in this terminal or close the window to exit.")
    print("=" * 70)

    app = QApplication(sys.argv)

    # Initialize Transparent Desktop Overlay
    overlay = DesktopCompanionOverlay(click_through=True)
    overlay.show()

    # Initialize Core Orchestrator
    orchestrator = Orchestrator(overlay)

    # Dynamic Polling Interval from config
    poller_timer = QTimer()
    poller_timer.timeout.connect(orchestrator.poll_tick)
    poller_timer.start(int(config.POLL_INTERVAL_SEC * 1000))

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("\nCompanion stopped.")


if __name__ == "__main__":
    main()
