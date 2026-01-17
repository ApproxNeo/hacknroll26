import math
import random
import sys

from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal
from PySide6.QtGui import (
    QPainter, QPixmap, QColor, QPen, QBrush, 
    QPainterPath, QPainterPathStroker, QPolygon, QRegion
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QCheckBox, QSlider, QSpinBox
)

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
        self.hit_corner_this_jump = False  # NEW: Track if corner was hit during this jump
        
        # Animation
        self.anim_frame = 0
        
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
        
        # NEW: Check if cat went off screen and flip to random edge
        self._ensure_on_screen(screen_rect)
        
        # Teleport to random edge periodically
        if self.teleport_timer <= 0:
            self._teleport_to_random_edge(screen_rect)
            self.teleport_timer = random.randint(8000, 15000)
    
    def _update_bottom_edge(self, screen_rect: QRect):
        """Cat walking on bottom edge"""
        ground_y = screen_rect.bottom() - self.height
        
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
                    self.x = screen_rect.left()
                elif min_dist == dist_to_right:
                    # Flip to right edge
                    self.edge = Cat.RIGHT
                    self.x = screen_rect.right() - self.width
                else:
                    # Flip to top edge
                    self.edge = Cat.TOP
                    self.y = screen_rect.top()
                
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return
            
            if new_y >= ground_y:
                new_y = ground_y
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # NEW: Reset on landing
            self.y = new_y
        else:
            self.y = ground_y
        
        self.x += self.speed * self.facing
        
        if random.random() < 0.05:  # Increased from 0.02
            self.facing = -self.facing  # Flip direction instead of random choice
    
    def _update_top_edge(self, screen_rect: QRect):
        """Cat walking upside down on top edge"""
        ground_y = screen_rect.top()
        
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
                    self.x = screen_rect.left()
                elif min_dist == dist_to_right:
                    # Flip to right edge
                    self.edge = Cat.RIGHT
                    self.x = screen_rect.right() - self.width
                else:
                    # Flip to bottom edge
                    self.edge = Cat.BOTTOM
                    self.y = screen_rect.bottom() - self.height
                
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return
            
            if new_y <= ground_y:
                new_y = ground_y
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # NEW: Reset on landing
            self.y = new_y
        else:
            self.y = ground_y
        
        self.x += self.speed * self.facing
        
        # Check if walked off left or right side
        if self.x + self.width < screen_rect.left():
            # Walked off left side, spawn on left wall
            self.edge = Cat.LEFT
            self.x = screen_rect.left()
        elif self.x > screen_rect.right():
            # Walked off right side, spawn on right wall
            self.edge = Cat.RIGHT
            self.x = screen_rect.right() - self.width
        
        if random.random() < 0.05:  # Increased from 0.02
            self.facing = -self.facing  # Flip direction instead of random choice
    
    def _update_left_edge(self, screen_rect: QRect):
        """Cat walking on left edge"""
        ground_x = screen_rect.left()
        
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
                    self.y = screen_rect.top()
                elif min_dist == dist_to_bottom:
                    # Flip to bottom edge
                    self.edge = Cat.BOTTOM
                    self.y = screen_rect.bottom() - self.height
                else:
                    # Flip to right edge
                    self.edge = Cat.RIGHT
                    self.x = screen_rect.right() - self.width
                
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return
            
            if new_x >= ground_x:
                new_x = ground_x
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # NEW: Reset on landing
            self.x = new_x
        else:
            self.x = ground_x
        
        self.y += self.speed * self.facing
        
        # Check if walked off top or bottom
        if self.y + self.height < screen_rect.top():
            # Walked off top, spawn on top wall
            self.edge = Cat.TOP
            self.y = screen_rect.top()
        elif self.y > screen_rect.bottom():
            # Walked off bottom, spawn on bottom wall
            self.edge = Cat.BOTTOM
            self.y = screen_rect.bottom() - self.height
        
        if random.random() < 0.05:  # Increased from 0.02
            self.facing = -self.facing  # Flip direction instead of random choice
    
    def _update_right_edge(self, screen_rect: QRect):
        """Cat walking on right edge"""
        ground_x = screen_rect.right() - self.width
        
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
                    self.y = screen_rect.top()
                elif min_dist == dist_to_bottom:
                    # Flip to bottom edge
                    self.edge = Cat.BOTTOM
                    self.y = screen_rect.bottom() - self.height
                else:
                    # Flip to left edge
                    self.edge = Cat.LEFT
                    self.x = screen_rect.left()
                
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False
                return
            
            if new_x <= ground_x:
                new_x = ground_x
                self.is_jumping = False
                self.jump_vel = 0
                self.hit_corner_this_jump = False  # NEW: Reset on landing
            self.x = new_x
        else:
            self.x = ground_x
        
        self.y += self.speed * self.facing
        
        # Check if walked off top or bottom
        if self.y + self.height < screen_rect.top():
            # Walked off top, spawn on top wall
            self.edge = Cat.TOP
            self.y = screen_rect.top()
        elif self.y > screen_rect.bottom():
            # Walked off bottom, spawn on bottom wall
            self.edge = Cat.BOTTOM
            self.y = screen_rect.bottom() - self.height
        
        if random.random() < 0.05:  # Increased from 0.02
            self.facing = -self.facing  # Flip direction instead of random choice
    
    # NEW: Check if cat hits a corner
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
    
    # NEW: Flip to random edge if off screen
    def _flip_to_random_edge(self, screen_rect: QRect):
        """Flip cat to a random edge when it goes off screen"""
        self.edge = random.choice([self.BOTTOM, self.TOP, self.LEFT, self.RIGHT])
        
        if self.edge == self.BOTTOM:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = screen_rect.bottom() - self.height
        elif self.edge == self.TOP:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = screen_rect.top()
        elif self.edge == self.LEFT:
            self.x = screen_rect.left()
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))
        elif self.edge == self.RIGHT:
            self.x = screen_rect.right() - self.width
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))
        
        self.facing = random.choice([1, -1])
        self.reset_jump_state()  # NEW: Reset jump when flipping
    
    def _clamp_to_screen(self, screen_rect: QRect):
        """Force cat position to stay within screen boundaries"""
        # Clamp X position
        if self.x < screen_rect.left():
            self.x = screen_rect.left()
        elif self.x + self.width > screen_rect.right():
            self.x = screen_rect.right() - self.width
        
        # Clamp Y position
        if self.y < screen_rect.top():
            self.y = screen_rect.top()
        elif self.y + self.height > screen_rect.bottom():
            self.y = screen_rect.bottom() - self.height
    
    # NEW: Ensure cat stays on screen
    def _ensure_on_screen(self, screen_rect: QRect):
        """Check if cat went off screen and flip to random edge"""
        off_screen = (
            self.x + self.width < screen_rect.left() or
            self.x > screen_rect.right() or
            self.y + self.height < screen_rect.top() or
            self.y > screen_rect.bottom()
        )
        
        if off_screen:
            self._flip_to_random_edge(screen_rect)
    
    def _teleport_to_random_edge(self, screen_rect: QRect):
        """Teleport cat to a random edge"""
        self.edge = random.choice([self.BOTTOM, self.TOP, self.LEFT, self.RIGHT])
        
        if self.edge == self.BOTTOM:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = screen_rect.bottom() - self.height
        elif self.edge == self.TOP:
            self.x = random.randint(screen_rect.left(), max(screen_rect.left(), screen_rect.right() - self.width))
            self.y = screen_rect.top()
        elif self.edge == self.LEFT:
            self.x = screen_rect.left()
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))
        elif self.edge == self.RIGHT:
            self.x = screen_rect.right() - self.width
            self.y = random.randint(screen_rect.top(), max(screen_rect.top(), screen_rect.bottom() - self.height))
        
        self.facing = random.choice([1, -1])
        self.reset_jump_state()  # NEW: Reset jump state on teleport
    
    # NEW: Reset jump state
    def reset_jump_state(self):
        """Reset jumping and corner hit flags"""
        self.is_jumping = False
        self.jump_vel = 0
        self.hit_corner_this_jump = False
    
    def check_and_jump(self):
        """Check if it's time to jump"""
        self.jump_cooldown -= 100
        if self.jump_cooldown <= 0 and not self.is_jumping:
            if random.random() < 0.55:  # Increased from 0.4 to 0.55
                self.jump_cooldown = random.randint(500, 1500)  # Reduced from 700-2000 to 500-1500
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


class CatOverlay(QWidget):
    panel_requested = Signal(object)
    cats_multiplied = Signal(int)
    cat_killed = Signal(object)  # NEW: Signal when a cat is killed
    
    def __init__(self):
        super().__init__()
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
        self.click_through = True  # NEW: Always click-through
        self.cats = [Cat(0, screen.bottom(), Cat.BOTTOM)]
        self.multiply_timer = random.randint(30000, 60000)  # Much less frequent
        self.shutting_down = False  # NEW: Shutdown flag
        
        self.jump_timer = QTimer(self)
        self.jump_timer.timeout.connect(self._check_jumps)
        self.jump_timer.start(200)  # Reduced frequency for performance
        
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._update_anim)
        self.anim_timer.start(250)  # Reduced frequency for performance
        
        self.move_timer = QTimer(self)
        self.move_timer.timeout.connect(self.tick_move)
        self.move_timer.start(33)  # ~30fps instead of 60fps for performance
        
        self._update_window_mask()

    def _update_window_mask(self):
        """Update window mask so only cat areas are clickable"""
        region = QRegion()
        padding = 20  # Add padding to prevent edge clipping
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
        if self.shutting_down:  # NEW: Guard against shutdown
            return
            
        self.multiply_timer -= 100
        
        if self.multiply_timer <= 0:
            self._multiply_cats()
            self.multiply_timer = random.randint(30000, 60000)  # Much less frequent
        
        for cat in self.cats:
            cat.check_and_jump()

    def _update_anim(self):
        """Update animation for all cats"""
        if self.shutting_down:  # NEW: Guard against shutdown
            return
            
        for cat in self.cats:
            cat.update_anim()
        self.update()

    def _multiply_cats(self):
        """Add a small number of new cats (non‑exponential)"""
        if self.shutting_down:
            return
        
        # NEW: Cap at 5 cats maximum
        if len(self.cats) >= 5:
            return
        
        screen = QApplication.primaryScreen().geometry()
        # Add only 1 cat per multiplication event
        spawn_count = 1

        new_cats = []
        for _ in range(spawn_count):
            base = random.choice(self.cats)
            new_edge = random.choice([Cat.BOTTOM, Cat.TOP, Cat.LEFT, Cat.RIGHT])
            new_cat = Cat(
                base.x + random.randint(-20, 20),
                base.y + random.randint(-20, 20),
                new_edge,
            )
            new_cats.append(new_cat)

        self.cats.extend(new_cats)
        self._update_window_mask()
        self.cats_multiplied.emit(len(self.cats))

    def set_speed(self, speed: int):
        speed = max(0, int(speed))
        self.speed_mag = speed if speed > 0 else 3

    def set_click_through(self, enabled: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))
        self.click_through = enabled

    def set_running(self, running: bool):
        running = bool(running)
        if running:
            self.anim_timer.start()
            self.move_timer.start(33)
            self.jump_timer.start(200)
        else:
            self.anim_timer.stop()
            self.move_timer.stop()
            self.jump_timer.stop()

    def tick_move(self):
        """Update all cats and redraw"""
        if self.speed_mag == 0 or self.shutting_down:  # NEW: Guard against shutdown
            return
        
        screen = QApplication.primaryScreen().geometry()
        
        # NEW: Iterate over a copy to handle multiplication during iteration
        for cat in list(self.cats):
            cat.update(screen, self.speed_mag)
            
            # NEW: Check for corner hits and multiply
            if cat.hits_corner(screen):
                self._multiply_cats()
        
        self._update_window_mask()  # Update mask during movement
        
        self.update()
    
    # NEW: Shutdown method
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
        
        # Clear with transparent background
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        
        for cat in self.cats:
            painter.save()

            # Rotate around cat center based on edge
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

    def _draw_cat(self, painter: QPainter, cat):
        """
        Draws a 'Chibi' style cat with 1:1 Head/Body proportions.
        Expression: Unimpressed/Judging.
        """
        w, h = self.cat_width, self.cat_height
        
        # --- 1. Palette (Clean, flat colors for better readability) ---
        fur_color = QColor(255, 170, 80)     # Bright Orange
        fur_shadow = QColor(215, 130, 40)    # For depth
        white = QColor(255, 255, 255)
        skin_pink = QColor(255, 180, 190)
        outline = QColor(60, 40, 20)         # Dark Brown (softer than black)
        
        # --- 2. Canvas Setup ---
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(outline, 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        
        # Animation Tick (0.0 to 1.0)
        # Slower breathing for a calmer, more annoying demeanor
        breath = math.sin(cat.anim_frame * 0.15) * 2 
        
        # Center point
        cx, cy = w // 2, h // 2 + 10  # Shift down slightly to center the mass

        # --- 3. Orientation Transform ---
        painter.save()
        if cat.facing < 0:
            painter.translate(w, 0)
            painter.scale(-1, 1)
            cx = w // 2 # Reset center x after flip relative to canvas
        
        # --- 4. The Tail (S-Curve) ---
        # Drawn first so it appears behind the body
        tail_path = QPainterPath()
        tail_start = QPoint(cx - 25, cy + 30)
        tail_swish = math.sin(cat.anim_frame * 0.2) * 10
        
        tail_path.moveTo(tail_start)
        tail_path.cubicTo(
            cx - 50, cy + 30,             # Control 1
            cx - 60, cy - 20 + tail_swish,# Control 2
            cx - 30, cy - 40 + tail_swish # Tip
        )
        
        # Draw Tail with thick pen
        painter.setBrush(Qt.NoBrush)
        tail_pen = QPen(outline, 14, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(tail_pen)
        painter.drawPath(tail_path)
        
        # Draw Tail Inner Color (slightly thinner line on top)
        tail_pen.setColor(fur_color)
        tail_pen.setWidth(9)
        painter.setPen(tail_pen)
        painter.drawPath(tail_path)
        
        # Reset Pen
        painter.setPen(QPen(outline, 2.5))

        # --- 5. The Body (Teardrop / Pear shape) ---
        # Proportion: Body is small and anchors the large head
        body_w, body_h = 50, 45
        body_y = cy + 15
        
        painter.setBrush(QBrush(fur_color))
        # Draw body as a path for organic shape
        body_path = QPainterPath()
        body_path.addRoundedRect(QRect(int(cx - body_w/2), int(body_y), int(body_w), int(body_h)), 20, 20)
        painter.drawPath(body_path)

        # Belly Patch (White)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(white))
        painter.drawEllipse(int(cx - 15), int(body_y + 10), 30, 25)
        
        # Restore Outline Pen
        painter.setPen(QPen(outline, 2.5))

        # --- 6. The Head (Large rounded rectangle) ---
        # Proportion: Head is roughly same size as body, sitting on top
        head_w, head_h = 80, 70
        head_x = cx - head_w // 2
        head_y = cy - 45 + breath # Head bobs with breathing
        
        # Draw Ears first (so they merge or sit behind)
        painter.setBrush(QBrush(fur_color))
        
        # Left Ear
        ear_l = QPolygon([
            QPoint(int(cx - 30), int(head_y + 10)),
            QPoint(int(cx - 38), int(head_y - 15)), # Tip
            QPoint(int(cx - 15), int(head_y + 5))
        ])
        painter.drawPolygon(ear_l)
        
        # Right Ear
        ear_r = QPolygon([
            QPoint(int(cx + 30), int(head_y + 10)),
            QPoint(int(cx + 38), int(head_y - 15)), # Tip
            QPoint(int(cx + 15), int(head_y + 5))
        ])
        painter.drawPolygon(ear_r)

        # Main Head Shape
        head_rect = QRect(int(head_x), int(head_y), int(head_w), int(head_h))
        painter.setBrush(QBrush(fur_color))
        painter.drawRoundedRect(head_rect, 30, 30) # Very round corners

        # --- 7. The Face (The "Annoying" Part) ---
        
        # Eyes: Large circles, but half closed
        eye_y = head_y + 28
        eye_offset = 18
        eye_size = 14
        
        # Eye Whites
        painter.setBrush(QBrush(white))
        painter.drawEllipse(int(cx - eye_offset - eye_size/2), int(eye_y), eye_size, eye_size)
        painter.drawEllipse(int(cx + eye_offset - eye_size/2), int(eye_y), eye_size, eye_size)
        
        # Pupils (Small dots for bored look)
        painter.setBrush(QBrush(outline))
        painter.drawEllipse(int(cx - eye_offset - 2), int(eye_y + 4), 4, 4)
        painter.drawEllipse(int(cx + eye_offset - 2), int(eye_y + 4), 4, 4)
        
        # Eyelids (Flat lines cutting off top of eye)
        # This creates the "unimpressed" look
        painter.setBrush(QBrush(fur_color))
        painter.setPen(QPen(outline, 2.5))
        
        # Left Eyelid
        painter.drawLine(int(cx - eye_offset - 8), int(eye_y + 2), int(cx - eye_offset + 8), int(eye_y + 2))
        # Right Eyelid
        painter.drawLine(int(cx + eye_offset - 8), int(eye_y + 2), int(cx + eye_offset + 8), int(eye_y + 2))

        # Nose & Mouth
        # Tiny nose, positioned high between eyes
        painter.setBrush(QBrush(skin_pink))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(cx - 3), int(eye_y + 12), 6, 4)
        
        # Mouth: The "smug cat" shape (small 'w')
        painter.setPen(QPen(outline, 2))
        painter.setBrush(Qt.NoBrush)
        mouth_y = eye_y + 18
        
        # Draw mouth path
        mouth_path = QPainterPath()
        mouth_path.moveTo(cx - 5, mouth_y)
        mouth_path.quadTo(cx - 2.5, mouth_y + 3, cx, mouth_y)
        mouth_path.quadTo(cx + 2.5, mouth_y + 3, cx + 5, mouth_y)
        painter.drawPath(mouth_path)

        # --- 8. Limbs (Tiny nubs) ---
        painter.setPen(QPen(outline, 2.5))
        painter.setBrush(QBrush(white)) # White paws (mittens)
        
        # Hands (Front paws) - Holding them close to chest (T-rex style)
        paw_y = body_y + 15 + breath
        painter.drawEllipse(int(cx - 15), int(paw_y), 12, 12)
        painter.drawEllipse(int(cx + 3), int(paw_y), 12, 12)
        
        # Feet (Bottom paws)
        foot_y = body_y + body_h - 8
        painter.setBrush(QBrush(white))
        # Draw feet slightly behind body curve
        painter.drawEllipse(int(cx - 20), int(foot_y), 14, 10)
        painter.drawEllipse(int(cx + 6), int(foot_y), 14, 10)

        painter.restore()

    def _update_window_mask(self):
        """Update window mask so only cat areas are clickable"""
        region = QRegion()
        padding = 20  # Add padding to prevent edge clipping
        for cat in self.cats:
            cat_rect = QRect(
                int(cat.x) - padding,
                int(cat.y) - padding,
                cat.width + padding * 2,
                cat.height + padding * 2
            )
            region = region.united(QRegion(cat_rect))
        self.setMask(region)
    
    
    def mousePressEvent(self, event):
        """Handle clicks on cats"""
        click_pos = event.position().toPoint()
        for cat in self.cats:
            cat_rect = QRect(int(cat.x), int(cat.y), cat.width, cat.height)
            if cat_rect.contains(click_pos):
                self.panel_requested.emit(cat)
                event.accept()
                return
    
    def kill_cat(self, cat):
        """Remove a specific cat from the overlay"""
        if cat in self.cats:
            self.cats.remove(cat)
            self._update_window_mask()
            self.cats_multiplied.emit(len(self.cats))


class ControlPanel(QWidget):
    def __init__(self, overlay: CatOverlay, selected_cat=None):
        super().__init__()
        self.overlay = overlay
        self.selected_cat = selected_cat  # NEW: Track which cat this panel is for
        self._quitting = False

        # Set window flags - NEW: Add WindowStaysOnTopHint to stay above overlay
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowStaysOnTopHint |  # NEW: Stay above the cat overlay
            Qt.CustomizeWindowHint |
            Qt.WindowTitleHint |
            Qt.WindowCloseButtonHint
        )

        title = "Cat Control Panel" if not selected_cat else "Individual Cat Panel"
        self.setWindowTitle(title)

        root = QVBoxLayout(self)

        self.lbl_count = QLabel(f"Cats on screen: {len(self.overlay.cats)}")
        root.addWidget(self.lbl_count)
        self.overlay.cats_multiplied.connect(lambda count: self.lbl_count.setText(f"Cats on screen: {count}"))

        self.chk_visible = QCheckBox("Show cats")
        self.chk_visible.setChecked(True)
        self.chk_visible.toggled.connect(self._on_visible)
        root.addWidget(self.chk_visible)


        btn_row = QHBoxLayout()
        
        # NEW: Add kill button if this is an individual cat panel
        if self.selected_cat is not None:
            btn_kill = QPushButton("Kill This Cat")
            btn_kill.clicked.connect(self._on_kill_cat)
            btn_row.addWidget(btn_kill)
        
        btn_quit = QPushButton("Quit")
        btn_quit.clicked.connect(self._on_quit_clicked)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_quit)
        root.addLayout(btn_row)

        self.overlay.set_speed(5)
    
    def _on_kill_cat(self):
        """Kill the selected cat"""
        if self.selected_cat is not None:
            self.overlay.kill_cat(self.selected_cat)
            self.hide()  # Close this panel after killing the cat

    def _on_visible(self, visible: bool):
        if visible:
            self.overlay.show()
        else:
            self.overlay.hide()

    def _on_quit_clicked(self):
        """Quit button explicitly quits the app"""
        self._quitting = True
        self.overlay.shutdown()  # NEW: Call shutdown before quitting
        QApplication.instance().quit()

    def closeEvent(self, event):
        """Close button (X) hides the panel; Quit button closes app"""
        if self._quitting:
            event.accept()
        else:
            self.hide()
            event.ignore()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    overlay = CatOverlay()
    overlay.show()

    def on_cat_clicked(cat):
        panel = ControlPanel(overlay, selected_cat=cat)
        panel.show()
        panel.raise_()
        panel.activateWindow()
    
    overlay.panel_requested.connect(on_cat_clicked)

    # NEW: Ensure clean shutdown on app quit
    app.aboutToQuit.connect(overlay.shutdown)

    sys.exit(app.exec())
