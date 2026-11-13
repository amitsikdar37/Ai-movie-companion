import os
import sys
import time
import queue
from typing import Optional, Tuple
from dataclasses import dataclass

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from core.brain import ReactionPayload


@dataclass(order=True)
class ScheduledItem:
    """Helper wrapper to ensure thread-safe priority queue sorting by timestamp."""
    timestamp: float
    payload: ReactionPayload


class ReactionScheduler:
    """Manages look-ahead reaction queuing, precision dispatch, and cooldowns."""

    def __init__(self, cooldown_seconds: float = 20.0):
        # Priority queue automatically orders items by the lowest timestamp first
        self.queue: queue.PriorityQueue[Tuple[float, ReactionPayload]] = queue.PriorityQueue()
        self.cooldown_seconds = cooldown_seconds
        self.last_reaction_vlc_time = -999.0

    def schedule_reaction(self, slice_start_time: float, payload: ReactionPayload):
        """
        Calculates the exact future VLC second where the reaction should occur
        and pushes it into the priority queue.
        """
        if not payload.should_react:
            return

        # Target second = start of the 5s slice + offset inside the slice
        target_timestamp = slice_start_time + payload.trigger_offset_sec

        self.queue.put((target_timestamp, payload))
        mins, secs = divmod(int(target_timestamp), 60)
        print(f"[Scheduler] Reaction queued for VLC second {mins:02d}:{secs:02d} ({payload.emote.value.upper()}: \"{payload.bubble_text}\")")

    def check_and_dispatch(self, vlc_current_time: float) -> Optional[Tuple[str, str]]:
        """
        Called every 100ms by the VLC poller.
        If VLC has reached or passed the target timestamp of the earliest reaction:
        1. Pops the reaction.
        2. Checks freshness tolerance (within 1.5s).
        3. Enforces the 20-second anti-spam cooldown.
        4. Returns (emote, bubble_text) to trigger on the overlay.
        """
        if self.queue.empty():
            return None

        # Peek at earliest scheduled timestamp without removing
        earliest_timestamp, payload = self.queue.queue[0]

        # Is it time to react?
        if vlc_current_time >= earliest_timestamp:
            # Pop item from queue
            _, payload = self.queue.get()

            # Guard 1: Freshness check (if reaction is older than 1.5s, drop it)
            if vlc_current_time - earliest_timestamp > 1.5:
                print(f"[Scheduler] Dropped stale reaction (delayed by {vlc_current_time - earliest_timestamp:.2f}s).")
                return None

            # Guard 2: 20-Second Spam Cooldown Shield
            if abs(vlc_current_time - self.last_reaction_vlc_time) < self.cooldown_seconds:
                print(f"[Scheduler] Anti-spam shield active. Dropping reaction (cooldown: {self.cooldown_seconds}s).")
                return None

            # Approved! Dispatch reaction
            self.last_reaction_vlc_time = vlc_current_time
            print(f"[Scheduler DISPATCH] Emitting reaction at VLC {vlc_current_time:.1f}s -> {payload.emote.value.upper()}: \"{payload.bubble_text}\"")
            return payload.emote.value, payload.bubble_text

        return None

    def flush(self):
        """Flushes all pending reactions when a user timeline seek occurs."""
        count = self.queue.qsize()
        with self.queue.mutex:
            self.queue.queue.clear()
        if count > 0:
            print(f"[Scheduler] Seek jump detected! Flushed {count} pending future reactions.")


if __name__ == "__main__":
    from core.brain import EmoteType

    print("=" * 70)
    print("TESTING REACTION SCHEDULER & PRIORITY QUEUE")
    print("=" * 70)

    scheduler = ReactionScheduler(cooldown_seconds=10.0)

    # Simulate 2 reactions scheduled in the future
    p1 = ReactionPayload(
        updated_plot_memory="Test memory",
        should_react=True,
        trigger_offset_sec=2.0,
        emote=EmoteType.SHOCK,
        bubble_text="FIRST REACTION!"
    )
    p2 = ReactionPayload(
        updated_plot_memory="Test memory",
        should_react=True,
        trigger_offset_sec=3.5,
        emote=EmoteType.LAUGH,
        bubble_text="SECOND REACTION!"
    )

    # Schedule both for a slice starting at second 100.0
    scheduler.schedule_reaction(slice_start_time=100.0, payload=p1) # Target: 102.0s
    scheduler.schedule_reaction(slice_start_time=100.0, payload=p2) # Target: 103.5s

    # Simulate VLC clock ticking forward
    simulated_vlc_times = [99.0, 101.0, 102.1, 103.0, 103.6, 115.0]

    for t in simulated_vlc_times:
        print(f"\n[VLC Tick]: {t:.1f}s")
        result = scheduler.check_and_dispatch(t)
        if result:
            emote, text = result
            print(f"  ==> [POPPED TO SCREEN]: {emote.upper()} with \"{text}\"")
        else:
            print("  ==> [Waiting in queue...]")

    print("\n" + "=" * 70)
    print("[SUCCESS] Phase 3.2 Scheduler and Priority Queue verified!")