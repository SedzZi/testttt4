# -*- coding: utf-8 -*-
"""
LAN arka plan servisi (python-for-android foreground + sticky servisi).

Uygulama kapatılsa bile çalışmaya devam eder:
  * LAN keşif duyuruları + TCP sunucu (sürekli dinler)
  * gelen mesajları tarihçeye ekler
  * gelen dosyaları uygulama özel klasörüne indirir
  * ekran kapalıyken bildirim gönderir
  * telefona güç/çoklu yayın kilitleri alarak Doze'e karşı korunur

UI (main.py) bu servise 127.0.0.1 üzerinden JSON-lines ile bağlanır.
"""
import json
import os
import queue
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lan_core  # uygulama kökünden

CONFIG_FILE = "config.json"       # {username}
CONTROL_FILE = "control.json"     # {port}
FILES_DIR_NAME = "LAN Dosyaları"
MAX_HISTORY = 600

_state = {
    "username": "",
    "files_dir": None,
    "private": None,
    "context": None,
    "clients": set(),
    "history": [],
    "networks": {},   # proto -> Network
    "queues": {},     # proto -> queue.Queue
    "control_port": 0,
    "control_sock": None,
    "notif_mgr": None,
    "notif_channel": "lan_background",
}


def _android_ctx():
    """Servisin Java Context'i (org.kivy.android.PythonService.mService)."""
    if _state["context"] is not None:
        return _state["context"]
    try:
        from jnius import autoclass
    except Exception:
        return None
    try:
        PythonService = autoclass("org.kivy.android.PythonService")
        ctx = PythonService.mService
    except Exception:
        return None
    _state["context"] = ctx
    return ctx


def _private_dir():
    if _state["private"]:
        return _state["private"]
    try:
        path = _android_ctx().getFilesDir().getAbsolutePath()
    except Exception:
        path = os.environ.get("ANDROID_PRIVATE",
                              os.path.join(os.path.expanduser("~"), ".lanbackup"))
    _state["private"] = path
    return path


def _files_dir():
    if not _state["files_dir"]:
        d = os.path.join(_private_dir(), FILES_DIR_NAME)
        os.makedirs(d, exist_ok=True)
        _state["files_dir"] = d
    return _state["files_dir"]


def _config_path():
    return os.path.join(_private_dir(), CONFIG_FILE)


def load_config():
    try:
        with open(_config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
    except OSError:
        pass


# ------------------------ bildirimler ------------------------
_notif_ready = False


def _setup_notifications():
    global _notif_ready
    if _notif_ready:
        return
    try:
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        NotificationManager = autoclass("android.app.NotificationManager")
        NotificationChannel = autoclass("android.app.NotificationChannel")
        ctx = _android_ctx()
        nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
        channel = NotificationChannel(_state["notif_channel"],
                                      "LAN Servisi",
                                      NotificationManager.IMPORTANCE_LOW)
        nm.createNotificationChannel(channel)
        _state["notif_mgr"] = nm
        _notif_ready = True
    except Exception:
        _notif_ready = False


def notify(title, text):
    if not _notif_ready:
        _setup_notifications()
    try:
        from jnius import autoclass
        Notification = autoclass("android.app.Notification")
        Builder = autoclass("android.app.Notification$Builder")
        ctx = _android_ctx()
        builder = Builder(ctx, _state["notif_channel"])
        builder.setContentTitle(title)
        builder.setContentText(text)
        builder.setSmallIcon(ctx.getApplicationInfo().icon)
        _state["notif_mgr"].notify(1001, builder.build())
    except Exception:
        pass


# ------------------------ güç / çoklu yayın kilitleri ------------------------
def _acquire_locks():
    """Wi-Fi multicast lock (UDP duyurulari icin).

    NOT: Kalici PARTIAL_WAKE_LOCK bilincli olarak kaldirildi; CPU'yu surekli
    uyik tutup pili yiyor, telefoni isitiyor ve termal kismana (throttle)
    yol acarak genel kasma yapiyordu. dataSync foreground servisi Doze
    korumasi icin yeterli.
    """
    try:
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        WifiManager = autoclass("android.net.wifi.WifiManager")
        ctx = _android_ctx().getApplicationContext()
        wm = ctx.getSystemService(Context.WIFI_SERVICE)
        mlock = wm.createMulticastLock("lan:multicast")
        mlock.acquire()
        _state["locks"] = (mlock,)
    except Exception:
        pass


def _control_path():
    return os.path.join(_private_dir(), CONTROL_FILE)


def _discover_control_port():
    """UI tarafı kullanır — servis portunu dosyadan okur."""
    try:
        with open(_control_path(), encoding="utf-8") as f:
            return json.load(f).get("port")
    except (OSError, ValueError):
        return None


def _start_control_server():
    for _ in range(30):  # artışlarla boş port bul
        port = 40000 + (int(time.time() * 1000) % 20000) + len(_state["clients"])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(8)
            s.settimeout(1.0)
            _state["control_sock"] = s
            _state["control_port"] = port
            try:
                with open(_control_path(), "w", encoding="utf-8") as f:
                    json.dump({"port": port, "pid": os.getpid()}, f)
            except OSError:
                pass
            r = threading.Thread(target=_accept_loop, daemon=True)
            r.start()
            print(f"[lan-service] kontrol portu {port}")
            return port
        except OSError:
            s.close()
            continue
    return None


def _accept_loop():
    while True:
        try:
            conn, addr = _state["control_sock"].accept()
        except (OSError, AttributeError):
            break
        conn.settimeout(30.0)
        _state["clients"].add(conn)
        tid = threading.Thread(target=_client_loop, args=(conn,), daemon=True)
        tid.start()


def _client_loop(conn):
    buf = b""
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    _handle_command(conn, line.decode("utf-8", "replace"))
    except (OSError, ValueError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        _state["clients"].discard(conn)


# ------------------------ olay yayını / geçmiş ------------------------
def _broadcast(event):
    """Tüm bağlı UI istemcilerine tek satırlık JSON yayınlar."""
    line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    dead = []
    for conn in list(_state["clients"]):
        try:
            conn.sendall(line)
        except OSError:
            dead.append(conn)
    for conn in dead:
        _state["clients"].discard(conn)


def _push_history(entry):
    _state["history"].append(entry)
    if len(_state["history"]) > MAX_HISTORY:
        _state["history"] = _state["history"][-MAX_HISTORY:]


def _engine_event(proto, evt):
    """Network.ui_queue olaylarını UI'ya ve geçmişe işler."""
    if evt[0] == "peers":
        peers = [{"name": p["name"], "ip": p["ip"], "port": p["port"]}
                 for p in _state["networks"][proto].get_peers()]
        _broadcast({"ev": "peers", "proto": proto, "peers": peers})
    elif evt[0] == "msg_in":
        _, who, ip, text = evt
        entry = {"ev": "msg_in", "proto": proto, "from": who, "ip": ip,
                 "text": text, "ts": time.time()}
        _push_history(entry)
        _broadcast(entry)
        if not _state["clients"]:
            notify(f"{who} mesaj gönderdi", text[:80])
    elif evt[0] == "file_in":
        _, who, path, size = evt
        entry = {"ev": "file_in", "proto": proto, "from": who,
                 "name": os.path.basename(path), "path": path,
                 "size": size, "ts": time.time()}
        _push_history(entry)
        _broadcast(entry)
        if not _state["clients"]:
            notify("Dosya alındı",
                   f"{who} → {os.path.basename(path)} ({round(size/1048576,1)} MB)")
    elif evt[0] == "file_sent":
        _, peer, name = evt
        _broadcast({"ev": "file_sent", "proto": proto, "to": peer, "name": name})
    elif evt[0] == "status":
        _broadcast({"ev": "status", "text": evt[1]})
    elif evt[0] == "progress":
        _broadcast({"ev": "progress", "proto": proto, "value": evt[1]})
    elif evt[0] == "sys_error":
        _broadcast({"ev": "status", "text": evt[1]})


# ------------------------ ağ motoru ------------------------
def _start_engine():
    if _state["networks"]:
        return
    username = (load_config().get("username") or "")[:24] or "Telefon"
    _state["username"] = username
    # Sohbet kaldirildi; telefon sadece dosya paylasimi (share) agini baslatir.
    # Boylece bir UDP announce dongusu ve TCP sunucu daha az duzenli is yapar.
    for proto in ("share",):
        q = queue.Queue()
        net = lan_core.Network(username, q, proto=proto,
                               save_dir=_files_dir())
        net.start()
        _state["queues"][proto] = q
        _state["networks"][proto] = net
        threading.Thread(target=_pump_queue, args=(proto, q), daemon=True).start()
        print(f"[lan-service] motor başladı: {proto} port={net.tcp_port} ip={net.local_ip}")


def _pump_queue(proto, q):
    while True:
        try:
            evt = q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            _engine_event(proto, evt)
        except Exception:
            pass


def _set_username(name):
    name = (name or "").strip()[:24]
    if not name:
        return
    _state["username"] = name
    cfg = load_config()
    cfg["username"] = name
    save_config(cfg)
    for net in _state["networks"].values():
        net.username = name


# ------------------------ komutlar ------------------------
def _reply(conn, payload):
    try:
        conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError:
        pass


def _find_peer(proto, ip, port):
    net = _state["networks"].get(proto)
    if not net:
        return None
    for p in net.get_peers():
        if p["ip"] == ip and p["port"] == int(port):
            return p
    return None


def _handle_command(conn, line):
    try:
        msg = json.loads(line)
    except ValueError:
        return
    cmd = msg.get("cmd")
    proto = msg.get("proto", "chat")

    if cmd == "ping":
        _reply(conn, {"pong": True, "port": _state["control_port"]})
    elif cmd == "login":
        _set_username(msg.get("username", ""))
        _reply(conn, {"ok": True, "username": _state["username"]})
    elif cmd == "status":
        nets = []
        for p, net in _state["networks"].items():
            nets.append({"proto": p, "port": net.tcp_port, "ip": net.local_ip})
        _reply(conn, {"ok": True, "username": _state["username"],
                      "nets": nets, "files_dir": _files_dir()})
    elif cmd == "peers":
        peers = [{"name": p["name"], "ip": p["ip"], "port": p["port"]}
                 for p in _state["networks"][proto].get_peers()]
        _reply(conn, {"ev": "peers", "proto": proto, "peers": peers})
    elif cmd == "history":
        _reply(conn, {"ev": "history", "items": _state["history"]})
    elif cmd == "send_msg":
        peer = _find_peer(proto, msg.get("ip"), msg.get("port"))
        if peer:
            threading.Thread(target=_do_send_msg, args=(proto, peer, msg.get("text", "")),
                             daemon=True).start()
            _reply(conn, {"ok": True})
        else:
            _reply(conn, {"ok": False, "error": "Kişi çevrimdışı"})
    elif cmd == "send_file":
        peer = _find_peer(proto, msg.get("ip"), msg.get("port"))
        path = msg.get("path")
        if peer and path and os.path.exists(path):
            threading.Thread(target=_do_send_file,
                             args=(proto, peer, path, msg.get("name")),
                             daemon=True).start()
            _reply(conn, {"ok": True})
        else:
            _reply(conn, {"ok": False, "error": "Kişi çevrimdışı veya dosya yok"})


def _do_send_msg(proto, peer, text):
    try:
        _state["networks"][proto].send_message(peer, text)
        _broadcast({"ev": "msg_out", "proto": proto, "to": peer["name"],
                    "text": text, "ts": time.time()})
    except OSError:
        _broadcast({"ev": "status", "text": f"⚠️ {peer['name']} kişisine ulaşılamadı."})


def _do_send_file(proto, peer, path, name=None):
    name = name or os.path.basename(path)
    try:
        _state["networks"][proto].send_file(peer, path, display_name=name)
        _broadcast({"ev": "status", "text": f"✅ {peer['name']} → {name} gönderildi."})
    except OSError:
        _broadcast({"ev": "status", "text": f"⚠️ {peer['name']} → {name} gönderilemedi."})


# ------------------------ giriş ------------------------
def main():
    # sticky serviste çökse de yeniden başlat
    try:
        from jnius import autoclass
        PythonService = autoclass("org.kivy.android.PythonService")
        PythonService.mService.setAutoRestartService(True)
        PythonService.mService.startType()
    except Exception:
        pass

    _acquire_locks()
    _setup_notifications()
    _start_engine()

    arg = os.environ.get("PYTHON_SERVICE_ARGUMENT", "")
    if arg:
        # boot'ta başlatıldıysa kullanıcı adı dosyadan gelir
        print("[lan-service] başlatıldı, arg:", arg)

    port = _start_control_server()
    if not port:
        print("[lan-service] kontrol portu açılamadı")
    # servisin yaşam döngüsünü koru
    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()