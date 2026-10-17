#!/usr/bin/env bash
# Build a standalone Linux bundle and pack it as a tar.gz for Releases.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python - <<'PY'
import pathlib
import re

text = pathlib.Path("app/main_window.py").read_text(encoding="utf-8")
match = re.search(r'APP_VERSION = "([^"]+)"', text)
print(match.group(1) if match else "0.0.0")
PY
)"

OUT="dist_linux"
DIST="$OUT/entry.dist"
BUNDLE="release/linux/NekoNightMusic-Linux"
TAR="dist_installer/Neko.night-Music-Linux-x86_64-${VERSION}.tar.gz"

rm -rf "$OUT" "$BUNDLE"
mkdir -p "$OUT" "release/linux" "dist_installer"

python -m nuitka --standalone --assume-yes-for-downloads \
    --enable-plugin=pyqt6 \
    --include-qt-plugins=multimedia \
    --include-module=PyQt6.QtNetwork \
    --include-data-file=app/theme.qss=app/theme.qss \
    "--include-data-files=app/ncm_core*.so=app/" \
    --include-data-dir=resources=resources \
    --include-package=mutagen \
    --include-package=websocket \
    --include-package=qrcode \
    --output-dir="$OUT" \
    --output-filename=NekoNightMusic \
    app/entry.py

SO="$(ls "$DIST"/app/ncm_core*.so | head -n 1)"
SO_NAME="$(basename "$SO")"

mkdir -p "$BUNDLE"
cp -a "$DIST"/. "$BUNDLE"/

# Bundle non-glibc shared libraries required by the C++ extension.
while read -r lib; do
    [ -n "$lib" ] || continue
    base="$(basename "$lib")"
    case "$base" in
        libc.so.*|libm.so.*|libpthread.so.*|libdl.so.*|librt.so.*|libresolv.so.*|libutil.so.*|ld-linux*) continue ;;
    esac
    cp -L "$lib" "$BUNDLE/app/$base"
done < <(ldd "$BUNDLE/app/$SO_NAME" | awk '/=> \// {print $3}')

if command -v patchelf >/dev/null 2>&1; then
    patchelf --set-rpath '$ORIGIN' "$BUNDLE/app/$SO_NAME"
fi

cp packaging/linux/NekoNightMusic.desktop "$BUNDLE/"
cp packaging/linux/nekonight.png "$BUNDLE/"
cp packaging/linux/install-desktop.sh "$BUNDLE/"
chmod +x "$BUNDLE/install-desktop.sh"

tar -C release/linux -czf "$TAR" NekoNightMusic-Linux
echo "package: $TAR"
