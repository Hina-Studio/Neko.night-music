# -*- coding: utf-8 -*-
"""Bundled font loading (HarmonyOS Sans SC + Inter + JetBrains Mono)."""
import sys
from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase

FONT_FILES = (
    "Inter-Regular.ttf",
    "Inter-Medium.ttf",
    "HarmonyOS_Sans_SC_Regular.ttf",
    "HarmonyOS_Sans_SC_Medium.ttf",
    "JetBrainsMono-Regular.ttf",
)

FAMILY_STACK = (
    "Inter",
    "HarmonyOS Sans SC",
    "MiSans",
    "Noto Sans SC",
    "Microsoft YaHei UI",
    "Segoe UI",
)
FAMILY_QSS = '"Inter", "HarmonyOS Sans SC", "MiSans", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif'
MONO_QSS = '"JetBrains Mono", "Consolas", "Cascadia Mono", monospace'


def fonts_dir():
    base = Path(__file__).resolve().parent.parent / "resources" / "fonts"
    if getattr(sys, "frozen", False):
        candidates = []
        mei = getattr(sys, "_MEIPASS", "")
        if mei:
            candidates.append(Path(mei) / "resources" / "fonts")
        candidates.append(Path(sys.executable).resolve().parent / "resources" / "fonts")
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
    return base


def load_fonts():
    loaded = []
    directory = fonts_dir()
    for name in FONT_FILES:
        path = directory / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            loaded.extend(QFontDatabase.applicationFontFamilies(font_id))
    return loaded


def apply_base_font(app):
    load_fonts()
    font = QFont()
    font.setFamilies(list(FAMILY_STACK))
    font.setPointSize(10)
    font.setWeight(QFont.Weight.Normal)
    app.setFont(font)
    return font
