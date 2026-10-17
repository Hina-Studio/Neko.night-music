# -*- coding: utf-8 -*-
"""Runtime path helpers.

Frozen builds put user data in ``%LOCALAPPDATA%/NekoNightMusic/config``
unless a ``portable.txt`` marker sits next to the executable.
"""
import os
import sys
from pathlib import Path


def frozen():
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def app_dir():
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _local_data_root():
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not root:
        return None
    base = Path(root) / "NekoNightMusic"
    legacy = Path(root) / "NeteaseMusicStudio"
    if not base.exists() and legacy.is_dir():
        try:
            legacy.rename(base)
        except OSError:
            pass
    return base


def config_dir():
    if frozen():
        base = app_dir()
        if (base / "portable.txt").is_file():
            return base / "config"
        root = _local_data_root()
        if root:
            return root / "config"
        return base / "config"
    return Path(__file__).resolve().parent.parent / "config"
