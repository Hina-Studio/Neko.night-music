# -*- coding: utf-8 -*-
"""Compact always-on-top mini player."""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from icons import IconButton


class MiniPlayer(QWidget):
    def __init__(self, window):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(400, 108)
        self._drag_offset = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self.cover = QLabel()
        self.cover.setFixedSize(64, 64)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setText("♪")
        layout.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.title = QLabel("未在播放")
        self.title.setObjectName("BarTitle")
        self.artist = QLabel("")
        self.artist.setObjectName("BarMeta")
        info.addWidget(self.title)
        info.addWidget(self.artist)
        layout.addLayout(info, 1)

        prev_btn = IconButton("prev", size=18, parent=self)
        prev_btn.clicked.connect(window.play_previous)
        self.play_btn = IconButton("play", size=20, parent=self)
        self.play_btn.clicked.connect(window.toggle_play)
        next_btn = IconButton("next", size=18, parent=self)
        next_btn.clicked.connect(window.play_next)
        restore_btn = IconButton("maximize", size=16, parent=self)
        restore_btn.setToolTip("返回主界面")
        restore_btn.clicked.connect(self.restore_main)
        close_btn = IconButton("close", size=16, parent=self)
        close_btn.setToolTip("关闭迷你模式")
        close_btn.clicked.connect(self.close_mini)
        for button in (prev_btn, self.play_btn, next_btn, restore_btn, close_btn):
            layout.addWidget(button)

        player = window.player
        player.state_changed.connect(self.on_state)
        player.position_changed.connect(lambda _p: self.refresh())
        self.refresh()

    def refresh(self):
        track = self.window.current_track
        if not track:
            return
        self.title.setText(track.get("name", ""))
        self.artist.setText(track.get("ar_name") or track.get("artists") or "")
        pic = track.get("pic", "")
        if pic:
            self.window.load_cover(pic, self.cover, 64)
        else:
            self.cover.setText("♪")

    def on_state(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer

        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.set_icon("pause" if playing else "play")

    def restore_main(self):
        self.hide()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def close_mini(self):
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14, 14)
        painter.fillPath(path, QColor(18, 15, 14, 235))
        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def show_mini(self):
        self.refresh()
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            self.move(
                screen.right() - self.width() - 28,
                screen.bottom() - self.height() - 28,
            )
        self.show()
        self.raise_()
