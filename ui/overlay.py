import os
import sys
from typing import Dict
from PIL import Image, ImageDraw, ImageFont

from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QMovie, QColor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QGraphicsDropShadowEffect
)

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")


def ensure_placeholder_assets():
    """Generates simple stylish animated placeholder GIFs if none exist yet."""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    emotes = {
        "idle": ("#4A90E2", "( ^ _ ^ )", "IDLE"),
        "shock": ("#F5A623", "( O _ O ! )", "SHOCK"),
        "laugh": ("#7ED321", "( > ▽ < )", "LAUGH"),
        "cringe": ("#BD10E0", "( > _ < ; )", "CRINGE"),
        "cry": ("#4A90E2", "( T _ T )", "CRY"),
        "hype": ("#D0021B", "٩(◕‿◕)۶", "HYPE"),
        "scared": ("#9013FE", "( ; ° Д ° )", "SCARED"),
        "confused": ("#50E3C2", "( ・_・? )", "CONFUSED"),
        "neutral": ("#9B9B9B", "( • _ • )", "NEUTRAL")
    }

    for name, (color, face, label) in emotes.items():
        gif_path = os.path.join(ASSETS_DIR, f"{name}.gif")
        if not os.path.exists(gif_path):
            frames = []
            for bounce in [0, -4, 0, 4]:
                img = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                # Outer circle
                draw.ellipse([10, 10 + bounce, 130, 130 + bounce], fill=color, outline="#222222", width=3)
                # Face text placeholder
                draw.text((70, 70 + bounce), face, fill="white", anchor="mm")
                frames.append(img)
            frames[0].save(
                gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=200,
                loop=0,
                transparency=0,
                disposal=2
            )


class DesktopCompanionOverlay(QWidget):
    """Hardware-accelerated, transparent, click-through companion window."""

    # Thread-safe signal to trigger reactions from background threads
    reaction_signal = pyqtSignal(str, str)

    def __init__(self, click_through: bool = True):
        super().__init__()
        ensure_placeholder_assets()

        self.click_through = click_through
        self.movies: Dict[str, QMovie] = {}
        self.current_emote = "idle"

        self._init_window_flags()
        self._init_ui()
        self._load_avatar_movies()
        self._position_bottom_right()

        # Connect thread-safe signal
        self.reaction_signal.connect(self.trigger_reaction)

    def _init_window_flags(self):
        """Configures transparency, click-through, and always-on-top flags."""
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Excludes window from Windows Alt+Tab & taskbar
        )
        if self.click_through:
            # Allows mouse clicks to pass completely through the avatar to VLC
            flags |= Qt.WindowType.WindowTransparentForInput

        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _init_ui(self):
        """Constructs the layout: Speech Bubble on top, Avatar below."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(6)

        # 1. Speech Bubble
        self.bubble_label = QLabel(self)
        self.bubble_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bubble_label.setWordWrap(True)
        self.bubble_label.setMaximumWidth(220)
        self.bubble_label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 245);
                color: #111111;
                border: 2px solid #222222;
                border-radius: 12px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: bold;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
        """)

        # Soft drop shadow for speech bubble
        bubble_shadow = QGraphicsDropShadowEffect(self)
        bubble_shadow.setBlurRadius(15)
        bubble_shadow.setColor(QColor(0, 0, 0, 120))
        bubble_shadow.setOffset(2, 4)
        self.bubble_label.setGraphicsEffect(bubble_shadow)
        self.bubble_label.hide()  # Hidden by default until reaction

        # Reusable auto-revert timer (prevents overlapping singleShot cuts)
        self.revert_timer = QTimer(self)
        self.revert_timer.setSingleShot(True)
        self.revert_timer.timeout.connect(self.revert_to_idle)

        # 2. Avatar Character Label
        self.avatar_label = QLabel(self)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setFixedSize(140, 140)

        self.main_layout.addWidget(self.bubble_label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.main_layout.addWidget(self.avatar_label, alignment=Qt.AlignmentFlag.AlignCenter)

    def _load_avatar_movies(self):
        """Loads and pre-caches QMovie animations for each emotion."""
        emotes = ["idle", "shock", "laugh", "cringe", "cry", "hype", "scared", "confused", "neutral"]
        for emote in emotes:
            gif_path = os.path.join(ASSETS_DIR, f"{emote}.gif")
            if os.path.exists(gif_path):
                movie = QMovie(gif_path)
                movie.setScaledSize(QSize(130, 130))
                self.movies[emote] = movie

        # Start on idle
        self._switch_emote("idle")

    def _switch_emote(self, emote_name: str):
        """Switches the active animation on the avatar."""
        target = emote_name.lower()
        if target not in self.movies:
            target = "idle"

        if self.current_emote in self.movies:
            self.movies[self.current_emote].stop()

        self.current_emote = target
        movie = self.movies[target]
        self.avatar_label.setMovie(movie)
        movie.start()

    def _position_bottom_right(self):
        """Places the overlay cleanly in the bottom-right corner above the taskbar."""
        self.adjustSize()
        screen = QApplication.primaryScreen().geometry()
        padding_x = 30
        padding_y = 60  # Clearance for Windows taskbar

        x = screen.width() - self.width() - padding_x
        y = screen.height() - self.height() - padding_y
        self.move(x, y)

    @pyqtSlot(str, str)
    def trigger_reaction(self, emote: str, bubble_text: str):
        """
        Activates reaction:
        1. Switches GIF to reaction emote.
        2. Displays punchy speech bubble.
        3. Arms a 3-second timer to auto-revert to idle.
        """
        self._switch_emote(emote)
        self.bubble_label.setText(bubble_text.upper())
        self.bubble_label.show()
        self._position_bottom_right()

        # Reset or start 3.0 second auto-revert timer
        self.revert_timer.start(3000)

    def revert_to_idle(self):
        """Reverts the avatar to idle state and hides the speech bubble."""
        self.bubble_label.hide()
        self._switch_emote("idle")
        self._position_bottom_right()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set click_through=False for this test so you can easily see and close it
    overlay = DesktopCompanionOverlay(click_through=False)
    overlay.show()

    print("=" * 70)
    print("PHASE 3.1 TEST: Transparent Desktop Overlay Running!")
    print("Look at the bottom-right corner of your screen.")
    print("Cycling through demo reactions every 4 seconds...")
    print("=" * 70)

    # Demo sequence to test animations & bubble
    test_reactions = [
        ("shock", "NO WAY!"),
        ("laugh", "LMAOO!"),
        ("hype", "LET'S GOOO!"),
        ("scared", "RUN BRO!"),
        ("confused", "WAIT, WHAT?")
    ]

    timer = QTimer()
    idx = 0

    def run_demo():
        global idx
        emote, text = test_reactions[idx % len(test_reactions)]
        print(f"Triggering Emote: {emote.upper()} | Bubble: \"{text}\"")
        overlay.trigger_reaction(emote, text)
        idx += 1

    timer.timeout.connect(run_demo)
    timer.start(4000)

    sys.exit(app.exec())