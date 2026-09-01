# 📱 LAN — Android APK (GitHub Actions ile derleme)

Bu klasör, `LANChat` / `LANShare` masaüstü uygulamalarının **telefon sürümüdür**.
Telefon ile PC aynı yerel ağda (Wi-Fi) birbirini otomatik bulur; mesaj ve dosya
gönderir; **arka planda uygulama kapalıyken bile çalışır** ve **telefon
açıldığında otomatik başlar**.

---

## Neler yapıldı?

| Dosya | Açıklama |
|---|---|
| `mobile/main.py` | Kivy arayüzü (Kişi listesi + mesajlaşma + dosya gönder/al + dışa aktar) |
| `mobile/lan_core.py` | Ağ çekirdeği — masaüstü ile **birebir aynı protokol** |
| `mobile/services/lan_service.py` | **Arka plan servisi**: uygulama kapalıyken LAN dinler, mesaj/dosya alır, bildirim atar |
| `mobile/saf.py` | Android dosya seçimi / dışa aktarma (SAF, ek kütüphane yok) |
| `mobile/src/org/lan/lanmobile/BootReceiver.java` | **Telefon açılınca** servisi otomatik başlatır |
| `mobile/extra_manifest.xml` | İzinler + boot receiver bildirimi |
| `buildozer.spec` | APK derleme ayarları |
| `.github/workflows/build-apk.yml` | **Bulutta (GitHub) ücretsiz derleme otomasyonu** |
| `test_engine.py` | Ağ + servis uçtan uca testi (Windows'ta hemen çalışır) |

## Arka planda çalışma nasıl sağlandı?

- **Foreground + sticky servis**: Kalıcı bir bildirim gösterir; uygulamadan
  ayrılsan bile LAN dinlemesi sürer; sistem öldürürse yeniden başlatır.
- **BootReceiver**: `BOOT_COMPLETED` ile telefon açıldığında servis başlar.
- **WakeLock + MulticastLock**: Doze ekran-cüzdanı sırasında ağ dinlemesi durmaz.
- Ekran kapalıyken gelen **mesaj/dosya bilgisi bildirim** olarak düşer.

> ⚠️ **Not (Android 14+):** `dataSync` ön plan servis türünün günlük sınırı
> ~6 saattir. Bazı cihazlarda sistem servisi o gün için durdurabilir; telefon
> yeniden başlayınca servis tekrar başlar. Bu, mağaza dışı (sideload) uygulamada
> mümkün olan en iyi yaklaşımdır.

---

## 📥 APK'yi ücretsiz derleme (GitHub Actions) — adım adım

1. **GitHub hesabı aç** → https://github.com (ücretsiz).

2. **Yeni depo oluştur** (Repository):
   - Name: `lan`
   - **Public** seç (bedava derleme dakikası için; Private de çalışır ama sınırlıdır)
   - Başka seçeneği değiştirme → **Create repository**

3. Bu klasörü GitHub'a gönder (VS Code açıkken):
   - Sol kenar çubuğundan **Source Control** (🪵) simgesini aç
   - Aşağıdaki alana `LAN yükle` yaz → **Commit** işaretine bas
   - **Publish Branch** → GitHub'da az önce açtığın `lan` deposunu seç → Gönder

   *(Ya da komutla: `git init && git add -A && git commit -m "LAN" && git remote add origin https://github.com/KULLANICI/lan.git && git push -u origin main`)*

4. **Derlemeyi başlat:**
   - GitHub'da depona gir → üstte **Actions** sekmesi
   - Sol listeden **APK Derle** iş akışını bul
   - **Run workflow** → yeşil butona bas
   - İlk derleme **~25-45 dakika** sürer (SDK/NDK indirir).

5. **APK'yi indir:**
   - İş akışı bitince **lan-apk** artifact'ini görürsün → tıkla → **Download**
   - `lanmobile-0.1.0-arm64-v8a-debug.apk` dosyasını telefona at (WhatsApp'la
     "kendine gönder", USB, Drive… hangisi kolaysa)

6. **Telefona kur:**
   - Telefonda: Ayarlar → Uygulamalar → özel erişim → **Bilinmeyen kaynaklar** /
     "Bu uygulamaya kaynağa izin ver" (tarayıcı/Gmail için) iznini ver
   - APK'ya dokun → **Kur**
   - Aç → kullanıcı adını yaz → **Başla**
   - Bildirim iznini **İzin ver** yap (arka plan bildirimleri için)

> 💡 **Yeni sürüm derlemek:** değişiklik yap → `git add -A` → `git commit`
> → `git push` → Actions'tan tekrar **Run workflow**. Artifact hep son sürümü verir.

---

## Telefon + PC birlikte çalışması

| İş | Nasıl |
|---|---|
| PC'de LANChat/LANShare | `LANChat.exe` / `LANShare.exe` çalıştır (Windows) — kök klasöründe zaten var |
| Telefonda uygulama | Aç → aynı Wi-Fi'ya bağlı olduktan sonra PC kişisi otomatik görünür |
| Telefon kapalıyken alım | Arka plan servisi çalışır; gelen dosyaları **uygulama özel klasörüne** indirir; uygulama bölümünden **İndir & Paylaş** ile Galeri/Drive/WhatsApp'a çıkar |
| PC ↔ telefon dosya | İki yönlü çalışır; klasör/zip desteği masaüstünde olduğu gibi |

### Önemli kurulum notları
- İkisi de **aynı Wi-Fi ağında** olmalı (AP'ler AP-isolation kullanmamalı).
- İlk kez Windows Güvenlik Duvarı sorarsa **“Erişime izin ver”** de.
- Alınan dosyalar önce uygulama içi depoda tutulur; telefonun normal klasörlerine
  **"İndir & Paylaş"** butonuyla dışa aktarılır (Android'in güvenlik modeli gereği).

---

## Yerinde test etme (Windows, Android gerektirmez)

```powershell
python test_engine.py
```

İki telefonu simüle edip keşif → mesaj → dosya → arka plan servisi komutlarını
uçtan uca doğrular.

---

## İstersen değiştirebileceklerin

- **Kullanıcı adı**: `mobile/main.py` giriş ekranında tutulur, servis `config.json`
  dosyasından okur.
- **Simge**: `mobile/icon.png` (512×512) — kendi resminle değiştir.
- **Uygulama adı / paket**: `buildozer.spec` içinde `title` ve `package.*`.

> Derleme için WSL/Docker **gerekmez**; hepsi GitHub'ın ücretsiz Linux sunucusunda
> otomatik yapılır. (Buildozer Windows'ta APK derleyemez.)