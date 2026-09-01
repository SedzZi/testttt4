# -*- coding: utf-8 -*-
"""
LAN mobil paketi fonksiyonel testi (Windows/masaüstünde çalışır, Android gerekmez).

1) lan_core: iki motor + tel protokolü (keşif, mesaj, dosya) doğrular.
2) lan_service: arka plan servisinin motor + kontrol sunucusu + komut akışını
   gerçek bir istemci ile uçtan uca çalıştırır.

Çalıştır:  python test_engine.py
"""
import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.join(ROOT, "mobile")
sys.path.insert(0, MOBILE)

import lan_core  # noqa: E402

USER_HOME = os.path.expanduser("~")
FALLBACK = os.path.join(USER_HOME, ".lanbackup")  # servisin masaüstü özel klasörü


def clean_fallback():
    shutil.rmtree(FALLBACK, ignore_errors=True)


def wait_cond(cond, timeout=8.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def test_lan_core():
    print("=== 1) lan_core: tel protokol testi ===")
    tmp = tempfile.mkdtemp()
    q1, q2 = queue.Queue(), queue.Queue()
    a = lan_core.Network("Telefon-Bir", q1, proto="chat", save_dir=tmp)
    b = lan_core.Network("Masaüstü", q2, proto="chat", save_dir=tmp)
    a.start(); b.start()

    def found():
        return any(p["name"] == "Telefon-Bir" for p in b.get_peers()) and \
               any(p["name"] == "Masaüstü" for p in a.get_peers())
    assert wait_cond(found), f"keşif başarısız a={a.get_peers()} b={b.get_peers()}"
    print("[OK] karşılıklı keşif")

    bob = next(p for p in a.get_peers() if p["name"] == "Masaüstü")
    alice = next(p for p in b.get_peers() if p["name"] == "Telefon-Bir")
    a.send_message(bob, "merhaba masaüstü")

    got_msg = wait_cond(lambda: any(e[0] == "msg_in" for e in list(b.ui_queue.queue)))
    assert got_msg, "mesaj alınamadı"
    # kuyruğu boşalt
    while not b.ui_queue.empty():
        b.ui_queue.get()
    print("[OK] mesaj iletildi")

    src = os.path.join(tmp, "ornek.bin")
    with open(src, "wb") as f:
        f.write(os.urandom(1 * 1024 * 1024))
    a.send_file(bob, src)

    target = [None]
    def received():
        while not b.ui_queue.empty():
            e = b.ui_queue.get_nowait()
            if e[0] == "file_in":
                target[0] = e[2]
                return True
        return False
    assert wait_cond(received, timeout=15), "dosya alınamadı"
    assert open(target[0], "rb").read() == open(src, "rb").read(), "içerik farklı"
    print("[OK] 1 MB dosya birebir alındı")

    a.stop(); b.stop()
    shutil.rmtree(tmp, ignore_errors=True)
    print("=== 1) lan_core TAMAM ===\n")


def test_service_roundtrip():
    print("=== 2) lan_service: motor + kontrol sunucusu uçtan uca ===")
    clean_fallback()
    import services.lan_service as svc

    # servisi hizmete sok (main() yerine test amaçlı kısımlar)
    svc._start_engine()
    port = svc._start_control_server()
    assert port, "kontrol portu açılamadı"
    assert svc._state["networks"], "motorlar başlamadı"
    print("[OK] motorlar + kontrol portu:", port, svc._state["networks"].keys())

    def client():
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        sock.settimeout(2.0)
        return sock

    # UI rolünü oyna
    c = client()
    buf = b""
    def send(obj):
        nonlocal buf
        c.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        # cevabı bekle
        while b"\n" not in buf:
            buf += c.recv(65536)
        line, buf = buf.split(b"\n", 1)
        return json.loads(line)

    r = send({"cmd": "ping"})
    assert r.get("pong") is True, r
    print("[OK] ping/pong")

    r = send({"cmd": "login", "username": "TestTelefon"})
    assert r.get("ok"), r
    assert svc._state["username"] == "TestTelefon"
    print("[OK] login:", svc._state["username"])

    # ikinci bir istemci gibi masaüstü motoru da başlat (tek başına aynı makinede)
    svc._state["queues"]  # erişim
    # share motorundan peer bekleyelim: ikinci makine yerine aynı süreçte ikinci servis?
    # Basit kanıt: chat motoru tel ile bizim test motorumuzu görebilmeli —
    # burada uçtan uca gerçek LAN keşfi zaten (1)'de doğrulandı; sunucu olayını
    # doğrulamak için ikinci svc motoru yerine sahte peer events broadcast yapalım.
    svc._broadcast({"ev": "peers", "proto": "chat",
                    "peers": [{"name": "Masaüstü", "ip": "192.168.1.5", "port": 50506}]})
    got = c.recv(65536).decode("utf-8")
    assert '"ev": "peers"' in got, got
    print("[OK] olay yayını:", got.strip()[:90])

    # geçmişe gelen mesaj ekleyip history komutu ile çek
    svc._push_history({"ev": "msg_in", "proto": "chat", "from": "Masaüstü",
                       "text": "deneme", "ts": time.time()})
    r = send({"cmd": "history"})
    assert r.get("ev") == "history" and any(i.get("text") == "deneme"
                                            for i in r["items"]), r
    print("[OK] geçmiş")

    # durum komutu
    r = send({"cmd": "status"})
    assert r.get("ok") and r.get("username") == "TestTelefon", r
    print("[OK] durum:", r.get("nets"))

    c.close()
    clean_fallback()
    print("=== 2) lan_service TAMAM ===\n")


if __name__ == "__main__":
    test_lan_core()
    test_service_roundtrip()
    print("TÜM TESTLER GEÇTİ ✅")