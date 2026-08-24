#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if [ ! -f "assets/kralyavuz_icon.icns" ]; then
    echo "Hata: assets/kralyavuz_icon.icns bulunamadı." >&2
    exit 1
fi

python3 -m PyInstaller --noconfirm --clean KraLYavuz.spec

echo "Hazır uygulama: dist/KraLYavuz.app"
