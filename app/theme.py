# -*- coding: utf-8 -*-
"""Theme variables + QSS loader.

All visual constants live in PALETTES below; theme.qss references them as
``@name@`` placeholders so a new skin is just another dict entry + qss.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
QSS_FILE = BASE_DIR / "theme.qss"


def qss_path():
    import sys

    candidates = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.append(base / "app" / "theme.qss")
        candidates.append(Path(sys.executable).parent / "app" / "theme.qss")
    candidates.append(BASE_DIR / "theme.qss")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


PALETTES = {
    "warm": {
        "icon": "#F5F2EE",
        "icon_hover": "#FFFFFF",
        "text": "#FFFFFF",
        "text_dim": "rgba(255, 255, 255, 0.74)",
        "icon_hover_bg": "rgba(255, 255, 255, 0.18)",
        "capsule_bg": "rgba(255, 255, 255, 0.14)",
        "capsule_hover": "rgba(255, 255, 255, 0.26)",
        "capsule_active": "rgba(255, 255, 255, 0.36)",
        "play_bg": "#FFFFFF",
        "play_hover": "#FFFFFF",
        "bar_bg": "rgba(16, 13, 12, 0.74)",
        "divider": "rgba(255, 255, 255, 0.24)",
        "slider_fill": "#FFFFFF",
        "radius": "14",
        "bg_center": "#37312b",
        "bg_edge": "#0c0a09",
    }
}

DEFAULT_THEME = "warm"


def palette(name=None):
    return dict(PALETTES[name or DEFAULT_THEME])


def load_qss(name=None):
    values = palette(name)
    text = qss_path().read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"@{key}@", str(value))
    return text
