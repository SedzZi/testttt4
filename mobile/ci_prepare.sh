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
