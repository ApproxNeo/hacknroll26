import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QSlider, QSpinBox


class SpriteOverlay(QWidget):
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

        # Optional click-through
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Size to first frame
        self.resize(self.frames[0].size())

        # Animation timer
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.next_frame)
        self.anim_timer.start(max(1, int(1000 / fps)))

        # Movement timer (demo: drift diagonally and bounce)
        self.vel = QPoint(3, 2)
        self.move_timer = QTimer(self)
        self.move_timer.timeout.connect(self.tick_move)
        self.move_timer.start(16)  # ~60Hz

    def set_fps(self, fps: int):
        fps = max(1, int(fps))
        self.anim_timer.start(max(1, int(1000 / fps)))

    def set_speed(self, speed: int):
        speed = max(0, int(speed))
        sx = 1 if self.vel.x() >= 0 else -1
        sy = 1 if self.vel.y() >= 0 else -1
        # Keep a slight diagonal by default
        self.vel = QPoint(sx * speed, sy * max(1 if speed > 0 else 0, int(round(speed * 0.66))))

    def set_click_through(self, enabled: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))

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
        screen = QApplication.primaryScreen().availableGeometry()
        p = self.pos() + self.vel

        if p.x() < screen.left() or p.x() + self.width() > screen.right():
            self.vel.setX(-self.vel.x())
        if p.y() < screen.top() or p.y() + self.height() > screen.bottom():
            self.vel.setY(-self.vel.y())

        self.move(self.pos() + self.vel)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Explicitly clear the backing store to avoid a 1px outline artifact on macOS.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        painter.drawPixmap(0, 0, self.frames[self.frame_i])


class ControlPanel(QWidget):
    def __init__(self, overlay: SpriteOverlay, initial_fps: int = 12):
        super().__init__()
        self.overlay = overlay

        self.setWindowTitle("Sprite Control Panel")

        root = QVBoxLayout(self)

        # Visibility
        self.chk_visible = QCheckBox("Show sprite overlay")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(self._on_visible)
        root.addWidget(self.chk_visible)

        # Running
        self.chk_running = QCheckBox("Animate / move")
        self.chk_running.setChecked(True)
        self.chk_running.toggled.connect(self.overlay.set_running)
        root.addWidget(self.chk_running)

        # Click-through
        self.chk_clickthrough = QCheckBox("Click-through overlay")
        self.chk_clickthrough.setChecked(True)
        self.chk_clickthrough.toggled.connect(self.overlay.set_click_through)
        root.addWidget(self.chk_clickthrough)

        # FPS
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("FPS"))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 60)
        self.spin_fps.setValue(int(initial_fps))
        self.spin_fps.valueChanged.connect(self.overlay.set_fps)
        fps_row.addWidget(self.spin_fps)
        fps_row.addStretch(1)
        root.addLayout(fps_row)

        # Speed
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        self.sld_speed = QSlider(Qt.Horizontal)
        self.sld_speed.setRange(0, 30)
        # default from current velocity magnitude
        self.sld_speed.setValue(max(abs(self.overlay.vel.x()), abs(self.overlay.vel.y())))
        self.sld_speed.valueChanged.connect(self.overlay.set_speed)
        speed_row.addWidget(self.sld_speed)
        root.addLayout(speed_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_quit = QPushButton("Quit")
        btn_quit.clicked.connect(QApplication.instance().quit)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_quit)
        root.addLayout(btn_row)

        # Apply initial settings
        self.overlay.set_fps(self.spin_fps.value())
        self.overlay.set_speed(self.sld_speed.value())
        self.overlay.set_click_through(self.chk_clickthrough.isChecked())

    def _on_visible(self, visible: bool):
        if visible:
            self.overlay.show()
        else:
            self.overlay.hide()

    def closeEvent(self, event):
        # Closing the control panel exits the app
        QApplication.instance().quit()
        event.accept()


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
    panel.show()

    sys.exit(app.exec())