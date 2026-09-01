# -*- coding: utf-8 -*-
"""LANShare — Modern arayüzlü yerel ağ dosya/klasör paylaşım aracı.
Solda çevrimiçi kişiler, ortada dosya ve klasör gönderme.
Pencere kapatılınca sistem tepsisinde çalışmaya devam eder.
"""
import json
import os
import queue
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import uuid
import zipfile

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import filedialog, messagebox

APP_NAME = "LANShare"
APP_ID = "lanshare-v1"
DISCOVERY_PORT = 50515
TCP_PORT_BASE = 50516
PEER_TIMEOUT = 8.0
CHUNK_SIZE = 64 * 1024
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".lanshare.json")


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
    except OSError:
        pass


def default_save_dir():
    home = os.path.expanduser("~")
    for p in (os.path.join(home, "Desktop"),
              os.path.join(home, "OneDrive", "Desktop"),
              os.path.join(home, "Masaüstü")):
        if os.path.isdir(p):
            return p
    return home


def get_save_dir():
    d = load_config().get("save_dir")
    return d if d and os.path.isdir(d) else default_save_dir()


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Network:
    """UDP ile keşif, TCP ile dosya aktarımı."""

    def __init__(self, username, ui_queue):
        self.username = username
        self.ui_queue = ui_queue
        self.my_id = str(uuid.uuid4())
        self.tcp_port = None
        self._srv = None
        self.peers = {}
        self.lock = threading.Lock()
        self.running = True
        self.local_ip = get_local_ip()

    def start(self):
        self.tcp_port = self._bind_tcp()
        threading.Thread(target=self._tcp_server, daemon=True).start()
        threading.Thread(target=self._udp_listener, daemon=True).start()
        threading.Thread(target=self._announcer, daemon=True).start()
        threading.Thread(target=self._pruner, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self._broadcast({"app": APP_ID, "type": "bye", "id": self.my_id})
        except OSError:
            pass

    # ---- UDP keşif ----
    def _broadcast(self, payload):
        data = json.dumps(payload).encode("utf-8")
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        targets = ["255.255.255.255"]
        parts = self.local_ip.split(".")
        if len(parts) == 4:
            targets.append(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
        for t in set(targets):
            try:
                udp.sendto(data, (t, DISCOVERY_PORT))
            except OSError:
                pass
        udp.close()

    def _announce(self):
        self._broadcast({"app": APP_ID, "type": "announce", "id": self.my_id,
                         "name": self.username, "port": self.tcp_port})

    def _announcer(self):
        while self.running:
            self._announce()
            time.sleep(2.0)

    def _udp_listener(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            udp.bind(("", DISCOVERY_PORT))
        except OSError:
            self.ui_queue.put(("status", "⚠️ Keşif portu meşgul — başka bir kopya açık mı?"))
            return
        while self.running:
            try:
                data, addr = udp.recvfrom(4096)
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if msg.get("app") != APP_ID or msg.get("id") == self.my_id:
                continue
            pid = msg.get("id")
            if msg.get("type") == "announce":
                with self.lock:
                    self.peers[pid] = {"name": str(msg.get("name", "?"))[:24],
                                       "ip": addr[0],
                                       "port": int(msg.get("port", TCP_PORT_BASE)),
                                       "last_seen": time.time()}
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
                    self.peers.pop(pid)
            if gone:
                self.ui_queue.put(("peers",))

    def get_peers(self):
        with self.lock:
            return sorted(self.peers.values(), key=lambda p: p["name"].lower())

    # ---- TCP ----
    def _bind_tcp(self):
        for port in range(TCP_PORT_BASE, TCP_PORT_BASE + 50):
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # NOT: Windows'ta SO_REUSEADDR aynı portu iki kez bağlamaya
                # izin verdiği için kullanılmıyor; çakışmada sonraki porta geçilir.
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
            threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True).start()

    def _handle_conn(self, conn, addr):
        try:
            while self.running:
                (length,) = struct.unpack(">I", self._recv_exact(conn, 4))
                if length > 512 * 1024:
                    raise ConnectionError("geçersiz çerçeve")
                msg = json.loads(self._recv_exact(conn, length).decode("utf-8"))
                if msg.get("kind") == "file":
                    self._recv_file(conn, msg, addr)
        except (ConnectionError, OSError, ValueError, UnicodeDecodeError, struct.error):
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
        if size < 0 or size > 20 * 1024 * 1024 * 1024:
            return
        target = os.path.join(get_save_dir(), self._unique_name(name))
        received = 0
        try:
            with open(target, "wb") as f:
                while received < size:
                    chunk = self._recv_exact(conn, min(CHUNK_SIZE, size - received))
                    f.write(chunk)
                    received += len(chunk)
                    self.ui_queue.put(("progress", received / max(size, 1)))
            self.ui_queue.put(("file_in", sender, target))
        except (ConnectionError, OSError):
            try:
                os.remove(target)
            except OSError:
                pass
            self.ui_queue.put(("status", f"⚠️ {sender} kişisinden dosya alınamadı: {name}"))
        self.ui_queue.put(("progress", None))

    @staticmethod
    def _unique_name(name):
        base, ext = os.path.splitext(name)
        candidate = name
        i = 1
        while os.path.exists(os.path.join(get_save_dir(), candidate)):
            candidate = f"{base} ({i}){ext}"
            i += 1
        return candidate

    # ---- gönderim ----
    def _connect(self, ip, port):
        conn = socket.create_connection((ip, port), timeout=5)
        conn.settimeout(None)
        return conn

    def _send_frame(self, conn, data):
        conn.sendall(struct.pack(">I", len(data)) + data)

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
                    self.ui_queue.put(("progress", sent / max(size, 1)))
            self.ui_queue.put(("file_sent", peer["name"], name))
        finally:
            conn.close()


# ----------------------------- Arayüz -----------------------------

ACCENT = "#5b8cff"
GREEN = "#7ee787"
RED = "#ff7b72"


def make_icon_image():
    img = Image.new("RGB", (64, 64), "#5b8cff")
    d = ImageDraw.Draw(img)
    d.polygon([(32, 8), (54, 32), (42, 32), (42, 54), (22, 54), (22, 32), (10, 32)],
              fill="white")
    return img


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_NAME)
        self.geometry("480x540")
        self.resizable(False, False)
        self.net = None
        self.tray = None
        self.selected = None
        self._peer_keys = set()
        self._sending = False
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # ---- giriş ekranı ----
        self.login = ctk.CTkFrame(self, fg_color="transparent")
        self.login.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(self.login, text="📤", font=("Segoe UI Emoji", 44)).pack()
        ctk.CTkLabel(self.login, text="LANShare",
                     font=("Segoe UI", 26, "bold")).pack()
        ctk.CTkLabel(self.login, text="Kullanıcı adın:",
                     font=("Segoe UI", 12), text_color="#8a8d99").pack(pady=(14, 2))
        self.name_entry = ctk.CTkEntry(self.login, width=220, height=38,
                                       font=("Segoe UI", 14), justify="center")
        self.name_entry.pack(pady=4)
        last = load_config().get("username")
        if last:
            self.name_entry.insert(0, last)
        self.name_entry.bind("<Return>", lambda e: self.join())
        ctk.CTkButton(self.login, text="Başla", width=220, height=38,
                      font=("Segoe UI", 14, "bold"), command=self.join).pack(pady=12)
        self.name_entry.focus_set()

        self._setup_tray()

    # ---------- ana ekran ----------
    def join(self):
        name = self.name_entry.get().strip()
        if not name:
            return
        name = name[:24]
        cfg = load_config()
        cfg["username"] = name
        save_config(cfg)

        self.login.destroy()
        self.net = Network(name, queue.Queue())
        try:
            self.net.start()
        except RuntimeError:
            messagebox.showerror(APP_NAME, "Ağ başlatılamadı.")
            self.destroy()
            return
        self.title(f"{APP_NAME} — {name}")
        if self.tray:
            self.tray.title = f"{APP_NAME} — {name}"
        self._build_main()
        self.after(150, self.poll_queue)
        self.refresh_peers()

    def _build_main(self):
        # sol: kişi listesi
        left = ctk.CTkFrame(self, width=140, corner_radius=0, fg_color="#1d1e25")
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="KİŞİLER", font=("Segoe UI", 11, "bold"),
                     text_color="#8a8d99").pack(pady=(14, 6))
        self.peer_box = ctk.CTkScrollableFrame(left, width=112, height=420,
                                               fg_color="#1d1e25",
                                               label_text="")
        self.peer_box.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        # sağ: gönderme alanı
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="right", fill="both", expand=True, padx=16, pady=16)

        self.target_label = ctk.CTkLabel(right, text="← soldan kişi seç",
                                         font=("Segoe UI", 13, "bold"),
                                         text_color=ACCENT)
        self.target_label.pack(pady=(4, 14))

        self.file_btn = ctk.CTkButton(right, text="📤  Dosya Gönder",
                                      font=("Segoe UI", 17, "bold"), height=110,
                                      corner_radius=16, command=self.pick_file)
        self.file_btn.pack(fill="x", pady=(0, 8))

        self.folder_btn = ctk.CTkButton(right, text="📁  Klasör Gönder (zip)",
                                        font=("Segoe UI", 14, "bold"), height=44,
                                        corner_radius=12, fg_color="#2b2d38",
                                        hover_color="#363947",
                                        command=self.pick_folder)
        self.folder_btn.pack(fill="x", pady=(0, 16))

        self.bar = ctk.CTkProgressBar(right, height=8, corner_radius=4)
        self.bar.set(0)
        self.bar.pack(fill="x", pady=(0, 8))
        self.bar.pack_forget()  # gönderim başlayınca görünecek

        self.status = ctk.CTkLabel(right, text="", font=("Segoe UI", 11),
                                   text_color=GREEN, wraplength=300, justify="center")
        self.status.pack()

        # alt: kayıt klasörü
        bottom = ctk.CTkFrame(right, fg_color="transparent")
        bottom.pack(side="bottom", fill="x")
        ctk.CTkButton(bottom, text="📂 Aç", width=60, height=28,
                      font=("Segoe UI", 11), fg_color="#2b2d38",
                      hover_color="#363947", command=self.open_save_dir).pack(side="left")
        ctk.CTkButton(bottom, text="⚙ Değiştir", width=80, height=28,
                      font=("Segoe UI", 11), fg_color="#2b2d38",
                      hover_color="#363947", command=self.change_save_dir).pack(side="left", padx=6)
        self.dir_label = ctk.CTkLabel(bottom, text=get_save_dir(),
                                      font=("Segoe UI", 9), text_color="#8a8d99")
        self.dir_label.pack(side="right")


    # ---------- olay döngüsü ----------
    def poll_queue(self):
        try:
            while True:
                item = self.net.ui_queue.get_nowait()
                kind = item[0]
                if kind == "peers":
                    self.refresh_peers()
                elif kind == "progress":
                    if item[1] is None:
                        self.bar.pack_forget()
                        self.bar.set(0)
                    else:
                        self.bar.pack(fill="x", pady=(0, 8), before=self.status)
                        self.bar.set(item[1])
                elif kind == "file_in":
                    _, sender, path = item
                    msg = f"📥 {sender} dosya gönderdi: {os.path.basename(path)}"
                    self.status.configure(text=msg, text_color=GREEN)
                    self.notify(f"{sender} dosya gönderdi",
                                os.path.basename(path) + f"\nKlasör: {get_save_dir()}")
                elif kind == "file_sent":
                    _, to, name = item
                    self.status.configure(text=f"✅ {name} → {to} gönderildi", text_color=GREEN)
                elif kind == "status":
                    self.status.configure(text=item[1], text_color=RED)
                elif kind == "status_zip":
                    self.status.configure(text="📦 Klasör sıkıştırılıyor…", text_color="#8a8d99")
                elif kind == "progress_text":
                    self.status.configure(text=item[1] or "", text_color="#8a8d99")
        except queue.Empty:
            pass
        self.after(120, self.poll_queue)

    # ---------- kişi listesi ----------
    def refresh_peers(self):
        peers = self.net.get_peers()
        keys = {(p["ip"], p["port"], p["name"]) for p in peers}
        if keys != self._peer_keys or not self.peer_box.winfo_children():
            self._peer_keys = keys
            for w in self.peer_box.winfo_children():
                w.destroy()
            if not peers:
                ctk.CTkLabel(self.peer_box, text="kimse yok…",
                             font=("Segoe UI", 11), text_color="#8a8d99").pack(pady=8, fill="x")
                self.selected = None
                self.target_label.configure(text="arkadaşların bekleniyor…")
            for p in peers:
                key = (p["ip"], p["port"])
                is_sel = self.selected and self.selected["ip"] == key[0] and self.selected["port"] == key[1]
                btn = ctk.CTkButton(
                    self.peer_box, text=p["name"], anchor="w", height=30,
                    font=("Segoe UI", 12), corner_radius=8,
                    fg_color=ACCENT if is_sel else "transparent",
                    text_color="white" if is_sel else "#e8e9ee",
                    hover_color="#2b2d38",
                    command=lambda pp=p: self.select_peer(pp))
                btn.pack(pady=2, fill="x")
            if not self.selected and len(peers) == 1:
                self.select_peer(peers[0])
        self.after(1000, self.refresh_peers)

    def select_peer(self, peer):
        self.selected = peer
        self.target_label.configure(text=f"→ {peer['name']} kişisine gönderilecek")
        self.refresh_peers_now()

    def refresh_peers_now(self):
        self._peer_keys = set()
        peers = self.net.get_peers()
        for w in self.peer_box.winfo_children():
            w.destroy()
        for p in peers:
            key = (p["ip"], p["port"])
            is_sel = self.selected and self.selected["ip"] == key[0] and self.selected["port"] == key[1]
            ctk.CTkButton(
                self.peer_box, text=p["name"], anchor="w", height=30,
                font=("Segoe UI", 12), corner_radius=8,
                fg_color=ACCENT if is_sel else "transparent",
                text_color="white" if is_sel else "#e8e9ee",
                hover_color="#2b2d38",
                command=lambda pp=p: self.select_peer(pp)).pack(pady=2, fill="x")


    # ---------- gönderim ----------
    def _guard(self):
        if self._sending:
            self.status.configure(text="Bir gönderim devam ediyor, bekle…", text_color=RED)
            return False
        if self.selected is None:
            peers = self.net.get_peers() if self.net else []
            if len(peers) == 1:
                self.select_peer(peers[0])
            else:
                self.status.configure(text="Önce soldan bir kişi seç.", text_color=RED)
                return False
        return True

    def pick_file(self):
        if not self.net or not self._guard():
            return
        path = filedialog.askopenfilename(title="Gönderilecek dosyayı seç", parent=self)
        if not path:
            return
        self._start_send(path)

    def pick_folder(self):
        if not self.net or not self._guard():
            return
        path = filedialog.askdirectory(title="Gönderilecek klasörü seç", parent=self)
        if not path:
            return
        self._start_send(path)

    def _start_send(self, path):
        peer = self.selected
        self._sending = True
        threading.Thread(target=self._send_worker, args=(peer, path), daemon=True).start()

    def _send_worker(self, peer, path):
        tmp_dir = None
        try:
            if os.path.isdir(path):
                # klasörü geçici bir zip'e sıkıştır
                self.net.ui_queue.put(("status_zip",))
                tmp_dir = tempfile.mkdtemp(prefix="lanshare_")
                base = os.path.join(tmp_dir, os.path.basename(path.rstrip("\\/")) or "klasor")
                self.net.ui_queue.put(("progress_text", f"📦 {os.path.basename(path)} sıkıştırılıyor…"))
                archive = shutil.make_archive(base, "zip", root_dir=os.path.dirname(path),
                                              base_dir=os.path.basename(path.rstrip("\\/")))
                self.net.ui_queue.put(("progress_text", ""))
                self.net.send_file(peer, archive,
                                   display_name=os.path.basename(archive))
            else:
                self.net.send_file(peer, path)
        except (OSError, zipfile.BadZipFile) as e:
            self.net.ui_queue.put(("status", f"⚠️ {peer['name']} kişisine gönderilemedi: {e}"))
        finally:
            self._sending = False
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self.net.ui_queue.put(("progress", None))

    # ---------- kayıt klasörü ----------
    def open_save_dir(self):
        try:
            os.startfile(get_save_dir())
        except OSError:
            pass

    def change_save_dir(self):
        d = filedialog.askdirectory(title="Alınan dosyalar için klasör seç", parent=self)
        if d:
            cfg = load_config()
            cfg["save_dir"] = d
            save_config(cfg)
            self.dir_label.configure(text=d)
            self.status.configure(text=f"Kayıt klasörü: {d}", text_color=GREEN)

    # ---------- sistem tepsisi ----------
    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Göster", lambda: self.after(0, self.show_window), default=True),
            pystray.MenuItem("Çıkış", lambda: self.after(0, self.quit_app)),
        )
        self.tray = pystray.Icon(APP_NAME, make_icon_image(), APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def notify(self, title, message):
        if self.tray:
            try:
                self.tray.notify(message, title)
            except Exception:
                pass

    def show_window(self):
        self.after(0, lambda: (self.deiconify(), self.lift(), self.focus_force()))

    def hide_to_tray(self):
        self.withdraw()

    def quit_app(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.net:
            self.net.stop()
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()

