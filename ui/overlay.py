import math
import os
import random
import re
import sys
import time
from typing import Dict, List, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QWidget

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
EMOTE_NAMES = [
    "idle", "shock", "laugh", "cringe",
    "cry", "hype", "scared", "confused", "neutral"
]


class DesktopCompanionOverlay(QWidget):
    """
    30 FPS 2D cartoon companion overlay with expression-specific body kinematics
    and multi-frame sprite alternation (e.g., laugh.png + laugh_2.png).
    """

    reaction_signal = pyqtSignal(str, str)

    def __init__(self, click_through: bool = True):
        super().__init__()
        os.makedirs(ASSETS_DIR, exist_ok=True)

        self.click_through = click_through
        self.sprites: Dict[str, List[QPixmap]] = {}
        self.current_emote = "idle"
        self.bubble_text = ""
        self.show_bubble = False

        # Timing & animation state
        self.start_time = time.perf_counter()
        self.emote_start_time = self.start_time
        self.is_blinking = False
        self.blink_countdown = random.randint(75, 150)

        self._init_window_flags()
        self._load_png_sprites()

        self.setFixedSize(270, 260)
        self._position_bottom_right()

        # Reusable single-shot timer to revert reaction back to idle
        self.revert_timer = QTimer(self)
        self.revert_timer.setSingleShot(True)
        self.revert_timer.timeout.connect(self.revert_to_idle)

        # 30 FPS animation loop
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_animation)
        self.anim_timer.start(33)

        self.reaction_signal.connect(self.trigger_reaction)

    def _init_window_flags(self):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if self.click_through:
            flags |= Qt.WindowType.WindowTransparentForInput

        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def _load_png_sprites(self):
        """
        Case-insensitively loads single PNGs (e.g. 'Scared.png') AND
        animation sequences ('laugh.png', 'laugh_2.png', 'laugh_3.png').
        """
        if not os.path.isdir(ASSETS_DIR):
            return

        files = sorted(os.listdir(ASSETS_DIR))
        grouped: Dict[str, List[Tuple[int, str]]] = {e: [] for e in EMOTE_NAMES}

        for fname in files:
            lower = fname.lower()
            if not lower.endswith(".png"):
                continue
            stem = lower[:-4]
            match = re.match(r"^([a-z]+)(?:_(\d+))?$", stem)
            if not match:
                continue
            base_emote, frame_idx = match.group(1), match.group(2)
            if base_emote in grouped:
                order = int(frame_idx) if frame_idx is not None else 1
                grouped[base_emote].append((order, os.path.join(ASSETS_DIR, fname)))

        for emote, items in grouped.items():
            items.sort(key=lambda x: x[0])
            loaded = []
            for _, path in items:
                pix = QPixmap(path)
                if not pix.isNull():
                    loaded.append(pix)
            if loaded:
                self.sprites[emote] = loaded

    def _position_bottom_right(self):
        """Docks the character directly onto the top edge of the Windows taskbar."""
        screen = QApplication.primaryScreen().availableGeometry()
        x = int(screen.right() - self.width() - 24)
        y = int(screen.bottom() - self.height() + 8)
        self.move(x, y)

    @pyqtSlot(str, str)
    def trigger_reaction(self, emote: str, bubble_text: str):
        target = emote.lower()
        self.current_emote = target if target in EMOTE_NAMES else "idle"
        self.emote_start_time = time.perf_counter()
        self.bubble_text = bubble_text.strip().upper()
        self.show_bubble = bool(self.bubble_text)

        self.revert_timer.start(3200)
        self.update()

    def revert_to_idle(self):
        self.current_emote = "idle"
        self.emote_start_time = time.perf_counter()
        self.show_bubble = False
        self.bubble_text = ""
        self.update()

    def _tick_animation(self):
        # Natural blink scheduler for idle state (uses idle_2.png)
        self.blink_countdown -= 1
        if self.blink_countdown <= 0:
            if not self.is_blinking:
                self.is_blinking = True
                self.blink_countdown = 4  # ~130ms blink duration
            else:
                self.is_blinking = False
                self.blink_countdown = random.randint(80, 160)

        self.update()

    def _get_active_sprite(self, emote_t: float) -> QPixmap:
        """Selects the active frame for the current emote (supports multi-frame mouth/blink)."""
        frames = self.sprites.get(self.current_emote) or self.sprites.get("idle")
        if not frames and self.sprites:
            frames = next(iter(self.sprites.values()))
        if not frames:
            return QPixmap()

        if len(frames) == 1:
            return frames[0]

        # During idle, frame[1] acts as the blink frame
        if self.current_emote in ("idle", "neutral"):
            return frames[1] if self.is_blinking else frames[0]

        # During active reactions, alternate frames at ~10 FPS or ~6 FPS
        fps = 10.0 if self.current_emote in ("laugh", "scared", "hype") else 6.0
        idx = int(emote_t * fps) % len(frames)
        return frames[idx]

    def _compute_kinematics(self, global_t: float, emote_t: float) -> Tuple[float, float, float, float, float]:
        """
        Returns expression-specific body motion:
        (dx, dy, scale_x, scale_y, angle_deg)
        """
        e = self.current_emote

        if e == "laugh":
            # Rapid rhythmic belly-laugh pulses + joyful head rocking
            pulse = abs(math.sin(emote_t * 14.0))
            dx = math.sin(emote_t * 7.0) * 2.0
            dy = -pulse * 6.5
            scale_x = 1.0 + pulse * 0.04
            scale_y = 1.0 - pulse * 0.045
            angle = math.sin(emote_t * 7.0) * 4.5
            return dx, dy, scale_x, scale_y, angle

        if e == "hype":
            # Energetic double-pump fist bounces
            pulse = abs(math.sin(emote_t * 11.5))
            dx = math.sin(emote_t * 5.75) * 3.0
            dy = -pulse * 12.0
            scale_x = 1.0 - pulse * 0.04
            scale_y = 1.0 + pulse * 0.07
            angle = math.sin(emote_t * 11.5) * 5.0
            return dx, dy, scale_x, scale_y, angle

        if e == "scared":
            # High-frequency terror shiver + crouched posture
            shiver = math.sin(emote_t * 58.0) * 4.2
            tremor = math.cos(emote_t * 46.0) * 2.0
            return shiver, 2.0, 0.95, 0.93, tremor

        if e == "shock":
            # Sharp upward recoil/stretch (0..0.18s), then wide-eyed freeze
            if emote_t < 0.18:
                k = math.sin((emote_t / 0.18) * math.pi)
                return 0.0, -16.0 * k, 1.0 - 0.08 * k, 1.0 + 0.13 * k, -4.0 * k
            after_jolt = math.sin(emote_t * 32.0) * max(0.0, 1.5 - emote_t)
            return after_jolt, -2.0, 1.0, 1.02, -2.5

        if e == "cry":
            # Heavy downward slump + periodic sobbing hiccups
            sob = max(0.0, math.sin(emote_t * 8.5)) ** 4
            dx = math.sin(emote_t * 17.0) * 1.2 * sob
            dy = 3.5 - sob * 4.5
            scale_x = 1.04 - sob * 0.03
            scale_y = 0.93 + sob * 0.05
            angle = math.sin(emote_t * 4.25) * 2.5
            return dx, dy, scale_x, scale_y, angle

        if e == "cringe":
            # Pulls back and leans away with awkward wince shudder
            ease = min(1.0, emote_t / 0.15)
            wince = math.sin(emote_t * 36.0) * 1.6
            return (7.0 * ease) + wince, 1.5 * ease, 0.95, 0.96, 6.5 * ease

        if e == "confused":
            # Smooth curious head tilt that holds while pondering
            ease = min(1.0, emote_t / 0.22)
            breath = math.sin(global_t * 2.2) * 0.012
            return -2.0 * ease, -1.5 * ease, 1.0 - breath, 1.0 + breath, -9.0 * ease

        # Default 'idle' or 'neutral': calm, slow chest breathing
        breath = math.sin(global_t * 2.2)
        sway = math.sin(global_t * 1.1) * 1.2
        return 0.0, 0.0, 1.0 - breath * 0.012, 1.0 + breath * 0.016, sway

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        now = time.perf_counter()
        global_t = now - self.start_time
        emote_t = now - self.emote_start_time

        dx, dy, scale_x, scale_y, angle = self._compute_kinematics(global_t, emote_t)

        cx = (self.width() / 2.0) + dx
        ground_y = self.height() - 16.0
        char_y = ground_y + dy

        # 1. Grounded Contact Shadow
        lift = max(0.0, -dy)
        shadow_scale = max(0.6, (1.0 - lift / 70.0) * scale_x)
        shadow_w = 108.0 * shadow_scale
        shadow_grad = QRadialGradient(QPointF(cx, ground_y + 2), shadow_w / 2.0)
        shadow_grad.setColorAt(0.0, QColor(0, 0, 0, 130))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(shadow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - shadow_w / 2.0, ground_y - 5, shadow_w, 14))

        # 2. Draw Character Sprite Pivoted at Bottom-Center
        pix = self._get_active_sprite(emote_t)
        draw_h = 142.0 * scale_y

        if not pix.isNull():
            aspect = pix.width() / max(1.0, float(pix.height()))
            draw_w = 142.0 * aspect * scale_x

            painter.save()
            painter.translate(cx, char_y)
            painter.rotate(angle)
            target_rect = QRectF(-draw_w / 2.0, -draw_h, draw_w, draw_h)
            painter.drawPixmap(target_rect, pix, QRectF(pix.rect()))
            painter.restore()

        # 3. Comic Speech Bubble Tracked Above Head
        if self.show_bubble and self.bubble_text:
            self._draw_speech_bubble(painter, cx, char_y - draw_h - 6.0)

    def _draw_speech_bubble(self, painter: QPainter, cx: float, bottom_y: float):
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        text_rect = metrics.boundingRect(
            QRectF(0, 0, 210, 80).toRect(),
            Qt.TextFlag.TextWordWrap,
            self.bubble_text
        )
        pad_x, pad_y = 16.0, 10.0
        bw = max(92.0, text_rect.width() + pad_x * 2)
        bh = max(36.0, text_rect.height() + pad_y * 2)
        bx = max(8.0, min(self.width() - bw - 8.0, cx - bw / 2.0))
        by = max(6.0, bottom_y - bh - 10.0)

        # Bubble Drop Shadow
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 75))
        painter.drawRoundedRect(QRectF(bx + 2, by + 4, bw, bh), 14.0, 14.0)

        # Bubble Body + Pointer Tail
        bubble_path = QPainterPath()
        bubble_path.addRoundedRect(QRectF(bx, by, bw, bh), 14.0, 14.0)

        tail_x = max(bx + 18.0, min(bx + bw - 18.0, cx))
        tail = QPainterPath()
        tail.moveTo(tail_x - 8, by + bh - 1)
        tail.lineTo(tail_x, by + bh + 10)
        tail.lineTo(tail_x + 8, by + bh - 1)
        bubble_path = bubble_path.united(tail)

        painter.setPen(QPen(QColor("#1E1B2E"), 2.5))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(bubble_path)

        # Bubble Text
        painter.setPen(QColor("#1E1B2E"))
        painter.drawText(
            QRectF(bx + pad_x / 2.0, by, bw - pad_x, bh),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.bubble_text
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = DesktopCompanionOverlay(click_through=False)
    overlay.show()

    test_reactions = [
        ("laugh", "LMAOOO!"),
        ("scared", "RUN RIGHT NOW!"),
        ("shock", "NO WAY BRO!"),
        ("cry", "NOT LIKE THIS..."),
        ("cringe", "BRO STOP..."),
        ("confused", "WAIT WHAT?"),
        ("hype", "LET'S GOOO!"),
    ]
    idx = 0

    def cycle():
        global idx
        emote, text = test_reactions[idx % len(test_reactions)]
        overlay.trigger_reaction(emote, text)
        idx += 1

    t = QTimer()
    t.timeout.connect(cycle)
    t.start(4000)
    sys.exit(app.exec())