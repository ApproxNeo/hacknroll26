import socket
import time
from zeroconf import ServiceBrowser, Zeroconf

SERVICE_TYPE = "_p2pchat._tcp.local."

class Listener:
    def __init__(self):
        self.target = None  # (ip, port)

    def add_service(self, zc, service_type, name):
        info = zc.get_service_info(service_type, name)
        if not info or not info.addresses:
            return
        ip = socket.inet_ntoa(info.addresses[0])
        port = info.port
        print(f"[mdns] found {name} at {ip}:{port}")
        self.target = (ip, port)

if __name__ == "__main__":
    zc = Zeroconf()
    listener = Listener()
    browser = ServiceBrowser(zc, SERVICE_TYPE, listener)

    # Wait a bit for discovery
    deadline = time.time() + 10
    while listener.target is None and time.time() < deadline:
        time.sleep(0.1)

    zc.close()

    if listener.target is None:
        raise SystemExit("No peer found via mDNS")

    ip, port = listener.target
    with socket.create_connection((ip, port), timeout=5) as s:
        s.sendall(b"hello from peer2\n")
        print("[client] reply:", s.recv(4096).decode("utf-8", "replace"))