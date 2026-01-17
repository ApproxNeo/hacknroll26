import sys
import json
import socket
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QObject, QDateTime
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QSlider, QSpinBox, QLineEdit, QTextEdit
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

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


# ---- Zeroconf P2P ----

class ZeroConfP2P(QObject):
    peer_found = Signal(str, str, int)  # (name, host, port)
    status_changed = Signal(str)

    SERVICE_TYPE = "_catclick._tcp.local."

    def __init__(self, port: int, instance_name: str | None = None, parent=None):
        super().__init__(parent)
        self.port = int(port)
        self.instance_id = (instance_name or str(uuid.uuid4()))
        self._zc: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._info: ServiceInfo | None = None
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


class SpriteOverlay(QWidget):
    clicked = Signal(QPoint)

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
        self.setMouseTracking(True)

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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # global click position (useful for logs / debugging)
            self.clicked.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)


class ControlPanel(QWidget):
    def __init__(self, overlay: SpriteOverlay, server: MessageServer, client: MessageClient, initial_fps: int = 12):
        super().__init__()
        self.overlay = overlay
        self.server = server
        self.client = client

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

        # Networking
        net_title = QLabel("Networking (TCP)")
        root.addWidget(net_title)

        net_row = QHBoxLayout()
        net_row.addWidget(QLabel("Host"))
        self.txt_host = QLineEdit("127.0.0.1")
        net_row.addWidget(self.txt_host)

        net_row.addWidget(QLabel("Port"))
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(50505)
        net_row.addWidget(self.spin_port)
        root.addLayout(net_row)

        net_btn_row = QHBoxLayout()
        self.btn_start_server = QPushButton("Start server")
        self.btn_connect_client = QPushButton("Connect client")
        self.btn_disconnect_client = QPushButton("Disconnect client")
        net_btn_row.addWidget(self.btn_start_server)
        net_btn_row.addWidget(self.btn_connect_client)
        net_btn_row.addWidget(self.btn_disconnect_client)
        root.addLayout(net_btn_row)

        self.lbl_net_status = QLabel("-")
        root.addWidget(self.lbl_net_status)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(140)
        root.addWidget(self.txt_log)

        def _host_port():
            return self.txt_host.text().strip() or "127.0.0.1", int(self.spin_port.value())

        self.btn_start_server.clicked.connect(lambda: self.server.start(*_host_port()))
        self.btn_connect_client.clicked.connect(lambda: self.client.connect_to(*_host_port()))
        self.btn_disconnect_client.clicked.connect(self.client.disconnect)

        self.server.status_changed.connect(self._append_log)
        self.client.status_changed.connect(self._append_log)
        self.server.message_received.connect(self._append_log)
        self.client.message_received.connect(self._append_log)

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

    def _run_action(self, action_str: str):
        action_json = action_str.strip()
        try:
            action_json = json.loads(action_json)
        except Exception as e:
            self._append_log(f"Invalid action JSON: {e}")
            return
        
        if action_json["action"] == "fire":
            x = action_json.get("x", 0)
            y = action_json.get("y", 0)
            self._append_log(f"Received 'fire' action at ({x}, {y})")
            # Here you could add code to trigger an animation or effect on the overlay

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


def _first_ipv4(addresses: list[bytes]) -> str | None:
    for a in addresses or []:
        if len(a) == 4:
            return socket.inet_ntoa(a)
    return None


# Auto-connect client to the first discovered peer (if not already connected).
_connected_to: tuple[str, int] | None = None

# When the cat is clicked, send a message through the client and server.
def on_cat_clicked(global_pos: QPoint):
    data = {"action": "fire", "x": global_pos.x(), "y": global_pos.y()}
    
    msg = json.dumps(data)
    # Send to peers connected to our server
    server.broadcast(msg)
    # Send to a peer we connected to as a client
    client.send(msg)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    frames = load_frames("frames")
    w = SpriteOverlay(frames, fps=12)

    w.move(200, 200)
    w.show()

    # Listen on all interfaces so peers can connect.
    server = MessageServer()
    client = MessageClient()
    server.start(get_lan_ip(), 50505)

    # Zeroconf peer discovery + advertising.
    zc = ZeroConfP2P(port=50505)
    zc.start()

    def on_peer(name: str, host: str, port: int):
        global _connected_to
        if _connected_to == (host, port):
            return
        _connected_to = (host, port)
        client.connect_to(host, port)

    zc.peer_found.connect(on_peer)

    # Clean up zeroconf on exit.
    app.aboutToQuit.connect(zc.close)

    w.clicked.connect(on_cat_clicked)

    panel = ControlPanel(w, server, client, initial_fps=12)
    panel.show()

    zc.status_changed.connect(panel._append_log)

    sys.exit(app.exec())