import math
import random
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QSlider, QSpinBox


class SpriteOverlay(QWidget):
    # Signal emitted when the defined click region is clicked
    panel_requested = Signal()
    def __init__(self, frames: list[QPixmap], fps: int = 12):
        super().__init__()
        self.frames = frames
        self.frame_i = 0

        # "Windowless" look: no title bar/borders; transparent background; stays on top
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.WindowDoesNotAcceptFocus |
            Qt.NoDropShadowWindowHint
        )

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        # Mouse interaction: default to NOT click-through so we can detect clicks
        # Users can toggle click-through from the control panel.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        # Size to first frame
        self.resize(self.frames[0].size())

        # Default clickable region: the entire sprite area
        fw, fh = self.frames[0].width(), self.frames[0].height()
        self.click_region = QRect(0, 0, fw, fh)

        # Animation timer
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.next_frame)
        self.anim_timer.start(max(1, int(1000 / fps)))

        # Movement timer (demo: drift diagonally and bounce)
        self.vel = QPoint(3, 2)
        self.speed_mag = max(abs(self.vel.x()), abs(self.vel.y())) or 1
        self.move_timer = QTimer(self)
        self.move_timer.timeout.connect(self.tick_move)
        self.move_timer.start(16)  # ~60Hz

    def set_fps(self, fps: int):
        fps = max(1, int(fps))
        self.anim_timer.start(max(1, int(1000 / fps)))

    def set_speed(self, speed: int):
        speed = max(0, int(speed))
        self.speed_mag = speed
        # Keep the current direction but normalize to the new speed
        vx, vy = self.vel.x(), self.vel.y()
        norm = math.hypot(vx, vy)
        if speed == 0 or norm == 0:
            self.vel = QPoint(0, 0)
            return
        scale = speed / norm
        self.vel = QPoint(int(round(vx * scale)), int(round(vy * scale)))

    def set_click_through(self, enabled: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))

    def set_click_region(self, rect: QRect):
        # Allow external customization of the clickable area
        self.click_region = QRect(rect)

    def set_running(self, running: bool):
        running = bool(running)
        if running:
            if not self.anim_timer.isActive():
                self.anim_timer.start()
            if not self.move_timer.isActive():
                self.move_timer.start(16)
        else:
            self.anim_timer.stop()
            self.move_timer.stop()

    def next_frame(self):
        self.frame_i = (self.frame_i + 1) % len(self.frames)
        self.update()

    def tick_move(self):
        if self.speed_mag == 0:
            return

        screen = QApplication.primaryScreen().availableGeometry()
        pos = self.pos()

        # Occasionally randomize direction
        if random.random() < 0.02:
            angle = random.uniform(0, 2 * math.pi)
            vx = math.cos(angle) * self.speed_mag
            vy = math.sin(angle) * self.speed_mag
            self.vel = QPoint(int(round(vx)), int(round(vy)))

        # If near edges, steer inward with a random inward vector
        margin = 40
        if (pos.x() < screen.left() + margin or
            pos.x() + self.width() > screen.right() - margin or
            pos.y() < screen.top() + margin or
            pos.y() + self.height() > screen.bottom() - margin):
            inward_x = 1 if pos.x() < screen.center().x() else -1
            inward_y = 1 if pos.y() < screen.center().y() else -1
            # Add randomness but keep bias inward
            vx = (inward_x * 0.7 + random.uniform(-0.3, 0.3))
            vy = (inward_y * 0.7 + random.uniform(-0.3, 0.3))
            norm = math.hypot(vx, vy) or 1
            self.vel = QPoint(int(round(vx / norm * self.speed_mag)),
                              int(round(vy / norm * self.speed_mag)))

        self.move(pos + self.vel)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Explicitly clear the backing store to avoid a 1px outline artifact on macOS.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        painter.drawPixmap(0, 0, self.frames[self.frame_i])

    def mousePressEvent(self, event):
        # Only respond when not click-through and clicking inside the defined region
        if self.testAttribute(Qt.WA_TransparentForMouseEvents):
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self.click_region.contains(pos):
            self.panel_requested.emit()
        # Let other clicks be ignored (no action)


class ControlPanel(QWidget):
    def __init__(self, overlay: SpriteOverlay, initial_fps: int = 12):
        super().__init__()
        self.overlay = overlay
        self._quitting = False

        # Set window flags BEFORE showing to remove minimize/maximize buttons (macOS-friendly)
        self.setWindowFlags(
            Qt.Window |
            Qt.CustomizeWindowHint |  # allows selecting which buttons appear
            Qt.WindowTitleHint |       # keep title bar text
            Qt.WindowCloseButtonHint   # keep only close button
        )

        self.setWindowTitle("Sprite Control Panel")

        root = QVBoxLayout(self)

        # Visibility
        self.chk_visible = QCheckBox("Show sprite overlay")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(self._on_visible)
        root.addWidget(self.chk_visible)

        # Speed
        speed_row = QHBoxLayout()
        self.sld_speed = QSlider(Qt.Horizontal)
        self.sld_speed.setRange(5, 5)  # Fixed: was locked at 5
        # default from current velocity magnitude
        self.sld_speed.setValue(max(abs(self.overlay.vel.x()), abs(self.overlay.vel.y())))
        self.sld_speed.valueChanged.connect(self.overlay.set_speed)
        speed_row.addWidget(self.sld_speed)
        root.addLayout(speed_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_quit = QPushButton("Quit")
        btn_quit.clicked.connect(self._on_quit_clicked)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_quit)
        root.addLayout(btn_row)

        # Apply initial settings
        self.overlay.set_speed(self.sld_speed.value())

    def _on_visible(self, visible: bool):
        if visible:
            self.overlay.show()
        else:
            self.overlay.hide()

    def _on_quit_clicked(self):
        # Quit button explicitly quits the app
        self._quitting = True
        QApplication.instance().quit()

    def closeEvent(self, event):
        # If quit was requested, allow the window to close; otherwise hide
        if self._quitting:
            event.accept()
        else:
            self.hide()
            event.ignore()


def load_frames(folder: str) -> list[QPixmap]:
    # Put frame PNGs in ./frames: 000.png, 001.png, ...
    paths = sorted(Path(folder).glob("*.png"))
    frames = [QPixmap(str(p)) for p in paths]
    if not frames or any(f.isNull() for f in frames):
        raise RuntimeError("No valid PNG frames found in ./frames")
    return frames


if __name__ == "__main__":
    app = QApplication(sys.argv)

    frames = load_frames("frames")
    w = SpriteOverlay(frames, fps=12)

    w.move(200, 200)
    w.show()

    panel = ControlPanel(w, initial_fps=12)
    # Show the panel only when clicking the defined region on the cat
    w.panel_requested.connect(panel.show)
    w.panel_requested.connect(panel.raise_)
    w.panel_requested.connect(panel.activateWindow)

    sys.exit(app.exec())
