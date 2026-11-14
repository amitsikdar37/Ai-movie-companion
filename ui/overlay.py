import math
import os
import random
import sys
from typing import Dict

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
    60/30 FPS anti-aliased 2D cartoon companion overlay.
    Loads 32-bit RGBA PNGs from assets/ if present, otherwise renders a
    cel-shaded procedural 2D mascot with blinking, breathing, and bounce physics.
    """

    reaction_signal = pyqtSignal(str, str)

    def __init__(self, click_through: bool = True):
        super().__init__()
        os.makedirs(ASSETS_DIR, exist_ok=True)

        self.click_through = click_through
        self.sprites: Dict[str, QPixmap] = {}
        self.current_emote = "idle"
        self.bubble_text = ""
        self.show_bubble = False

        # Animation & physics state
        self.phase = 0.0
        self.bounce_velocity = 0.0
        self.bounce_offset = 0.0
        self.squash = 0.0
        self.is_blinking = False
        self.blink_countdown = random.randint(70, 140)

        self._init_window_flags()
        self._load_png_sprites()

        # Fixed canvas ample enough for speech bubble + mascot + shadow
        self.setFixedSize(260, 250)
        self._position_bottom_right()

        # Auto-revert timer (reusable single-shot)
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
        """Loads true 32-bit RGBA .png sprites if placed in assets/."""
        for emote in EMOTE_NAMES:
            png_path = os.path.join(ASSETS_DIR, f"{emote}.png")
            if os.path.exists(png_path):
                pix = QPixmap(png_path)
                if not pix.isNull():
                    self.sprites[emote] = pix

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
        self.bubble_text = bubble_text.strip().upper()
        self.show_bubble = bool(self.bubble_text)

        # Trigger squash-and-stretch hop
        self.bounce_velocity = -11.0
        self.squash = 0.16

        self.revert_timer.start(3200)
        self.update()

    def revert_to_idle(self):
        self.current_emote = "idle"
        self.show_bubble = False
        self.bubble_text = ""
        self.update()

    def _tick_animation(self):
        self.phase += 0.12

        # Spring physics for reaction hop
        if self.bounce_offset < 0.0 or self.bounce_velocity != 0.0:
            self.bounce_offset += self.bounce_velocity
            self.bounce_velocity += 1.4  # Gravity
            if self.bounce_offset >= 0.0:
                self.bounce_offset = 0.0
                self.bounce_velocity = 0.0
                self.squash = -0.12  # Landing squish

        # Decay squash-and-stretch back to 0
        self.squash *= 0.82

        # Natural eye blink scheduler
        self.blink_countdown -= 1
        if self.blink_countdown <= 0:
            if not self.is_blinking:
                self.is_blinking = True
                self.blink_countdown = 4  # Blink duration (~130ms)
            else:
                self.is_blinking = False
                self.blink_countdown = random.randint(80, 160)

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        cx = self.width() / 2.0
        ground_y = self.height() - 18.0

        # Idle breathing wave
        breath = math.sin(self.phase) * 2.5
        char_y = ground_y + self.bounce_offset + breath

        # 1. Grounded Contact Shadow
        shadow_scale = max(0.55, 1.0 + (self.bounce_offset / 80.0))
        shadow_w = 96.0 * shadow_scale
        shadow_grad = QRadialGradient(QPointF(cx, ground_y + 4), shadow_w / 2.0)
        shadow_grad.setColorAt(0.0, QColor(0, 0, 0, 135))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(shadow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - shadow_w / 2.0, ground_y - 4, shadow_w, 16))

        # 2. Render Custom PNG Sprite OR Procedural 2D Cartoon Mascot
        if self.current_emote in self.sprites or "idle" in self.sprites:
            pix = self.sprites.get(self.current_emote, self.sprites["idle"])
            scale_x = 1.0 - self.squash
            scale_y = 1.0 + self.squash
            draw_w = 135.0 * scale_x
            draw_h = 135.0 * scale_y
            target_rect = QRectF(cx - draw_w / 2.0, char_y - draw_h, draw_w, draw_h)
            painter.drawPixmap(target_rect, pix, QRectF(pix.rect()))
        else:
            self._draw_procedural_mascot(painter, cx, char_y)

        # 3. Comic Speech Bubble with Tail
        if self.show_bubble and self.bubble_text:
            self._draw_speech_bubble(painter, cx, char_y - 128.0)

    def _draw_procedural_mascot(self, painter: QPainter, cx: float, bottom_y: float):
        """Draws a clean cel-shaded 2D mascot with headphones and dynamic facial rigs."""
        scale_x = 1.0 - self.squash
        scale_y = 1.0 + self.squash
        w = 112.0 * scale_x
        h = 104.0 * scale_y
        left = cx - w / 2.0
        top = bottom_y - h

        outline = QPen(QColor("#1E1B2E"), 3.2)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(outline)

        # Little cat/fox ears behind headphones
        for side in (-1, 1):
            ear = QPainterPath()
            ear.moveTo(cx + side * (w * 0.22), top + 16)
            ear.lineTo(cx + side * (w * 0.42), top - 14)
            ear.lineTo(cx + side * (w * 0.44), top + 28)
            ear.closeSubpath()
            painter.setBrush(QColor("#FF9EBB"))
            painter.drawPath(ear)

        # Headband arc
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#2B2D42"), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(left - 4, top - 8, w + 8, h * 0.85), 20 * 16, 140 * 16)

        # Main Chibi Head (Warm Cream Cel-Shaded)
        painter.setPen(outline)
        painter.setBrush(QColor("#FFF8F0"))
        painter.drawRoundedRect(QRectF(left, top, w, h), 44.0, 42.0)

        # Headphone Earcups (Coral Accent)
        painter.setBrush(QColor("#FF5D8F"))
        painter.drawRoundedRect(QRectF(left - 11, top + h * 0.34, 16, 34), 8, 8)
        painter.drawRoundedRect(QRectF(left + w - 5, top + h * 0.34, 16, 34), 8, 8)

        # Rosy Cheeks
        painter.setPen(Qt.PenStyle.NoPen)
        cheek_color = QColor("#FF4D6D" if self.current_emote in ("shock", "hype", "laugh") else "#FF8FA3")
        cheek_color.setAlpha(150)
        painter.setBrush(cheek_color)
        eye_y = top + h * 0.48
        painter.drawEllipse(QRectF(cx - 36, eye_y + 8, 16, 9))
        painter.drawEllipse(QRectF(cx + 20, eye_y + 8, 16, 9))

        # Eyes & Expressions
        painter.setPen(outline)
        self._draw_face_expression(painter, cx, eye_y)

    def _draw_face_expression(self, painter: QPainter, cx: float, eye_y: float):
        e = self.current_emote

        # Eyes
        if self.is_blinking and e == "idle":
            for side in (-1, 1):
                painter.drawLine(QPointF(cx + side * 22 - 7, eye_y), QPointF(cx + side * 22 + 7, eye_y))
        elif e in ("laugh", "hype"):
            # Happy curved arcs (^ ^)
            for side in (-1, 1):
                ex = cx + side * 22
                path = QPainterPath()
                path.moveTo(ex - 8, eye_y + 3)
                path.quadTo(ex, eye_y - 8, ex + 8, eye_y + 3)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
        elif e in ("cringe", "scared"):
            # > < squeezed eyes
            for side in (-1, 1):
                ex = cx + side * 22
                painter.drawLine(QPointF(ex - 6, eye_y - 5), QPointF(ex + 6, eye_y))
                painter.drawLine(QPointF(ex + 6, eye_y), QPointF(ex - 6, eye_y + 5))
        else:
            # Big glossy anime eyes with white specular highlights
            r = 8.5 if e == "shock" else 7.0
            for side in (-1, 1):
                ex = cx + side * 22
                painter.setBrush(QColor("#1E1B2E"))
                painter.drawEllipse(QRectF(ex - r, eye_y - r, r * 2, r * 2))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(QRectF(ex - r * 0.45, eye_y - r * 0.55, r * 0.65, r * 0.65))
                painter.setPen(QPen(QColor("#1E1B2E"), 3.0))

        # Eyebrows
        for side in (-1, 1):
            bx = cx + side * 22
            tilt = -4 * side if e in ("shock", "scared", "cry") else (4 * side if e == "cringe" else 0)
            y_off = -15 if e == "shock" else -12
            painter.drawLine(QPointF(bx - 7, eye_y + y_off - tilt), QPointF(bx + 7, eye_y + y_off + tilt))

        # Mouth
        mouth_y = eye_y + 18
        if e in ("shock", "scared"):
            painter.setBrush(QColor("#FF5D8F"))
            painter.drawEllipse(QRectF(cx - 6, mouth_y - 4, 12, 14))
        elif e in ("laugh", "hype"):
            painter.setBrush(QColor("#FF5D8F"))
            path = QPainterPath()
            path.moveTo(cx - 10, mouth_y - 2)
            path.lineTo(cx + 10, mouth_y - 2)
            path.quadTo(cx, mouth_y + 13, cx - 10, mouth_y - 2)
            painter.drawPath(path)
        elif e in ("cringe", "confused"):
            path = QPainterPath()
            path.moveTo(cx - 9, mouth_y + 2)
            path.quadTo(cx - 3, mouth_y - 3, cx, mouth_y + 2)
            path.quadTo(cx + 3, mouth_y + 6, cx + 9, mouth_y + 1)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif e == "cry":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 8, mouth_y + 5)
            path.quadTo(cx, mouth_y - 3, cx + 8, mouth_y + 5)
            painter.drawPath(path)
            # Tears
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#4CC9F0"))
            painter.drawEllipse(QRectF(cx - 25, eye_y + 9, 5, 11))
            painter.drawEllipse(QRectF(cx + 20, eye_y + 9, 5, 11))
        else:
            # Cute cat 'w' smile
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 7, mouth_y)
            path.quadTo(cx - 3.5, mouth_y + 5, cx, mouth_y + 1)
            path.quadTo(cx + 3.5, mouth_y + 5, cx + 7, mouth_y)
            painter.drawPath(path)

    def _draw_speech_bubble(self, painter: QPainter, cx: float, bottom_y: float):
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        text_rect = metrics.boundingRect(QRectF(0, 0, 210, 80).toRect(), Qt.TextFlag.TextWordWrap, self.bubble_text)
        pad_x, pad_y = 16.0, 10.0
        bw = max(90.0, text_rect.width() + pad_x * 2)
        bh = max(36.0, text_rect.height() + pad_y * 2)
        bx = cx - bw / 2.0
        by = max(6.0, bottom_y - bh - 12.0)

        # Bubble shadow
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(QRectF(bx + 2, by + 4, bw, bh), 14.0, 14.0)

        # Bubble body + triangular pointer tail
        bubble_path = QPainterPath()
        bubble_path.addRoundedRect(QRectF(bx, by, bw, bh), 14.0, 14.0)

        tail = QPainterPath()
        tail.moveTo(cx - 8, by + bh - 1)
        tail.lineTo(cx, by + bh + 10)
        tail.lineTo(cx + 8, by + bh - 1)
        bubble_path = bubble_path.united(tail)

        painter.setPen(QPen(QColor("#1E1B2E"), 2.5))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(bubble_path)

        # Bubble text
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
        ("shock", "NO WAY BRO!"),
        ("laugh", "LMAOOO!"),
        ("hype", "LET'S GOOO!"),
        ("scared", "RUN RIGHT NOW!"),
        ("cry", "NOT LIKE THIS..."),
        ("confused", "WAIT WHAT?")
    ]
    idx = 0

    def cycle():
        global idx
        emote, text = test_reactions[idx % len(test_reactions)]
        overlay.trigger_reaction(emote, text)
        idx += 1

    t = QTimer()
    t.timeout.connect(cycle)
    t.start(3800)
    sys.exit(app.exec())