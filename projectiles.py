"""
Multi-Projectile System for shoot.py

Implements three new projectile types with unique behaviors:
- Plane: 30% spawn, 1.4x speed
- Missile: 20% spawn, 1.8x speed (fastest)
- Flashbang: 40% spawn, 3-second white-screen effect
- Default: 10% spawn, normal speed

Usage:
    from projectiles import ProjectileType, select_projectile_type, FlashbangOverlay
    
    ptype = select_projectile_type()  # Random selection based on probabilities
    speed_mult = ptype.speed_multiplier
    vx = vx * speed_mult  # Apply to projectile velocity
"""

import sys
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QPixmap, QColor
from PySide6.QtCore import Qt, QTimer, QDateTime, Signal
from PySide6.QtWidgets import QWidget, QApplication
import random


# ============================================================================
# Asset Paths - Update these to match your project structure
# ============================================================================

ASSET_DIR = Path(__file__).resolve().parent / "assets"

# Default projectile assets
PROJECTILE_PNG = ASSET_DIR / "sprites" / "projectile.png"
EXPLOSION_MP3 = ASSET_DIR / "sounds" / "explode.mp3"

# Plane projectile assets
PLANE_PNG = ASSET_DIR / "sprites" / "plane.png"
PLANE_SOUND = ASSET_DIR / "sounds" / "plane_sound.mp3"

# Missile projectile assets
MISSILE_PNG = ASSET_DIR / "sprites" / "missile.png"
MISSILE_SOUND = ASSET_DIR / "sounds" / "missile_sound.mp3"

# Flashbang projectile assets
FLASHBANG_SOUND = ASSET_DIR / "sounds" / "flashbang_sound.mp3"


# ============================================================================
# Projectile Type Definition
# ============================================================================

class ProjectileType(Enum):
    """
    Projectile types with spawn probabilities and speed multipliers.
    
    Attributes:
        name_str: Display name
        spawn_chance: Probability (0.0-1.0) of being selected
        speed_multiplier: Velocity multiplier (1.0 = normal speed)
    """
    DEFAULT = ("default", 0.10, 1.0)      # 10% spawn, 1.0x speed
    PLANE = ("plane", 0.30, 1.4)          # 30% spawn, 1.4x speed (2nd fastest)
    MISSILE = ("missile", 0.20, 1.8)      # 20% spawn, 1.8x speed (fastest)
    FLASHBANG = ("flashbang", 0.40, 1.0)  # 40% spawn, normal speed
    
    @property
    def name_str(self) -> str:
        """Return display name of projectile type."""
        return self.value[0]
    
    @property
    def spawn_chance(self) -> float:
        """Return spawn probability (0.0-1.0)."""
        return self.value[1]
    
    @property
    def speed_multiplier(self) -> float:
        """Return velocity multiplier."""
        return self.value[2]


# ============================================================================
# Pixmap Caches - One per projectile type
# ============================================================================

# Default projectile caching
PROJECTILE_PIX: Optional[QPixmap] = None
PROJECTILE_SCALED: dict = {}
PROJECTILE_TINTED: dict = {}

# Plane caching
PLANE_PIX: Optional[QPixmap] = None
PLANE_SCALED: dict = {}
PLANE_TINTED: dict = {}

# Missile caching
MISSILE_PIX: Optional[QPixmap] = None
MISSILE_SCALED: dict = {}
MISSILE_TINTED: dict = {}

# Flashbang caching (uses default sprite)
FLASHBANG_PIX: Optional[QPixmap] = None
FLASHBANG_SCALED: dict = {}
FLASHBANG_TINTED: dict = {}


# ============================================================================
# Projectile Type Selection
# ============================================================================

def select_projectile_type() -> ProjectileType:
    """
    Randomly select a projectile type based on spawn probabilities.
    
    Spawn distribution:
        - Flashbang: 40%
        - Plane: 30%
        - Missile: 20%
        - Default: 10%
    
    Returns:
        ProjectileType: One of DEFAULT, PLANE, MISSILE, or FLASHBANG
    
    Example:
        >>> ptype = select_projectile_type()
        >>> print(ptype.name_str)  # "missile" or "plane" or ...
        >>> speed_mult = ptype.speed_multiplier  # 1.8 or 1.4 or 1.0
    """
    rand = random.random()
    cumulative = 0.0
    
    # Check types in order (order doesn't matter for correctness, just convention)
    for ptype in [ProjectileType.FLASHBANG, ProjectileType.PLANE,
                  ProjectileType.MISSILE, ProjectileType.DEFAULT]:
        cumulative += ptype.spawn_chance
        if rand < cumulative:
            return ptype
    
    return ProjectileType.DEFAULT  # Fallback (should never reach)


# ============================================================================
# Pixmap Loading & Caching
# ============================================================================

def get_projectile_pixmap(target_size: int, 
                         ptype: ProjectileType = ProjectileType.DEFAULT) -> Optional[QPixmap]:
    """
    Load and cache projectile pixmap for the given type.
    
    Args:
        target_size: Desired size in pixels (will be scaled proportionally)
        ptype: ProjectileType to load (default: DEFAULT)
    
    Returns:
        QPixmap object or None if file not found
    
    Example:
        >>> pix = get_projectile_pixmap(20, ProjectileType.PLANE)
        >>> # Next call uses cached result
        >>> pix2 = get_projectile_pixmap(20, ProjectileType.PLANE)
    """
    if ptype == ProjectileType.PLANE:
        return _get_plane_pixmap(target_size)
    elif ptype == ProjectileType.MISSILE:
        return _get_missile_pixmap(target_size)
    elif ptype == ProjectileType.FLASHBANG:
        # Flashbang uses default sprite
        return _get_projectile_pixmap(target_size)
    else:  # DEFAULT
        return _get_projectile_pixmap(target_size)


def _get_projectile_pixmap(target_size: int) -> Optional[QPixmap]:
    """Load default projectile.png pixmap with caching."""
    global PROJECTILE_PIX, PROJECTILE_SCALED
    target_size = max(1, int(target_size))
    
    # Check cache first
    cached = PROJECTILE_SCALED.get(target_size)
    if cached is not None and not cached.isNull():
        return cached
    
    # Load base pixmap if not already loaded
    if PROJECTILE_PIX is None:
        if PROJECTILE_PNG.exists():
            pix = QPixmap(str(PROJECTILE_PNG))
            PROJECTILE_PIX = pix if not pix.isNull() else None
        else:
            PROJECTILE_PIX = None
    
    if PROJECTILE_PIX is None or PROJECTILE_PIX.isNull():
        return None
    
    # Scale and cache
    scaled = PROJECTILE_PIX.scaled(int(target_size), int(target_size),
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
    PROJECTILE_SCALED[target_size] = scaled
    return scaled


def _get_plane_pixmap(target_size: int) -> Optional[QPixmap]:
    """Load plane.png pixmap with caching. Falls back to default sprite."""
    global PLANE_PIX, PLANE_SCALED
    target_size = max(1, int(target_size))
    
    cached = PLANE_SCALED.get(target_size)
    if cached is not None and not cached.isNull():
        return cached
    
    if PLANE_PIX is None:
        if PLANE_PNG.exists():
            pix = QPixmap(str(PLANE_PNG))
            PLANE_PIX = pix if not pix.isNull() else None
        else:
            PLANE_PIX = None
    
    if PLANE_PIX is None or PLANE_PIX.isNull():
        # Fallback to default sprite
        return _get_projectile_pixmap(target_size)
    
    scaled = PLANE_PIX.scaled(int(target_size), int(target_size),
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
    PLANE_SCALED[target_size] = scaled
    return scaled


def _get_missile_pixmap(target_size: int) -> Optional[QPixmap]:
    """Load missile.png pixmap with caching. Falls back to default sprite."""
    global MISSILE_PIX, MISSILE_SCALED
    target_size = max(1, int(target_size))
    
    cached = MISSILE_SCALED.get(target_size)
    if cached is not None and not cached.isNull():
        return cached
    
    if MISSILE_PIX is None:
        if MISSILE_PNG.exists():
            pix = QPixmap(str(MISSILE_PNG))
            MISSILE_PIX = pix if not pix.isNull() else None
        else:
            MISSILE_PIX = None
    
    if MISSILE_PIX is None or MISSILE_PIX.isNull():
        # Fallback to default sprite
        return _get_projectile_pixmap(target_size)
    
    scaled = MISSILE_PIX.scaled(int(target_size), int(target_size),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
    MISSILE_SCALED[target_size] = scaled
    return scaled


# ============================================================================
# Sound Playback
# ============================================================================

def play_projectile_sound(ptype: ProjectileType) -> None:
    """
    Play sound for projectile impact based on type.
    
    Uses subprocess for cross-platform playback, avoiding PipeWire hangs.
    Falls back gracefully if assets or players unavailable.
    
    Args:
        ptype: ProjectileType to play sound for
    
    Example:
        >>> play_projectile_sound(ProjectileType.PLANE)
    """
    if ptype == ProjectileType.PLANE and PLANE_SOUND.exists():
        play_sound_subprocess(PLANE_SOUND)
    elif ptype == ProjectileType.MISSILE and MISSILE_SOUND.exists():
        play_sound_subprocess(MISSILE_SOUND)
    elif ptype == ProjectileType.FLASHBANG and FLASHBANG_SOUND.exists():
        play_sound_subprocess(FLASHBANG_SOUND)
    else:
        # Default to explosion sound
        if EXPLOSION_MP3.exists():
            play_sound_subprocess(EXPLOSION_MP3)


def play_sound_subprocess(sound_path: Path) -> None:
    """
    Play audio via subprocess (cross-platform, avoids PipeWire hangs).
    
    Supports:
        - macOS: afplay (native)
        - Windows: powershell Media.SoundPlayer
        - Linux: mpv, ffplay, paplay, aplay (in preference order)
    
    Args:
        sound_path: Path to audio file
    
    Example:
        >>> play_sound_subprocess(Path("sounds/explode.mp3"))
    """
    if not sound_path.exists():
        return
    
    try:
        if sys.platform == "darwin":
            # macOS: use afplay
            subprocess.Popen(["afplay", str(sound_path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            # Windows: use powershell
            subprocess.Popen(["powershell", "-c",
                            f'(New-Object Media.SoundPlayer "{sound_path}").PlaySync()'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Linux: try common players in preference order
            for cmd in ["mpv", "ffplay", "paplay", "aplay"]:
                try:
                    subprocess.Popen([cmd, str(sound_path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
                except FileNotFoundError:
                    continue
    except Exception:
        pass  # Best-effort: fail silently


# ============================================================================
# Flashbang Visual Effect
# ============================================================================

class FlashbangOverlay(QWidget):
    """
    White-screen overlay for flashbang projectiles.
    
    Displays a full-screen white flash that fades out over 3 seconds,
    blinding the player temporarily.
    
    Example:
        >>> overlay = FlashbangOverlay(duration_ms=3000)
        >>> overlay.finished.connect(cleanup_callback)
        >>> overlay.show()
    """
    
    finished = Signal()
    """Emitted when fade-out completes."""
    
    def __init__(self, duration_ms: int = 3000, parent=None):
        """
        Initialize flashbang overlay.
        
        Args:
            duration_ms: How long to display white screen before fading (milliseconds)
            parent: Parent QWidget (optional)
        """
        super().__init__(parent)
        
        # Set window properties for full-screen overlay
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                          Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("background-color: white;")
        
        # Cover entire screen
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.setGeometry(geo)
        
        self.duration_ms = max(100, int(duration_ms))
        self.t0_ms = QDateTime.currentMSecsSinceEpoch()
        
        # Timer for fade-out effect
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)  # Update every 50ms for smooth fade
    
    def tick(self):
        """
        Update fade-out progress and close when complete.
        
        Called every 50ms to update transparency gradient.
        """
        now = QDateTime.currentMSecsSinceEpoch()
        elapsed = now - self.t0_ms
        
        if elapsed >= self.duration_ms:
            # Fade complete
            self.timer.stop()
            self.finished.emit()
            self.close()
            self.deleteLater()
            return
        
        # Calculate alpha: fade from 255 (opaque) to 0 (transparent)
        alpha = int(255 * (1.0 - elapsed / self.duration_ms))
        self.setStyleSheet(f"background-color: rgba(255, 255, 255, {alpha});")


# ============================================================================
# Asset Directory Structure (Reference)
# ============================================================================

"""
Expected directory structure for assets:

assets/
├── sprites/
│   ├── projectile.png          # Default projectile (required)
│   ├── plane.png               # Plane sprite (optional, falls back to default)
│   └── missile.png             # Missile sprite (optional, falls back to default)
├── sounds/
│   ├── explode.mp3             # Default explosion (required)
│   ├── plane_sound.mp3         # Plane flyby sound (optional)
│   ├── missile_sound.mp3       # Missile impact sound (optional)
│   └── flashbang_sound.mp3     # Flashbang blast sound (optional)
└── anim/
    └── explode.gif             # Explosion animation (required)

If a sprite or sound file is missing, the system gracefully falls back:
- Missing plane.png → uses default projectile sprite
- Missing missile.png → uses default projectile sprite
- Missing plane_sound.mp3 → plays default explosion sound
- Missing missile_sound.mp3 → plays default explosion sound
- Missing flashbang_sound.mp3 → plays default explosion sound
"""


if __name__ == "__main__":
    # Quick test: verify projectile type selection probabilities
    print("Testing ProjectileType selection (1000 samples):")
    counts = {ptype: 0 for ptype in ProjectileType}
    
    for _ in range(1000):
        ptype = select_projectile_type()
        counts[ptype] += 1
    
    for ptype in ProjectileType:
        actual = counts[ptype] / 1000
        expected = ptype.spawn_chance
        print(f"  {ptype.name_str:10s}: {actual:.1%} (expected {expected:.1%})")
    
    # Test pixmap loading
    print("\nTesting pixmap loading:")
    for ptype in ProjectileType:
        pix = get_projectile_pixmap(20, ptype)
        status = "OK" if pix and not pix.isNull() else "Missing (fallback used)"
        print(f"  {ptype.name_str:10s}: {status}")
    
    # Test sound existence
    print("\nTesting sound files:")
    sound_map = {
        ProjectileType.DEFAULT: EXPLOSION_MP3,
        ProjectileType.PLANE: PLANE_SOUND,
        ProjectileType.MISSILE: MISSILE_SOUND,
        ProjectileType.FLASHBANG: FLASHBANG_SOUND,
    }
    for ptype, path in sound_map.items():
        status = "Found" if path.exists() else "Missing"
        print(f"  {ptype.name_str:10s}: {status}")
