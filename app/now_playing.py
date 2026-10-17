# -*- coding: utf-8 -*-
"""Now-playing page (UI only).

A frameless window with a vinyl record, capsule tabs, scrolling lyrics and a
translucent bottom bar. Playback behaviour stays in MainWindow/Player; this
module mirrors state and calls the existing public methods.
"""
import bisect
import re

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core_client import JX_PLATFORMS, QUALITY_NAMES
from icons import IconButton
from theme import load_qss, palette

LRC_PATTERN = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def format_time(ms):
    if not ms or ms < 0:
        return "00:00"
    seconds = int(ms) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_lrc(text):
    lines = {}
    for raw in (text or "").splitlines():
        stamps = LRC_PATTERN.findall(raw)
        if not stamps:
            continue
        content = LRC_PATTERN.sub("", raw).strip()
        for minute, second, fraction in stamps:
            ms = int(minute) * 60000 + int(second) * 1000
            if fraction:
                ms += int(fraction.ljust(3, "0")[:3])
            lines[ms] = content
    return lines


def cover_palette(pixmap):
    defaults = palette()
    fallback = {
        "center": QColor(defaults["bg_center"]),
        "edge": QColor(defaults["bg_edge"]),
    }
    if pixmap is None or pixmap.isNull():
        return fallback
    image = pixmap.toImage().scaled(
        24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    total_r = total_g = total_b = count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 40:
                continue
            total_r += pixel.red()
            total_g += pixel.green()
            total_b += pixel.blue()
            count += 1
    if not count:
        return fallback
    r, g, b = total_r / count, total_g / count, total_b / count
    gray = (r + g + b) / 3 * 0.88
    warm = (gray + 18, gray + 10, gray - 6)
    mix = 0.42
    base = [c * mix + w * (1 - mix) for c, w in zip((r, g, b), warm)]

    def clamp(value):
        return max(0, min(255, int(value)))

    center = QColor(clamp(base[0] * 1.22), clamp(base[1] * 1.2), clamp(base[2] * 1.18))
    edge = QColor(clamp(base[0] * 0.42), clamp(base[1] * 0.4), clamp(base[2] * 0.38))
    return {"center": center, "edge": edge}


class VinylWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover = QPixmap()
        self._rotation = 0.0
        self._arm_angle = -24.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._advance)
        self._arm_anim = QPropertyAnimation(self, b"armAngle", self)
        self._arm_anim.setDuration(300)
        self._arm_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.setMinimumSize(300, 280)

    def set_cover(self, pixmap):
        self._cover = pixmap if pixmap is not None else QPixmap()
        self.update()

    def set_playing(self, playing):
        if playing:
            if not self._timer.isActive():
                self._timer.start()
            self._animate_arm(0.0)
        else:
            self._timer.stop()
            self._animate_arm(-24.0)

    def _animate_arm(self, angle):
        self._arm_anim.stop()
        self._arm_anim.setStartValue(self._arm_angle)
        self._arm_anim.setEndValue(float(angle))
        self._arm_anim.start()

    def _advance(self):
        self._rotation = (self._rotation + 3.0) % 360.0
        self.update()

    def get_rotation(self):
        return self._rotation

    def set_rotation(self, value):
        self._rotation = float(value)
        self.update()

    rotation = pyqtProperty(float, get_rotation, set_rotation)

    def get_arm_angle(self):
        return self._arm_angle

    def set_arm_angle(self, value):
        self._arm_angle = float(value)
        self.update()

    armAngle = pyqtProperty(float, get_arm_angle, set_arm_angle)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = max(220.0, min(self.width(), self.height()) - 30.0)
        cx = self.width() / 2.0
        cy = self.height() / 2.0 + 8.0
        disc_r = side / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 80))
        painter.drawEllipse(
            QRectF(cx - disc_r - 4, cy - disc_r + 6, (disc_r + 4) * 2, (disc_r + 4) * 2)
        )

        gradient = QRadialGradient(cx - disc_r * 0.25, cy - disc_r * 0.3, disc_r * 1.6)
        gradient.setColorAt(0.0, QColor("#2c2825"))
        gradient.setColorAt(0.65, QColor("#1c1917"))
        gradient.setColorAt(1.0, QColor("#111010"))
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(cx - disc_r, cy - disc_r, disc_r * 2, disc_r * 2))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = disc_r * 0.52
        index = 0
        while radius < disc_r * 0.985:
            alpha = 14 if index % 2 == 0 else 7
            painter.setPen(QColor(255, 255, 255, alpha))
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
            radius += 5.5
            index += 1

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self._rotation)
        sheen = QLinearGradient(-disc_r, -disc_r, disc_r, disc_r)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
        sheen.setColorAt(0.45, QColor(255, 255, 255, 16))
        sheen.setColorAt(0.55, QColor(255, 255, 255, 4))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(sheen)
        painter.drawEllipse(QRectF(-disc_r, -disc_r, disc_r * 2, disc_r * 2))
        painter.restore()

        label_r = disc_r * 0.42
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self._rotation)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0e0d0c"))
        painter.drawEllipse(
            QRectF(-label_r - 3, -label_r - 3, (label_r + 3) * 2, (label_r + 3) * 2)
        )
        clip = QPainterPath()
        clip.addEllipse(QRectF(-label_r, -label_r, label_r * 2, label_r * 2))
        painter.setClipPath(clip)
        if not self._cover.isNull():
            painter.drawPixmap(
                QRectF(-label_r, -label_r, label_r * 2, label_r * 2),
                self._cover,
                QRectF(self._cover.rect()),
            )
        else:
            painter.setBrush(QColor("#272320"))
            painter.drawEllipse(QRectF(-label_r, -label_r, label_r * 2, label_r * 2))
        painter.setClipping(False)
        painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0a0908"))
        painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))

        self._draw_arm(painter, cx, cy, disc_r)

    def _draw_arm(self, painter, cx, cy, disc_r):
        pivot = QPointF(cx + disc_r * 0.36, cy - disc_r * 0.94)
        t = (self._arm_angle + 24.0) / 24.0
        lifted = QPointF(pivot.x() - disc_r * 0.02, pivot.y() + disc_r * 0.62)
        playing = QPointF(cx + disc_r * 0.10, cy - disc_r * 0.06)
        tip = QPointF(
            lifted.x() + (playing.x() - lifted.x()) * t,
            lifted.y() + (playing.y() - lifted.y()) * t,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawEllipse(QRectF(pivot.x() - 17, pivot.y() - 17, 34, 34))

        pen = QPen(QColor("#cfc9c2"))
        pen.setWidthF(9.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(pivot, tip)

        pen = QPen(QColor("#f3f0eb"))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(pivot, tip)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e7e2db"))
        painter.drawEllipse(QRectF(tip.x() - 9, tip.y() - 9, 18, 18))
        painter.setBrush(QColor("#b8b2aa"))
        painter.drawEllipse(QRectF(pivot.x() - 12, pivot.y() - 12, 24, 24))
        painter.setBrush(QColor("#8f8a83"))
        painter.drawEllipse(QRectF(pivot.x() - 5, pivot.y() - 5, 10, 10))


class LyricsView(QWidget):
    LINE_HEIGHT = 40
    FADE = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        self._current = -1
        self._scroll = 0.0
        self._anim = QPropertyAnimation(self, b"scroll", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setMinimumHeight(220)

    def set_lyrics(self, lrc, tlyric=""):
        self._entries = []
        self._current = -1
        self._scroll = 0.0
        original = parse_lrc(lrc)
        translation = parse_lrc(tlyric or "")
        for ms in sorted(original):
            text = original[ms]
            trans = translation.get(ms, "")
            if trans:
                text = f"{text}   {trans}"
            self._entries.append((ms, text))
        self.update()

    def set_position(self, position_ms):
        if not self._entries:
            return
        times = [entry[0] for entry in self._entries]
        index = bisect.bisect_right(times, int(position_ms)) - 1
        index = max(0, index)
        if index != self._current:
            self._current = index
            self._anim.stop()
            self._anim.setStartValue(self._scroll)
            self._anim.setEndValue(float(index))
            self._anim.start()
            self.update()

    def get_scroll(self):
        return self._scroll

    def set_scroll(self, value):
        self._scroll = float(value)
        self.update()

    scroll = pyqtProperty(float, get_scroll, set_scroll)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._entries:
            painter.setPen(QColor(255, 255, 255, 90))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无歌词")
            return

        width, height = self.width(), self.height()
        center = height / 2.0
        layer = QPixmap(self.size())
        layer.fill(Qt.GlobalColor.transparent)
        content = QPainter(layer)
        content.setRenderHint(QPainter.RenderHint.Antialiasing)
        span = height // self.LINE_HEIGHT // 2 + 3
        first = max(0, int(self._scroll) - span)
        last = min(len(self._entries), int(self._scroll) + span + 1)
        for index in range(first, last):
            text = self._entries[index][1]
            y = center + (index - self._scroll) * self.LINE_HEIGHT
            font = QFont(self.font())
            if index == self._current:
                font.setPixelSize(20)
                font.setWeight(QFont.Weight.Medium)
                content.setFont(font)
                content.setPen(QColor(255, 255, 255, 255))
            else:
                font.setPixelSize(16)
                font.setWeight(QFont.Weight.Normal)
                content.setFont(font)
                content.setPen(QColor(255, 255, 255, 153))
            content.drawText(
                QRectF(10, y - self.LINE_HEIGHT / 2.0, width - 20, self.LINE_HEIGHT),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                text,
            )
        content.end()

        mask = QPixmap(self.size())
        mask.fill(Qt.GlobalColor.transparent)
        mask_painter = QPainter(mask)
        fade = min(self.FADE, height / 3.0)
        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(fade / height, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0 - fade / height, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        mask_painter.fillRect(self.rect(), gradient)
        mask_painter.end()

        cut = QPainter(layer)
        cut.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        cut.drawPixmap(0, 0, mask)
        cut.end()
        painter.drawPixmap(0, 0, layer)


class NowPlayingWindow(QWidget):
    def __init__(self, window):
        super().__init__(
            window,
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint,
        )
        self.window = window
        self.setObjectName("NowPlaying")
        self.setWindowTitle("Neko.night - Music")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(load_qss())
        self._seeking = False
        self._drag_offset = None
        self._maximized = False
        self._normal_geometry = None
        self._palette = cover_palette(None)
        self._animation = QPropertyAnimation(self, b"geometry", self)
        self._animation.setDuration(300)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._build_ui()
        self._connect_player()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 14, 30, 0)
        root.setSpacing(6)

        top = QHBoxLayout()
        collapse = IconButton("chevron_down", size=18, parent=self)
        collapse.clicked.connect(self.slide_out)
        top.addWidget(collapse)
        top.addStretch(1)
        mode_hint = QLabel("播放器模式")
        mode_hint.setObjectName("Dim")
        top.addWidget(mode_hint)
        top.addSpacing(6)
        self.win_min = IconButton("minimize", size=16, parent=self)
        self.win_min.clicked.connect(self.showMinimized)
        self.win_max = IconButton("maximize", size=16, parent=self)
        self.win_max.clicked.connect(self.toggle_maximize)
        self.win_close = IconButton("close", size=16, parent=self)
        self.win_close.clicked.connect(self.close_player)
        for button in (self.win_min, self.win_max, self.win_close):
            top.addWidget(button)
        root.addLayout(top)

        content = QHBoxLayout()
        content.setSpacing(42)
        self.vinyl = VinylWidget(self)
        content.addWidget(self.vinyl, 45)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.title = QLabel("未在播放")
        self.title.setObjectName("TrackTitle")
        self.title.setWordWrap(True)
        right.addWidget(self.title)
        self.meta = QLabel("")
        self.meta.setObjectName("TrackMeta")
        self.meta.setWordWrap(True)
        right.addWidget(self.meta)
        right.addSpacing(6)

        capsules = QHBoxLayout()
        capsules.setSpacing(8)
        self.tab_buttons = []
        for index, name in enumerate(("歌词", "百科", "相似推荐")):
            button = QPushButton(name)
            button.setObjectName("Capsule")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, page=index: self._select_tab(page)
            )
            capsules.addWidget(button)
            self.tab_buttons.append(button)
        capsules.addStretch(1)
        right.addLayout(capsules)

        self.stack = QStackedWidget()
        self.lyrics = LyricsView(self)
        self.stack.addWidget(self.lyrics)
        for name in ("百科", "相似推荐"):
            placeholder = QLabel(f"{name}功能暂未接入")
            placeholder.setObjectName("Dim")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stack.addWidget(placeholder)
        right.addWidget(self.stack, 1)
        content.addLayout(right, 55)
        root.addLayout(content, 1)

        root.addWidget(self._build_bottom_bar())
        self._select_tab(0)

    def _build_bottom_bar(self):
        bar = QFrame()
        bar.setObjectName("BottomBar")
        bar.setFixedHeight(72)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)

        self.bar_thumb = QLabel()
        self.bar_thumb.setFixedSize(44, 44)
        self.bar_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar_thumb.setText("♪")
        layout.addWidget(self.bar_thumb)

        info = QVBoxLayout()
        info.setSpacing(1)
        self.bar_title = QLabel("未在播放")
        self.bar_title.setObjectName("BarTitle")
        self.bar_meta = QLabel("")
        self.bar_meta.setObjectName("BarMeta")
        info.addWidget(self.bar_title)
        info.addWidget(self.bar_meta)
        layout.addLayout(info)

        self.fav_btn = IconButton("heart", size=18, parent=self)
        self.fav_btn.clicked.connect(self.toggle_favorite)
        layout.addWidget(self.fav_btn)
        layout.addStretch(1)

        self.mode_btn = IconButton("repeat", size=18, parent=self)
        self.mode_btn.setToolTip("播放方式：列表循环 / 单曲循环 / 随机")
        self.mode_btn.clicked.connect(self.cycle_mode)
        prev_btn = IconButton("prev", size=20, parent=self)
        prev_btn.clicked.connect(self.window.play_previous)
        self.play_btn = IconButton(
            "play", size=22, color="#1b1815", hover="#1b1815", parent=self,
            object_name="PlayButton",
        )
        self.play_btn.setFixedSize(44, 44)
        self.play_btn.clicked.connect(self.window.toggle_play)
        next_btn = IconButton("next", size=20, parent=self)
        next_btn.clicked.connect(self.window.play_next)
        queue_btn = IconButton("playlist", size=18, parent=self)
        queue_btn.setToolTip("播放队列")
        queue_btn.clicked.connect(self.window._show_queue)
        for button in (self.mode_btn, prev_btn, self.play_btn, next_btn, queue_btn):
            layout.addWidget(button)
        layout.addStretch(1)

        self.quality_label = QLabel("标准音质")
        self.quality_label.setObjectName("QualityCapsule")
        layout.addWidget(self.quality_label)

        volume_icon = QLabel()
        volume_icon.setPixmap(
            IconButton("volume", size=18, parent=self).icon().pixmap(18, 18)
        )
        layout.addWidget(volume_icon)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setValue(self.window.volume_slider.value())
        self.volume_slider.valueChanged.connect(self.window.volume_slider.setValue)
        self.window.volume_slider.valueChanged.connect(self.volume_slider.setValue)
        layout.addWidget(self.volume_slider)

        lyrics_btn = IconButton("lyrics", size=18, parent=self)
        lyrics_btn.setToolTip("桌面歌词")
        lyrics_btn.clicked.connect(self.window.toggle_desktop_lyrics)
        layout.addWidget(lyrics_btn)

        more_btn = IconButton("more", size=18, parent=self)
        more_btn.clicked.connect(self._show_more_menu)
        layout.addWidget(more_btn)

        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("Dim")
        layout.addWidget(self.time_label)
        return bar

    def _select_tab(self, index):
        for position, button in enumerate(self.tab_buttons):
            button.setChecked(position == index)
        self.stack.setCurrentIndex(index)

    def _connect_player(self):
        player = self.window.player
        player.state_changed.connect(self.on_state)
        player.position_changed.connect(self.on_position)
        player.duration_changed.connect(self.on_duration)
        if hasattr(self.window, "mode_changed"):
            self.window.mode_changed.connect(lambda _mode: self._update_mode_icon())
        self._update_mode_icon()
        self.on_state(player.player.playbackState())
        self.on_position(player.position())

    def refresh(self):
        track = self.window.current_track
        if not track:
            return
        self.title.setText(track.get("name", ""))
        album = track.get("al_name") or track.get("album") or ""
        artist = track.get("ar_name") or track.get("artists") or ""
        source = self._source_text(track)
        parts = []
        if album:
            parts.append(f"专辑：{album}")
        if artist:
            parts.append(f"歌手：{artist}")
        if source:
            parts.append(f"来源：{source}")
        self.meta.setText("    ".join(parts))
        self.bar_title.setText(track.get("name", ""))
        self.bar_meta.setText(f"{artist} · {source}" if artist else source)
        level = track.get("level") or ""
        self.quality_label.setText(QUALITY_NAMES.get(level, level or "标准音质"))
        self.lyrics.set_lyrics(track.get("lyric", ""), track.get("tlyric", ""))
        self.lyrics.set_position(self.window.player.position())
        self._update_fav_icon()
        self._update_mode_icon()
        self._load_cover(track.get("pic", ""))

    def _source_text(self, track):
        source = track.get("source") or self.window.core.current_source()
        if source == "local":
            return "本地 Cookie"
        if source == "online":
            return "在线 API"
        if source == "file":
            return "本地文件"
        if source == "jx":
            platform = self.window.core.settings.get("jx_platform", "")
            name = JX_PLATFORMS.get(platform, platform)
            return f"{name}（单平台）" if name else "单平台"
        return str(source)

    def _load_cover(self, url):
        def on_loaded(pixmap):
            self.vinyl.set_cover(pixmap)
            self.bar_thumb.setPixmap(
                pixmap.scaled(
                    44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._palette = cover_palette(pixmap)
            self.update()

        if not url:
            self.vinyl.set_cover(QPixmap())
            self.bar_thumb.setText("♪")
            self._palette = cover_palette(None)
            self.update()
            return
        self.window.load_cover(url, self.bar_thumb, 44, on_loaded=on_loaded)

    def _update_fav_icon(self):
        track = self.window.current_track
        favorite = bool(track) and self.window.library.is_favorite(track)
        if favorite:
            self.fav_btn.set_icon("heart_filled", color="#E87878", hover="#F09090")
        else:
            self.fav_btn.set_icon("heart")

    def _update_mode_icon(self):
        mode = self.window.core.settings.get("play_mode", "list")
        self.mode_btn.set_icon(
            {"list": "repeat", "single": "repeat_one", "shuffle": "shuffle"}.get(
                mode, "repeat"
            )
        )

    def cycle_mode(self):
        self.window._cycle_play_mode()
        self._update_mode_icon()

    def toggle_favorite(self):
        self.window.toggle_current_favorite()
        self._update_fav_icon()

    def on_state(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer

        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.set_icon("pause" if playing else "play")
        self.vinyl.set_playing(playing)

    def on_position(self, position):
        duration = self.window.player.duration()
        if duration:
            self.time_label.setText(
                f"{format_time(position)} / {format_time(duration)}"
            )
        else:
            self.time_label.setText(format_time(position))
        self.lyrics.set_position(position)

    def on_duration(self, duration):
        self.time_label.setText(
            f"{format_time(self.window.player.position())} / {format_time(duration)}"
        )

    def _show_more_menu(self):
        menu = QMenu(self)
        track = self.window.current_track or {}
        copy_action = menu.addAction("复制播放链接")
        copy_action.triggered.connect(
            lambda: QApplication.clipboard().setText(track.get("url", ""))
        )
        path = track.get("path", "")
        reveal_action = menu.addAction("在资源管理器中显示")
        reveal_action.setEnabled(bool(path))
        reveal_action.triggered.connect(
            lambda: self.window._show_in_explorer({"path": path})
        )
        progress_action = menu.addAction("清除播放进度")
        progress_action.triggered.connect(
            lambda: self.window.library.clear_progress(track)
        )
        menu.addSeparator()
        menu.addAction("桌面歌词", self.window.toggle_desktop_lyrics)
        menu.addAction("迷你模式", self.window.enter_mini_mode)
        menu.exec(self.cursor().pos())

    def _target_rect(self):
        return self.window.frameGeometry()

    def slide_in(self):
        self.refresh()
        target = self._target_rect()
        start = QRect(target.x(), target.y() + target.height(), target.width(), target.height())
        self.setGeometry(start)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._animate_to(target)

    def slide_out(self):
        target = self.geometry()
        end = QRect(target.x(), target.y() + target.height(), target.width(), target.height())
        self._animate_to(end, hide=True)

    def close_player(self):
        self.slide_out()

    def toggle_maximize(self):
        if self._maximized:
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self._maximized = False
            self.win_max.set_icon("maximize")
        else:
            self._normal_geometry = self.geometry()
            self.setGeometry(self.screen().availableGeometry())
            self._maximized = True
            self.win_max.set_icon("restore")

    def _animate_to(self, rect, hide=False):
        self._animation.stop()
        try:
            self._animation.finished.disconnect()
        except TypeError:
            pass
        self._animation.setStartValue(self.geometry())
        self._animation.setEndValue(rect)
        if hide:
            self._animation.finished.connect(self.hide)
        self._animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14, 14)
        center = self.rect().center()
        radius = max(self.width(), self.height()) * 0.72
        gradient = QRadialGradient(QPointF(center), radius)
        gradient.setColorAt(0.0, self._palette["center"])
        gradient.setColorAt(0.65, self._palette["center"].darker(135))
        gradient.setColorAt(1.0, self._palette["edge"])
        painter.fillPath(path, gradient)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.slide_out()
            return
        if event.key() == Qt.Key.Key_Space:
            self.window.toggle_play()
            return
        super().keyPressEvent(event)
