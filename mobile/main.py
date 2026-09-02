# -*- coding: utf-8 -*-
"""
LAN — Android Kivy uygulaması.

Yalnızca arayüz. Ağ motoru ve dosya indirme, arka plan servisinde
(services/lan_service.py) çalışır; bu arayüz ona 127.0.0.1 JSON-lines
üzerinden bağlanır. Böylece uygulama kapatılsa bile LAN dinlemesi sürer.
"""
import json
import os
import queue
import socket
import threading
import time

try:
    import kivy
except ImportError:
    kivy = None

if kivy is not None:
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.metrics import dp, sp
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.popup import Popup
    from kivy.uix.progressbar import ProgressBar
    from kivy.uix.screenmanager import Screen, ScreenManager
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.spinner import Spinner
    from kivy.uix.textinput import TextInput
    from kivy.utils import get_color_from_hex

import saf

APP_SERVICE_CLASS = "org.lan.lanmobile.ServiceLanengine"
CONTROL_FILE = "control.json"
FILES_DIR_NAME = "LAN Dosyaları"

# ---------- tema ----------
BG = "#14161d"
PANEL = "#1e2230"
PANEL2 = "#262d3d"
ACCENT = "#4e8cff"
ACCENT_DK = "#3a6fd6"
TEXT = "#e7eaf3"
MUTED = "#8b93a7"
GREEN = "#3fd68f"
RED = "#ff5f6e"


# ---------- Android yardımcıları ----------
def on_android():
    try:
        import jnius  # noqa: F401
        return True
    except Exception:
        return False


def private_dir():
    if on_android():
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            return PythonActivity.mActivity.getFilesDir().getAbsolutePath()
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), ".lanmobile_dev")


def files_dir():
    d = os.path.join(private_dir(), FILES_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def start_service():
    """p4a servisini başlat (varsa)."""
    if not on_android():
        return
    try:
        from jnius import autoclass
        ServiceLanengine = autoclass(APP_SERVICE_CLASS)
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        ServiceLanengine.start(PythonActivity.mActivity, "")
    except Exception:
        pass


def stop_service():
    if not on_android():
        return
    try:
        from jnius import autoclass
        ServiceLanengine = autoclass(APP_SERVICE_CLASS)
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        ServiceLanengine.stop(PythonActivity.mActivity)
    except Exception:
        pass


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except OSError:
        pass


# ---------- servis iletişim istemcisi ----------
class ControlClient:
    def __init__(self, events):
        self.events = events       # queue.Queue — arayüz Clock ile boşaltır
        self.sock = None
        self._running = False
        self.connected = False

    def connect(self):
        """Kontrol portunu oku, bağlan, dinleme iş parçacığını başlat."""
        port = _load_json(os.path.join(private_dir(), CONTROL_FILE), {}).get("port")
        if not port:
            return False
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            sock.settimeout(1.0)
        except OSError:
            return False
        self.sock = sock
        self.connected = True
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        self.send({"cmd": "ping", "proto": "share"})
        self.send({"cmd": "history", "proto": "share"})
        return True

    def _recv_loop(self):
        buf = b""
        while self._running:
            try:
                data = self.sock.recv(65536)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        self.events.put(json.loads(line))
                    except ValueError:
                        pass
        self.connected = False

    def send(self, obj):
        try:
            self.sock.sendall(
                (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except (OSError, AttributeError):
            self.connected = False
            return False

    def close(self):
        self._running = False
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None


# ---------- ortak küçük yardımcılar ----------
def _label(text, size=14, color=TEXT, bold=False, halign="left"):
    lbl = Label(text=text, font_size=sp(size), color=color, halign=halign,
                valign="middle", bold=bold)
    lbl.bind(size=lambda inst, sz: setattr(inst, "text_size", sz))
    return lbl


def _btn(text, handler=None, bg=ACCENT, height=44):
    b = Button(text=text, background_color=get_color_from_hex(bg),
               color=get_color_from_hex("#ffffff"), size_hint_y=None,
               height=dp(height), font_size=sp(14))
    if handler:
        b.bind(on_release=handler)
    return b


# ---------- giriş ekranı ----------
class LoginScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self.app = app
        root = BoxLayout(orientation="vertical",
                         padding=dp(24), spacing=dp(14))
        root.add_widget(Label(text="", size_hint_y=0.3))  # üst boşluk
        root.add_widget(_label("📡", 52, ACCENT, True, "center"))
        root.add_widget(_label("LAN", 30, TEXT, True, "center"))
        root.add_widget(_label("Aynı ağdaki cihazlarla mesajlaş ve dosya gönder — "
                              "arkada kapanmadan çalışır.", 13, MUTED, False, "center"))
        root.add_widget(Label(text="", size_hint_y=0.1))

        self.name_input = TextInput(
            hint_text="Kullanıcı adın", multiline=False, size_hint_y=None,
            height=dp(46), font_size=sp(16), write_tab=False,
            background_color=get_color_from_hex(PANEL2),
            foreground_color=get_color_from_hex(TEXT),
            cursor_color=get_color_from_hex(ACCENT),
            padding=(dp(10), dp(10)))
        self.name_input.bind(on_text_validate=lambda i: self.join())
        self.name_input.text = self.app.saved_username() or ""
        root.add_widget(self.name_input)

        root.add_widget(_btn("Başla ▶", self.join, ACCENT))

        self.info = _label("", 12, MUTED, False, "center")
        root.add_widget(self.info)
        root.add_widget(Label(text="", size_hint_y=0.5))
        self.add_widget(root)

    def join(self, *_):
        name = self.name_input.text.strip()
        if not name:
            self.info.text = "Bir kullanıcı adı girmelisin."
            return
        self.app.login(name)


# ---------- dosya ekranı içeriği ----------

class FilesView(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation="vertical", spacing=dp(6),
                         padding=(dp(8), dp(8), dp(8), dp(6)), **kw)
        self.app = app
        self.peers = []
        self._peer_by_name = {}

        top = BoxLayout(orientation="vertical", size_hint_y=None,
                        height=dp(68), spacing=dp(4))
        top.add_widget(_label("DOSYALAR", 12, MUTED, True, "left"))
        self.peer_spinner = Spinner(
            text="Kişi seç…", values=("Kişi seç…",),
            size_hint_y=None, height=dp(40), font_size=sp(14),
            background_color=get_color_from_hex(PANEL2),
            color=get_color_from_hex(TEXT))
        top.add_widget(self.peer_spinner)
        self.add_widget(top)

        send_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height=dp(46), spacing=dp(6))
        self.send_btn = _btn("📎 Dosya Gönder", self.send_file, ACCENT, 46)
        send_row.add_widget(self.send_btn)
        self.add_widget(send_row)

        self.progress = ProgressBar(max=1.0, value=0.0, size_hint_y=None,
                                    height=dp(10))
        self.add_widget(self.progress)
        self.status = _label("", 12, MUTED, False, "left")
        self.add_widget(self.status)

        self.list_scroll = ScrollView()
        self.list_box = BoxLayout(orientation="vertical", spacing=dp(4),
                                  size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        self.list_scroll.add_widget(self.list_box)
        self.add_widget(self.list_scroll)

    def set_peers(self, peers):
        self.peers = peers
        mapping = {}
        for ip, port, name in peers:
            mapping.setdefault(name, (ip, port, name))
        self._peer_by_name = mapping
        current = self.peer_spinner.text
        names = [p[2] for p in peers] or ["Kişi seç…"]
        self.peer_spinner.values = tuple(names)
        if current in names:
            self.peer_spinner.text = current
        elif names and names[0] != "Kişi seç…":
            self.peer_spinner.text = names[0]
        else:
            self.peer_spinner.text = "Kişi seç…"

    def selected_peer(self):
        name = self.peer_spinner.text
        if not name or name == "Kişi seç…":
            return None
        return self._peer_by_name.get(name)

    def refresh(self):
        """Alınan dosyaları listeley."""
        dirpath = files_dir()
        try:
            entries = sorted(os.listdir(dirpath), key=str.lower)
        except OSError:
            entries = []
        self.list_box.clear_widgets()
        for entry in entries:
            full = os.path.join(dirpath, entry)
            if os.path.isfile(full) and not entry.startswith("."):
                try:
                    self._add_row(entry, full, os.path.getsize(full))
                except OSError:
                    pass

    def _add_row(self, name, path, size):
        row = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=(dp(6), dp(4)))
        row.bind(minimum_height=row.setter("height"))
        row.add_widget(_label(name, 14, TEXT, True, "left"))
        meta = f"{round(size / 1048576, 1)} MB"
        row.add_widget(_label(meta, 11, MUTED, False, "left"))
        actions = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(34), spacing=dp(6))
        b1 = _btn("İndir & Paylaş", lambda b, n=name, p=path: self.export_share(n, p),
                  GREEN, 34)
        b1.font_size = sp(10)
        b2 = _btn("Sil", lambda b, n=name, p=path: self.delete_file(n, p), RED, 34)
        b2.font_size = sp(10)
        actions.add_widget(b1)
        actions.add_widget(b2)
        row.add_widget(actions)
        self.list_box.add_widget(row)

    def export_share(self, name, path):
        if not on_android():
            self.app.flash("Bu özellik yalnızca telefonda çalışır.")
            return

        def on_uri(uri):
            if not uri:
                self.app.flash("Kayıt iptal edildi.")
                return
            try:
                saf.write_file_to_uri(path, uri)
                self.app.flash("Dosya dışa aktarıldı.")
                saf.share_uri(uri, "LAN — Dosya aç")
            except Exception as e:
                self.app.flash(f"Kayıt hatası: {e}")

        saf.create_file(on_uri, name)

    def delete_file(self, name, path):
        try:
            os.remove(path)
        except OSError:
            pass
        self.refresh()
        self.app.flash(f"{name} silindi.")

    def handle_file_event(self, name, path, size):
        self._add_row(name, path, size)
        self.status.text = f"📥 {name} alındı."

    def send_file(self, *_):
        peer = self.selected_peer()
        if not peer:
            self.app.flash("Önce bir kişi seç.")
            return
        if not on_android():
            self.app.flash("Bu özellik yalnızca telefonda çalışır.")
            return
        self.status.text = "Dosya seçiliyor…"
        try:
            saf.pick_file(self._picked)
        except Exception as e:
            self.app.flash(f"Hata: {e}")

    def _picked(self, uri):
        if not uri:
            self.status.text = ""
            return

        def work():
            tmp = ""
            try:
                tmp = os.path.join(files_dir(), "__gonder_" + str(int(time.time() * 1000)))
                saf.read_uri_to_file(uri, tmp)
                name = saf.display_name(uri) or os.path.basename(tmp)
                peer = self.selected_peer()
                if not peer:
                    self.status.text = "Önce bir kişi seç."
                    return
                ok = self.app.ctl.send({"cmd": "send_file", "proto": "share",
                                        "ip": peer[0], "port": peer[1],
                                        "path": tmp, "name": name})
                text = "Gönderiliyor…" if ok else "Servise ulaşılamadı."
                Clock.schedule_once(lambda dt, t=text: self._set_status(t), 0)
            except Exception as e:
                Clock.schedule_once(lambda dt, e=e: self._set_status(f"Hata: {e}"), 0)
            finally:
                Clock.schedule_once(lambda dt, p=tmp: self._clean_tmp(p), 2)

        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, text):
        self.status.text = text

    def _clean_tmp(self, path):
        try:
            if path:
                os.remove(path)
        except OSError:
            pass


# ---------- ana ekran (sekmeli) ----------
class MainScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self.app = app

        root = BoxLayout(orientation="vertical")
        header = BoxLayout(orientation="vertical", size_hint_y=None,
                           height=dp(48), padding=(dp(8), dp(2)))
        self.flash_lbl = _label("", 12, GREEN, False, "left")
        self.status = _label("Servise bağlanılıyor…", 12, MUTED, False, "left")
        header.add_widget(self.flash_lbl)
        header.add_widget(self.status)
        root.add_widget(header)

        self.files_view = FilesView(app)
        root.add_widget(self.files_view)
        self.add_widget(root)
        self.files_view.refresh()

    def flash(self, text, color=GREEN):
        self.flash_lbl.color = get_color_from_hex(color)
        self.flash_lbl.text = text
        Clock.schedule_once(lambda dt: self._clear_flash(), 3.5)

    def _clear_flash(self):
        self.flash_lbl.text = ""


# ---------- uygulama ----------
class LanApp(App):
    title = "LAN"

    def build(self):
        if kivy is None:
            raise SystemExit("Bu uygulama için Kivy gerekli.")
        Window.clearcolor = get_color_from_hex(BG)
        # Klavye açılınca tum pencere resize olmasin (relayout kasmasi)
        try:
            Window.softinput_mode = "below_target"
        except Exception:
            pass
        self.events = queue.Queue()
        self.ctl = ControlClient(self.events)
        self.peers_by_proto = {"share": []}
        self.cur_name = self.saved_username()

        self.sm = ScreenManager()
        self.login_screen = LoginScreen(self, name="login")
        self.main_screen = MainScreen(self, name="main")
        self.sm.add_widget(self.login_screen)
        self.sm.add_widget(self.main_screen)
        self.sm.current = "login"

        self._drain_evt = Clock.schedule_interval(self._drain, 0.15)
        # servis yoksa her 1sn baglanma denemesi yerine 2.5sn
        self._conn_evt = Clock.schedule_interval(self._ensure_connection, 2.5)

        if on_android():
            Clock.schedule_once(lambda dt: self._android_start(), 1.5)
        return self.sm

    def _android_start(self):
        saf.init()
        start_service()

    def saved_username(self):
        cfg = _load_json(os.path.join(private_dir(), "config.json"), {})
        return cfg.get("username", "")

    def login(self, name):
        self.cur_name = name
        cfg = _load_json(os.path.join(private_dir(), "config.json"), {})
        cfg["username"] = name
        _save_json(os.path.join(private_dir(), "config.json"), cfg)
        self.sm.current = "main"
        self.main_screen.flash(f"Merhaba {name} 👋")
        if on_android():
            start_service()

    def _ensure_connection(self, _dt):
        if not self.ctl.connected:
            if self.ctl.connect():
                self.main_screen.status.text = "Servise bağlandı, kişiler aranıyor…"
                self.ctl.send({"cmd": "login", "username": self.cur_name or "Telefon"})
                self.ctl.send({"cmd": "peers", "proto": "share"})
                self.ctl.send({"cmd": "history"})
                self.main_screen.files_view.refresh()

    def _drain(self, _dt):
        try:
            while True:
                ev = self.events.get_nowait()
                self._on_event(ev)
        except queue.Empty:
            pass

    def _on_event(self, ev):
        kind = ev.get("ev")
        if kind == "peers":
            peers = [(p["ip"], p["port"], p["name"]) for p in ev.get("peers", [])]
            self.peers_by_proto[ev.get("proto", "share")] = peers
            self.main_screen.files_view.set_peers(peers)
        elif kind == "msg_in" or kind == "msg_out":
            pass  # sohbet kaldirildi; servis artık chat agini baslatmiyor
        elif kind == "file_in":
            self.main_screen.files_view.handle_file_event(
                ev.get("name", "dosya"), ev.get("path", ""), ev.get("size", 0))
        elif kind == "file_sent":
            self.main_screen.status.text = f"📤 {ev.get('name', '')} gönderildi."
            self.main_screen.flash(f"{ev.get('to', '')} → dosya gönderildi ✅")
        elif kind == "status":
            self.main_screen.status.text = ev.get("text", "")
        elif kind == "progress":
            value = ev.get("value")
            if value is None:
                self.main_screen.files_view.progress.value = 0
            else:
                self.main_screen.files_view.progress.value = float(value)
        elif kind == "history":
            for item in ev.get("items", []):
                if item.get("ev") == "msg_in":
                    continue  # eski sohbet kayitlari atlanir
                self._on_event(item)
        elif kind == "pong":
            self.main_screen.status.text = "Arka plan servisi çalışıyor."

    def flash(self, text):
        if self.main_screen:
            self.main_screen.flash(text)

    def on_stop(self):
        try:
            self.ctl.close()
        except Exception:
            pass


def main():
    if kivy is None:
        print("Kivy kurulu değil. APK derlemesi buildozer ile yapılır.")
        return
    LanApp().run()


if __name__ == "__main__":
    main()