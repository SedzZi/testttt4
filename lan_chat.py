# -*- coding: utf-8 -*-
"""
LANChat — Aynı yerel ağ üzerinden mesaj ve dosya paylaşımı.
Kullanıcılar otomatik keşfedilir (UDP broadcast), mesaj/dosya TCP ile iletilir.
"""
import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_NAME = "LANChat"
APP_ID = "lanchat-v1"
DISCOVERY_PORT = 50505          # UDP broadcast portu
TCP_PORT_BASE = 50506           # TCP sunucu başlangıç portu
PEER_TIMEOUT = 8.0              # saniye içinde duyuru gelmezse çevrimdışı say
CHUNK_SIZE = 64 * 1024


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def save_dir():
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    base = downloads if os.path.isdir(downloads) else os.path.dirname(
        sys.executable if getattr(sys, "frozen", False) else __file__)
    d = os.path.join(base, "LANChat Dosyaları")
    os.makedirs(d, exist_ok=True)
    return d


# ----------------------------- Ağ Katmanı -----------------------------

class Network:
    def __init__(self, username, ui_queue):
        self.username = username
        self.ui_queue = ui_queue
        self.my_id = str(uuid.uuid4())
        self.tcp_port = None
        self._srv = None
        self.peers = {}          # id -> {"name","ip","port","last_seen"}
        self.lock = threading.Lock()
        self.running = True
        self.local_ip = get_local_ip()

    # ---- başlatma ----
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
        self._broadcast({
            "app": APP_ID, "type": "announce", "id": self.my_id,
            "name": self.username, "port": self.tcp_port,
        })

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
            self.ui_queue.put(("sys_error", "UDP keşif portu kullanılamıyor."))
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
                    known = pid in self.peers
                    self.peers[pid] = {
                        "name": str(msg.get("name", "?"))[:24],
                        "ip": addr[0],
                        "port": int(msg.get("port", TCP_PORT_BASE)),
                        "last_seen": time.time(),
                    }
                if not known:
                    self.ui_queue.put(("peer_online", str(msg.get("name", "?"))[:24]))
            elif msg.get("type") == "bye":
                with self.lock:
                    p = self.peers.pop(pid, None)
                if p:
                    self.ui_queue.put(("peer_offline", p["name"], p["ip"], p["port"]))

    def _pruner(self):
        while self.running:
            time.sleep(2.0)
            now = time.time()
            with self.lock:
                gone = [pid for pid, p in self.peers.items()
                        if now - p["last_seen"] > PEER_TIMEOUT]
                for pid in gone:
                    p = self.peers.pop(pid)
                    self.ui_queue.put(("peer_offline", p["name"], p["ip"], p["port"]))

    def get_peers(self):
        with self.lock:
            return list(self.peers.values())

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
                payload = self._recv_exact(conn, length)
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("kind") == "msg":
                    self.ui_queue.put(("message", str(msg.get("from", "?")),
                                       addr[0], "in", str(msg.get("text", ""))))
                elif msg.get("kind") == "file":
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
        target = os.path.join(save_dir(), self._unique_name(name))
        received = 0
        try:
            with open(target, "wb") as f:
                while received < size:
                    chunk = self._recv_exact(conn, min(CHUNK_SIZE, size - received))
                    f.write(chunk)
                    received += len(chunk)
                    self.ui_queue.put(("progress",
                        f"{sender} → {name} alınıyor… %{int(received * 100 / max(size, 1))}"))
            self.ui_queue.put(("message", sender, addr[0], "file_in", target))
            self.ui_queue.put(("progress", ""))
        except (ConnectionError, OSError):
            try:
                os.remove(target)
            except OSError:
                pass
            self.ui_queue.put(("message", sender, addr[0], "in", f"⚠️ Dosya alınamadı: {name}"))
            self.ui_queue.put(("progress", ""))

    @staticmethod
    def _unique_name(name):
        base, ext = os.path.splitext(name)
        candidate = name
        i = 1
        while os.path.exists(os.path.join(save_dir(), candidate)):
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

    def send_message(self, peer, text):
        payload = json.dumps({"kind": "msg", "from": self.username,
                              "text": text}).encode("utf-8")
        conn = self._connect(peer["ip"], peer["port"])
        try:
            self._send_frame(conn, payload)
        finally:
            conn.close()

    def send_file(self, peer, path):
        name = os.path.basename(path)
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
                    self.ui_queue.put(("progress",
                        f"{name} gönderiliyor… %{int(sent * 100 / max(size, 1))}"))
            self.ui_queue.put(("progress", ""))
        finally:
            conn.close()


# ----------------------------- Arayüz -----------------------------

BG = "#15161b"
PANEL = "#1d1e25"
ENTRY_BG = "#262833"
ACCENT = "#5b8cff"
ACCENT_DARK = "#3f6de0"
TEXT = "#e8e9ee"
MUTED = "#8a8d99"
FONT = ("Segoe UI", 10)


class LoginFrame(tk.Frame):
    def __init__(self, master, on_join):
        super().__init__(master, bg=BG)
        self.on_join = on_join
        self.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self, text="💬", font=("Segoe UI Emoji", 44), bg=BG, fg=TEXT).pack(pady=(0, 6))
        tk.Label(self, text=APP_NAME, font=("Segoe UI", 22, "bold"), bg=BG, fg=TEXT).pack()
        tk.Label(self, text="Aynı ağdaki arkadaşlarınla mesajlaş ve dosya paylaş",
                 font=FONT, bg=BG, fg=MUTED).pack(pady=(2, 22))

        self.entry = tk.Entry(self, font=("Segoe UI", 12), bg=ENTRY_BG, fg=TEXT,
                              insertbackground=TEXT, relief="flat", width=26,
                              justify="center")
        self.entry.pack(ipady=9)
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self.join())

        tk.Button(self, text="Katıl", font=("Segoe UI", 11, "bold"),
                  bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
                  activeforeground="white", relief="flat", cursor="hand2",
                  command=self.join).pack(pady=14, ipadx=30, ipady=7)

        self.error = tk.Label(self, text="", font=FONT, bg=BG, fg="#ff7b72")
        self.error.pack()

    def join(self):
        name = self.entry.get().strip()
        if not name:
            self.error.config(text="Lütfen bir kullanıcı adı gir.")
            return
        if len(name) > 24:
            self.error.config(text="Kullanıcı adı en fazla 24 karakter olabilir.")
            return
        self.on_join(name)


class ChatFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.net = None
        self.history = {}      # (ip, port) -> [(sender, side, text)]
        self.current = None    # seçili eşin (ip, port)
        self.peer_rows = {}    # (ip, port) -> listbox index
        self._last_path = None

        # --- sol panel: eş listesi ---
        left = tk.Frame(self, bg=PANEL, width=220)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="  ÇEVRİMİÇİ", font=("Segoe UI", 9, "bold"),
                 bg=PANEL, fg=MUTED, anchor="w").pack(fill="x", pady=(12, 6))

        self.peer_list = tk.Listbox(left, bg=PANEL, fg=TEXT, relief="flat",
                                    highlightthickness=0, selectbackground=ACCENT,
                                    selectforeground="white", font=("Segoe UI", 11),
                                    activestyle="none")
        self.peer_list.pack(fill="both", expand=True, padx=6, pady=(0, 10))
        self.peer_list.bind("<<ListboxSelect>>", self.on_peer_select)

        # --- sağ panel: sohbet ---
        right = tk.Frame(self, bg=BG)
        right.pack(side="right", fill="both", expand=True)

        self.header = tk.Label(right, text="Bir sohbet seç ←", font=("Segoe UI", 12, "bold"),
                               bg=BG, fg=TEXT, anchor="w")
        self.header.pack(fill="x", padx=16, pady=(12, 8))

        self.chat = ScrolledText(right, bg=BG, fg=TEXT, relief="flat", wrap="word",
                                 font=FONT, highlightthickness=0, state="disabled",
                                 padx=14, pady=10, spacing3=4)
        self.chat.pack(fill="both", expand=True, padx=6)
        self.chat.tag_config("me", foreground="#9db9ff", justify="right")
        self.chat.tag_config("other", foreground="#ffb86c")
        self.chat.tag_config("sys", foreground=MUTED, justify="center", font=("Segoe UI", 9))
        self.chat.tag_config("file", foreground="#7ee787")
        self.chat.tag_config("path", foreground=MUTED, font=("Segoe UI", 8))
        self.chat.tag_bind("path", "<Enter>", lambda e: self.chat.config(cursor="hand2"))
        self.chat.tag_bind("path", "<Leave>", lambda e: self.chat.config(cursor=""))
        self.chat.tag_bind("path", "<Button-1>", self.open_path)

        # --- alt giriş alanı ---
        bottom = tk.Frame(self, bg=BG)
        bottom.pack(side="bottom", fill="x", padx=10, pady=10)
        self.progress = tk.Label(bottom, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.progress.pack(anchor="w", pady=(0, 4))

        row = tk.Frame(bottom, bg=ENTRY_BG)
        row.pack(fill="x")

        tk.Button(row, text="📎", font=("Segoe UI", 12), bg=ENTRY_BG, fg=TEXT,
                  relief="flat", activebackground=ENTRY_BG, cursor="hand2",
                  command=self.send_file_dialog).pack(side="left", padx=(10, 4), pady=8)


    # ---------- sohbet mantığı ----------
    def start(self, net):
        self.net = net
        self.after(150, self.poll_queue)
        self.refresh_peers()

    def poll_queue(self):
        try:
            while True:
                item = self.net.ui_queue.get_nowait()
                kind = item[0]
                if kind == "message":
                    _, sender, ip, side, text = item
                    key = self.key_by_ip(ip)
                    if key is None:
                        self.append_system(f"⚠️ {sender} listede görünmüyor, mesaj alınamadı.")
                    else:
                        self.append_message(key, sender, side, text)
                elif kind == "peer_online":
                    self.append_system(f"✅ {item[1]} çevrimiçi oldu")
                    self.refresh_peers()
                elif kind == "peer_offline":
                    self.append_system(f"❌ {item[1]} çevrimdışı oldu")
                    self.refresh_peers()
                elif kind == "progress":
                    self.progress.config(text=item[1])
                elif kind == "sys_error":
                    self.append_system(f"⚠️ {item[1]}")
        except queue.Empty:
            pass
        self.after(120, self.poll_queue)

    def key_by_ip(self, ip):
        for p in self.net.get_peers():
            if p["ip"] == ip:
                return (p["ip"], p["port"])
        return None

    def open_path(self, _event):
        if self._last_path and os.path.exists(self._last_path):
            try:
                os.startfile(os.path.dirname(self._last_path))
            except OSError:
                pass

    # ---------- eş listesi ----------
    def refresh_peers(self):
        sel_key = None
        sel = self.peer_list.curselection()
        if sel:
            for key, idx in self.peer_rows.items():
                if idx == sel[0]:
                    sel_key = key
                    break
        self.peer_list.delete(0, "end")
        self.peer_rows.clear()
        peers = sorted(self.net.get_peers(), key=lambda p: p["name"].lower())
        for i, p in enumerate(peers):
            key = (p["ip"], p["port"])
            self.peer_list.insert("end", f"  ●  {p['name']}")
            self.peer_rows[key] = i
            if key == sel_key or (sel_key is None and key == self.current):
                self.peer_list.selection_set(i)

    # ---------- sohbet çizimi ----------
    def render_history(self, key):
        self.chat.config(state="normal")
        self.chat.delete("1.0", "end")
        for sender, side, text in self.history.get(key, []):
            self._write(sender, side, text)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _write(self, sender, side, text):
        if side == "me":
            self.chat.insert("end", f"{sender}  {text}\n\n", "me")
        elif side == "file_out":
            self.chat.insert("end", f"{sender}  📤 {text}  (gönderildi)\n\n", "me")
        elif side == "file_in":
            self.chat.insert("end", f"{sender}  📥 ", "other")
            self.chat.insert("end", text + "\n", "file")
            self._last_path = text
            self.chat.insert("end", "   kaydedildi → 📂 klasörü aç\n\n", "path")
        elif side == "sys":
            self.chat.insert("end", text + "\n\n", "sys")
        else:
            self.chat.insert("end", f"{sender}  {text}\n\n", "other")

    def append_message(self, key, sender, side, text):
        self.history.setdefault(key, []).append((sender, side, text))
        if key == self.current:
            self.chat.config(state="normal")
            self._write(sender, side, text)
            self.chat.config(state="disabled")
            self.chat.see("end")

    def append_system(self, text):
        self.chat.config(state="normal")
        self._write("", "sys", text)
        self.chat.config(state="disabled")
        self.chat.see("end")

    # ---------- gönderim ----------
    def send_message(self):
        text = self.msg_entry.get().strip()
        if not text or not self.current or not self.net:
            return
        peer = self._peer(self.current)
        if not peer:
            self.append_system("⚠️ Kişi artık çevrimdışı.")
            return
        self.msg_entry.delete(0, "end")
        try:
            self.net.send_message(peer, text)
            self.append_message(self.current, self.net.username, "me", text)
        except OSError:
            self.append_system(f"⚠️ {peer['name']} kişisine ulaşılamadı.")

    def send_file_dialog(self):
        if not self.current or not self.net:
            self.append_system("Önce soldan bir kişi seç.")
            return
        peer = self._peer(self.current)
        if not peer:
            self.append_system("⚠️ Kişi artık çevrimdışı.")
            return
        path = filedialog.askopenfilename(title="Gönderilecek dosyayı seç", parent=self)
        if not path:
            return
        self.append_message(self.current, self.net.username,
                            "file_out", os.path.basename(path))
        threading.Thread(target=self._send_file_worker,
                         args=(peer, path), daemon=True).start()

    def _send_file_worker(self, peer, path):
        try:
            self.net.send_file(peer, path)
        except OSError:
            self.net.ui_queue.put(("message", "", peer["ip"], "sys",
                                   f"⚠️ Dosya gönderilemedi: {os.path.basename(path)}"))

    def on_peer_select(self, _event=None):
        sel = self.peer_list.curselection()
        if not sel:
            return
        for key, idx in self.peer_rows.items():
            if idx == sel[0]:
                self.current = key
                break
        name = self._peer_name(self.current) or "?"
        self.header.config(text=name)
        self.render_history(self.current)
        self.msg_entry.focus_set()

    def _peer_name(self, key):
        p = self._peer(key)
        return p["name"] if p else None

    def _peer(self, key):
        for p in self.net.get_peers():
            if (p["ip"], p["port"]) == key:
                return p
        return None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x620")
        self.minsize(720, 480)
        self.configure(bg=BG)
        self.net = None

        self.login = LoginFrame(self, self.join)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def join(self, username):
        self.login.destroy()
        self.net = Network(username, queue.Queue())
        try:
            self.net.start()
        except RuntimeError:
            messagebox.showerror(APP_NAME, "Ağ başlatılamadı. Uygulama kapatılacak.")
            self.destroy()
            return
        self.chat = ChatFrame(self)
        self.chat.pack(fill="both", expand=True)
        self.chat.start(self.net)
        self.title(f"{APP_NAME} — {username}")

    def on_close(self):
        if self.net:
            self.net.stop()
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()

