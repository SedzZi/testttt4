#!/usr/bin/env python3
"""CI yardimcisi: p4a'nin AndroidManifest sablonuna BootReceiver'i <application>
icine ekler.

Neden gerekli?
  buildozer'in `android.extra_manifest_xml` secenegi XML'i <manifest> KOK
  seviyesine ekler. <receiver> orada gecersizdir (aapt2 hatasi:
  "unexpected element <receiver> found in <manifest>"). p4a'nin
  <application> icine eleman ekleyen bir secenegi yoktur; bu yüzden
  derlemeden once sablon yamalanir.
"""
import os
import sys

RECEIVER_XML = """        <receiver
            android:name="org.lan.lanmobile.BootReceiver"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
                <action android:name="android.intent.action.MY_PACKAGE_REPLACED" />
            </intent-filter>
        </receiver>
"""

# Sablonda <application ...> acilis etiketini takip eden guvenilir cengeller
ANCHORS = (
    'android:extractNativeLibs="true" >',
    '{% for l in args.android_used_libs %}',
)


def main() -> int:
    if len(sys.argv) != 2:
        print("kullanim: ci_patch_p4a.py <p4a_klasoru>", file=sys.stderr)
        return 1

    p4a_dir = sys.argv[1]
    tmpl_path = os.path.join(
        p4a_dir, "pythonforandroid", "bootstraps", "_sdl_common",
        "build", "templates", "AndroidManifest.tmpl.xml",
    )
    if not os.path.isfile(tmpl_path):
        print(f"HATA: sablon bulunamadi: {tmpl_path}", file=sys.stderr)
        return 1

    with open(tmpl_path, encoding="utf-8") as f:
        src = f.read()

    if "org.lan.lanmobile.BootReceiver" in src:
        print("ci_patch_p4a: yama zaten uygulanmis, atlandi")
        return 0

    for anchor in ANCHORS:
        if anchor in src:
            src = src.replace(anchor, anchor + "\n" + RECEIVER_XML, 1)
            break
    else:
        print("HATA: sablonda cengel satiri bulunamadi; p4a surumu degismis "
              "olabilir (ANCHORS listesini guncelleyin)", file=sys.stderr)
        return 1

    with open(tmpl_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)

    print(f"ci_patch_p4a: BootReceiver sablona eklendi -> {tmpl_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
