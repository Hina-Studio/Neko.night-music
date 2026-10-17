Neko.night - Music (Linux x86_64)
==================================

Run:
  ./NekoNightMusic

Optional desktop menu entry:
  ./install-desktop.sh

Requirements:
  - glibc 2.35+ (Ubuntu 22.04+ / Debian 12+ / Fedora 35+)
  - Audio output needs libpulse0 or libasound2 (usually preinstalled)
  - Qt runtime libs are bundled; if the window fails under Wayland, try:
      QT_QPA_PLATFORM=xcb ./NekoNightMusic

Configuration:
  ~/.config/NekoNightMusic/config
