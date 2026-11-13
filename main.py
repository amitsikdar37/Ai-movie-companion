import os
import sys
import time 
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot, QObject
from PyQt6.QtWidgets import QApplication

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from core.vlc_client import VLCClient, PlaybackState
from core.demuxer import demux_slice
from core.sensory import SensoryEngine
from core.brain import CognitiveBrain
from core.scheduler import ReactionScheduler
from ui.overlay import DesktopCompanionOverlay


class LookaheadWorker(QThread):
    """
    Background worker thread that slices future video/audio in RAM,
    evaluates scenes with Qwen2.5-VL, and schedules reactions.
    """
    status_log = pyqtSignal(str)

    def __init__(self, scheduler: ReactionScheduler):
        super().__init__()
        self.scheduler = scheduler
        self.brain = CognitiveBrain(model_name="qwen2.5vl:3b")
        self.sensory = SensoryEngine(model_size="small.en")
        
        self.is_busy = False
        self.current_file = ""
        self.target_sec = 0.0

    def analyze_future_slice(self, file_path: str, target_sec: float):
        """Triggers analysis for a future timestamp if not already processing."""
        if self.is_busy:
            return

        self.current_file = file_path
        self.target_sec = target_sec
        self.start()

    def run(self):
        """Runs in-memory demuxing, sensory analysis, and VLM brain inference."""
        self.is_busy = True
        try:
            # 1. Demux 5-second slice at T + 12.0s
            frames, audio_bytes, subtitles = demux_slice(self.current_file, self.target_sec)
            if not frames:
                self.is_busy = False
                return

            # 2. Sensory processing (Physics dB check + Subtitles/Whisper)
            loud_spike, delta_db, dialogue, source = self.sensory.process(audio_bytes, subtitles)

            # 3. Cognitive Brain Evaluation (Ollama Qwen2.5-VL)
            reaction = self.brain.evaluate_scene(frames, dialogue, loud_spike)

            # 4. Schedule reaction in Priority Queue if should_react is True
            if reaction and reaction.should_react:
                self.scheduler.schedule_reaction(
                    slice_start_time=self.target_sec,
                    payload=reaction
                )
                self.status_log.emit(
                    f"[VLM Reaction Queued] Emote: {reaction.emote.value.upper()} | Text: \"{reaction.bubble_text}\""
                )

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

        # Look-ahead tracking
        self.last_analyzed_target = -1.0
        self.step_interval = config.SLICE_DURATION_SEC  # Analyze in 5-second steps

    def _log(self, message: str):
        print(message)

    @pyqtSlot()
    def poll_tick(self):
        """Called every 100ms by the VLC poller timer."""
        status = self.vlc.get_status()

        # If VLC is stopped or not playing, do nothing
        if status.state != "playing" or not status.file_path:
            return

        # 1. Check for Timeline Seek (Flush Pipeline)
        if status.is_seek:
            print("\n[Seek Event Detected] Flushing future pipeline & memory...")
            self.scheduler.flush()
            self.brain_reset()
            self.last_analyzed_target = status.time + config.LOOKAHEAD_SEC
            return

        # 2. Check Priority Queue for Due Reactions
        reaction = self.scheduler.check_and_dispatch(status.time)
        if reaction:
            emote, bubble_text = reaction
            # Trigger animation and speech bubble on overlay
            self.overlay.reaction_signal.emit(emote, bubble_text)

        # 3. Look-Ahead Scheduling: Peek 12s ahead
        lookahead_target = status.time + config.LOOKAHEAD_SEC

        # Only trigger a new 5-second slice if VLC has advanced to the next chunk
        if lookahead_target - self.last_analyzed_target >= self.step_interval:
            self.last_analyzed_target = lookahead_target
            mins, secs = divmod(int(status.time), 60)
            target_mins, target_secs = divmod(int(lookahead_target), 60)
            print(f"[VLC {mins:02d}:{secs:02d}] Slicing future at {target_mins:02d}:{target_secs:02d} (T+{config.LOOKAHEAD_SEC:.0f}s)...")
            self.worker.analyze_future_slice(status.file_path, lookahead_target)

    def brain_reset(self):
        """Resets the rolling memory inside the brain."""
        self.worker.brain.reset_memory()


def main():
    print("=" * 70)
    print("AI MOVIE DESKTOP COMPANION - STARTING")
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

    # Poll VLC every 100ms
    from PyQt6.QtCore import QTimer
    poller_timer = QTimer()
    poller_timer.timeout.connect(orchestrator.poll_tick)
    poller_timer.start(100)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()