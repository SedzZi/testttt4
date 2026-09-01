# -*- coding: utf-8 -*-
"""LANShare ağ katmanı hızlı testi (GUI olmadan)."""
import os, queue, shutil, tempfile, time, zipfile
import lan_share

# kayıt klasörünü geçici yap (masaüstünü kirletmemek için)
tmp = tempfile.mkdtemp()
lan_share.get_save_dir = lambda: tmp

q1, q2 = queue.Queue(), queue.Queue()
a = lan_share.Network("Alice", q1)
b = lan_share.Network("Bob", q2)
a.start(); b.start()
time.sleep(3)

peers_a = a.get_peers()
assert any(p["name"] == "Bob" for p in peers_a), f"Keşif başarısız: {peers_a}"
print("[OK] Keşif:", [(p["name"], p["ip"], p["port"]) for p in peers_a])
bob = next(p for p in peers_a if p["name"] == "Bob")
print("bob peer:", bob, "| alice port:", a.tcp_port)

test_path = os.path.join(tmp, "test_dosyasi.bin")
with open(test_path, "wb") as f:
    f.write(os.urandom(3 * 1024 * 1024))  # 3 MB

a.send_file(bob, test_path)

# klasör testi: klasörü zip'leyip gönder (uygulamanın yaptığı gibi)
folder = os.path.join(tmp, "test_klasoru")
os.makedirs(folder, exist_ok=True)
with open(os.path.join(folder, "ic_dosya.txt"), "w") as f:
    f.write("klasör içi")
archive = shutil.make_archive(os.path.join(tmp, "test_klasoru"), "zip",
                              root_dir=tmp, base_dir="test_klasoru")
a.send_file(bob, archive, display_name=os.path.basename(archive))

deadline = time.time() + 20
received, received_zip = None, None
while time.time() < deadline and not (received and received_zip):
    try:
        item = b.ui_queue.get(timeout=0.5)
    except queue.Empty:
        continue
    if item[0] == "file_in":
        if item[2].endswith(".zip"):
            received_zip = item[2]
        else:
            received = item[2]

assert received and os.path.exists(received), "Dosya alınamadı"
assert open(received, "rb").read() == open(test_path, "rb").read(), "İçerik farklı!"
print("[OK] 3 MB dosya birebir alındı:", os.path.basename(received))

assert received_zip and os.path.exists(received_zip), "Klasör zip'i alınamadı"
with zipfile.ZipFile(received_zip) as z:
    names = [n.replace("\\", "/") for n in z.namelist()]
    assert "test_klasoru/ic_dosya.txt" in names, f"Zip içeriği hatalı: {names}"
print("[OK] Klasör zip olarak alındı, içeriği doğrulandı:", os.path.basename(received_zip))

a.stop(); b.stop()
os.remove(received); os.remove(received_zip); os.remove(test_path)
shutil.rmtree(folder, ignore_errors=True)
print("TÜM TESTLER GEÇTİ")
