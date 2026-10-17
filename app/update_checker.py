# -*- coding: utf-8 -*-
"""Silent update checker against the public GitHub releases."""
import json

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

RELEASES_URL = "https://github.com/Hina-Studio/Neko.night-music/releases"
API_URL = (
    "https://api.github.com/repos/Hina-Studio/Neko.night-music/releases/latest"
)


def version_tuple(text):
    parts = []
    for chunk in str(text).strip().lstrip("vV").split("."):
        number = ""
        for char in chunk:
            if char.isdigit():
                number += char
            else:
                break
        parts.append(int(number or 0))
    return tuple(parts)


def is_newer(latest, current):
    latest_parts = list(version_tuple(latest))
    current_parts = list(version_tuple(current))
    length = max(len(latest_parts), len(current_parts))
    latest_parts += [0] * (length - len(latest_parts))
    current_parts += [0] * (length - len(current_parts))
    return tuple(latest_parts) > tuple(current_parts)


class UpdateChecker(QObject):
    """Fetch the latest release tag asynchronously."""

    checked = pyqtSignal(str, str)  # latest version, html url ("" on failure)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)

    def check(self):
        request = QNetworkRequest(QUrl(API_URL))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", b"Neko.night-Music-update-check")
        request.setTransferTimeout(10000)
        reply = self._manager.get(request)
        reply.finished.connect(lambda: self._on_finished(reply))

    def _on_finished(self, reply):
        tag = ""
        url = ""
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                payload = json.loads(
                    bytes(reply.readAll()).decode("utf-8", "ignore")
                )
                tag = str(payload.get("tag_name") or "").strip().lstrip("vV")
                url = str(payload.get("html_url") or RELEASES_URL)
        except (ValueError, TypeError):
            tag = ""
        reply.deleteLater()
        self.checked.emit(tag, url)
