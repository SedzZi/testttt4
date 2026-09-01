# -*- coding: utf-8 -*-
"""LANChat ağ katmanı hızlı fonksiyonel testi (GUI olmadan)."""
import os, queue, tempfile, time
from lan_chat import Network

q1, q2 = queue.Queue(), queue.Queue()
a = Network("Alice", q1)
b = Network("Bob", q2)
a.start(); b.start()
time.sleep(3)  # keşif duyuruları için bekle

pa = a.get_peers(); pb = b.get_peers()
assert any(p["name"] == "Bob" for p in pa), f"Alice Bob'u bulamadı: {pa}"
assert any(p["name"] == "Alice" for p in pb), f"Bob Alice'i bulamadı: {pb}"
print("[OK] Keşif çalışıyor:", [(p['name'], p['ip']) for p in pa])

bob = next(p for p in pa if p["name"] == "Bob")
alice = next(p for p in pb if p["name"] == "Alice")

# mesaj
a.send_message(bob, "merhaba dünya")
b.send_file(alice, __file__)  # bu test dosyasını gönder

deadline = time.time() + 10
got_msg, got_file = False, None
while time.time() < deadline and not (got_msg and got_file):
    try:
        item = b.ui_queue.get(timeout=0.5)
    except queue.Empty:
        item = q1.get_nowait() if not q1.empty() else ("none",)
    if item[0] == "message" and item[4] == "merhaba dünya":
        got_msg = True
    elif item[0] == "message" and item[3] == "file_in":
        got_file = item[4]

assert got_msg, "Mesaj alınamadı"
assert got_file and os.path.exists(got_file), "Dosya alınamadı"
assert open(got_file, encoding="utf-8").read() == open(__file__, encoding="utf-8").read(), "Dosya içeriği farklı"
print("[OK] Mesaj iletildi, dosya birebir alındı:", got_file)

a.stop(); b.stop()
os.remove(got_file)
print("TÜM TESTLER GEÇTİ")
