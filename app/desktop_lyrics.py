# -*- coding: utf-8 -*-
"""Desktop lyrics: frameless, translucent, always-on-top lyric bar."""
import bisect

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QMenu, QWidget

from now_playing import parse_lrc

COLOR_PRESETS = {
    "white": "白色",
    "gold": "暖金",
    "pink": "樱粉",
    "blue": "天蓝",
}

COLORS = {
    "white": QColor(255, 255, 255),
    "gold": QColor(255, 214, 140),
    "pink": QColor(255, 170, 205),
    "blue": QColor(150, 205, 255),
}


class DesktopLyrics(QWidget):
    def __init__(self, window):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("桌面歌词")
        self._entries = []
        self._current = -1
        self._drag_offset = None
        settings = window.core.settings
        self._font_size = int(settings.get("lyrics_font_size", 28))
        self._locked = bool(settings.get("lyrics_locked", False))
        self._color_name = str(settings.get("lyrics_color", "white"))
        if self._color_name not in COLORS:
            self._color_name = "white"
        self._single_line = bool(settings.get("lyrics_single_line", False))
        self.resize(920, 160)
        position = settings.get("lyrics_pos") or [120, 120]
        try:
            self.move(QPoint(int(position[0]), int(position[1])))
        except (TypeError, ValueError):
            self.move(QPoint(120, 120))
        player = window.player
        player.position_changed.connect(self.set_position)
        player.duration_changed.connect(lambda _duration: self.refresh_track())
        self.refresh_track()

    def refresh_track(self):
        track = self.window.current_track
        if not track:
            self._entries = []
            self._current = -1
            self.update()
            return
        original = parse_lrc(track.get("lyric", ""))
        translation = parse_lrc(track.get("tlyric", ""))
        self._entries = [
            (ms, original[ms], translation.get(ms, "")) for ms in sorted(original)
        ]
        self._current = -1
        self.set_position(self.window.player.position())

    def set_position(self, position_ms):
        if not self._entries:
            return
        times = [entry[0] for entry in self._entries]
        index = max(0, bisect.bisect_right(times, int(position_ms)) - 1)
        if index != self._current:
            self._current = index
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._entries:
            text = "暂无歌词" if self.window.current_track else "未在播放"
            self._draw_line(painter, text, max(14, self._font_size - 8),
                            QColor(255, 255, 255, 190), 0.5)
            return
        current = self._entries[self._current]
        base = COLORS[self._color_name]
        main_color = QColor(base)
        main_color.setAlpha(246)
        self._draw_line(painter, current[1], self._font_size, main_color, 0.36)
        if self._single_line:
            return
        if current[2]:
            sub_color = QColor(base)
            sub_color.setAlpha(200)
            self._draw_line(
                painter, current[2], max(13, self._font_size - 8), sub_color, 0.64
            )
        if self._current + 1 < len(self._entries):
            next_color = QColor(base)
            next_color.setAlpha(120)
            self._draw_line(
                painter,
                self._entries[self._current + 1][1],
                max(13, self._font_size - 8),
                next_color,
                0.86,
            )

    def _draw_line(self, painter, text, size, color, y_ratio):
        if not text:
            return
        font = QFont(self.font())
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text)
        x = int((self.width() - width) / 2)
        y = int(self.height() * y_ratio)
        painter.setPen(QColor(0, 0, 0, 200))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    painter.drawText(x + dx, y + dy, text)
        painter.setPen(color)
        painter.drawText(x, y, text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._locked:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._locked
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_position()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("解锁位置" if self._locked else "锁定位置", self.toggle_lock)
        color_menu = menu.addMenu("颜色")
        for key, name in COLOR_PRESETS.items():
            action = color_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(key == self._color_name)
            action.triggered.connect(
                lambda _checked=False, value=key: self.set_color(value)
            )
        single = menu.addAction("单行模式（只显示当前句）")
        single.setCheckable(True)
        single.setChecked(self._single_line)
        single.toggled.connect(self.set_single_line)
        menu.addAction("字体 +", lambda: self.change_font(2))
        menu.addAction("字体 -", lambda: self.change_font(-2))
        menu.addSeparator()
        menu.addAction("关闭桌面歌词", self.close_lyrics)
        menu.exec(event.globalPosition().toPoint())

    def set_color(self, name):
        if name not in COLORS:
            return
        self._color_name = name
        self.window.core.settings["lyrics_color"] = name
        self.window.core.save_settings()
        self.update()

    def set_single_line(self, enabled):
        self._single_line = bool(enabled)
        self.window.core.settings["lyrics_single_line"] = self._single_line
        self.window.core.save_settings()
        self.update()

    def toggle_lock(self):
        self._locked = not self._locked
        self.window.core.settings["lyrics_locked"] = self._locked
        self.window.core.save_settings()

    def change_font(self, delta):
        self._font_size = max(14, min(64, self._font_size + delta))
        self.window.core.settings["lyrics_font_size"] = self._font_size
        self.window.core.save_settings()
        self.update()

    def _save_position(self):
        self.window.core.settings["lyrics_pos"] = [self.x(), self.y()]
        self.window.core.save_settings()

    def show_lyrics(self):
        self.refresh_track()
        self.show()
        self.raise_()

    def close_lyrics(self):
        self.hide()
        self.window.core.settings["desktop_lyrics"] = False
        self.window.core.save_settings()
