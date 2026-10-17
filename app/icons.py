# -*- coding: utf-8 -*-
"""Uniform 1.5px linear SVG icons rendered on demand (no asset files)."""
from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QPushButton

SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="{width}" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

ICONS = {
    "play": '<path d="M8 5.2v13.6L19 12z"/>',
    "pause": '<path d="M9 5v14M15 5v14"/>',
    "prev": '<path d="M18.5 6v12L9.5 12z"/><path d="M6 5v14"/>',
    "next": '<path d="M5.5 6v12l9-6z"/><path d="M18 5v14"/>',
    "repeat": (
        '<path d="M17 3l3 3-3 3"/><path d="M20 6H8a4 4 0 0 0-4 4v1"/>'
        '<path d="M7 21l-3-3 3-3"/><path d="M4 18h12a4 4 0 0 0 4-4v-1"/>'
    ),
    "repeat_one": (
        '<path d="M17 3l3 3-3 3"/><path d="M20 6H8a4 4 0 0 0-4 4v1"/>'
        '<path d="M7 21l-3-3 3-3"/><path d="M4 18h12a4 4 0 0 0 4-4v-1"/>'
        '<path d="M11.2 10.8L12.6 9.8V15"/>'
    ),
    "shuffle": (
        '<path d="M3 6h3l4.5 6L15 6h4"/>'
        '<path d="M18 3.8L20.4 6 18 8.2"/>'
        '<path d="M3 18h3l4.5-6"/>'
        '<path d="M18 15.8L20.4 18 18 20.2"/>'
        '<path d="M15.4 15.4l1.2 1.6"/><path d="M17 18h3.4"/>'
    ),
    "heart": (
        '<path d="M12 20.2S3.8 15.4 2.6 10.6A5.1 5.1 0 0 1 12 7.6a5.1 5.1 0 0 1 '
        '9.4 3c-1.2 4.8-9.4 9.6-9.4 9.6z"/>'
    ),
    "heart_filled": (
        '<path d="M12 20.2S3.8 15.4 2.6 10.6A5.1 5.1 0 0 1 12 7.6a5.1 5.1 0 0 1 '
        '9.4 3c-1.2 4.8-9.4 9.6-9.4 9.6z" fill="{color}" stroke="none"/>'
    ),
    "playlist": (
        '<path d="M4 6h11M4 12h11M4 18h7"/><path d="M15.5 11.5l5 3-5 3z"/>'
    ),
    "volume": (
        '<path d="M4 9.5h3l4.5-3.2v11.4L7 14.5H4z"/>'
        '<path d="M15.5 9.5a3.6 3.6 0 0 1 0 5"/>'
    ),
    "more": (
        '<circle cx="5.5" cy="12" r="1" fill="{color}" stroke="none"/>'
        '<circle cx="12" cy="12" r="1" fill="{color}" stroke="none"/>'
        '<circle cx="18.5" cy="12" r="1" fill="{color}" stroke="none"/>'
    ),
    "chevron_down": '<path d="M6 9.5l6 6 6-6"/>',
    "minimize": '<path d="M5 12h14"/>',
    "maximize": '<rect x="5.5" y="5.5" width="13" height="13" rx="2"/>',
    "restore": (
        '<rect x="8" y="8" width="10.5" height="10.5" rx="2"/>'
        '<path d="M5.5 15.5V7A1.5 1.5 0 0 1 7 5.5h8.5"/>'
    ),
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "lyrics": '<path d="M4 6h16M4 11h11M4 16h14M4 21h7"/>',
    "mini": '<rect x="4" y="5.5" width="16" height="13" rx="2"/><path d="M10 14.5h7"/>',
}

ICON_WIDTH = 1.5


def svg_icon(name, color="#E8E5E1", size=20, width=ICON_WIDTH):
    body = ICONS[name]
    if "{color}" in body:
        body = body.replace("{color}", color)
    svg = SVG_TEMPLATE.format(color=color, width=width, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class IconButton(QPushButton):
    def __init__(self, name, size=20, color=None, hover=None, parent=None,
                 object_name="IconButton"):
        super().__init__(parent)
        from theme import palette

        colors = palette()
        self._icon_name = name
        self._size = size
        self._color = color or colors["icon"]
        self._hover = hover or colors["icon_hover"]
        self.setObjectName(object_name)
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self._apply(self._color)

    def _apply(self, color):
        self.setIcon(svg_icon(self._icon_name, color, self._size))

    def set_icon(self, name, color=None, hover=None):
        from theme import palette

        colors = palette()
        self._icon_name = name
        self._color = color or colors["icon"]
        self._hover = hover or colors["icon_hover"]
        self._apply(self._color)

    def enterEvent(self, event):
        self._apply(self._hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply(self._color)
        super().leaveEvent(event)
