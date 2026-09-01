# -*- coding: utf-8 -*-
"""
Android Storage Access Framework (SAF) köprüsü — pyjnius + android.activity ile.
Harici kütüphane kullanmaz; masaüstünde sessizce devre dışı kalır.

Kullanım:
    import saf
    saf.init()                      # uygulama açılışında
    def on_pick(uri):
        path = saf.read_uri_to_file(uri, "/tmp/kopya.bin")
    saf.pick_file(on_pick)

    def on_create(uri):
        saf.write_file_to_uri("/data/kaynak.bin", uri)
    saf.create_file(on_create, "dosya.bin")
"""
import os

_JNIUS = None
Intent = PythonActivity = Uri = None

REQ_PICK = 7001
REQ_CREATE = 7002
RESULT_OK = -1

_pending = {}


def init():
    """Activity sonuç köprüsünü kur. Android'de çağrılmalı, boş dönerse SAF kapalı."""
    global _JNIUS, Intent, PythonActivity, Uri
    try:
        from jnius import autoclass
        from android.activity import bind
        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
    except Exception:
        _JNIUS = False
        return False
    _JNIUS = True
    bind(on_activity_result=_on_activity_result)
    return True


def _on_activity_result(request_code, result_code, data):
    cb = _pending.pop(request_code, None)
    if cb and result_code == RESULT_OK and data is not None:
        uri = data.getData().toString()
        cb(uri)
    elif cb:
        cb(None)


def pick_file(callback):
    """Dosya seçim ekranını açar. Sonuç: callback(uri_string | None) (arka iş parçacığı)."""
    if not _JNIUS:
        callback(None)
        return
    intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    intent.setType("*/*")
    _pending[REQ_PICK] = callback
    try:
        PythonActivity.mActivity.startActivityForResult(intent, REQ_PICK)
    except Exception:
        _pending.pop(REQ_PICK, None)
        callback(None)


def create_file(callback, name="dosya"):
    """Nereye yazılacağını kullanıcının seçtiği 'yeni dosya' ekranı. URI döner."""
    if not _JNIUS:
        callback(None)
        return
    intent = Intent(Intent.ACTION_CREATE_DOCUMENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    intent.setType("application/octet-stream")
    intent.putExtra(Intent.EXTRA_TITLE, name)
    _pending[REQ_CREATE] = callback
    try:
        PythonActivity.mActivity.startActivityForResult(intent, REQ_CREATE)
    except Exception:
        _pending.pop(REQ_CREATE, None)
        callback(None)


def _resolver():
    return PythonActivity.mActivity.getContentResolver()


def display_name(uri_str):
    """SAF kaynağının görünen adını döner (ör. "rapor.pdf"); bulunamazsa None."""
    if not _JNIUS:
        return None
    uri = Uri.parse(uri_str)
    cursor = None
    try:
        cursor = _resolver().query(
            uri, _StringArray(["_display_name"]), None, None, None)
        if cursor and cursor.moveToFirst():
            idx = cursor.getColumnIndex("_display_name")
            if idx >= 0:
                name = cursor.getString(idx)
                return name.replace("/", "_").replace("\\", "_") or None
    except Exception:
        pass
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
    return None


def _StringArray(items):
    from jnius import autoclass
    JavaArray = autoclass("java.lang.String")
    arr = [None] * len(items)
    for i, val in enumerate(items):
        arr[i] = JavaArray(val)
    return arr


def read_uri_to_file(uri_str, dest_path):
    """content:// kaynağını yerel dosyaya kopyalar; dest_path döner."""
    if not _JNIUS:
        return None
    uri = Uri.parse(uri_str)
    stream = _resolver().openInputStream(uri)
    try:
        with open(dest_path, "wb") as f:
            buf = _jbytearray(64 * 1024)
            while True:
                n = stream.read(buf)
                if n < 0:
                    break
                f.write(bytes(buf[:n]))
    finally:
        stream.close()
    return dest_path


def write_file_to_uri(src_path, uri_str):
    """Yerel dosyayı content:// hedefine kopyalar."""
    if not _JNIUS:
        return False
    uri = Uri.parse(uri_str)
    stream = _resolver().openOutputStream(uri, "w")
    try:
        with open(src_path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                stream.write(_jbytearray(chunk))
    finally:
        stream.close()
    return True


def share_uri(uri_str, title="Paylaş"):
    """SAF'ten alınan bir URI'yi Android paylaşım ekranıyla başka uygulamaya iletir."""
    if not _JNIUS:
        return
    intent = Intent(Intent.ACTION_SEND)
    intent.setType("*/*")
    intent.putExtra(Intent.EXTRA_STREAM, Uri.parse(uri_str))
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    chooser = Intent.createChooser(intent, title)
    chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try:
        PythonActivity.mActivity.startActivity(chooser)
    except Exception:
        pass


def _jbytearray(data):
    from jnius import jbytearray
    return jbytearray(data)