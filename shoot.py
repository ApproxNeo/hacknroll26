import sys
import os
import json
import socket
import threading
import uuid
import random
import subprocess
from pathlib import Path
import math
from PySide6.QtGui import QPainter, QPixmap, QMovie, QColor, QPen, QBrush, QPainterPath, QPolygon, QRegion
from PySide6.QtCore import QRect

from projectiles import ProjectileType, select_projectile_type, FlashbangOverlay, play_projectile_sound

# Global hotkey (non-blocking). Uses pynput so keypresses still reach other apps.
try:
    from pynput import keyboard as _pynput_keyboard  # type: ignore
    _PYNPUT_AVAILABLE = True
except Exception:
    _pynput_keyboard = None  # type: ignore
    _PYNPUT_AVAILABLE = False

# Debounce global hotkey shots (avoid OS key repeat spam).
_LAST_HOTKEY_SHOT_MS = 0
_CAT_OVERLAY = None
BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"
EXPLOSION_GIF = ASSET_DIR / "anims/explode.gif"
EXPLOSION_MP3 = ASSET_DIR / "sounds/explode.mp3"
PEW_MP3 = ASSET_DIR / "sounds/pew.mp3"
PROJECTILE_PNG = ASSET_DIR / "sprites/projectile.png"
SETTINGS_PATH = BASE_DIR / "control_panel_settings.json"

# Cache the projectile pixmap (and scaled variants) so paint events are cheap.
_PROJECTILE_PIX: QPixmap = None
_PROJECTILE_SCALED: dict[int, QPixmap] = {}
_PROJECTILE_TINTED: dict[tuple[int, int], QPixmap] = {}

# Default colors (customizable via control panel).
_PROJECTILE_COLOR = QColor(110, 110, 110)

os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false")

def _parse_color(text: str) -> QColor:
    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) in (3, 4):
            try:
                r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]))
                a = int(parts[3]) if len(parts) == 4 else 255
                c = QColor(r, g, b, a)
                return c if c.isValid() else None
            except Exception:
                return None
    c = QColor(raw)
    return c if c.isValid() else None


def _tint_pixmap(pix: QPixmap, color: QColor) -> QPixmap:
    if pix is None or pix.isNull() or color is None or not color.isValid():
        return pix
    key = (int(pix.cacheKey()), int(color.rgba()))
    cached = _PROJECTILE_TINTED.get(key)
    if cached is not None and not cached.isNull():
        return cached

    tinted = QPixmap(pix.size())
    tinted.setDevicePixelRatio(pix.devicePixelRatio())
    tinted.fill(Qt.transparent)
    painter = QPainter(tinted)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.drawPixmap(0, 0, pix)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    _PROJECTILE_TINTED[key] = tinted
    return tinted


# Shooting direction configuration
_SHOOT_DIRECTION: str = "left_to_right"  # "left_to_right" or "right_to_left"

# Sprite scaling (1.0 = original frame size). Reduce to make the cat smaller.

CAT_SCALE: float = 0.6

# Visual insets (px) so the *drawn* cat appears flush to the screen edge.
# The current cat drawing has significant transparent padding inside its 300x270 box.
CAT_EDGE_INSET_TOP: int = 23
CAT_EDGE_INSET_LEFT: int = 78
CAT_EDGE_INSET_RIGHT: int = 78
CAT_EDGE_INSET_BOTTOM: int = 0

# Shooting animation length (in anim ticks; anim timer runs ~200ms).
_SHOOT_ANIM_TICKS = 5


def _get_projectile_pixmap(target_size: int) -> QPixmap:
    """Load projectile.png if present and return a scaled pixmap sized ~target_size."""
    global _PROJECTILE_PIX, _PROJECTILE_SCALED
    target_size = max(1, int(target_size))

    # Return cached scaled pixmap if available.
    cached = _PROJECTILE_SCALED.get(target_size)
    if cached is not None and not cached.isNull():
        return cached

    # Load base pixmap once.
    if _PROJECTILE_PIX is None:
        if PROJECTILE_PNG.exists():
            pix = QPixmap(str(PROJECTILE_PNG))
            _PROJECTILE_PIX = pix if not pix.isNull() else None
        else:
            _PROJECTILE_PIX = None

    if _PROJECTILE_PIX is None or _PROJECTILE_PIX.isNull():
        return None

    scaled = _PROJECTILE_PIX.scaled(
        int(target_size),
        int(target_size),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    _PROJECTILE_SCALED[target_size] = scaled
    return scaled

from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QObject, QDateTime, QSize, QThread, QUrl
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QSlider, QSpinBox, QLineEdit, QTextEdit
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

# QtMultimedia gives much lower latency than spawning OS player processes.
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    _QT_AUDIO_AVAILABLE = True
except Exception:
    QMediaPlayer = None  # type: ignore
    QAudioOutput = None  # type: ignore
    _QT_AUDIO_AVAILABLE = False

from zeroconf import Zeroconf, ServiceInfo, ServiceBrowser, ServiceStateChange

class MessageServer(QObject):
    message_received = Signal(str)
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._clients: list[QTcpSocket] = []
        self._listening = False

    def start(self, host: str = "127.0.0.1", port: int = 50505) -> bool:
        if self._listening:
            return True
        ok = self._server.listen(QHostAddress(host), int(port))
        self._listening = bool(ok)
        if ok:
            self.status_changed.emit(f"Server listening on {host}:{port}")
        else:
            self.status_changed.emit(f"Server failed to listen on {host}:{port} ({self._server.errorString()})")
        return bool(ok)

    def stop(self):
        if not self._listening:
            return
        for s in list(self._clients):
            s.disconnectFromHost()
        self._clients.clear()
        self._server.close()
        self._listening = False
        self.status_changed.emit("Server stopped")

    def broadcast(self, text: str):
        data = (text.rstrip("\n") + "\n").encode("utf-8")
        for s in list(self._clients):
            if s.state() == QTcpSocket.ConnectedState:
                s.write(data)

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            sock.setParent(self)
            sock._rx_buf = b""  # type: ignore[attr-defined]
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            self._clients.append(sock)
            self.status_changed.emit(f"Client connected ({sock.peerAddress().toString()}:{sock.peerPort()})")

    def _on_disconnected(self, sock: QTcpSocket):
        try:
            self._clients.remove(sock)
        except ValueError:
            pass
        self.status_changed.emit("Client disconnected")
        sock.deleteLater()

    def _on_ready_read(self, sock: QTcpSocket):
        buf = bytes(sock.readAll())
        rx = getattr(sock, "_rx_buf", b"") + buf
        while b"\n" in rx:
            line, rx = rx.split(b"\n", 1)
            try:
                text = line.decode("utf-8", errors="replace")
            except Exception:
                text = str(line)
            if text:
                self.message_received.emit(text)
        sock._rx_buf = rx  # type: ignore[attr-defined]


class Cat:
    """Represents a single cat entity"""
    # Edge constants
    BOTTOM = 0
    TOP = 1
    LEFT = 2
    RIGHT = 3

    def __init__(self, x: int, y: int, edge: int = BOTTOM):
        self.x = x
        self.y = y
        self.width = 300
        self.height = 270
        self.edge = edge  # Which edge the cat is on

        # Movement state
        self.speed = 5
        self.facing = random.choice([1, -1])

        # Jumping state
        self.is_jumping = False
        self.jump_vel = 0
        self.gravity = 0.5
        self.jump_cooldown = random.randint(1000, 4000)
        self.hit_corner_this_jump = False  # Track if corner was hit during this jump

        # Animation
        self.anim_frame = 0

        # Shooting animation (countdown ticks)
        self.shoot_anim = 0

        # Teleport timer
        self.teleport_timer = random.randint(8000, 15000)

    def update(self, screen_rect: QRect, speed_mag: int):
        """Update cat position and physics"""
        if speed_mag == 0:
            return

        self.speed = max(3, speed_mag)  # Ensure minimum speed
        self.teleport_timer -= 16

        # Ensure facing is never 0
        if self.facing == 0:
            self.facing = random.choice([1, -1])

        # Update position based on edge
        if self.edge == self.BOTTOM:
            self._update_bottom_edge(screen_rect)
        elif self.edge == self.TOP:
            self._update_top_edge(screen_rect)
        elif self.edge == self.LEFT:
            self._update_left_edge(screen_rect)
        elif self.edge == self.RIGHT:
            self._update_right_edge(screen_rect)

        # Force cats to stay within screen boundaries
        self._clamp_to_screen(screen_rect)

        # Check if cat went off screen and flip to random edge
        self._ensure_on_screen(screen_rect)

        # Teleport to random edge periodically
        # if self.teleport_timer <= 0:
        #     self._teleport_to_random_edge(screen_rect)
        #     self.teleport_timer = random.randint(8000, 15000)

    def _update_bottom_edge(self, screen_rect: QRect):
        """Cat walking on bottom edge"""
        ground_y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM

        if self.is_jumping:
            self.jump_vel += self.gravity
            new_y = self.y + self.jump_vel

            # Check if jumped off top of screen
            if new_y + self.height < screen_rect.top():
                # Determine nearest wall based on x position
                cat_center_x = self.x + self.width / 2
                screen_center_x = (screen_rect.left() + screen_rect.right()) / 2

                dist_to_left = cat_center_x - screen_rect.left()
                dist_to_right = screen_rect.right() - cat_center_x
                dist_to_top = abs(new_y - screen_rect.top())

                # Find nearest wall
                min_dist = min(dist_to_left, dist_to_right, dist_to_top)

                if min_dist == dist_to_left:
                    # Flip to left edge
                    self.edge = Cat.LEFT
                    self.x = screen_rect.left() - self.width
                elif min_dist == dist_to_right:
                    # Flip to right edge
                    self.edge = Cat.RIGHT
                    self.x = screen_rect.right() - self.width
                else:
                    # Flip to top edge
                    self.edge = Cat.TOP
                    self.y = screen_rect.top() - CAT_EDGE_INSET_TOP

                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return

            if new_y >= ground_y:
                new_y = ground_y
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # Reset on landing
            self.y = new_y
        else:
            self.y = ground_y

        self.x += self.speed * self.facing

        if random.random() < 0.05:
            self.facing = -self.facing  # Flip direction instead of random choice

    def _update_top_edge(self, screen_rect: QRect):
        """Cat walking upside down on top edge"""
        ground_y = screen_rect.top() - CAT_EDGE_INSET_TOP

        if self.is_jumping:
            self.jump_vel += self.gravity
            new_y = self.y - self.jump_vel

            # Check if jumped off bottom of screen
            if new_y > screen_rect.bottom():
                # Determine nearest wall based on x position
                cat_center_x = self.x + self.width / 2

                dist_to_left = cat_center_x - screen_rect.left()
                dist_to_right = screen_rect.right() - cat_center_x
                dist_to_bottom = abs(new_y - screen_rect.bottom())

                # Find nearest wall
                min_dist = min(dist_to_left, dist_to_right, dist_to_bottom)

                if min_dist == dist_to_left:
                    # Flip to left edge
                    self.edge = Cat.LEFT
                    self.x = screen_rect.left() - self.width
                elif min_dist == dist_to_right:
                    # Flip to right edge
                    self.edge = Cat.RIGHT
                    self.x = screen_rect.right() - self.width
                else:
                    # Flip to bottom edge
                    self.edge = Cat.BOTTOM
                    self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM

                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return

            if new_y <= ground_y:
                new_y = ground_y
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # Reset on landing
            self.y = new_y
        else:
            self.y = ground_y

        self.x += self.speed * self.facing

        # Check if walked off left or right side
        if self.x + self.width < screen_rect.left():
            # Walked off left side, spawn on left wall
            self.edge = Cat.LEFT
            self.x = screen_rect.left() - self.width
        elif self.x > screen_rect.right():
            # Walked off right side, spawn on right wall
            self.edge = Cat.RIGHT
            self.x = screen_rect.right() - self.width

        if random.random() < 0.05:
            self.facing = -self.facing  # Flip direction instead of random choice

    def _update_left_edge(self, screen_rect: QRect):
        """Cat walking on left edge"""
        ground_x = screen_rect.left() - CAT_EDGE_INSET_LEFT

        if self.is_jumping:
            self.jump_vel += self.gravity
            new_x = self.x + self.jump_vel

            # Check if jumped off right side of screen
            if new_x > screen_rect.right():
                # Determine nearest wall based on y position
                cat_center_y = self.y + self.height / 2

                dist_to_top = cat_center_y - screen_rect.top()
                dist_to_bottom = screen_rect.bottom() - cat_center_y
                dist_to_right = abs(new_x - screen_rect.right())

                # Find nearest wall
                min_dist = min(dist_to_top, dist_to_bottom, dist_to_right)

                if min_dist == dist_to_top:
                    # Flip to top edge
                    self.edge = Cat.TOP
                    self.y = screen_rect.top() - CAT_EDGE_INSET_TOP
                elif min_dist == dist_to_bottom:
                    # Flip to bottom edge
                    self.edge = Cat.BOTTOM
                    self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM
                else:
                    # Flip to right edge
                    self.edge = Cat.RIGHT
                    self.x = (screen_rect.right() - self.width + 1) + CAT_EDGE_INSET_RIGHT

                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return

            if new_x >= ground_x:
                new_x = ground_x
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # Reset on landing
            self.x = new_x
        else:
            self.x = ground_x

        self.y += self.speed * self.facing

        # Check if walked off top or bottom
        if self.y + self.height < screen_rect.top():
            # Walked off top, spawn on top wall
            self.edge = Cat.TOP
            self.y = screen_rect.top() - CAT_EDGE_INSET_TOP
        elif self.y > screen_rect.bottom():
            # Walked off bottom, spawn on bottom wall
            self.edge = Cat.BOTTOM
            self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM

        if random.random() < 0.05:
            self.facing = -self.facing  # Flip direction instead of random choice

    def _update_right_edge(self, screen_rect: QRect):
        """Cat walking on right edge"""
        ground_x = (screen_rect.right() - self.width + 1) + CAT_EDGE_INSET_RIGHT

        if self.is_jumping:
            self.jump_vel += self.gravity
            new_x = self.x - self.jump_vel

            # Check if jumped off left side of screen
            if new_x + self.width < screen_rect.left():
                # Determine nearest wall based on y position
                cat_center_y = self.y + self.height / 2

                dist_to_top = cat_center_y - screen_rect.top()
                dist_to_bottom = screen_rect.bottom() - cat_center_y
                dist_to_left = abs(new_x - screen_rect.left())

                # Find nearest wall
                min_dist = min(dist_to_top, dist_to_bottom, dist_to_left)

                if min_dist == dist_to_top:
                    # Flip to top edge
                    self.edge = Cat.TOP
                    self.y = screen_rect.top() - CAT_EDGE_INSET_TOP
                elif min_dist == dist_to_bottom:
                    # Flip to bottom edge
                    self.edge = Cat.BOTTOM
                    self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM
                else:
                    # Flip to left edge
                    self.edge = Cat.LEFT
                    self.x = screen_rect.left() - CAT_EDGE_INSET_LEFT

                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return

            if new_x <= ground_x:
                new_x = ground_x
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # Reset on landing
            self.x = new_x
        else:
            self.x = ground_x

        self.y += self.speed * self.facing

        # Check if walked off top or bottom
        if self.y + self.height < screen_rect.top():
            # Walked off top, spawn on top wall
            self.edge = Cat.TOP
            self.y = screen_rect.top() - CAT_EDGE_INSET_TOP
        elif self.y > screen_rect.bottom():
            # Walked off bottom, spawn on bottom wall
            self.edge = Cat.BOTTOM
            self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM

        if random.random() < 0.05:
            self.facing = -self.facing  # Flip direction instead of random choice

    def hits_corner(self, screen_rect: QRect) -> bool:
        """Check if jumping cat overlaps any screen corner"""
        if not self.is_jumping or self.hit_corner_this_jump:
            return False

        corner_margin = 150  # Detection zone size
        cat_rect = QRect(int(self.x), int(self.y), self.width, self.height)

        # Define corner zones
        top_left = QRect(screen_rect.left(), screen_rect.top(), corner_margin, corner_margin)
        top_right = QRect(screen_rect.right() - corner_margin, screen_rect.top(), corner_margin, corner_margin)
        bottom_left = QRect(screen_rect.left(), screen_rect.bottom() - corner_margin, corner_margin, corner_margin)
        bottom_right = QRect(screen_rect.right() - corner_margin, screen_rect.bottom() - corner_margin, corner_margin, corner_margin)

        if cat_rect.intersects(top_left) or cat_rect.intersects(top_right) or \
           cat_rect.intersects(bottom_left) or cat_rect.intersects(bottom_right):
            self.hit_corner_this_jump = True
            return True
        return False

    def _flip_to_random_edge(self, screen_rect: QRect):
        """Flip cat to a random edge when it goes off screen"""
        self.edge = random.choice([self.BOTTOM, self.TOP, self.LEFT, self.RIGHT])

        if self.edge == self.BOTTOM:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM
        elif self.edge == self.TOP:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = screen_rect.top() - CAT_EDGE_INSET_TOP
        elif self.edge == self.LEFT:
            self.x = screen_rect.left() - CAT_EDGE_INSET_LEFT
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))
        elif self.edge == self.RIGHT:
            self.x = (screen_rect.right() - self.width + 1) + CAT_EDGE_INSET_RIGHT
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))

        self.facing = random.choice([1, -1])
        self.reset_jump_state()

    def _clamp_to_screen(self, screen_rect: QRect):
        """Force cat position to stay within screen boundaries"""
        # Base limits (Qt QRect right()/bottom() are inclusive, so +1 for max top-left).
        min_x = screen_rect.left()
        max_x = screen_rect.right() - self.width + 1
        min_y = screen_rect.top()
        max_y = screen_rect.bottom() - self.height + 1

        # Allow small overscan so the visible cat (which is centered inside its box)
        # can appear flush to the edges.
        if self.edge == self.LEFT:
            min_x -= CAT_EDGE_INSET_LEFT
        elif self.edge == self.RIGHT:
            max_x += CAT_EDGE_INSET_RIGHT

        if self.edge == self.TOP:
            min_y -= CAT_EDGE_INSET_TOP
        elif self.edge == self.BOTTOM:
            max_y += CAT_EDGE_INSET_BOTTOM

        # Clamp X position
        if self.x < min_x:
            self.x = min_x
        elif self.x > max_x:
            self.x = max_x

        # Clamp Y position
        if self.y < min_y:
            self.y = min_y
        elif self.y > max_y:
            self.y = max_y

    def _ensure_on_screen(self, screen_rect: QRect):
        """Check if cat went off screen and flip to random edge"""
        off_screen = (
            self.x + self.width < screen_rect.left() or
            self.x > screen_rect.right() or
            self.y + self.height < screen_rect.top() or
            self.y > screen_rect.bottom()
        )

        # if off_screen:
        #     self._flip_to_random_edge(screen_rect)

    def _teleport_to_random_edge(self, screen_rect: QRect):
        """Teleport cat to a random edge"""
        self.edge = random.choice([self.BOTTOM, self.TOP, self.LEFT, self.RIGHT])

        if self.edge == self.BOTTOM:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = (screen_rect.bottom() - self.height + 1) + CAT_EDGE_INSET_BOTTOM
        elif self.edge == self.TOP:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = screen_rect.top() - CAT_EDGE_INSET_TOP
        elif self.edge == self.LEFT:
            self.x = screen_rect.left() - CAT_EDGE_INSET_LEFT
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))
        elif self.edge == self.RIGHT:
            self.x = (screen_rect.right() - self.width + 1) + CAT_EDGE_INSET_RIGHT
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))

        self.facing = random.choice([1, -1])
        self.reset_jump_state()

    def reset_jump_state(self):
        """Reset jumping and corner hit flags"""
        self.is_jumping = False
        self.jump_vel = 0
        self.hit_corner_this_jump = False

    def check_and_jump(self):
        """Check if it's time to jump"""
        self.jump_cooldown -= 100
        if self.jump_cooldown <= 0 and not self.is_jumping:
            if random.random() < 0.55:
                self.jump_cooldown = random.randint(500, 1500)
                self.is_jumping = True
                # Jump direction depends on which edge the cat is on
                jump_strength = random.randint(-20, -10)
                if self.edge == Cat.BOTTOM:
                    self.jump_vel = jump_strength  # Jump up (negative Y)
                elif self.edge == Cat.TOP:
                    self.jump_vel = jump_strength  # Jump down (negative Y becomes positive)
                elif self.edge == Cat.LEFT:
                    self.jump_vel = -jump_strength  # Jump right (invert to positive X)
                elif self.edge == Cat.RIGHT:
                    self.jump_vel = -jump_strength  # Jump left (invert to positive, then subtracted)

    def update_anim(self):
        """Update animation frame"""
        if not self.is_jumping:
            self.anim_frame = (self.anim_frame + 1) % 4
        if self.shoot_anim > 0:
            self.shoot_anim -= 1

    def trigger_shoot(self, ticks: int = _SHOOT_ANIM_TICKS):
        try:
            ticks = int(ticks)
        except Exception:
            ticks = _SHOOT_ANIM_TICKS
        self.shoot_anim = max(self.shoot_anim, max(1, ticks))


class CatOverlay(QWidget):
    from PySide6.QtCore import Signal
    panel_requested = Signal(object)
    cats_multiplied = Signal(int)
    cat_killed = Signal(object)

    def __init__(self):
        super().__init__()
        global _CAT_OVERLAY
        _CAT_OVERLAY = self
        self.cat_width = 300
        self.cat_height = 270

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
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setFocusPolicy(Qt.NoFocus)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self.speed_mag = 5
        self.click_through = True
        self.cats = [Cat(0, screen.bottom(), Cat.BOTTOM)]
        self.multiply_timer = random.randint(30000, 60000)
        self.shutting_down = False

        self.jump_timer = QTimer(self)
        self.jump_timer.timeout.connect(self._check_jumps)
        self.jump_timer.start(200)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._update_anim)
        self.anim_timer.start(200)

        self.move_timer = QTimer(self)
        self.move_timer.timeout.connect(self.tick_move)
        self.move_timer.start(16)  # ~60fps for smoother motion

        self._update_window_mask()

    def _update_window_mask(self):
        """Update window mask so only cat areas are clickable"""
        region = QRegion()
        padding = 40  # Larger padding to avoid clipping after rotation
        for cat in self.cats:
            cat_rect = QRect(
                int(cat.x) - padding,
                int(cat.y) - padding,
                cat.width + padding * 2,
                cat.height + padding * 2
            )
            region = region.united(QRegion(cat_rect))
        self.setMask(region)

    def _check_jumps(self):
        """Check and update jump state for all cats"""
        if self.shutting_down:
            return

        self.multiply_timer -= 100

        if self.multiply_timer <= 0:
            self._multiply_cats()
            self.multiply_timer = random.randint(30000, 60000)

        for cat in self.cats:
            cat.check_and_jump()

    def _update_anim(self):
        """Update animation for all cats"""
        if self.shutting_down:
            return

        for cat in self.cats:
            cat.update_anim()
        self.update()

    def _multiply_cats(self):
        """Add a small number of new cats (non-exponential)"""
        if self.shutting_down:
            return

        if len(self.cats) >= 5:
            return

        screen = self.geometry()
        spawn_count = 1

        new_cats = []
        for _ in range(spawn_count):
            base = random.choice(self.cats) if self.cats else None
            base_x = base.x if base else screen.center().x()
            base_y = base.y if base else screen.center().y()
            new_edge = Cat.BOTTOM # random.choice([Cat.BOTTOM, Cat.TOP, Cat.LEFT, Cat.RIGHT])
            new_cat = Cat(
                base_x + random.randint(-20, 20),
                base_y + random.randint(-20, 20),
                new_edge,
            )
            new_cats.append(new_cat)

        self.cats.extend(new_cats)
        self._update_window_mask()
        self.cats_multiplied.emit(len(self.cats))

    def set_speed(self, speed: int):
        speed = max(0, int(speed))
        self.speed_mag = speed if speed > 0 else 1

    def set_click_through(self, enabled: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))
        self.click_through = enabled

    def set_running(self, running: bool):
        running = bool(running)
        if running:
            self.anim_timer.start()
            self.move_timer.start(16)
            self.jump_timer.start(200)
        else:
            self.anim_timer.stop()
            self.move_timer.stop()
            self.jump_timer.stop()

    def tick_move(self):
        """Update all cats and redraw"""
        if self.speed_mag == 0 or self.shutting_down:
            return

        screen = self.geometry()

        for cat in list(self.cats):
            cat.update(screen, self.speed_mag)

            if cat.hits_corner(screen):
                self._multiply_cats()
                
        if not self.cats and random.random() < 0.01:
            self._multiply_cats()

        self._update_window_mask()

        self.update()

    def random_cat_center_global(self) -> QPoint:
        """Return the global center point of a random cat (fallback: overlay center)."""
        try:
            if not getattr(self, "cats", None):
                return self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
            cat = random.choice(self.cats)
            local = QPoint(int(cat.x + cat.width / 2), int(cat.y + cat.height / 2))
            return self.mapToGlobal(local)
        except Exception:
            return self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))

    def random_cat_center_global_with_cat(self) -> tuple[Cat, QPoint]:
        """Return a (cat, global_center) pair for a random cat."""
        try:
            if not getattr(self, "cats", None):
                return None, self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
            cat = random.choice(self.cats)
            local = QPoint(int(cat.x + cat.width / 2), int(cat.y + cat.height / 2))
            return cat, self.mapToGlobal(local)
        except Exception:
            return None, self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))

    def trigger_shoot(self, cat: Cat, ticks: int = _SHOOT_ANIM_TICKS):
        if cat is None:
            return
        try:
            cat.trigger_shoot(ticks)
        except Exception:
            pass
        self.update()

    def trigger_shoot_near_global(self, global_pos: QPoint, ticks: int = _SHOOT_ANIM_TICKS):
        if not getattr(self, "cats", None):
            return
        try:
            local_pos = self.mapFromGlobal(global_pos)
        except Exception:
            return
        best_cat = None
        best_dist = None
        for cat in self.cats:
            cx = cat.x + cat.width / 2
            cy = cat.y + cat.height / 2
            dx = cx - local_pos.x()
            dy = cy - local_pos.y()
            d2 = dx * dx + dy * dy
            if best_dist is None or d2 < best_dist:
                best_dist = d2
                best_cat = cat
        if best_cat is not None:
            self.trigger_shoot(best_cat, ticks)

    def cannon_muzzle_global(self, cat: Cat) -> QPoint:
        if cat is None:
            return self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))

        w, h = cat.width, cat.height
        cx = w / 2.0
        cy = h / 2.0 + 10.0

        breath = math.sin(cat.anim_frame * 0.15) * 2.0
        body_y = cy + 15.0
        cannon_y = body_y + 10.0 + breath

        barrel_len = 38.0
        barrel_x = cx + 6.0

        muzzle_x = barrel_x + barrel_len
        muzzle_y = cannon_y

        # Apply facing flip (same transform as draw)
        if cat.facing < 0:
            muzzle_x = w - muzzle_x

        # Apply edge rotation around center
        angle = 0.0
        if cat.edge == Cat.TOP:
            angle = 180.0
        elif cat.edge == Cat.LEFT:
            angle = 90.0
        elif cat.edge == Cat.RIGHT:
            angle = -90.0

        if angle != 0.0:
            rad = math.radians(angle)
            dx = muzzle_x - (w / 2.0)
            dy = muzzle_y - (h / 2.0)
            rx = dx * math.cos(rad) - dy * math.sin(rad)
            ry = dx * math.sin(rad) + dy * math.cos(rad)
            muzzle_x = (w / 2.0) + rx
            muzzle_y = (h / 2.0) + ry

        local = QPoint(int(cat.x + muzzle_x), int(cat.y + muzzle_y))
        return self.mapToGlobal(local)

    def shutdown(self):
        """Clean shutdown: stop timers, clear cats, close overlay"""
        self.shutting_down = True
        self.jump_timer.stop()
        self.anim_timer.stop()
        self.move_timer.stop()
        self.cats.clear()
        self.hide()
        self.close()

    def paintEvent(self, event):
        """Draw all cats"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        for cat in self.cats:
            painter.save()

            angle = 0
            if cat.edge == Cat.TOP:
                angle = 180
            elif cat.edge == Cat.LEFT:
                angle = 90
            elif cat.edge == Cat.RIGHT:
                angle = -90

            cx = cat.x + self.cat_width / 2
            cy = cat.y + self.cat_height / 2
            painter.translate(cx, cy)
            painter.rotate(angle)
            painter.translate(-self.cat_width / 2, -self.cat_height / 2)

            self._draw_cat(painter, cat)
            painter.restore()

    def _cat_hit_rect(self, cat, extra: int = 0) -> QRect:
        """Return a tighter hitbox for the visible cat sprite."""
        try:
            extra = int(extra)
        except Exception:
            extra = 0
        extra = max(0, extra)

        inset_left = int(CAT_EDGE_INSET_LEFT)
        inset_right = int(CAT_EDGE_INSET_RIGHT)
        inset_top = int(CAT_EDGE_INSET_TOP)
        inset_bottom = int(CAT_EDGE_INSET_BOTTOM)

        left = int(cat.x + inset_left - extra)
        top = int(cat.y + inset_top - extra)
        width = int(cat.width - inset_left - inset_right + (extra * 2))
        height = int(cat.height - inset_top - inset_bottom + (extra * 2))

        width = max(1, width)
        height = max(1, height)
        return QRect(left, top, width, height)

    def _draw_cat(self, painter: QPainter, cat):
        """
        Draws a 'Chibi' style cat with 1:1 Head/Body proportions.
        Expression: Unimpressed/Judging.
        """
        w, h = self.cat_width, self.cat_height

        fur_color = QColor(255, 170, 80)
        fur_shadow = QColor(215, 130, 40)
        white = QColor(255, 255, 255)
        skin_pink = QColor(255, 180, 190)
        outline = QColor(60, 40, 20)

        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(outline, 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        breath = math.sin(cat.anim_frame * 0.15) * 2

        shooting = cat.shoot_anim > 0
        shoot_phase = int(cat.shoot_anim) if shooting else 0

        cx, cy = w // 2, h // 2 + 10

        painter.save()
        if cat.facing < 0:
            painter.translate(w, 0)
            painter.scale(-1, 1)
            cx = w // 2

        tail_path = QPainterPath()
        tail_start = QPoint(cx - 25, cy + 30)
        tail_swish = math.sin(cat.anim_frame * 0.2) * 10

        tail_path.moveTo(tail_start)
        tail_path.cubicTo(
            cx - 50, cy + 30,
            cx - 60, cy - 20 + tail_swish,
            cx - 30, cy - 40 + tail_swish
        )

        painter.setBrush(Qt.NoBrush)
        tail_pen = QPen(outline, 14, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(tail_pen)
        painter.drawPath(tail_path)

        tail_pen.setColor(fur_color)
        tail_pen.setWidth(9)
        painter.setPen(tail_pen)
        painter.drawPath(tail_path)

        painter.setPen(QPen(outline, 2.5))

        body_w, body_h = 50, 45
        body_y = cy + 15

        painter.setBrush(QBrush(fur_color))
        body_path = QPainterPath()
        body_path.addRoundedRect(QRect(int(cx - body_w/2), int(body_y), int(body_w), int(body_h)), 20, 20)
        painter.drawPath(body_path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(white))
        painter.drawEllipse(int(cx - 15), int(body_y + 10), 30, 25)

        painter.setPen(QPen(outline, 2.5))

        head_w, head_h = 80, 70
        head_x = cx - head_w // 2
        head_y = cy - 45 + breath

        painter.setBrush(QBrush(fur_color))

        ear_l = QPolygon([
            QPoint(int(cx - 30), int(head_y + 10)),
            QPoint(int(cx - 38), int(head_y - 15)),
            QPoint(int(cx - 15), int(head_y + 5))
        ])
        painter.drawPolygon(ear_l)

        ear_r = QPolygon([
            QPoint(int(cx + 30), int(head_y + 10)),
            QPoint(int(cx + 38), int(head_y - 15)),
            QPoint(int(cx + 15), int(head_y + 5))
        ])
        painter.drawPolygon(ear_r)

        head_rect = QRect(int(head_x), int(head_y), int(head_w), int(head_h))
        painter.setBrush(QBrush(fur_color))
        painter.drawRoundedRect(head_rect, 30, 30)

        eye_y = head_y + 28
        eye_offset = 18
        eye_size = 14

        painter.setBrush(QBrush(white))
        painter.drawEllipse(int(cx - eye_offset - eye_size/2), int(eye_y), eye_size, eye_size)
        painter.drawEllipse(int(cx + eye_offset - eye_size/2), int(eye_y), eye_size, eye_size)

        painter.setBrush(QBrush(outline))
        painter.drawEllipse(int(cx - eye_offset - 2), int(eye_y + 4), 4, 4)
        painter.drawEllipse(int(cx + eye_offset - 2), int(eye_y + 4), 4, 4)

        painter.setBrush(QBrush(fur_color))
        painter.setPen(QPen(outline, 2.5))

        painter.drawLine(int(cx - eye_offset - 8), int(eye_y + 2), int(cx - eye_offset + 8), int(eye_y + 2))
        painter.drawLine(int(cx + eye_offset - 8), int(eye_y + 2), int(cx + eye_offset + 8), int(eye_y + 2))

        painter.setBrush(QBrush(skin_pink))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(cx - 3), int(eye_y + 12), 6, 4)

        painter.setPen(QPen(outline, 2))
        painter.setBrush(Qt.NoBrush)
        mouth_y = eye_y + 18

        mouth_path = QPainterPath()
        mouth_path.moveTo(cx - 5, mouth_y)
        mouth_path.quadTo(cx - 2.5, mouth_y + 3, cx, mouth_y)
        mouth_path.quadTo(cx + 2.5, mouth_y + 3, cx + 5, mouth_y)
        painter.drawPath(mouth_path)

        if shooting:
            # Cannon with recoil + muzzle flash + a cannonball exiting the barrel.
            recoil = 0
            if shoot_phase == _SHOOT_ANIM_TICKS - 1:
                recoil = 4
            elif shoot_phase <= _SHOOT_ANIM_TICKS - 2:
                recoil = 2

            cannon_y = body_y + 10 + breath
            barrel_len = 38
            barrel_thick = 13
            barrel_x = cx + 6 - recoil

            # Tilt the cannon upwards to follow the arc.
            tilt_deg = -18

            painter.save()
            painter.translate(barrel_x, cannon_y)
            painter.rotate(tilt_deg)
            painter.translate(-barrel_x, -cannon_y)

            barrel_rect = QRect(int(barrel_x), int(cannon_y - barrel_thick / 2), int(barrel_len), int(barrel_thick))
            painter.setPen(QPen(outline, 2))
            painter.setBrush(QBrush(QColor(60, 60, 70)))
            painter.drawRoundedRect(barrel_rect, 4, 4)

            # Cannonball travel: starts inside the barrel, moves out over the first frames.
            travel = (_SHOOT_ANIM_TICKS - shoot_phase) / float(_SHOOT_ANIM_TICKS)
            travel = max(0.0, min(1.0, travel))
            ball_r = 7
            ball_start_x = barrel_x + 8
            ball_end_x = barrel_x + barrel_len + 20
            ball_x = ball_start_x + (ball_end_x - ball_start_x) * (travel ** 0.7)
            ball_y = cannon_y - 1
            painter.setBrush(QBrush(QColor(80, 80, 90)))
            painter.setPen(QPen(outline, 1.5))
            painter.drawEllipse(int(ball_x - ball_r), int(ball_y - ball_r), ball_r * 2, ball_r * 2)

            # Muzzle flash only on the first frame of the shot.
            if shoot_phase == _SHOOT_ANIM_TICKS:
                flash = QPolygon([
                    QPoint(int(barrel_x + barrel_len + 2), int(cannon_y - 4)),
                    QPoint(int(barrel_x + barrel_len + 20), int(cannon_y - 12)),
                    QPoint(int(barrel_x + barrel_len + 12), int(cannon_y + 2)),
                    QPoint(int(barrel_x + barrel_len + 20), int(cannon_y + 14)),
                    QPoint(int(barrel_x + barrel_len + 2), int(cannon_y + 8)),
                ])
                painter.setBrush(QBrush(QColor(255, 210, 80)))
                painter.setPen(QPen(QColor(255, 150, 40), 2))
                painter.drawPolygon(flash)

            # Smoke puffs trailing the shot.
            if shoot_phase <= _SHOOT_ANIM_TICKS - 1:
                puff_alpha = 140 if shoot_phase >= _SHOOT_ANIM_TICKS - 2 else 90
                puff_color = QColor(200, 200, 200, puff_alpha)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(puff_color))
                puff_x = barrel_x + barrel_len + 8 + (1.0 - travel) * 8
                painter.drawEllipse(int(puff_x), int(cannon_y - 12), 14, 14)
                painter.drawEllipse(int(puff_x + 8), int(cannon_y - 4), 10, 10)

            painter.restore()

            wheel_r = 8
            painter.setBrush(QBrush(QColor(90, 90, 100)))
            painter.drawEllipse(int(barrel_x - 8), int(cannon_y + 6), wheel_r * 2, wheel_r * 2)

        painter.setPen(QPen(outline, 2.5))
        painter.setBrush(QBrush(white))

        paw_y = body_y + 15 + breath
        paw_left_x = cx - 15
        paw_right_x = cx + 3
        if shooting:
            paw_y = body_y + 10 + breath
            paw_left_x = cx - 18
            paw_right_x = cx + 8
        painter.drawEllipse(int(paw_left_x), int(paw_y), 12, 12)
        painter.drawEllipse(int(paw_right_x), int(paw_y), 12, 12)

        foot_y = body_y + body_h - 8
        painter.setBrush(QBrush(white))
        painter.drawEllipse(int(cx - 20), int(foot_y), 14, 10)
        painter.drawEllipse(int(cx + 6), int(foot_y), 14, 10)

        painter.restore()

    def mousePressEvent(self, event):
        """Handle clicks on cats"""
        click_pos = event.position().toPoint()
        for cat in self.cats:
            cat_rect = self._cat_hit_rect(cat)
            if cat_rect.contains(click_pos):
                self.panel_requested.emit(cat)
                # Shoot from the clicked cat.
                try:
                    self.trigger_shoot(cat)
                except Exception:
                    pass
                try:
                    origin_global = self.cannon_muzzle_global(cat)
                except Exception:
                    origin_global = self.mapToGlobal(click_pos)
                try:
                    on_cat_clicked(origin_global)
                except Exception:
                    pass
                event.accept()
                return

    def kill_cat(self, cat):
        """Remove a specific cat from the overlay"""
        if cat in self.cats:
            self.cats.remove(cat)
            self._update_window_mask()
            self.cats_multiplied.emit(len(self.cats))

    def kill_cat_at_global(self, global_pos: QPoint, radius: int = 1) -> bool:
        if not getattr(self, "cats", None):
            return False
        try:
            local_pos = self.mapFromGlobal(global_pos)
        except Exception:
            return False
        r = max(1, int(radius))
        for cat in list(self.cats):
            cat_rect = self._cat_hit_rect(cat, extra=r)
            if cat_rect.contains(local_pos):
                self.kill_cat(cat)
                try:
                    self.cat_killed.emit(cat)
                except Exception:
                    pass
                return True
        return False


class MessageClient(QObject):
    message_received = Signal(str)
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = QTcpSocket(self)
        self._sock.connected.connect(lambda: self.status_changed.emit("Client connected"))
        self._sock.disconnected.connect(lambda: self.status_changed.emit("Client disconnected"))
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.errorOccurred.connect(lambda _e: self.status_changed.emit(f"Client error: {self._sock.errorString()}"))
        self._rx_buf = b""

    def connect_to(self, host: str = "127.0.0.1", port: int = 50505):
        self.status_changed.emit(f"Client connecting to {host}:{port}...")
        self._sock.connectToHost(host, int(port))

    def disconnect(self):
        self._sock.disconnectFromHost()

    def send(self, text: str):
        if self._sock.state() != QTcpSocket.ConnectedState:
            self.status_changed.emit("Client not connected")
            return
        self._sock.write((text.rstrip("\n") + "\n").encode("utf-8"))

    def _on_ready_read(self):
        self._rx_buf += bytes(self._sock.readAll())
        while b"\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split(b"\n", 1)
            self.message_received.emit(line.decode("utf-8", errors="replace"))


class MultiMessageClient(QObject):
    """Manages multiple MessageClient connections in parallel."""
    message_received = Signal(str)
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clients: dict[tuple[str, int], MessageClient] = {}

    def connect_to(self, host: str = "127.0.0.1", port: int = 50505):
        key = (str(host), int(port))
        if key in self._clients:
            return
        client = MessageClient(self)
        client.message_received.connect(self.message_received.emit)
        client.status_changed.connect(self.status_changed.emit)
        self._clients[key] = client
        client.connect_to(host, port)

    def disconnect(self):
        for client in list(self._clients.values()):
            try:
                client.disconnect()
            except Exception:
                pass
        self._clients.clear()

    def send(self, text: str):
        for client in list(self._clients.values()):
            try:
                client.send(text)
            except Exception:
                pass


# ---- Zeroconf P2P ----

class ZeroConfP2P(QObject):
    peer_found = Signal(str, str, int)  # (name, host, port)
    status_changed = Signal(str)

    SERVICE_TYPE = "_catclick._tcp.local."

    def __init__(self, port: int, instance_name: str = None, parent=None):
        super().__init__(parent)
        self.port = int(port)
        self.instance_id = (instance_name or str(uuid.uuid4()))
        self._zc: Zeroconf = None
        self._browser: ServiceBrowser = None
        self._info: ServiceInfo = None
        self._closed = False

    def start(self):
        if self._zc is not None:
            return
        self._zc = Zeroconf()

        # Advertise this instance
        host_ip = get_lan_ip()
        service_name = f"CatClick-{self.instance_id}.{self.SERVICE_TYPE}"
        props = {b"instance_id": self.instance_id.encode("utf-8")}
        self._info = ServiceInfo(
            type_=self.SERVICE_TYPE,
            name=service_name,
            addresses=[socket.inet_aton(host_ip)],
            port=self.port,
            properties=props,
            server=f"catclick-{self.instance_id}.local.",
        )
        try:
            self._zc.register_service(self._info)
            self.status_changed.emit(f"Zeroconf advertising {service_name} ({host_ip}:{self.port})")
        except Exception as e:
            self.status_changed.emit(f"Zeroconf advertise failed: {e}")

        # Discover peers
        self._browser = ServiceBrowser(self._zc, self.SERVICE_TYPE, handlers=[self._on_service_state_change])
        self.status_changed.emit(f"Zeroconf browsing {self.SERVICE_TYPE}")
        print("Zeroconf started at "f"{host_ip}:{self.port} with instance ID {self.instance_id}")

    def close(self):
        self._closed = True
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        except Exception:
            pass
        try:
            self._zc.close()
        except Exception:
            pass
        self._zc = None
        self._browser = None
        self._info = None
        self.status_changed.emit("Zeroconf stopped")

    def _on_service_state_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange):
        if self._closed:
            return
        if state_change != ServiceStateChange.Added:
            return

        # Resolve in a thread so we don't block callbacks
        def _resolve():
            try:
                info = zeroconf.get_service_info(service_type, name, timeout=2000)
                if not info:
                    return
                props = info.properties or {}
                peer_id = props.get(b"instance_id", b"").decode("utf-8", errors="ignore")
                if peer_id == self.instance_id:
                    return  # ignore ourselves
                host = _first_ipv4(list(info.addresses))
                if not host:
                    return
                port = int(info.port)
                self.status_changed.emit(f"Peer discovered: {name} ({host}:{port})")
                self.peer_found.emit(name, host, port)
            except Exception as e:
                self.status_changed.emit(f"Peer resolve failed: {e}")

        threading.Thread(target=_resolve, daemon=True).start()


class GifOverlay(QWidget):
    finished = Signal()

    def __init__(self, gif_path: Path, pos: QPoint):
        super().__init__()
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.movie = QMovie(str(gif_path))
        if not self.movie.isValid():
            raise RuntimeError(f"Invalid GIF: {gif_path}")
        self.movie.setCacheMode(QMovie.CacheAll)
        # Some PySide6 builds don't expose QMovie.setLoopCount().
        # We'll stop manually after one full loop using frameChanged.
        self._loops_done = 0
        self._prev_frame = -1
        if hasattr(self.movie, "setLoopCount"):
            try:
                self.movie.setLoopCount(1)  # prefer native API when available
            except Exception:
                pass

        self.label.setMovie(self.movie)
        self.label.setScaledContents(True)

        # QMovie.frameRect() can be (0,0,0,0) until a frame is loaded.
        # Force-load the first frame so we get a real size.
        try:
            self.movie.jumpToFrame(0)
        except Exception:
            pass

        pix = self.movie.currentPixmap()
        if not pix.isNull():
            size = pix.size()
        else:
            size = self.movie.frameRect().size()

        # Fallback if still empty
        if size.width() <= 0 or size.height() <= 0:
            size = self.movie.scaledSize()
        if size.width() <= 0 or size.height() <= 0:
            size = self.label.sizeHint()
        if size.width() <= 0 or size.height() <= 0:
            size = QSize(128, 128)

        size.setWidth(size.width() / 2)
        size.setHeight(size.height() / 2)
        self.resize(size)
        self.label.resize(size)

        # Center on the click position, but clamp to the current screen bounds.
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()
        x = pos.x() - size.width() // 2
        y = pos.y() - size.height() // 2
        x = max(geo.left(), min(x, geo.right() - size.width()))
        y = max(geo.top(), min(y, geo.bottom() - size.height()))
        self.move(x, y)

        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.finished.connect(self._finish)

    def _on_frame_changed(self, frame_no: int):
        # Detect a loop restart when frame numbers wrap around (e.g. 10 -> 0).
        if self._prev_frame != -1 and frame_no < self._prev_frame:
            self._loops_done += 1
            if self._loops_done >= 1:
                # Stop after the first full loop.
                QTimer.singleShot(0, self._finish)
                return
        self._prev_frame = frame_no

    def _finish(self):
        try:
            self.movie.stop()
        except Exception:
            pass
        self.finished.emit()
        self.close()
        self.deleteLater()


# ---- Cannonball Overlay ----

class CannonBallOverlay(QWidget):
    finished = Signal(QPoint)  # emits landing position

    def __init__(self, start_pos: QPoint, end_pos: QPoint, duration_ms: int = 900, radius: int = 10, arc_height: int = 220, *, color: QColor = None):
        super().__init__()
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._start = QPoint(int(start_pos.x()), int(start_pos.y()))
        self._end = QPoint(int(end_pos.x()), int(end_pos.y()))
        self._duration = max(100, int(duration_ms))
        self._radius = max(2, int(radius))
        self._arc = int(arc_height)
        self._color = QColor(color) if color is not None else QColor(_PROJECTILE_COLOR)

        # Widget size is just big enough to draw the projectile.
        d = self._radius * 6 + 2
        self._pix = _get_projectile_pixmap(d)
        self.resize(d, d)

        # Start at start_pos (centered).
        self._t0 = QDateTime.currentMSecsSinceEpoch()
        self._set_center(self._start)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _set_center(self, p: QPoint):
        # Move widget so that its center sits on p.
        self.move(int(p.x() - self.width() // 2), int(p.y() - self.height() // 2))

    def _tick(self):
        now = QDateTime.currentMSecsSinceEpoch()
        t = (now - self._t0) / float(self._duration)
        if t >= 1.0:
            self._timer.stop()
            self._set_center(self._end)
            self.finished.emit(self._end)
            self.close()
            self.deleteLater()
            return

        # Linear interpolation + parabola arc.
        x0, y0 = self._start.x(), self._start.y()
        x1, y1 = self._end.x(), self._end.y()
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t

        # Screen Y grows downward; subtract to arc upward.
        y = y - self._arc * 4.0 * t * (1.0 - t)

        self._set_center(QPoint(int(x), int(y)))

    def set_color(self, color: QColor):
        if color is None or not color.isValid():
            return
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Clear backing store.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # Draw the projectile image if available; otherwise fall back to a simple cannonball.
        if getattr(self, "_pix", None) is not None and not self._pix.isNull():
            pix = _tint_pixmap(self._pix, self._color)
            x = (self.width() - pix.width()) // 2
            y = (self.height() - pix.height()) // 2
            painter.drawPixmap(int(x), int(y), pix)
        else:
            r = self._radius
            cx = self.width() // 2
            cy = self.height() // 2

            painter.setPen(Qt.NoPen)
            base = self._color if self._color is not None else QColor(Qt.gray)
            painter.setBrush(base)
            painter.drawEllipse(QPoint(cx, cy), r, r)

            painter.setBrush(base.lighter(160))
            painter.setOpacity(0.25)
            painter.drawEllipse(
                QPoint(cx - max(2, r // 3), cy - max(2, r // 3)),
                max(2, r // 3),
                max(2, r // 3),
            )
            painter.setOpacity(1.0)


# ---- Projectile Overlay ----

class ProjectileOverlay(QWidget):
    finished = Signal(QPoint)  # emits finish position (global)

    def __init__(
        self,
        geo,
        x0: float,
        y0: float,
        vx: float,
        vy: float,
        g: float,
        t_end: float,
        *,
        radius: int = 10,
        color: QColor = None,
        allow_kill: bool = True,
    ):
        super().__init__()
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._geo = geo
        self._x0 = float(x0)
        self._y0 = float(y0)
        self._vx = float(vx)
        self._vy = float(vy)
        self._g = float(g)
        self._t_end = max(0.0, float(t_end))
        self._color = QColor(color) if color is not None else QColor(_PROJECTILE_COLOR)
        self._allow_kill = bool(allow_kill)

        self._radius = max(2, int(radius))
        d = self._radius * 4 + 2
        self._pix = _get_projectile_pixmap(d)
        self.resize(d, d)

        self._t0_ms = QDateTime.currentMSecsSinceEpoch()
        self._set_center(self._pos_at(0.0))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _pos_at(self, t: float) -> QPoint:
        # Normalized projectile motion.
        x = self._x0 + self._vx * t
        y = self._y0 + self._vy * t + 0.5 * self._g * t * t

        # Map normalized coords to pixels in this screen's available geometry.
        px = self._geo.left() + int(round(x * self._geo.width()))
        py = self._geo.top() + int(round(y * self._geo.height()))
        return QPoint(int(px), int(py))

    def _set_center(self, p: QPoint):
        self.move(int(p.x() - self.width() // 2), int(p.y() - self.height() // 2))

    def _check_kill(self, p: QPoint) -> bool:
        try:
            if not self._allow_kill:
                return False
            if _CAT_OVERLAY is None:
                return False
            return _CAT_OVERLAY.kill_cat_at_global(p)
        except Exception:
            return False

    def _tick(self):
        now = QDateTime.currentMSecsSinceEpoch()
        t = (now - self._t0_ms) / 1000.0
        if t >= self._t_end:
            self._timer.stop()
            end_pos = self._pos_at(self._t_end)
            self._set_center(end_pos)
            self.finished.emit(end_pos)
            self.close()
            self.deleteLater()
            return

        pos = self._pos_at(t)
        self._set_center(pos)
        if self._check_kill(pos):
            try:
                self._timer.stop()
            except Exception:
                pass
            self.finished.emit(pos)
            self.close()
            self.deleteLater()

    def set_color(self, color: QColor):
        if color is None or not color.isValid():
            return
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # Draw the projectile image if available; otherwise fall back to a simple ball.
        if getattr(self, "_pix", None) is not None and not self._pix.isNull():
            pix = _tint_pixmap(self._pix, self._color)
            x = (self.width() - pix.width()) // 2
            y = (self.height() - pix.height()) // 2
            painter.drawPixmap(int(x), int(y), pix)
        else:
            r = self._radius
            cx = self.width() // 2
            cy = self.height() // 2

            painter.setPen(Qt.NoPen)
            base = self._color if self._color is not None else QColor(Qt.gray)
            painter.setBrush(base)
            painter.drawEllipse(QPoint(cx, cy), r, r)

            painter.setBrush(base.lighter(160))
            painter.setOpacity(0.25)
            painter.drawEllipse(
                QPoint(cx - max(2, r // 3), cy - max(2, r // 3)),
                max(2, r // 3),
                max(2, r // 3),
            )
            painter.setOpacity(1.0)


_active_cannonballs: list[CannonBallOverlay] = []
_active_projectiles: list[ProjectileOverlay] = []
_active_explosions: list[GifOverlay] = []

# Caps to avoid lag if many projectiles/explosions are active at once.
_MAX_ACTIVE_CANNONBALLS = 10
_MAX_ACTIVE_PROJECTILES = 16
_MAX_ACTIVE_EXPLOSIONS = 12


def set_projectile_color(color: QColor):
    global _PROJECTILE_COLOR
    if color is None or not color.isValid():
        return
    _PROJECTILE_COLOR = QColor(color)
    for ov in list(_active_projectiles):
        try:
            ov.set_color(_PROJECTILE_COLOR)
        except Exception:
            pass
    for ov in list(_active_cannonballs):
        try:
            ov.set_color(_PROJECTILE_COLOR)
        except Exception:
            pass


def _drop_oldest(overlays: list):
    """Close & delete the oldest overlay in the list (best-effort)."""
    if not overlays:
        return
    try:
        ov = overlays.pop(0)
    except Exception:
        return
    try:
        ov.close()
    except Exception:
        pass
    try:
        ov.deleteLater()
    except Exception:
        pass


# ---- Explosion sound ----
# Prefer QtMultimedia (low-latency) and fall back to OS tools if unavailable.

_EXPLODE_PROCS: list[subprocess.Popen] = []
_EXPLODE_MAX_SIMULTANEOUS = 4
_PEW_PROCS: list[subprocess.Popen] = []
_PEW_MAX_SIMULTANEOUS = 6

_SFX_POOLS: dict[str, dict] = {}

# If QtMultimedia backend is broken on this system (common on some Linux/PipeWire setups),
# disable it at runtime and fall back to OS playback.
_QT_SFX_DISABLED = False
_QT_SFX_DISABLE_REASON = ""


def _disable_qt_sfx(reason: str):
    global _QT_SFX_DISABLED, _QT_SFX_DISABLE_REASON
    if _QT_SFX_DISABLED:
        return
    _QT_SFX_DISABLED = True
    _QT_SFX_DISABLE_REASON = str(reason or "")
    try:
        # Best-effort: stop any players so we don't hang on a broken backend.
        for pool in list(_SFX_POOLS.values()):
            players = pool.get("players", [])
            try:
                for p in list(players):
                    try:
                        p.stop()
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


def _get_sfx_pool(key: str, max_simultaneous: int) -> dict:
    pool = _SFX_POOLS.get(key)
    if pool is None:
        pool = {
            "ready": False,
            "players": [],
            "audio": [],
            "rr": 0,
            "max": max(1, int(max_simultaneous)),
        }
        _SFX_POOLS[key] = pool
    return pool


def _preload_sfx(sound_path: Path, max_simultaneous: int, key: str) -> bool:
    """Preload audio for low-latency playback (best-effort)."""
    pool = _get_sfx_pool(key, max_simultaneous)

    if pool.get("ready"):
        return True
    if not sound_path.exists():
        return False
    if not _QT_AUDIO_AVAILABLE:
        return False

    try:
        url = QUrl.fromLocalFile(str(sound_path))
        for _ in range(pool["max"]):
            out = QAudioOutput()
            out.setVolume(0.9)
            p = QMediaPlayer()
            p.setAudioOutput(out)
            p.setSource(url)

            try:
                p.errorOccurred.connect(lambda err, err_str, _p=p: _disable_qt_sfx(f"QMediaPlayer error: {err_str}"))
            except Exception:
                pass

            pool["audio"].append(out)
            pool["players"].append(p)

        pool["ready"] = True
        return True
    except Exception:
        pool["players"] = []
        pool["audio"] = []
        pool["ready"] = False
        return False


def _spawn_sfx_process(sound_path: Path, procs: list[subprocess.Popen], max_simultaneous: int):
    try:
        procs[:] = [p for p in procs if p is not None and p.poll() is None]
        if len(procs) >= max_simultaneous:
            procs[:] = procs[-(max_simultaneous - 1):]

        proc = None
        if sys.platform == "darwin":
            proc = subprocess.Popen(
                ["afplay", str(sound_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform.startswith("win"):
            proc = subprocess.Popen(
                ["cmd", "/c", "start", "", str(sound_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            for cmd in (["paplay"], ["pw-play"], ["aplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"], ["xdg-open"]):
                try:
                    proc = subprocess.Popen(
                        cmd + [str(sound_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    break
                except Exception:
                    proc = None

        if proc is not None:
            procs.append(proc)
    except Exception:
        pass


def _play_sfx(sound_path: Path, max_simultaneous: int, key: str, procs: list[subprocess.Popen]):
    """Best-effort, non-blocking playback for a sound file."""
    if not sound_path.exists():
        return

    pool = _get_sfx_pool(key, max_simultaneous)

    if (not _QT_SFX_DISABLED) and _preload_sfx(sound_path, max_simultaneous, key) and pool["players"]:
        try:
            p = pool["players"][pool["rr"] % len(pool["players"])]
            pool["rr"] = (pool["rr"] + 1) % len(pool["players"])
            try:
                p.stop()
            except Exception:
                pass
            try:
                p.setPosition(0)
            except Exception:
                pass
            p.play()
            return
        except Exception as e:
            _disable_qt_sfx(f"QtMultimedia play failed: {e}")

    _spawn_sfx_process(sound_path, procs, max_simultaneous)


def _preload_explosion_sound() -> bool:
    return _preload_sfx(EXPLOSION_MP3, _EXPLODE_MAX_SIMULTANEOUS, "explode")


def _preload_pew_sound() -> bool:
    return _preload_sfx(PEW_MP3, _PEW_MAX_SIMULTANEOUS, "pew")


def _play_explosion_sound():
    """Best-effort, non-blocking playback of explode.mp3."""
    _play_sfx(EXPLOSION_MP3, _EXPLODE_MAX_SIMULTANEOUS, "explode", _EXPLODE_PROCS)


def _play_pew_sound():
    """Best-effort, non-blocking playback of pew.mp3."""
    _play_sfx(PEW_MP3, _PEW_MAX_SIMULTANEOUS, "pew", _PEW_PROCS)

def show_explosion(global_pos: QPoint) -> str:
    gif_path = EXPLOSION_GIF
    if not gif_path.exists():
        return f"explode.gif not found at {gif_path}"

    # Avoid too many simultaneous GIF decoders.
    if len(_active_explosions) >= _MAX_ACTIVE_EXPLOSIONS:
        _drop_oldest(_active_explosions)

    try:
        overlay = GifOverlay(gif_path, global_pos)
    except Exception as e:
        return f"Failed to start explosion: {e}"

    _active_explosions.append(overlay)
    overlay.finished.connect(lambda ov=overlay: _active_explosions.remove(ov) if ov in _active_explosions else None)
    overlay.show()
    overlay.movie.start()

    _play_explosion_sound()
    return None


# ---- Cannonball helpers ----

def _norm_point(global_pos: QPoint) -> tuple[float, float]:
    screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
    geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

    # Clamp within the screen so we stay in [0,1].
    x = max(geo.left(), min(int(global_pos.x()), geo.right() - 1))
    y = max(geo.top(), min(int(global_pos.y()), geo.bottom() - 1))

    nx = (x - geo.left()) / float(max(1, geo.width()))
    ny = (y - geo.top()) / float(max(1, geo.height()))
    return float(nx), float(ny)



def _denorm_point(nx: float, ny: float, reference_pos: QPoint = None) -> QPoint:
    ref = reference_pos or QPoint(0, 0)
    screen = QApplication.screenAt(ref) or QApplication.primaryScreen()
    geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

    x = geo.left() + int(nx * geo.width())
    y = geo.top() + int(ny * geo.height())

    # Clamp.
    x = max(geo.left(), min(x, geo.right() - 1))
    y = max(geo.top(), min(y, geo.bottom() - 1))
    return QPoint(int(x), int(y))


def _cannon_origin_for_screen_pos(reference_pos: QPoint) -> QPoint:
    screen = QApplication.screenAt(reference_pos) or QApplication.primaryScreen()
    geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()
    return QPoint(geo.left() + 60, geo.bottom() - 60)


def _extend_line_offscreen(start: QPoint, through: QPoint, *, margin: int = 160) -> QPoint:
    """Return a point beyond the screen bounds along the ray start->through."""
    screen = QApplication.screenAt(start) or QApplication.screenAt(through) or QApplication.primaryScreen()
    geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

    sx, sy = float(start.x()), float(start.y())
    tx, ty = float(through.x()), float(through.y())
    dx, dy = (tx - sx), (ty - sy)
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        dx, dy = 1.0, 0.0

    # Find t where (sx + t*dx, sy + t*dy) exits the screen rect.
    t_candidates = []

    # Vertical sides
    if abs(dx) > 1e-6:
        t_left = (geo.left() - sx) / dx
        t_right = ((geo.right() - 1) - sx) / dx
        t_candidates.extend([t_left, t_right])

    # Horizontal sides
    if abs(dy) > 1e-6:
        t_top = (geo.top() - sy) / dy
        t_bottom = ((geo.bottom() - 1) - sy) / dy
        t_candidates.extend([t_top, t_bottom])

    # Take the smallest t > 0 that gets us to a boundary, then push further by margin.
    t_exit = None
    for t in sorted(t_candidates):
        if t > 0:
            x = sx + t * dx
            y = sy + t * dy
            if geo.left() - 2 <= x <= geo.right() + 2 and geo.top() - 2 <= y <= geo.bottom() + 2:
                t_exit = t
                break

    if t_exit is None:
        t_exit = 1.0

    # Normalize direction and go beyond boundary.
    import math
    mag = math.hypot(dx, dy)
    ux, uy = dx / mag, dy / mag
    x2 = sx + t_exit * dx + ux * margin
    y2 = sy + t_exit * dy + uy * margin
    return QPoint(int(round(x2)), int(round(y2)))


def _offscreen_start_towards_target(target: QPoint, vx: float, vy: float, *, margin: int = 160) -> QPoint:
    """Return an offscreen start point so the ball flies along +v into target."""
    screen = QApplication.screenAt(target) or QApplication.primaryScreen()
    geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

    import math
    mag = math.hypot(vx, vy)
    if mag < 1e-6:
        vx, vy = 1.0, 0.0
        mag = 1.0
    ux, uy = vx / mag, vy / mag

    # Step backwards from target until we're outside the rect, then add margin.
    step = max(geo.width(), geo.height()) * 2
    x0 = float(target.x()) - ux * step
    y0 = float(target.y()) - uy * step

    # Ensure outside + margin.
    x0 -= ux * margin
    y0 -= uy * margin
    return QPoint(int(round(x0)), int(round(y0)))


# ---- Projectile helpers ----

def _screen_geo_for_pos(pos: QPoint):
    screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
    return screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()


def _solve_landing_time(y0: float, vy: float, g: float, y_land: float) -> float:
    # Solve: y0 + vy*t + 0.5*g*t^2 = y_land
    # => 0.5*g*t^2 + vy*t + (y0 - y_land) = 0
    import math
    a = 0.5 * g
    b = vy
    c = (y0 - y_land)
    if abs(a) < 1e-9:
        if abs(b) < 1e-9:
            return 0.0
        t = -c / b
        return max(0.0, t)

    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return 0.0
    s = math.sqrt(disc)
    t1 = (-b - s) / (2.0 * a)
    t2 = (-b + s) / (2.0 * a)
    t = max(t1, t2)
    return max(0.0, t)


def shoot_projectile_local_exit_right(start_global_pos: QPoint, vx: float, vy: float, g: float) -> str:
    geo = _screen_geo_for_pos(start_global_pos)
    sx, sy = _norm_point(start_global_pos)

    # Stop slightly beyond the right edge so it visually exits.
    x_end = 1.05  # local: travel slightly past right edge before disappearing
    if vx <= 0.0:
        return "Projectile vx must be > 0"

    t_end = (x_end - sx) / vx
    if t_end <= 0.0:
        return "Projectile already past right edge"

    try:
        proj = ProjectileOverlay(geo, sx, sy, vx, vy, g, t_end, allow_kill=False)
    except Exception as e:
        return f"Failed to start projectile: {e}"

    if len(_active_projectiles) >= _MAX_ACTIVE_PROJECTILES:
        _drop_oldest(_active_projectiles)
    _active_projectiles.append(proj)
    proj.finished.connect(lambda _p, pr=proj: _active_projectiles.remove(pr) if pr in _active_projectiles else None)
    proj.show()
    return None


def shoot_projectile_remote_arrive_left(
    sx: float,
    sy: float,
    vx: float,
    vy0: float,
    g: float,
    *,
    start_delay_ms: int = None,
    land_nx: float = None,
    land_ny: float = None,
    entry_ny: float = 0.5,
    color: QColor = None,
) -> str:
    """Remote projectile arriving from the left edge (left-to-right shooting).

    If land_nx/land_ny are provided, the projectile will land at that normalized
    location (randomized by the sender), instead of landing based on the sender's
    cat origin.
    """
    geo = QApplication.primaryScreen().availableGeometry()

    if vx <= 0.0:
        return "Projectile vx must be > 0"

    # Match the sender: local projectile runs until x=1.05 then disappears.
    x_exit = 1.05
    x_entry = -0.05

    # Time from sender start until it fully exits (x=1.05). Used for visual sync.
    t_exit = (x_exit - sx) / vx
    if t_exit < 0.0:
        t_exit = 0.0

    # Delay remote start so it visually syncs with the sender exiting the screen.
    if start_delay_ms is None:
        start_delay_ms = int(round(t_exit * 1000.0))
    else:
        start_delay_ms = max(0, int(start_delay_ms))

    # Always continue from the sender's exit state so the arc lines up across machines.
    # State at the moment it fully exits on the sender.
    y_exit = sy + vy0 * t_exit + 0.5 * g * t_exit * t_exit
    vy_exit = vy0 + g * t_exit

    # Remote starts slightly offscreen to the left using that same state.
    x0 = x_entry
    y0 = float(y_exit)
    vy_start = float(vy_exit)

    # If a landing Y is provided, land when y reaches that value (gives random landing x too).
    # Otherwise, default to landing back at the sender's original y (previous behavior).
    if land_ny is not None:
        try:
            y_land = float(land_ny)
        except Exception:
            y_land = sy
        y_land = max(0.05, min(0.95, y_land))
    else:
        y_land = sy

    t_land = _solve_landing_time(y0, vy_start, g, y_land)
    if t_land <= 0.05:
        t_land = 0.05

    # If the projectile would "land" before it ever becomes visible on this screen,
    # don't spawn it and (critically) don't explode.
    t_enter = (0.0 - x0) / vx  # x crosses 0.0 (enters the screen)
    t_exit_screen = (x_exit - x0) / vx  # x reaches the sender's exit point on this screen
    if t_land <= t_enter:
        return None

    # If it would land offscreen (after it has already exited), let it just fly across
    # and disappear at the screen-exit point; no explosion.
    t_end = min(t_land, t_exit_screen)

    def _spawn():
        try:
            proj = ProjectileOverlay(geo, x0, y0, vx, vy_start, g, t_end, color=color)
        except Exception as e:
            try:
                print(f"Failed to start remote projectile: {e}")
            except Exception:
                pass
            return

        if len(_active_projectiles) >= _MAX_ACTIVE_PROJECTILES:
            _drop_oldest(_active_projectiles)
        _active_projectiles.append(proj)
        proj.finished.connect(lambda _p, pr=proj: _active_projectiles.remove(pr) if pr in _active_projectiles else None)

        def _maybe_explode(p: QPoint, *, _geo=geo, _t_end=t_end, _t_land=t_land):
            # Only explode if the projectile actually lands on this screen (not offscreen).
            if _t_end >= (_t_land - 1e-6) and _geo.contains(p):
                show_explosion(p)

        proj.finished.connect(_maybe_explode)
        proj.show()

    QTimer.singleShot(int(start_delay_ms), _spawn)
    return None


def shoot_projectile_local_exit_left(start_global_pos: QPoint, vx: float, vy: float, g: float) -> str:
    """Shoot projectile from right to left, exiting left edge."""

    # Apply speed multiplier
    ptype = select_projectile_type()
    speed_mult = ptype.speed_multiplier
    vx = vx * speed_mult

    geo = _screen_geo_for_pos(start_global_pos)
    sx, sy = _norm_point(start_global_pos)

    # Stop slightly beyond the left edge so it visually exits.
    x_end = -0.05  # local: travel slightly past left edge before disappearing
    if vx >= 0.0:
        return "Projectile vx must be < 0 for right-to-left shooting"

    t_end = (x_end - sx) / vx
    if t_end <= 0.0:
        return "Projectile already past left edge"

    try:
        proj = ProjectileOverlay(geo, sx, sy, vx, vy, g, t_end, allow_kill=False)
    except Exception as e:
        return f"Failed to start projectile: {e}"

    if len(_active_projectiles) >= _MAX_ACTIVE_PROJECTILES:
        _drop_oldest(_active_projectiles)
    _active_projectiles.append(proj)
    proj.finished.connect(lambda _p, pr=proj: _active_projectiles.remove(pr) if pr in _active_projectiles else None)
    proj.show()
    return None


def shoot_projectile_remote_arrive_right(
    sx: float,
    sy: float,
    vx: float,
    vy0: float,
    g: float,
    *,
    start_delay_ms: int = None,
    land_nx: float = None,
    land_ny: float = None,
    entry_ny: float = 0.5,
    color: QColor = None,
) -> str:
    """Remote projectile arriving from the right edge (right-to-left shooting).

    If land_nx/land_ny are provided, the projectile will land at that normalized
    location (randomized by the sender), instead of landing based on the sender's
    cat origin.
    """
    geo = QApplication.primaryScreen().availableGeometry()

    if vx >= 0.0:
        return "Projectile vx must be < 0 for right-to-left shooting"

    # Match the sender: local projectile runs until x=-0.05 then disappears.
    x_exit = -0.05
    x_entry = 1.05

    # Time from sender start until it fully exits (x=-0.05). Used for visual sync.
    t_exit = (x_exit - sx) / vx
    if t_exit < 0.0:
        t_exit = 0.0

    # Delay remote start so it visually syncs with the sender exiting the screen.
    if start_delay_ms is None:
        start_delay_ms = int(round(t_exit * 1000.0))
    else:
        start_delay_ms = max(0, int(start_delay_ms))

    # Always continue from the sender's exit state so the arc lines up across machines.
    # State at the moment it fully exits on the sender.
    y_exit = sy + vy0 * t_exit + 0.5 * g * t_exit * t_exit
    vy_exit = vy0 + g * t_exit

    # Remote starts slightly offscreen to the right using that same state.
    x0 = x_entry
    y0 = float(y_exit)
    vy_start = float(vy_exit)

    # If a landing Y is provided, land when y reaches that value (gives random landing x too).
    # Otherwise, default to landing back at the sender's original y (previous behavior).
    if land_ny is not None:
        try:
            y_land = float(land_ny)
        except Exception:
            y_land = sy
        y_land = max(0.05, min(0.95, y_land))
    else:
        y_land = sy

    t_land = _solve_landing_time(y0, vy_start, g, y_land)
    if t_land <= 0.05:
        t_land = 0.05

    # If the projectile would "land" before it ever becomes visible on this screen,
    # don't spawn it and (critically) don't explode.
    t_enter = (1.0 - x0) / vx  # x crosses 1.0 (enters the screen from the right)
    t_exit_screen = (x_exit - x0) / vx  # x reaches the sender's exit point on this screen
    if t_land <= t_enter:
        return None

    # If it would land offscreen (after it has already exited), let it just fly across
    # and disappear at the screen-exit point; no explosion.
    t_end = min(t_land, t_exit_screen)

    def _spawn():
        try:
            proj = ProjectileOverlay(geo, x0, y0, vx, vy_start, g, t_end, color=color)
        except Exception as e:
            try:
                print(f"Failed to start remote projectile: {e}")
            except Exception:
                pass
            return

        if len(_active_projectiles) >= _MAX_ACTIVE_PROJECTILES:
            _drop_oldest(_active_projectiles)
        _active_projectiles.append(proj)
        proj.finished.connect(lambda _p, pr=proj: _active_projectiles.remove(pr) if pr in _active_projectiles else None)

        def _maybe_explode(p: QPoint, *, _geo=geo, _t_end=t_end, _t_land=t_land):
            # Only explode if the projectile actually lands on this screen (not offscreen).
            if _t_end >= (_t_land - 1e-6) and _geo.contains(p):
                show_explosion(p)

        proj.finished.connect(_maybe_explode)
        proj.show()

    QTimer.singleShot(int(start_delay_ms), _spawn)
    return None


def shoot_cannon_to(
    target_global_pos: QPoint,
    start_global_pos: QPoint = None,
    *,
    explode_on_land: bool = True,
    duration_ms: int = 900,
    arc_height: int = 220,
) -> str:
    # Choose a start position: bottom-left-ish of the screen containing the target.
    screen = QApplication.screenAt(target_global_pos) or QApplication.primaryScreen()
    geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

    if start_global_pos is None:
        start_global_pos = QPoint(geo.left() + 60, geo.bottom() - 60)

    try:
        ball = CannonBallOverlay(start_global_pos, target_global_pos, duration_ms=duration_ms, arc_height=arc_height)
    except Exception as e:
        return f"Failed to start cannonball: {e}"

    if len(_active_cannonballs) >= _MAX_ACTIVE_CANNONBALLS:
        _drop_oldest(_active_cannonballs)
    _active_cannonballs.append(ball)
    ball.finished.connect(lambda _p, b=ball: _active_cannonballs.remove(b) if b in _active_cannonballs else None)

    if explode_on_land:
        def _on_land(p: QPoint):
            # Explosion on landing.
            show_explosion(p)

        ball.finished.connect(_on_land)

    ball.show()
    return None


class ControlPanel(QWidget):
    def __init__(self, overlay: CatOverlay, server: MessageServer, client: MessageClient):
        super().__init__()
        self.overlay = overlay
        self.server = server
        self.client = client

        self.setWindowTitle("Sprite Control Panel")

        root = QVBoxLayout(self)

        # Speed
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        self.sld_speed = QSlider(Qt.Horizontal)
        self.sld_speed.setRange(0, 30)
        # default from current velocity magnitude
        self.sld_speed.setValue(abs(getattr(self.overlay, "_vel_x", 0)) or 3)
        self.sld_speed.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self.sld_speed)
        root.addLayout(speed_row)

        # Visibility
        self.chk_visible = QCheckBox("Show sprite overlay")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(self._on_visible)
        root.addWidget(self.chk_visible)

        # Running
        self.chk_running = QCheckBox("Animate / move")
        self.chk_running.setChecked(True)
        self.chk_running.toggled.connect(self._on_running)
        root.addWidget(self.chk_running)

        # Click-through
        self.chk_clickthrough = QCheckBox("Click-through overlay")
        self.chk_clickthrough.setChecked(True)
        self.chk_clickthrough.toggled.connect(self._on_clickthrough)
        root.addWidget(self.chk_clickthrough)

        # Shooting direction
        self.chk_direction = QCheckBox("Shoot right-to-left (unchecked: left-to-right)")
        self.chk_direction.setChecked(False)
        self.chk_direction.toggled.connect(self._on_direction_changed)
        root.addWidget(self.chk_direction)

        # Color customization
        projectile_color_row = QHBoxLayout()
        projectile_color_row.addWidget(QLabel("Projectile/Cannon color"))
        self.txt_projectile_color = QLineEdit()
        self.txt_projectile_color.setPlaceholderText("#RRGGBB / #RRGGBBAA / name / r,g,b")
        self.txt_projectile_color.setText(_PROJECTILE_COLOR.name(QColor.HexRgb))
        self.txt_projectile_color.editingFinished.connect(self._on_projectile_color)
        projectile_color_row.addWidget(self.txt_projectile_color)
        root.addLayout(projectile_color_row)

        # Shoot config
        shoot_row = QHBoxLayout()
        self.btn_shoot = QPushButton("Shoot")
        self.btn_shoot.clicked.connect(self._shoot)
        shoot_row.addWidget(self.btn_shoot)
        root.addLayout(shoot_row)

        # Network status / log
        root.addWidget(QLabel("Network Status / Log:"))
        self.lbl_net_status = QLabel("-")
        root.addWidget(self.lbl_net_status)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(140)
        root.addWidget(self.txt_log)

        self.server.status_changed.connect(self._append_log)
        self.client.status_changed.connect(self._append_log)
        self.server.message_received.connect(self._run_action)
        self.client.message_received.connect(self._run_action)

        # Buttons
        btn_row = QHBoxLayout()

        btn_quit = QPushButton("Quit")
        btn_quit.clicked.connect(QApplication.instance().quit)

        btn_row.addStretch(1)
        btn_row.addWidget(btn_quit)
        root.addLayout(btn_row)

        # Apply initial settings
        self._loading_settings = False
        self._settings_path = SETTINGS_PATH
        self._load_settings()
        self.overlay.set_speed(self.sld_speed.value())
        self.overlay.set_click_through(self.chk_clickthrough.isChecked())
        
        # Settings auto reload
        self._settings_reload_timer = QTimer(self)
        self._settings_reload_timer.timeout.connect(self._reload_settings_if_changed)
        self._settings_reload_timer.start(5000)  # Reload every 5 seconds
        self._last_settings_mtime = 0
        self._last_settings_text = None

    def _run_action(self, action_str: str):
        action_json = action_str.strip()
        try:
            action_json = json.loads(action_json)
        except Exception as e:
            self._append_log(f"Invalid action JSON: {e}")
            return

        action = action_json.get("action")

        if action == "cannon":
            # Parse remote color override if present (applies only to received projectile).
            color = _parse_color(action_json.get("projectile_color", ""))

        if action == "fire":
            x = action_json.get("x", 0)
            y = action_json.get("y", 0)
            err = show_explosion(QPoint(int(x), int(y)))
            if err:
                self._append_log(err)
                return
            self._append_log(f"Showing 'fire' action at ({x}, {y})")
            return

        if action == "cannon":
            # New mode: endpoint is determined by the projectile arc from the sender's cat origin.
            if "sx" in action_json and "sy" in action_json:
                try:
                    sx = float(action_json.get("sx"))
                    sy = float(action_json.get("sy"))
                    vx = float(action_json.get("vx"))
                    vy = float(action_json.get("vy"))
                    g = float(action_json.get("g"))
                except Exception as e:
                    self._append_log(f"Invalid cannon payload: {e}")
                    return

                delay_ms = action_json.get("delay_ms", None)
                direction = action_json.get("direction", "left_to_right")
                land_nx = action_json.get("land_nx", None)
                land_ny = action_json.get("land_ny", None)

                if direction == "right_to_left":
                    err = shoot_projectile_remote_arrive_right(
                        sx, sy, vx, vy, g,
                        start_delay_ms=delay_ms,
                        land_nx=land_nx,
                        land_ny=land_ny,
                        color=color,
                    )
                    if err:
                        self._append_log(err)
                        return
                    self._append_log("Projectile received (right->left)")
                else:
                    err = shoot_projectile_remote_arrive_left(
                        sx, sy, vx, vy, g,
                        start_delay_ms=delay_ms,
                        land_nx=land_nx,
                        land_ny=land_ny,
                        color=color,
                    )
                    if err:
                        self._append_log(err)
                        return
                    self._append_log("Projectile received (left->right)")
                return

            # Backward compatibility: old target-based cannon.
            if "nx" in action_json and "ny" in action_json:
                try:
                    nx = float(action_json.get("nx", 0.5))
                    ny = float(action_json.get("ny", 0.5))
                except Exception:
                    nx, ny = 0.5, 0.5
                target = _denorm_point(nx, ny)
            else:
                x = action_json.get("x", 0)
                y = action_json.get("y", 0)
                target = QPoint(int(x), int(y))

            err = shoot_cannon_to(target, explode_on_land=True)
            if err:
                self._append_log(err)
                return

            self._append_log(f"Cannonball received; landing at ({target.x()}, {target.y()})")
            return

    def _append_log(self, text: str):
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        line = f"[{ts}] {text}"
        self.lbl_net_status.setText(text)
        self.txt_log.append(line)

    def _on_visible(self, visible: bool):
        if visible:
            self.overlay.show()
        else:
            self.overlay.hide()
        self._maybe_save_settings()

    def _on_direction_changed(self, right_to_left: bool):
        global _SHOOT_DIRECTION
        old = _SHOOT_DIRECTION
        _SHOOT_DIRECTION = "right_to_left" if right_to_left else "left_to_right"
        if old != _SHOOT_DIRECTION:
            self._append_log(f"Shooting direction: {_SHOOT_DIRECTION}")
        self._maybe_save_settings()

    def _on_projectile_color(self):
        color = _parse_color(self.txt_projectile_color.text())
        if color is None:
            self._append_log("Invalid color")
            return
        set_projectile_color(color)
        self.txt_projectile_color.setText(color.name(QColor.HexRgb))
        self._maybe_save_settings()

    def _on_speed_changed(self, value: int):
        self.overlay.set_speed(value)
        self._maybe_save_settings()

    def _on_running(self, running: bool):
        self.overlay.set_running(running)
        self._maybe_save_settings()

    def _on_clickthrough(self, enabled: bool):
        self.overlay.set_click_through(enabled)
        self._maybe_save_settings()

    def _maybe_save_settings(self):
        if getattr(self, "_loading_settings", False):
            return
        self._save_settings()
        
    def _reload_settings_if_changed(self):
        """Check if settings file has been modified and reload if needed"""
        try:
            if not self._settings_path.exists():
                return
                
            # Get current modification time
            current_mtime = self._settings_path.stat().st_mtime
            
            # If file has been modified since last check
            if current_mtime > self._last_settings_mtime:
                current_text = self._settings_path.read_text(encoding="utf-8")
                if current_text != self._last_settings_text:
                    self._last_settings_text = current_text
                    self._last_settings_mtime = current_mtime
                    self._load_settings()
                # self._append_log("Settings reloaded from file")
        except Exception as e:
            # Don't spam the log with errors on every failed check
            pass

    def _load_settings(self):
        data = {}
        try:
            if self._settings_path.exists():
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        print(data)

        self._loading_settings = True
        try:
            if "speed" in data:
                self.sld_speed.setValue(int(data.get("speed")))
            if "visible" in data:
                self.chk_visible.setChecked(bool(data.get("visible")))
            if "running" in data:
                self.chk_running.setChecked(bool(data.get("running")))
            if "click_through" in data:
                self.chk_clickthrough.setChecked(bool(data.get("click_through")))
            if "right_to_left" in data:
                self.chk_direction.setChecked(bool(data.get("right_to_left")))
            if "projectile_color" in data:
                self.txt_projectile_color.setText(str(data.get("projectile_color")))
                self._on_projectile_color()
        finally:
            self._loading_settings = False

        # Apply non-signal changes explicitly.
        self._on_visible(self.chk_visible.isChecked())
        self._on_running(self.chk_running.isChecked())
        self._on_clickthrough(self.chk_clickthrough.isChecked())
        self._on_direction_changed(self.chk_direction.isChecked())

    def _save_settings(self):
        data = {
            "speed": int(self.sld_speed.value()),
            "visible": bool(self.chk_visible.isChecked()),
            "running": bool(self.chk_running.isChecked()),
            "click_through": bool(self.chk_clickthrough.isChecked()),
            "right_to_left": bool(self.chk_direction.isChecked()),
            "projectile_color": self.txt_projectile_color.text().strip(),
        }
        try:
            self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _shoot(self):
        # Fire from a random cat (if overlay supports it); otherwise use overlay center.
        if hasattr(self.overlay, "random_cat_center_global_with_cat"):
            try:
                cat, center_global = self.overlay.random_cat_center_global_with_cat()
                if cat is not None:
                    try:
                        self.overlay.trigger_shoot(cat)
                    except Exception:
                        pass
                    try:
                        center_global = self.overlay.cannon_muzzle_global(cat)
                    except Exception:
                        pass
            except Exception:
                center_local = QPoint(self.overlay.width() // 2, self.overlay.height() // 2)
                center_global = self.overlay.mapToGlobal(center_local)
        elif hasattr(self.overlay, "random_cat_center_global"):
            try:
                center_global = self.overlay.random_cat_center_global()
            except Exception:
                center_local = QPoint(self.overlay.width() // 2, self.overlay.height() // 2)
                center_global = self.overlay.mapToGlobal(center_local)
        else:
            center_local = QPoint(self.overlay.width() // 2, self.overlay.height() // 2)
            center_global = self.overlay.mapToGlobal(center_local)
        try:
            # Stop moving briefly while shooting.
            try:
                self.overlay.pause_for_shot(350)
            except Exception:
                pass

            try:
                if hasattr(self.overlay, "trigger_shoot_near_global"):
                    self.overlay.trigger_shoot_near_global(center_global)
            except Exception:
                pass

            on_cat_clicked(center_global)
        except Exception as e:
            self._append_log(f"Shoot failed: {e}")

    def closeEvent(self, event):
        self._save_settings()
        # Closing the control panel exits the app
        QApplication.instance().quit()
        event.accept()


def load_frames(folder: str) -> list[QPixmap]:
    # Put frame PNGs in ./frames: 000.png, 001.png, ...
    paths = sorted(Path(folder).glob("*.png"))
    frames = [QPixmap(str(p)) for p in paths]
    if not frames or any(f.isNull() for f in frames):
        raise RuntimeError("No valid PNG frames found in ./frames")

    # Scale frames to adjust the cat size.
    scale = float(globals().get("CAT_SCALE", 1.0))
    if scale > 0 and abs(scale - 1.0) > 1e-6:
        scaled_frames: list[QPixmap] = []
        for f in frames:
            w = max(1, int(round(f.width() * scale)))
            h = max(1, int(round(f.height() * scale)))
            sf = f.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled_frames.append(sf)
        frames = scaled_frames

    return frames


def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't send traffic; just asks OS to choose a route/interface
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _first_ipv4(addresses: list[bytes]) -> str:
    for a in addresses or []:
        if len(a) == 4:
            return socket.inet_ntoa(a)
    return None


# Auto-connect client to all discovered peers (deduped by host/port).


# When the cat is clicked, the projectile direction is determined by _SHOOT_DIRECTION.
# The endpoint on the remote is NOT the click position; it is determined by the natural arc
# from the cat's origin.
def on_cat_clicked(global_pos: QPoint):
    global _SHOOT_DIRECTION

    _play_pew_sound()

    # Sender cat origin in normalized coordinates.
    sx, sy = _norm_point(global_pos)

    import math

    # Randomize arc per shot (shared with peer via payload).
    # g: normalized units/s^2 (positive, y increases downward)
    g = random.uniform(1.9, 3.2)

    # Peak height as a fraction of screen height (higher => larger arc).
    # Increased range to make the arc noticeably higher.
    peak_h = random.uniform(0.38, 0.62)

    # Initial vertical velocity that yields the chosen peak height.
    vy = -math.sqrt(max(0.0, 2.0 * g * peak_h))

    if _SHOOT_DIRECTION == "right_to_left":
        # Slightly slower horizontal speed to steepen the arc.
        vx = -random.uniform(0.65, 1.15)  # Negative velocity for leftward motion
        x_exit = -0.05

        t_exit = (x_exit - sx) / vx
        if t_exit < 0.0:
            t_exit = 0.0
        delay_ms = int(round(t_exit * 1000.0))

        # Choose a landing Y that is reachable from the exit state (so the remote arc stays aligned).
        y_exit = sy + vy * t_exit + 0.5 * g * t_exit * t_exit
        vy_exit = vy + g * t_exit
        y_min_exit = y_exit - (vy_exit * vy_exit) / (2.0 * g) if g > 1e-9 else y_exit

        low = max(0.05, min(0.95, y_min_exit + 0.02))
        high = 0.90
        if low >= high:
            high = min(0.95, low + 0.10)
        land_ny = random.uniform(low, high)
        data = {
            "action": "cannon",
            "sx": sx,
            "sy": sy,
            "vx": vx,
            "vy": vy,
            "g": g,
            "delay_ms": delay_ms,
            "direction": "right_to_left",
            "land_ny": land_ny,
            "projectile_color": _PROJECTILE_COLOR.name(QColor.HexRgb),
        }
        msg = json.dumps(data)

        # Local effect: start at the cat and exit the local screen to the left (no explosion).
        err = shoot_projectile_local_exit_left(global_pos, vx, vy, g)
        if err:
            try:
                panel._append_log(err)
            except Exception:
                pass
    else:
        # Slightly slower horizontal speed to steepen the arc.
        vx = random.uniform(0.65, 1.15)  # Positive velocity for rightward motion
        x_exit = 1.05

        t_exit = (x_exit - sx) / vx
        if t_exit < 0.0:
            t_exit = 0.0
        delay_ms = int(round(t_exit * 1000.0))

        # Choose a landing Y that is reachable from the exit state (so the remote arc stays aligned).
        y_exit = sy + vy * t_exit + 0.5 * g * t_exit * t_exit
        vy_exit = vy + g * t_exit
        y_min_exit = y_exit - (vy_exit * vy_exit) / (2.0 * g) if g > 1e-9 else y_exit

        low = max(0.05, min(0.95, y_min_exit + 0.02))
        high = 0.90
        if low >= high:
            high = min(0.95, low + 0.10)
        land_ny = random.uniform(low, high)
        data = {
            "action": "cannon",
            "sx": sx,
            "sy": sy,
            "vx": vx,
            "vy": vy,
            "g": g,
            "delay_ms": delay_ms,
            "direction": "left_to_right",
            "land_ny": land_ny,
            "projectile_color": _PROJECTILE_COLOR.name(QColor.HexRgb),
        }
        msg = json.dumps(data)

        # Local effect: start at the cat and exit the local screen to the right (no explosion).
        err = shoot_projectile_local_exit_right(global_pos, vx, vy, g)
        if err:
            try:
                panel._append_log(err)
            except Exception:
                pass

    # Send to peers
    server.broadcast(msg)
    client.send(msg)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Preload audio to avoid first-play lag.
    _preload_explosion_sound()
    _preload_pew_sound()

    # frames = load_frames("frames")
    w = CatOverlay()

    # w.move(200, 200)
    w.show()

    # Listen on all interfaces so peers can connect.
    server = MessageServer()
    client = MultiMessageClient()
    server.start(get_lan_ip(), 50505)

    # Zeroconf peer discovery + advertising.
    zc = ZeroConfP2P(port=50505)
    zc.start()

    def on_peer(name: str, host: str, port: int):
        client.connect_to(host, port)

    zc.peer_found.connect(on_peer)

    # Clean up zeroconf on exit.
    app.aboutToQuit.connect(zc.close)

    # w.clicked.connect(on_cat_clicked)

    panel = ControlPanel(w, server, client)
    panel.show()

    zc.status_changed.connect(panel._append_log)

    # --- Global hotkey: press 's' anywhere to shoot (does not block typing in other apps) ---
    _hotkey_listener = None

    def _hotkey_shoot():
        # Fire from the sprite overlay's current center position.
        # center_local = QPoint(w.width() // 2, w.height() // 2)
        try:
            cat, center_global = w.random_cat_center_global_with_cat()
            if cat is not None:
                try:
                    w.trigger_shoot(cat)
                except Exception:
                    pass
                try:
                    center_global = w.cannon_muzzle_global(cat)
                except Exception:
                    pass
        except Exception:
            center_global = w.random_cat_center_global()
        try:
            try:
                w.pause_for_shot(350)
            except Exception:
                pass
            try:
                w.trigger_shoot_near_global(center_global)
            except Exception:
                pass
            on_cat_clicked(center_global)
        except Exception as e:
            try:
                panel._append_log(f"Hotkey shoot failed: {e}")
            except Exception:
                pass

    def _on_global_key_press(key):
        # Only react to printable 's'/'S' keys.
        try:
            ch = key.char
        except Exception:
            return

        if ch not in ("s", "S"):
            return

        # Debounce to avoid key repeat flooding.
        global _LAST_HOTKEY_SHOT_MS
        now_ms = QDateTime.currentMSecsSinceEpoch()
        if now_ms - int(_LAST_HOTKEY_SHOT_MS) < 200:
            return
        _LAST_HOTKEY_SHOT_MS = now_ms

        # IMPORTANT: this callback is coming from pynput's background thread.
        # QTimer.singleShot without a receiver schedules on the *current* thread's event loop
        # (which the pynput thread doesn't have), so it may never fire.
        # Provide a QObject that lives on the Qt GUI thread so the call is queued correctly.
        QTimer.singleShot(0, w, _hotkey_shoot)

    if _PYNPUT_AVAILABLE:
        try:
            # suppress=False (default) so keypresses still reach other applications.
            _hotkey_listener = _pynput_keyboard.Listener(on_press=_on_global_key_press, suppress=False)
            _hotkey_listener.daemon = True
            _hotkey_listener.start()
            panel._append_log("Global hotkey enabled: press 's' to shoot")
        except Exception as e:
            panel._append_log(f"Global hotkey failed to start: {e}")
    else:
        panel._append_log("Global hotkey unavailable (install pynput).")

    def _stop_hotkey():
        try:
            if _hotkey_listener is not None:
                _hotkey_listener.stop()
        except Exception:
            pass

    app.aboutToQuit.connect(_stop_hotkey)

    sys.exit(app.exec())
