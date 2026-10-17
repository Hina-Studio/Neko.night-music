#!/usr/bin/env bash
# Register the app in the desktop menu (no root required).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$APPS" "$ICONS"
cp "$DIR/nekonight.png" "$ICONS/nekonight.png"
sed "s|^Exec=.*|Exec=\"$DIR/NekoNightMusic\"|" \
    "$DIR/NekoNightMusic.desktop" > "$APPS/NekoNightMusic.desktop"
update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "desktop entry installed: $APPS/NekoNightMusic.desktop"
