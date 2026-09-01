# -*- coding: utf-8 -*-
"""Bağlantı hata ayıklama."""
import queue, socket, time
import lan_share

q = queue.Queue()
a = lan_share.Network("A", q)
a.start()
print("tcp_port:", a.tcp_port, "local_ip:", a.local_ip)
time.sleep(0.5)

candidates = ["127.0.0.1", a.local_ip, "172.19.64.1", "192.168.1.130"]
for ip in candidates:
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((ip, a.tcp_port))
        print(f"{ip:16} OK")
    except OSError as e:
        print(f"{ip:16} FAIL: {e}")
    finally:
        s.close()
a.stop()
