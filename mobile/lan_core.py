# -*- coding: utf-8 -*-
"""
LAN çekirdeği — masaüstü LANChat / LANShare ile BİREBİR aynı kablo protokolü.
- UDP keşif: JSON broadcast (announce / bye)
- TCP veri: 4 bayt (big-endian) uzunluk + JSON başlık; dosyada başlığın
  arkasından ham baytlar doğrudan akar.

Telefon uygulaması (mobile/) bu modülü kullanır ve masaüstü uygulamalarla
doğrudan haberleşir.
"""
import json
import os
import socket
import struct
import threading
import time
import uuid

PEER_TIMEOUT = 15.0
CHUNK_SIZE = 64 * 1024
MAX_FILE_SIZE = 20 * 1024 * 1024 * 1024
ANNOUNCE_INTERVAL = 3.0   # UDP duyuru araligi; masaustu PEER_TIMEOUT=8sn oldugu
                          # icin tek kayip paket listeyi ziplamasin
PROGRESS_MIN_GAP = 0.4    # UI'ya progress olayi arasindaki min sure (sn)

PROTOKOLS = {
    # key : (APP_ID, DISCOVERY_UDP_PORT, TCP_SERVER_BASE_PORT)
    "chat":     ("lanchat-v1",  50505, 50506),
    "share":    ("lanshare-v1", 50515, 50516),
}

DEFAULT_PROTO = "chat"


def _score(ip):
    """Yerel adres adaylarını gerçek LAN önceliğine göre puanlar."""
    parts = ip.split(".")
    if len(parts) != 4:
        return -1
    try:
        ints = [int(p) for p in parts]
    except ValueError:
        return -1
    if 1 <= ints[0] <= 223:
        if ip.startswith("192.168."):
            return 4        # ev/ofis Wi-Fi en yaygın
        if ints[0] == 10:
            return 3
        if ints[0] == 172 and 16 <= ints[1] <= 31:
            return 2        # 172.* sanal adaptörlerde de olabilir, gerisine
        return 1
    return 0


def get_local_ip():
    """Makinenin yerel ağ adresi. Önce varsayılan rota, sonra tüm arayüzler;
    gerçek LAN (192.168/10/172) tercih edilir."""
    found = []

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # paket göndermez; yalnızca rotayı seçer
        found.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass

    if not found:
        return "127.0.0.1"
    return max(found, key=_score)


class Network:
    """Tek bir protokole bağlı (sohbet ya da paylaşım) LAN motoru.

    ui_queue'ya olay demetleri düşer:
      ("peers",)
      ("msg_in",       sender_name, sender_ip, text)
      ("file_in",      sender_name, target_path, size)
      ("file_sent",    peer_name, display_name)
      ("status",       text)
      ("progress",     0.0-1.0 ya da None)
      ("sys_error",    text)
    """

    def __init__(self, username, ui_queue, proto="chat", save_dir=None):
        app_id, disc_port, tcp_base = PROTOKOLS[proto]
        self.proto = proto
        self.app_id = app_id
        self.discovery_port = disc_port
        self.tcp_base = tcp_base
        self.username = username
        self.ui_queue = ui_queue
        self.save_dir = save_dir  # alınan dosyaların yazılacağı klasör
        self.my_id = str(uuid.uuid4())
        self.tcp_port = None
        self._srv = None
        self._udp_sock = None
        self.peers = {}
        self.lock = threading.Lock()
        self.running = True
        self.local_ip = get_local_ip()
        self._last_progress = 0.0

    def _maybe_progress(self, frac, final=False):
        """Progress olaylarini kisitla; her 64KB'da UI'yi bogma."""
        now = time.time()
        if final or (now - self._last_progress) >= PROGRESS_MIN_GAP:
            self._last_progress = now
            self.ui_queue.put(("progress", frac))

    # ----------------------- ömür döngüsü -----------------------
    def start(self):
        self.tcp_port = self._bind_tcp()
        for target in (self._tcp_server, self._udp_listener,
                       self._announcer, self._pruner):
            threading.Thread(target=target, daemon=True).start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self._broadcast({"app": self.app_id, "type": "bye", "id": self.my_id})
        except OSError:
            pass
        for sock in (self._srv, self._udp_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    # ----------------------- UDP keşif -----------------------
    def _broadcast(self, payload):
        data = json.dumps(payload).encode("utf-8")
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Kaynak adresini seçili LAN arayüzüne sabitle (sanal adaptör
            # üzerinden yayın yapılmasın; karşı taraf yanlış IP görür).
            try:
                udp.bind((self.local_ip, 0))
            except OSError:
                pass
            targets = {"255.255.255.255"}
            parts = self.local_ip.split(".")
            if len(parts) == 4:
                targets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
            for target in targets:
                try:
                    udp.sendto(data, (target, self.discovery_port))
                except OSError:
                    pass
        finally:
            udp.close()

    def _announce(self):
        self._broadcast({
            "app": self.app_id, "type": "announce", "id": self.my_id,
            "name": self.username, "port": self.tcp_port,
        })

    def _announcer(self):
        while self.running:
            self._announce()
            time.sleep(ANNOUNCE_INTERVAL)

    def _udp_listener(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            udp.bind(("", self.discovery_port))
        except OSError:
            self.ui_queue.put(("sys_error", "UDP keşif portu kullanılamıyor."))
            return
        self._udp_sock = udp
        while self.running:
            try:
                data, addr = udp.recvfrom(4096)
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if msg.get("app") != self.app_id or msg.get("id") == self.my_id:
                continue
            pid = msg.get("id")
            if msg.get("type") == "announce":
                with self.lock:
                    self.peers[pid] = {
                        "name": str(msg.get("name", "?"))[:24],
                        "ip": addr[0],
                        "port": int(msg.get("port", self.tcp_base)),
                        "last_seen": time.time(),
                    }
                self.ui_queue.put(("peers",))
            elif msg.get("type") == "bye":
                with self.lock:
                    self.peers.pop(pid, None)
                self.ui_queue.put(("peers",))

    def _pruner(self):
        while self.running:
            time.sleep(2.0)
            now = time.time()
            with self.lock:
                gone = [pid for pid, p in self.peers.items()
                        if now - p["last_seen"] > PEER_TIMEOUT]
                for pid in gone:
                    self.peers.pop(pid, None)
            if gone:
                self.ui_queue.put(("peers",))

    def get_peers(self):
        with self.lock:
            return sorted(self.peers.values(), key=lambda p: p["name"].lower())

    # ----------------------- TCP -----------------------
    def _bind_tcp(self):
        for port in range(self.tcp_base, self.tcp_base + 50):
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.bind(("", port))
                srv.listen(16)
                self._srv = srv
                return port
            except OSError:
                continue
        raise RuntimeError("TCP portu açılamadı")

    def _recv_exact(self, conn, n):
        buf = b""
        while len(buf) < n:
            part = conn.recv(min(n - len(buf), CHUNK_SIZE))
            if not part:
                raise ConnectionError("bağlantı koptu")
            buf += part
        return buf

    def _tcp_server(self):
        while self.running:
            try:
                conn, addr = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn, addr),
                             daemon=True).start()

    def _handle_conn(self, conn, addr):
        try:
            while self.running:
                (length,) = struct.unpack(">I", self._recv_exact(conn, 4))
                if length > 512 * 1024:
                    raise ConnectionError("geçersiz çerçeve")
                msg = json.loads(self._recv_exact(conn, length).decode("utf-8"))
                if msg.get("kind") == "msg":
                    self.ui_queue.put(("msg_in",
                                       str(msg.get("from", "?")),
                                       addr[0], str(msg.get("text", ""))))
                elif msg.get("kind") == "file":
                    self._recv_file(conn, msg, addr)
        except (ConnectionError, OSError, ValueError,
                UnicodeDecodeError, struct.error):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _recv_file(self, conn, meta, addr):
        name = os.path.basename(str(meta.get("name", "dosya"))) or "dosya"
        size = int(meta.get("size", 0))
        sender = str(meta.get("from", "?"))
        if size < 0 or size > MAX_FILE_SIZE:
            return
        target = os.path.join(self.save_dir or ".", self._unique_name(name, self.save_dir or "."))
        received = 0
        try:
            with open(target, "wb") as f:
                while received < size:
                    chunk = self._recv_exact(conn, min(CHUNK_SIZE, size - received))
                    f.write(chunk)
                    received += len(chunk)
                    self._maybe_progress(received / max(size, 1))
            self._maybe_progress(received / max(size, 1), final=True)
            self.ui_queue.put(("file_in", sender, target, size))
        except (ConnectionError, OSError):
            try:
                os.remove(target)
            except OSError:
                pass
            self.ui_queue.put(("status",
                               f"⚠️ {sender} kişisinden dosya alınamadı: {name}"))
        self.ui_queue.put(("progress", None))

    @staticmethod
    def _unique_name(name, base_dir="."):
        """Hedef klasörde çakışmayan bir dosya adı üretir."""
        base, ext = os.path.splitext(name)
        candidate = name
        i = 1
        while os.path.exists(os.path.join(base_dir, candidate)):
            candidate = f"{base} ({i}){ext}"
            i += 1
        return candidate

    # ----------------------- gönderim -----------------------
    def _connect(self, ip, port):
        conn = socket.create_connection((ip, port), timeout=5)
        conn.settimeout(None)
        return conn

    def _send_frame(self, conn, data):
        conn.sendall(struct.pack(">I", len(data)) + data)

    def send_message(self, peer, text):
        payload = json.dumps({"kind": "msg", "from": self.username,
                              "text": text}).encode("utf-8")
        conn = self._connect(peer["ip"], peer["port"])
        try:
            self._send_frame(conn, payload)
        finally:
            conn.close()

    def send_file(self, peer, path, display_name=None):
        name = display_name or os.path.basename(path)
        size = os.path.getsize(path)
        conn = self._connect(peer["ip"], peer["port"])
        try:
            meta = json.dumps({"kind": "file", "from": self.username,
                               "name": name, "size": size}).encode("utf-8")
            self._send_frame(conn, meta)
            sent = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.sendall(chunk)
                    sent += len(chunk)
                    self._maybe_progress(sent / max(size, 1))
            self._maybe_progress(sent / max(size, 1), final=True)
            self.ui_queue.put(("file_sent", peer["name"], name))
        finally:
            conn.close()