import socket
import threading
from zeroconf import ServiceInfo, Zeroconf
import ipaddress

SERVICE_TYPE = "_p2pchat._tcp.local."
SERVICE_NAME = "PeerOne._p2pchat._tcp.local."
PORT = 5050

def tcp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", PORT))
        s.listen()
        print(f"[server] listening on {PORT}")
        conn, addr = s.accept()
        with conn:
            print(f"[server] connected by {addr}")
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                print("[server] received:", data.decode("utf-8", "replace"))
                conn.sendall(b"ack\n")

def advertise():
    zc = Zeroconf()
    hostname = socket.gethostname()

    # Pick a LAN IP (simple approach: use gethostbyname; may need adjustment on some setups)
    ip = socket.gethostbyname(hostname)
    ip_bytes = ipaddress.ip_address(ip).packed

    info = ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[ip_bytes],
        port=PORT,
        properties={"id": "peer1"},
        server=f"{hostname}.local.",
    )
    zc.register_service(info)
    print(f"[mdns] advertised {SERVICE_NAME} at {ip}:{PORT}")
    return zc, info

if __name__ == "__main__":
    zc, info = advertise()
    t = threading.Thread(target=tcp_server, daemon=True)
    t.start()
    try:
        t.join()
    finally:
        zc.unregister_service(info)
        zc.close()