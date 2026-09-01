# LAN — Android APK derleme ayarları (buildozer)
# Derleme GitHub Actions'da yapılır (workflow: .github/workflows/build-apk.yml)

[app]

# (str) Uygulama başlığı
title = LAN

# (str) Paket adı — Java sınıf paket adresini belirler
package.name = lanmobile

# (str) Paket domaini — tam Java paketi: org.lan.lanmobile
package.domain = org.lan

# (str) Kaynak kodun bulunduğu klasör (main.py burada)
source.dir = mobile

# (list) Paketlenecek uzantılar
source.include_exts = py,json,png

# (str) Sürüm
version = 0.1.0

# (list) Gereksinimler
# python3, kivy (arayüz), pyjnius (Java köprüsü), android (p4a android modülü)
# charset_normalizer: kivy->requests zincirinin bağımlılığı; recipe olarak
# derlenmesi için burada açıkça listelenir (pip android wheel'ini kuramaz)
requirements = python3,kivy==2.3.1,pyjnius,android,charset_normalizer

# (list) Yönlendirme
orientation = portrait

# (bool) Tam ekran kapalı
fullscreen = 0

# (list) Arka plan servisleri
# lanengine = servis adı -> ServiceLanengine Java sınıfı oluşur
# foreground: bildirimle ön plan servisi; sticky: kapanınca sistem yeniden başlatır
# foregroundServiceType=dataSync: Android 14+ zorunlu tür
services = lanengine:services/lan_service.py:foreground:sticky:foregroundServiceType=dataSync

# (str) Uygulama simgesi
icon.filename = mobile/icon.png

#
# Android özel
#

# (list) İzinler
android.permissions = android.permission.INTERNET,android.permission.ACCESS_NETWORK_STATE,android.permission.ACCESS_WIFI_STATE,android.permission.CHANGE_WIFI_MULTICAST_STATE,android.permission.WAKE_LOCK,android.permission.RECEIVE_BOOT_COMPLETED,android.permission.FOREGROUND_SERVICE,android.permission.FOREGROUND_SERVICE_DATA_SYNC,android.permission.POST_NOTIFICATIONS

# (str) Eklenen Java kaynak klasörü (BootReceiver burada)
android.add_src = mobile/src

# (str) AndroidManifest'e gömülecek ek XML (boot receiver + izinler)
android.extra_manifest_xml = mobile/extra_manifest.xml

# (int) Hedef Android API
android.api = 34

# (int) Minimum Android API
android.minapi = 24

# (int) NDK API seviyesi
android.ndk_api = 24

# (str) NDK sürümü
android.ndk = 25b

# (list) Mimari — arm64 (günümüz telefonlarının hepsi; derleme hızlı)
android.archs = arm64-v8a

# (bool) Lisansları otomatik kabul et (CI için)
android.accept_sdk_license = True

# (str) p4a dalı
# NOT: develop dali kararsizdir (libthorvg tarifi arm64'te patlar);
# buildozer'in varsayilani ve kivy 2.3.1 ile uyumlu olan master kullanilir.
p4a.branch = master

[buildozer]

# (int) Log seviyesi
log_level = 2