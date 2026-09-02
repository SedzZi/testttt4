#!/usr/bin/env bash
# CI hazirlik adimi:
#  1) p4a'yi buildozer'in bekledigi yere klonla (yoksa)
#  2) BootReceiver yamasini uygula
# Sonrasinda buildozer, url/branch eslestigi icin bu kopyayi oldugu gibi
# kullanir (yeniden klonlamaz, yamayi ezmez).
set -e

cd "$(dirname "$0")/.."   # depo koku

P4A_DIR=".buildozer/android/platform/python-for-android"
P4A_URL="https://github.com/kivy/python-for-android.git"
P4A_BRANCH="master"       # buildozer.spec: p4a.branch ile ayni olmali

if [ ! -d "$P4A_DIR/.git" ]; then
    echo "ci_prepare: p4a klonlaniyor ($P4A_BRANCH) -> $P4A_DIR"
    git clone -b "$P4A_BRANCH" --single-branch "$P4A_URL" "$P4A_DIR"
else
    echo "ci_prepare: p4a zaten mevcut, klonlama atlandi"
fi

python3 mobile/ci_patch_p4a.py "$P4A_DIR"

# pip 26 regresyonu: android etiketli wheel'i cozumlemede secip kurulumda
# reddediyor ("is not a supported wheel on this platform"). Venv pip'ini
# sagli 25.3 surumune sabitle.
BUILD_PY="$P4A_DIR/pythonforandroid/build.py"
if grep -q "pip install -U pip" "$BUILD_PY"; then
    sed -i "s/pip install -U pip/pip install -U 'pip==25.3'/" "$BUILD_PY"
    echo "ci_prepare: build.py venv pip'i 25.3'e sabitlendi"
else
    echo "ci_prepare: build.py pip yamasi zaten uygulanmis veya desen degisti"
fi

# gradle ciktisi _tail=20 ile loglaniyor -> manifest merger hatasinin gercek
# nedeni ("Caused by: ...") log'a hic dusmuyor. --stacktrace ekle ve ciktiyi
# genislet.
TC_PY="$P4A_DIR/pythonforandroid/toolchain.py"
if grep -q 'shprint(gradlew, "clean", gradle_task, _tail=20,' "$TC_PY"; then
    sed -i 's/shprint(gradlew, "clean", gradle_task, _tail=20,/shprint(gradlew, "clean", gradle_task, "--stacktrace", _tail=400,/' "$TC_PY"
    echo "ci_prepare: gradle --stacktrace ve genis cikti yamasi uygulandi"
else
    echo "ci_prepare: gradle yamasi zaten uygulanmis veya desen degisti"
fi

# charset_normalizer'in p4a recipe'si yok; pip --target ile C-eklentili
# android wheel'ini kuramiyor ("not a supported wheel on this platform").
# Saf python recipe ekleyerek pip yolunu bypass ediyoruz (p4a'nin onerdigi
# cozum: "IF THIS FAILS, THE MODULES MAY NEED A RECIPE").
CN_DIR="$P4A_DIR/pythonforandroid/recipes/charset_normalizer"
if [ ! -f "$CN_DIR/__init__.py" ]; then
    mkdir -p "$CN_DIR"
    cat > "$CN_DIR/__init__.py" <<'EOF'
from pythonforandroid.recipe import PythonRecipe


class CharsetNormalizerRecipe(PythonRecipe):
    name = 'charset_normalizer'
    version = '3.4.2'
    # dosya adi underscore'lu (charset_normalizer-...), harf yolu
    # package adiyla (charset-normalizer) ayri seyler
    url = ('https://pypi.org/packages/source/c/charset-normalizer/'
           'charset_normalizer-{version}.tar.gz')
    depends = ['python3', 'setuptools']
    # saf python: dogrudan hedef site-packages'a kurulur,
    # pip'in android wheel kontrolune takilmaz
    call_hostpython_via_targetpython = False
    install_in_hostpython = False


recipe = CharsetNormalizerRecipe()
EOF
    echo "ci_prepare: charset_normalizer recipe eklendi ($CN_DIR)"
else
    echo "ci_prepare: charset_normalizer recipe zaten mevcut"
fi

# ---- Release imza anahtari ---------------------------------------------
# Debug APK (android:debuggable=true) ART uzerinde belirgin yavas calisir;
# release derleme + sabit imza hem hiz hem de "guncelleme ustune kurulur"
# avantaji saglar. Anahtar Actions cache'inde yasar; ilk derlemede uretilir.
# NOT: buildozer master imzayi spec'ten degil, P4A_RELEASE_* ortam
# degiskenlerinden alir (workflow'da export edilir); android.release_artifact
# = apk buildozer.spec'te ayarli (yoksa varsayilan aab uretir).
KS="${LAN_KEYSTORE:-/tmp/lan-release.keystore}"
if [ ! -f "$KS" ]; then
    if keytool -genkeypair -v -keystore "$KS" -alias lan -keyalg RSA \
        -keysize 2048 -validity 10000 -storepass lan12345 \
        -keypass lan12345 \
        -dname "CN=LAN, OU=LAN, O=LAN, L=Istanbul, C=TR" >/dev/null 2>&1; then
        echo "ci_prepare: release imza anahtari uretildi: $KS"
    else
        echo "ci_prepare: UYARI keytool bulunamadi; APK release-unsigned olur"
    fi
else
    echo "ci_prepare: imza anahtari cache'ten hazir: $KS"
fi
