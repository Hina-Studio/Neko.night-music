# -*- coding: utf-8 -*-
import io
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import qrcode
import requests
from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core_client import (
    CoreError,
    JX_PLATFORMS,
    QUALITY_NAMES,
    SEARCH_PLATFORMS,
    format_size,
    referer_for,
)
from edition import PUBLIC_EDITION
from player import Player
from update_checker import RELEASES_URL, UpdateChecker, is_newer
from workers import ProgressBridge, get_download_pool, run_async, run_download, shutdown

TRACK_ROLE = Qt.ItemDataRole.UserRole

APP_VERSION = "1.2.1"

NAV_SEARCH = 0
NAV_DISCOVER = 1
NAV_ARTIST = 2
NAV_COLLECTION = 3
NAV_LOCAL = 4
NAV_DOWNLOADS = 5
NAV_API = 6
NAV_SETTINGS = 7

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


def merge_lyrics(lrc, tlyric):
    original = parse_lrc(lrc)
    translation = parse_lrc(tlyric)
    if not original:
        return lrc or ""
    out = []
    for ms in sorted(original):
        out.append(original[ms])
        translated = translation.get(ms)
        if translated:
            out.append(translated)
    return "\n".join(out)


class QRLoginDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.unikey = None
        self.setWindowTitle("扫码登录")
        self.setFixedSize(320, 400)

        layout = QVBoxLayout(self)
        self.qr_label = QLabel("正在获取二维码...")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(260, 260)
        layout.addWidget(self.qr_label)
        self.status = QLabel("请使用网易云音乐 APP 扫码")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        close_btn = QPushButton("取消")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)

        run_async(
            self.core.qr_unikey, on_done=self.on_key, on_error=self.on_error
        )

    def on_key(self, data):
        if data.get("code") != 200 or not data.get("unikey"):
            self.status.setText(f"获取二维码失败: {data.get('message', '未知错误')}")
            return
        self.unikey = data["unikey"]
        image = qrcode.make(f"https://music.163.com/login?codekey={self.unikey}")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue())
        self.qr_label.setPixmap(
            pixmap.scaled(250, 250, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        )
        self.status.setText("等待扫码...")
        self.timer.start()

    def poll(self):
        if not self.unikey:
            return
        run_async(self.core.qr_check, self.unikey,
                  on_done=self.on_check, on_error=self.on_error)

    def on_check(self, data):
        code = data.get("code")
        if code == 803 and data.get("music_u"):
            self.timer.stop()
            self.core.save_cookie(f"MUSIC_U={data['music_u']};os=pc;appver=8.9.70;")
            self.accept()
        elif code == 802:
            self.status.setText("扫码成功，请在手机上确认")
        elif code == 801:
            self.status.setText("等待扫码...")
        elif code == 800:
            self.timer.stop()
            self.status.setText("二维码已过期，请重新打开")

    def on_error(self, message):
        self.status.setText(f"登录失败: {message}")


class QueueDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("播放队列")
        self.resize(520, 560)
        layout = QVBoxLayout(self)

        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.doubleClicked.connect(self.play_selected)
        self.list.model().rowsMoved.connect(self.on_moved)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        play_btn = QPushButton("播放选中")
        play_btn.setObjectName("Accent")
        play_btn.clicked.connect(self.play_selected)
        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(self.remove_selected)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self.clear_queue)
        save_btn = QPushButton("保存为歌单")
        save_btn.clicked.connect(self.save_as_playlist)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(play_btn)
        row.addWidget(remove_btn)
        row.addWidget(clear_btn)
        row.addWidget(save_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for index, track in enumerate(self.window.play_queue):
            label = f"{index + 1}. {track.get('name', '')}"
            artists = track.get("artists") or track.get("ar_name") or ""
            if artists:
                label += f" - {artists}"
            item = QListWidgetItem(label)
            item.setData(TRACK_ROLE, track)
            if index == self.window.play_index:
                item.setText("▶ " + label)
            self.list.addItem(item)

    def on_moved(self, *_args):
        tracks = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is None:
                continue
            track = item.data(TRACK_ROLE)
            if track:
                tracks.append(track)
        if len(tracks) != len(self.window.play_queue):
            return
        current = self.window.play_queue[self.window.play_index] if 0 <= self.window.play_index < len(self.window.play_queue) else None
        self.window.play_queue = tracks
        if current is not None:
            try:
                self.window.play_index = tracks.index(current)
            except ValueError:
                pass
        self.window._persist_queue()

    def play_selected(self):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.window.play_queue):
            return
        self.window.play_index = row
        self.window.play_track(self.window.play_queue[row])
        self.refresh()

    def remove_selected(self):
        rows = sorted({index.row() for index in self.list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self.window.play_queue):
                self.window.play_queue.pop(row)
                if row < self.window.play_index:
                    self.window.play_index -= 1
        self.window._persist_queue()
        self.refresh()

    def clear_queue(self):
        self.window.play_queue = []
        self.window.play_index = -1
        self.window._persist_queue()
        self.refresh()

    def save_as_playlist(self):
        if not self.window.play_queue:
            return
        name, ok = QInputDialog.getText(self, "保存为歌单", "歌单名称：")
        if not ok or not name.strip():
            return
        playlist_id = self.window.library.create_playlist(name.strip())
        if playlist_id:
            for track in self.window.play_queue:
                self.window.library.add_to_playlist(playlist_id, track)
            self.window._refresh_local_playlists()
            QMessageBox.information(self, "已保存", f"已保存到歌单「{name.strip()}」")


class TrackListDialog(QDialog):
    def __init__(self, window, title, tracks):
        super().__init__(window)
        self.window = window
        self.tracks = [track for track in (tracks or []) if track]
        self.setWindowTitle(title)
        self.resize(840, 580)
        layout = QVBoxLayout(self)
        header = QLabel(title)
        header.setObjectName("Section")
        layout.addWidget(header)
        self.table = window._make_track_table()
        window.fill_track_table(self.table, self.tracks)
        self.table.doubleClicked.connect(self.play_current)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        play_btn = QPushButton("播放选中")
        play_btn.setObjectName("Accent")
        play_btn.clicked.connect(self.play_current)
        play_all_btn = QPushButton("播放全部")
        play_all_btn.clicked.connect(lambda: window.play_from_list(self.tracks, 0))
        download_btn = QPushButton("下载选中")
        download_btn.clicked.connect(self.download_selected)
        download_all_btn = QPushButton("下载全部")
        download_all_btn.clicked.connect(
            lambda: [window.start_download(track) for track in self.tracks]
        )
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(play_btn)
        row.addWidget(play_all_btn)
        row.addWidget(download_btn)
        row.addWidget(download_all_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def play_current(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.window.play_from_list(self.tracks, row)

    def download_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        for row in rows:
            if 0 <= row < len(self.tracks):
                self.window.start_download(self.tracks[row])


class CommentsDialog(QDialog):
    def __init__(self, title, data):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(640, 620)
        layout = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        lines = [f"共 {data.get('total', 0)} 条评论", ""]
        hot = data.get("hot") or []
        if hot:
            lines.append("———— 热门评论 ————")
            for comment in hot:
                lines.append(self._format(comment))
            lines.append("")
        comments = data.get("comments") or []
        if comments:
            lines.append("———— 最新评论 ————")
            for comment in comments:
                lines.append(self._format(comment))
        view.setPlainText("\n".join(lines) if len(lines) > 2 else "暂无评论")
        layout.addWidget(view)

    @staticmethod
    def _format(comment):
        return (
            f"{comment.get('nickname', '匿名')}   ♥{comment.get('liked_count', 0)}\n"
            f"    {comment.get('content', '')}\n"
        )


class MainWindow(QMainWindow):
    mode_changed = pyqtSignal(str)

    def __init__(self, core, library=None):
        super().__init__()
        self.core = core
        if library is None:
            from library import Library

            library = Library()
        self.library = library
        self.core.attach_library(self.library)
        self.player = Player(self)
        self.cover_cache = {}
        self.current_track = None
        self.play_queue = []
        self.play_index = -1
        self.seeking = False
        self.download_rows = {}
        self.next_download_key = 1
        self.collection_tracks = []
        self.local_tracks = []
        self._syncing_quality = False
        self.api_last_result = {}
        self.api_last_url = ""
        self.queue_dialog = None
        self.now_playing = None
        self.desktop_lyrics = None
        self.mini_player = None
        self._fallback_tried = False
        self._hotkeys_registered = False
        self._really_quit = False
        self._pending_resume = 0
        self._last_progress_save = 0.0
        self._fade_start = 0
        self._fade_target = 0
        self._fade_step = 0
        self._fade_steps = 1
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setSingleShot(True)
        self._sleep_timer.timeout.connect(self._on_sleep_timeout)
        self._update_checker = UpdateChecker(self)
        self._update_checker.checked.connect(self._on_update_checked)
        self._update_url = RELEASES_URL
        self._manual_update_check = False

        self.setWindowTitle("Neko.night - Music")
        self.resize(1200, 780)
        self._build_ui()
        self._connect_player()
        self._apply_settings()
        self._build_tray()
        self._restore_queue()
        self._refresh_local_playlists()
        self._refresh_local_tracks()
        self._refresh_login_state()
        if self.core.settings.get("desktop_lyrics"):
            self.toggle_desktop_lyrics()
        if self.core.has_cookie():
            run_async(
                lambda: self.core._parse(self.core.client.user_account()),
                on_done=self._update_api_account,
                on_error=lambda _message: None,
            )
        QTimer.singleShot(4000, self._update_checker.check)

    @staticmethod
    def _nav_items():
        items = [
            "🔍  搜索",
            "✨  发现",
            "🎤  歌手",
            "📚  歌单 / 专辑",
            "🎧  本地音乐",
            "⬇️  下载管理",
        ]
        if not PUBLIC_EDITION:
            items.append("🧩  解析")
        items.append("⚙️  设置")
        return items

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)
        outer.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(10)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(156)
        for text in self._nav_items():
            self.nav.addItem(text)
        body.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_search_page())
        self.stack.addWidget(self._build_discover_page())
        self.stack.addWidget(self._build_artist_page())
        self.stack.addWidget(self._build_collection_page())
        self.stack.addWidget(self._build_local_page())
        self.stack.addWidget(self._build_downloads_page())
        if not PUBLIC_EDITION:
            self.stack.addWidget(self._build_api_page())
        self.stack.addWidget(self._build_settings_page())

        items = self._nav_items()
        self.nav_downloads_index = items.index("⬇️  下载管理")
        self.nav_settings_index = items.index("⚙️  设置")
        self.nav_api_index = (
            items.index("🧩  解析") if "🧩  解析" in items else -1
        )

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_player_bar())
        self.statusBar().showMessage("就绪")

    def _build_header(self):
        layout = QHBoxLayout()
        layout.setSpacing(10)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Neko.night - Music")
        title.setObjectName("Title")
        subtitle = QLabel("C++ 核心 · WinHTTP + BCrypt · PyQt6 界面")
        subtitle.setObjectName("SubTitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)

        layout.addWidget(QLabel("音源"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("本地 Cookie（网易云）", ("local", None))
        if not PUBLIC_EDITION:
            self.source_combo.addItem("在线 API（网易云）", ("online", None))
        for key, name in SEARCH_PLATFORMS.items():
            self.source_combo.addItem(f"{name}（单平台）", ("jx", key))
        self.source_combo.setFixedWidth(170)
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        layout.addWidget(self.source_combo)

        layout.addWidget(QLabel("音质"))
        self.quality_combo = QComboBox()
        for key, name in QUALITY_NAMES.items():
            self.quality_combo.addItem(name, key)
        self.quality_combo.setFixedWidth(130)
        self.quality_combo.currentIndexChanged.connect(self.on_quality_changed)
        layout.addWidget(self.quality_combo)

        self.login_label = QLabel("未登录")
        self.login_label.setObjectName("SubTitle")
        layout.addWidget(self.login_label)
        return layout

    def _build_search_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        search_card = QFrame()
        search_card.setObjectName("Panel")
        card_layout = QHBoxLayout(search_card)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入歌名 / 歌手 / 专辑，回车搜索")
        self.search_input.returnPressed.connect(self.do_search)
        card_layout.addWidget(self.search_input, 1)
        self.search_limit = QSpinBox()
        self.search_limit.setRange(1, 100)
        self.search_limit.setValue(30)
        self.search_limit.setFixedWidth(80)
        card_layout.addWidget(self.search_limit)
        search_btn = QPushButton("搜索")
        search_btn.setObjectName("Accent")
        search_btn.clicked.connect(self.do_search)
        card_layout.addWidget(search_btn)
        layout.addWidget(search_card)

        content = QHBoxLayout()
        content.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(10)
        table_card = QFrame()
        table_card.setObjectName("Panel")
        table_layout = QVBoxLayout(table_card)
        self.search_table = QTableWidget(0, 4)
        self.search_table.setHorizontalHeaderLabels(["#", "歌曲", "歌手", "专辑"])
        self.search_table.verticalHeader().setVisible(False)
        self.search_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.search_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.search_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.search_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.search_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.search_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.search_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.search_table.doubleClicked.connect(self.play_selected_search)
        table_layout.addWidget(self.search_table)
        left.addWidget(table_card, 1)

        actions = QHBoxLayout()
        play_btn = QPushButton("▶  播放")
        play_btn.setObjectName("Accent")
        play_btn.clicked.connect(self.play_selected_search)
        download_btn = QPushButton("⬇  下载")
        download_btn.clicked.connect(self.download_selected_search)
        lyrics_btn = QPushButton("词  查看歌词")
        lyrics_btn.clicked.connect(self.show_selected_lyrics)
        comments_btn = QPushButton("💬  评论")
        comments_btn.clicked.connect(self.show_selected_comments)
        artist_btn = QPushButton("🎤  歌手")
        artist_btn.clicked.connect(self.show_selected_artist)
        actions.addWidget(play_btn)
        actions.addWidget(download_btn)
        actions.addWidget(lyrics_btn)
        actions.addWidget(comments_btn)
        actions.addWidget(artist_btn)
        actions.addStretch(1)
        left.addLayout(actions)
        content.addLayout(left, 1)

        detail = QFrame()
        detail.setObjectName("Card")
        detail.setFixedWidth(340)
        detail_layout = QVBoxLayout(detail)
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(300, 300)
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setText("封面")
        detail_layout.addWidget(self.detail_cover, 0, Qt.AlignmentFlag.AlignHCenter)
        self.detail_name = QLabel("未选择歌曲")
        self.detail_name.setObjectName("Section")
        self.detail_name.setWordWrap(True)
        detail_layout.addWidget(self.detail_name)
        self.detail_artist = QLabel("")
        self.detail_artist.setObjectName("SubTitle")
        self.detail_artist.setWordWrap(True)
        detail_layout.addWidget(self.detail_artist)
        self.detail_album = QLabel("")
        self.detail_album.setObjectName("SubTitle")
        self.detail_album.setWordWrap(True)
        detail_layout.addWidget(self.detail_album)
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("SubTitle")
        detail_layout.addWidget(self.detail_meta)
        detail_actions = QHBoxLayout()
        detail_play = QPushButton("播放")
        detail_play.setObjectName("Accent")
        detail_play.clicked.connect(self.play_selected_search)
        detail_download = QPushButton("下载")
        detail_download.clicked.connect(self.download_selected_search)
        detail_actions.addWidget(detail_play)
        detail_actions.addWidget(detail_download)
        detail_layout.addLayout(detail_actions)
        self.lyric_view = QPlainTextEdit()
        self.lyric_view.setReadOnly(True)
        self.lyric_view.setPlaceholderText("歌词")
        detail_layout.addWidget(self.lyric_view, 1)
        content.addWidget(detail)

        layout.addLayout(content, 1)
        return page

    def _build_discover_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        bar = QFrame()
        bar.setObjectName("Panel")
        bar_layout = QHBoxLayout(bar)
        self.discover_tabs = []
        for index, name in enumerate(("每日推荐", "私人FM", "新歌速递", "排行榜", "我的歌单")):
            button = QPushButton(name)
            button.setObjectName("TabButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, page_index=index: self._select_discover_tab(page_index)
            )
            bar_layout.addWidget(button)
            self.discover_tabs.append(button)
        bar_layout.addStretch(1)
        self.discover_play_btn = QPushButton("播放全部")
        self.discover_play_btn.clicked.connect(self._discover_play_all)
        self.discover_download_btn = QPushButton("下载全部")
        self.discover_download_btn.clicked.connect(self._discover_download_all)
        self.discover_refresh_btn = QPushButton("刷新")
        self.discover_refresh_btn.setObjectName("Accent")
        self.discover_refresh_btn.clicked.connect(self._load_discover_tab)
        bar_layout.addWidget(self.discover_play_btn)
        bar_layout.addWidget(self.discover_download_btn)
        bar_layout.addWidget(self.discover_refresh_btn)
        layout.addWidget(bar)

        self.discover_stack = QStackedWidget()
        self.daily_table = self._make_track_table()
        self.fm_table = self._make_track_table()
        self.new_table = self._make_track_table()
        self.discover_stack.addWidget(self.daily_table)
        self.discover_stack.addWidget(self.fm_table)
        self.discover_stack.addWidget(self.new_table)

        self.toplist_table = QTableWidget(0, 3)
        self.toplist_table.setHorizontalHeaderLabels(["#", "榜单", "更新频率"])
        self.toplist_table.verticalHeader().setVisible(False)
        self.toplist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.toplist_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.toplist_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.toplist_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.toplist_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.toplist_table.doubleClicked.connect(self._open_toplist)
        self.discover_stack.addWidget(self.toplist_table)

        self.user_playlist_table = QTableWidget(0, 3)
        self.user_playlist_table.setHorizontalHeaderLabels(["#", "歌单", "歌曲数"])
        self.user_playlist_table.verticalHeader().setVisible(False)
        self.user_playlist_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.user_playlist_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.user_playlist_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.user_playlist_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.user_playlist_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.user_playlist_table.doubleClicked.connect(self._open_user_playlist)
        self.discover_stack.addWidget(self.user_playlist_table)

        layout.addWidget(self.discover_stack, 1)
        self._select_discover_tab(0)
        return page

    def _build_artist_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QHBoxLayout(card)
        self.artist_input = QLineEdit()
        self.artist_input.setPlaceholderText("输入歌手 ID，或从歌曲列表右键「查看歌手」")
        self.artist_input.returnPressed.connect(self.load_artist)
        card_layout.addWidget(self.artist_input, 1)
        query_btn = QPushButton("查询")
        query_btn.setObjectName("Accent")
        query_btn.clicked.connect(self.load_artist)
        card_layout.addWidget(query_btn)
        layout.addWidget(card)

        header = QFrame()
        header.setObjectName("Card")
        header_layout = QHBoxLayout(header)
        self.artist_avatar = QLabel()
        self.artist_avatar.setFixedSize(96, 96)
        self.artist_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_avatar.setText("♪")
        header_layout.addWidget(self.artist_avatar)
        info = QVBoxLayout()
        self.artist_name = QLabel("未选择歌手")
        self.artist_name.setObjectName("Title")
        self.artist_alias = QLabel("")
        self.artist_alias.setObjectName("SubTitle")
        self.artist_alias.setWordWrap(True)
        info.addWidget(self.artist_name)
        info.addWidget(self.artist_alias)
        header_layout.addLayout(info)
        header_layout.addStretch(1)
        play_btn = QPushButton("播放热门")
        play_btn.setObjectName("Accent")
        play_btn.clicked.connect(
            lambda: self.play_from_list(self.artist_tracks, 0)
        )
        download_btn = QPushButton("下载热门")
        download_btn.clicked.connect(
            lambda: [self.start_download(track) for track in self.artist_tracks]
        )
        header_layout.addWidget(play_btn)
        header_layout.addWidget(download_btn)
        layout.addWidget(header)

        tables = QHBoxLayout()
        tables.setSpacing(10)
        songs_card = QFrame()
        songs_card.setObjectName("Panel")
        songs_layout = QVBoxLayout(songs_card)
        songs_layout.addWidget(self._section_label("热门歌曲"))
        self.artist_songs_table = self._make_track_table()
        self.artist_songs_table.doubleClicked.connect(
            lambda: self.play_from_list(self.artist_tracks, self.artist_songs_table.currentRow())
        )
        songs_layout.addWidget(self.artist_songs_table)
        tables.addWidget(songs_card, 3)

        albums_card = QFrame()
        albums_card.setObjectName("Panel")
        albums_layout = QVBoxLayout(albums_card)
        albums_layout.addWidget(self._section_label("专辑"))
        self.artist_album_table = QTableWidget(0, 3)
        self.artist_album_table.setHorizontalHeaderLabels(["#", "专辑", "曲目"])
        self.artist_album_table.verticalHeader().setVisible(False)
        self.artist_album_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.artist_album_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.artist_album_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.artist_album_table.doubleClicked.connect(self._open_artist_album)
        albums_layout.addWidget(self.artist_album_table)
        tables.addWidget(albums_card, 2)
        layout.addLayout(tables, 1)
        self.artist_tracks = []
        self.artist_albums = []
        return page

    def _make_track_table(self):
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["#", "歌曲", "歌手", "专辑"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        return table

    @staticmethod
    def _table_tracks(table):
        tracks = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None:
                track = item.data(TRACK_ROLE)
                if track:
                    tracks.append(track)
        return tracks

    def _select_discover_tab(self, index):
        for position, button in enumerate(self.discover_tabs):
            button.setChecked(position == index)
        self.discover_stack.setCurrentIndex(index)
        table_tab = index in (0, 1, 2)
        self.discover_play_btn.setEnabled(table_tab)
        self.discover_download_btn.setEnabled(table_tab)
        if not self._require_cookie(silent=True):
            return
        if index == 0 and self.daily_table.rowCount() == 0:
            self._load_daily()
        elif index == 1 and self.fm_table.rowCount() == 0:
            self._load_fm()
        elif index == 2 and self.new_table.rowCount() == 0:
            self._load_new_songs()
        elif index == 3 and self.toplist_table.rowCount() == 0:
            self._load_toplists()
        elif index == 4 and self.user_playlist_table.rowCount() == 0:
            self._load_user_playlists()

    def _load_discover_tab(self):
        index = self.discover_stack.currentIndex()
        if index == 0:
            self._load_daily()
        elif index == 1:
            self._load_fm()
        elif index == 2:
            self._load_new_songs()
        elif index == 3:
            self._load_toplists()
        elif index == 4:
            self._load_user_playlists()

    def _load_daily(self):
        if not self._require_cookie():
            return
        self.statusBar().showMessage("加载每日推荐 ...")
        run_async(
            self.core.recommend_songs,
            on_done=lambda data: self._on_songs_loaded("daily", data),
            on_error=self.on_error,
        )

    def _load_fm(self):
        if not self._require_cookie():
            return
        self.statusBar().showMessage("加载私人FM ...")
        run_async(
            self.core.personal_fm,
            on_done=lambda data: self._on_songs_loaded("fm", data),
            on_error=self.on_error,
        )

    def _load_new_songs(self):
        if not self._require_cookie():
            return
        self.statusBar().showMessage("加载新歌速递 ...")
        run_async(
            self.core.new_songs,
            on_done=lambda data: self._on_songs_loaded("new", data),
            on_error=self.on_error,
        )

    def _on_songs_loaded(self, which, data):
        songs = data.get("songs", [])
        table = {
            "daily": self.daily_table,
            "fm": self.fm_table,
            "new": self.new_table,
        }[which]
        self.fill_track_table(table, songs)
        labels = {"daily": "每日推荐", "fm": "私人FM", "new": "新歌速递"}
        self.statusBar().showMessage(f"{labels[which]}: {len(songs)} 首", 5000)

    def _load_toplists(self):
        if not self._require_cookie():
            return
        self.statusBar().showMessage("加载排行榜 ...")
        run_async(
            self.core.toplists, on_done=self._on_toplists, on_error=self.on_error
        )

    def _on_toplists(self, data):
        lists = data.get("lists", [])
        table = self.toplist_table
        table.setRowCount(len(lists))
        for row, item in enumerate(lists):
            index_item = QTableWidgetItem(str(row + 1))
            index_item.setData(TRACK_ROLE, item)
            table.setItem(row, 0, index_item)
            table.setItem(row, 1, QTableWidgetItem(item.get("name", "")))
            table.setItem(row, 2, QTableWidgetItem(item.get("update_frequency", "")))
        self.statusBar().showMessage(f"排行榜: {len(lists)} 个", 5000)

    def _load_user_playlists(self):
        if not self._require_cookie():
            return
        self.statusBar().showMessage("加载我的歌单 ...")
        run_async(
            self.core.user_playlists,
            on_done=self._on_user_playlists,
            on_error=self.on_error,
        )

    def _on_user_playlists(self, data):
        playlists = data.get("playlists", [])
        table = self.user_playlist_table
        table.setRowCount(len(playlists))
        for row, item in enumerate(playlists):
            index_item = QTableWidgetItem(str(row + 1))
            index_item.setData(TRACK_ROLE, item)
            table.setItem(row, 0, index_item)
            table.setItem(row, 1, QTableWidgetItem(item.get("name", "")))
            table.setItem(
                row, 2, QTableWidgetItem(str(item.get("track_count", 0)))
            )
        self.statusBar().showMessage(f"我的歌单: {len(playlists)} 个", 5000)

    def _discover_play_all(self):
        table = self.discover_stack.currentWidget()
        tracks = self._table_tracks(table) if isinstance(table, QTableWidget) else []
        if not tracks:
            self.statusBar().showMessage("当前列表为空", 3000)
            return
        self.play_from_list(tracks, 0)

    def _discover_download_all(self):
        table = self.discover_stack.currentWidget()
        tracks = self._table_tracks(table) if isinstance(table, QTableWidget) else []
        for track in tracks:
            self.start_download(track)

    def _open_toplist(self):
        row = self.toplist_table.currentRow()
        if row < 0:
            return
        item = self.toplist_table.item(row, 0)
        info = item.data(TRACK_ROLE) if item else None
        if not info:
            return
        self.statusBar().showMessage(f"加载榜单 {info.get('name')} ...")
        run_async(
            self.core.playlist,
            str(info.get("id")),
            on_done=lambda data: TrackListDialog(
                self, f"{info.get('name')}", data.get("tracks")
            ).exec(),
            on_error=self.on_error,
        )

    def _open_user_playlist(self):
        row = self.user_playlist_table.currentRow()
        if row < 0:
            return
        item = self.user_playlist_table.item(row, 0)
        info = item.data(TRACK_ROLE) if item else None
        if not info:
            return
        run_async(
            self.core.playlist,
            str(info.get("id")),
            on_done=lambda data: TrackListDialog(
                self, info.get("name", "歌单"), data.get("tracks")
            ).exec(),
            on_error=self.on_error,
        )

    def show_artist(self, artist_id):
        self.nav.setCurrentRow(NAV_ARTIST)
        self.artist_input.setText(str(artist_id))
        self.load_artist()

    def load_artist(self):
        artist_id = self.artist_input.text().strip()
        if not artist_id:
            return
        if not self._require_cookie():
            return
        self.statusBar().showMessage("加载歌手 ...")
        run_async(
            self.core.artist_detail,
            artist_id,
            on_done=self._on_artist_detail,
            on_error=self.on_error,
        )
        run_async(
            self.core.artist_songs,
            artist_id,
            50,
            0,
            on_done=self._on_artist_songs,
            on_error=self.on_error,
        )
        run_async(
            self.core.artist_albums,
            artist_id,
            30,
            0,
            on_done=self._on_artist_albums,
            on_error=self.on_error,
        )

    def _on_artist_detail(self, data):
        self.artist_name.setText(data.get("name", ""))
        aliases = data.get("alias") or []
        self.artist_alias.setText(" / ".join(str(alias) for alias in aliases))
        self.artist_input.setText(str(data.get("id") or self.artist_input.text()))
        pic = data.get("picUrl", "")
        if pic:
            self.load_cover(pic, self.artist_avatar, 96)

    def _on_artist_songs(self, data):
        songs = data.get("songs", [])
        self.artist_tracks = songs
        self.fill_track_table(self.artist_songs_table, songs)
        self.statusBar().showMessage(f"热门歌曲: {len(songs)} 首", 4000)

    def _on_artist_albums(self, data):
        albums = data.get("albums", [])
        self.artist_albums = albums
        table = self.artist_album_table
        table.setRowCount(len(albums))
        for row, album in enumerate(albums):
            index_item = QTableWidgetItem(str(row + 1))
            index_item.setData(TRACK_ROLE, album)
            table.setItem(row, 0, index_item)
            table.setItem(row, 1, QTableWidgetItem(album.get("name", "")))
            table.setItem(row, 2, QTableWidgetItem(str(album.get("size", 0))))
        self.statusBar().showMessage(f"专辑: {len(albums)} 张", 4000)

    def _open_artist_album(self):
        row = self.artist_album_table.currentRow()
        if row < 0 or row >= len(self.artist_albums):
            return
        album = self.artist_albums[row]
        run_async(
            self.core.album,
            str(album.get("id")),
            on_done=lambda data: TrackListDialog(
                self, album.get("name", "专辑"), data.get("songs")
            ).exec(),
            on_error=self.on_error,
        )

    def _require_cookie(self, silent=False):
        if not self.core.has_cookie():
            if not silent:
                QMessageBox.warning(self, "未登录", "该功能需要登录网易云账号。")
            return False
        return True

    def show_selected_comments(self):
        track = self.selected_track(self.search_table)
        if track is None:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        if not self._require_cookie():
            return
        title = f"{track.get('name', '')} · 评论"
        run_async(
            self.core.comments,
            track.get("id"),
            20,
            0,
            on_done=lambda data: CommentsDialog(title, data).exec(),
            on_error=self.on_error,
        )

    def show_selected_artist(self):
        track = self.selected_track(self.search_table)
        if track is None:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        artist_ids = track.get("artist_ids") or []
        if not artist_ids:
            QMessageBox.information(self, "提示", "该结果没有歌手 ID，可切换音源再试。")
            return
        self.show_artist(str(artist_ids[0]))

    def _build_collection_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Panel")
        row = QHBoxLayout(card)
        self.collection_type = QComboBox()
        self.collection_type.addItem("歌单", "playlist")
        self.collection_type.addItem("专辑", "album")
        self.collection_type.setFixedWidth(90)
        row.addWidget(self.collection_type)
        self.collection_input = QLineEdit()
        self.collection_input.setPlaceholderText("输入歌单 / 专辑 ID 或链接")
        self.collection_input.returnPressed.connect(self.do_parse_collection)
        row.addWidget(self.collection_input, 1)
        parse_btn = QPushButton("解析")
        parse_btn.setObjectName("Accent")
        parse_btn.clicked.connect(self.do_parse_collection)
        row.addWidget(parse_btn)
        layout.addWidget(card)

        self.collection_info = QLabel("尚未解析")
        self.collection_info.setObjectName("SubTitle")
        layout.addWidget(self.collection_info)

        table_card = QFrame()
        table_card.setObjectName("Panel")
        table_layout = QVBoxLayout(table_card)
        self.collection_table = QTableWidget(0, 4)
        self.collection_table.setHorizontalHeaderLabels(["#", "歌曲", "歌手", "专辑"])
        self.collection_table.verticalHeader().setVisible(False)
        self.collection_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.collection_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.collection_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.collection_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.collection_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.collection_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.collection_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.collection_table.doubleClicked.connect(self.play_selected_collection)
        table_layout.addWidget(self.collection_table)
        layout.addWidget(table_card, 1)

        actions = QHBoxLayout()
        play_btn = QPushButton("▶  播放选中")
        play_btn.setObjectName("Accent")
        play_btn.clicked.connect(self.play_selected_collection)
        download_btn = QPushButton("⬇  下载选中")
        download_btn.clicked.connect(self.download_selected_collection)
        download_all_btn = QPushButton("⬇  全部下载")
        download_all_btn.clicked.connect(self.download_all_collection)
        actions.addWidget(play_btn)
        actions.addWidget(download_btn)
        actions.addWidget(download_all_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_local_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QHBoxLayout(card)
        self.local_filter = QLineEdit()
        self.local_filter.setPlaceholderText("过滤歌名 / 歌手 / 专辑")
        self.local_filter.textChanged.connect(self._refresh_local_tracks)
        card_layout.addWidget(self.local_filter, 1)
        card_layout.addWidget(QLabel("列表"))
        self.local_playlist_combo = QComboBox()
        self.local_playlist_combo.setFixedWidth(170)
        self.local_playlist_combo.currentIndexChanged.connect(self._refresh_local_tracks)
        card_layout.addWidget(self.local_playlist_combo)
        new_btn = QPushButton("新建歌单")
        new_btn.clicked.connect(self._create_local_playlist)
        card_layout.addWidget(new_btn)
        del_btn = QPushButton("删除歌单")
        del_btn.clicked.connect(self._delete_local_playlist)
        card_layout.addWidget(del_btn)
        export_btn = QPushButton("导出歌单")
        export_btn.clicked.connect(self._export_local_playlist)
        card_layout.addWidget(export_btn)
        scan_btn = QPushButton("扫描目录")
        scan_btn.setObjectName("Accent")
        scan_btn.clicked.connect(self._scan_library)
        card_layout.addWidget(scan_btn)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_local_tracks)
        card_layout.addWidget(refresh_btn)
        layout.addWidget(card)

        table_card = QFrame()
        table_card.setObjectName("Panel")
        table_layout = QVBoxLayout(table_card)
        self.local_table = QTableWidget(0, 6)
        self.local_table.setHorizontalHeaderLabels(
            ["#", "歌曲", "歌手", "专辑", "时长", "格式"]
        )
        self.local_table.verticalHeader().setVisible(False)
        self.local_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.local_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.local_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for column, mode in (
            (0, QHeaderView.ResizeMode.ResizeToContents),
            (1, QHeaderView.ResizeMode.Stretch),
            (2, QHeaderView.ResizeMode.ResizeToContents),
            (3, QHeaderView.ResizeMode.Stretch),
            (4, QHeaderView.ResizeMode.ResizeToContents),
            (5, QHeaderView.ResizeMode.ResizeToContents),
        ):
            self.local_table.horizontalHeader().setSectionResizeMode(column, mode)
        self.local_table.doubleClicked.connect(self.play_selected_local)
        self.local_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.local_table.customContextMenuRequested.connect(self._local_context_menu)
        table_layout.addWidget(self.local_table)
        layout.addWidget(table_card, 1)

        actions = QHBoxLayout()
        play_btn = QPushButton("▶  播放选中")
        play_btn.setObjectName("Accent")
        play_btn.clicked.connect(self.play_selected_local)
        queue_btn = QPushButton("加入队列")
        queue_btn.clicked.connect(self.queue_selected_local)
        fav_btn = QPushButton("❤  收藏")
        fav_btn.clicked.connect(self.favorite_selected_local)
        add_btn = QPushButton("加入歌单")
        add_btn.clicked.connect(self.playlist_selected_local)
        remove_btn = QPushButton("从歌单移除")
        remove_btn.clicked.connect(self.remove_selected_from_playlist)
        self.local_info_label = QLabel("")
        self.local_info_label.setObjectName("SubTitle")
        actions.addWidget(play_btn)
        actions.addWidget(queue_btn)
        actions.addWidget(fav_btn)
        actions.addWidget(add_btn)
        actions.addWidget(remove_btn)
        actions.addStretch(1)
        actions.addWidget(self.local_info_label)
        layout.addLayout(actions)
        return page

    def _build_downloads_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        self.download_table = QTableWidget(0, 5)
        self.download_table.setHorizontalHeaderLabels(
            ["歌曲", "音质", "状态", "进度", "操作"]
        )
        self.download_table.verticalHeader().setVisible(False)
        self.download_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.download_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.download_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.download_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.download_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.download_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        card_layout.addWidget(self.download_table)
        layout.addWidget(card, 1)

        row = QHBoxLayout()
        clear_btn = QPushButton("清除已完成")
        clear_btn.clicked.connect(self.clear_finished_downloads)
        row.addWidget(clear_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _build_api_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        row = QHBoxLayout()
        row.addWidget(QLabel("功能"))
        self.api_op_combo = QComboBox()
        for key, name in [
            ("song_url", "歌曲URL"),
            ("song_detail", "歌曲详情"),
            ("lyric", "歌词"),
            ("song_full", "完整信息"),
            ("search", "搜索"),
            ("playlist", "歌单详情"),
            ("album", "专辑详情"),
            ("download", "下载歌曲"),
            ("account", "账号信息"),
        ]:
            self.api_op_combo.addItem(name, key)
        self.api_op_combo.currentIndexChanged.connect(self.on_api_op_changed)
        row.addWidget(self.api_op_combo)
        self.api_input = QLineEdit()
        self.api_input.returnPressed.connect(self.run_api_op)
        row.addWidget(self.api_input, 1)
        self.api_level_label = QLabel("音质")
        row.addWidget(self.api_level_label)
        self.api_level_combo = QComboBox()
        for key, name in QUALITY_NAMES.items():
            self.api_level_combo.addItem(name, key)
        row.addWidget(self.api_level_combo)
        self.api_limit_label = QLabel("数量")
        row.addWidget(self.api_limit_label)
        self.api_limit_spin = QSpinBox()
        self.api_limit_spin.setRange(1, 100)
        self.api_limit_spin.setValue(10)
        row.addWidget(self.api_limit_spin)
        run_btn = QPushButton("执行")
        run_btn.setObjectName("Accent")
        run_btn.clicked.connect(self.run_api_op)
        row.addWidget(run_btn)
        card_layout.addLayout(row)

        actions = QHBoxLayout()
        copy_btn = QPushButton("复制 JSON")
        copy_btn.clicked.connect(self.copy_api_json)
        copy_url_btn = QPushButton("复制播放链接")
        copy_url_btn.clicked.connect(self.copy_api_url)
        play_btn = QPushButton("播放结果")
        play_btn.clicked.connect(self.play_api_result)
        download_btn = QPushButton("下载结果")
        download_btn.clicked.connect(self.download_api_result)
        self.api_account_label = QLabel("")
        self.api_account_label.setObjectName("SubTitle")
        actions.addWidget(copy_btn)
        actions.addWidget(copy_url_btn)
        actions.addWidget(play_btn)
        actions.addWidget(download_btn)
        actions.addStretch(1)
        actions.addWidget(self.api_account_label)
        card_layout.addLayout(actions)
        layout.addWidget(card)
        self.api_card = card

        self.api_output = QPlainTextEdit()
        self.api_output.setReadOnly(True)
        self.api_output.setPlaceholderText(
            "结果以 JSON 显示在这里；全部在本机 C++ 核心内完成，不开放任何网络端口"
        )
        layout.addWidget(self.api_output, 1)
        self.on_api_op_changed()
        return page

    def on_api_op_changed(self):
        op = self.api_op_combo.currentData()
        self.api_input.setVisible(op != "account")
        level_visible = op in ("song_url", "song_full", "download")
        self.api_level_label.setVisible(level_visible)
        self.api_level_combo.setVisible(level_visible)
        self.api_limit_label.setVisible(op == "search")
        self.api_limit_spin.setVisible(op == "search")
        placeholders = {
            "song_url": "输入歌曲 ID 或网易云链接",
            "song_detail": "输入歌曲 ID 或网易云链接",
            "lyric": "输入歌曲 ID 或网易云链接",
            "song_full": "输入歌曲 ID 或网易云链接",
            "search": "输入关键词",
            "playlist": "输入歌单 ID 或链接",
            "album": "输入专辑 ID 或链接",
            "download": "输入歌曲 ID 或链接",
        }
        self.api_input.setPlaceholderText(placeholders.get(op, ""))

    def run_api_op(self):
        op = self.api_op_combo.currentData()
        text = self.api_input.text().strip()
        level = self.api_level_combo.currentData()
        limit = self.api_limit_spin.value()
        core = self.core
        if op != "account" and not text:
            self.api_output.setPlainText("请输入内容")
            return

        if op == "song_url":
            fn = lambda: core.song_url(text, level)
        elif op == "song_detail":
            fn = lambda: core.song_detail(text)
        elif op == "lyric":
            fn = lambda: core.lyric(text)
        elif op == "song_full":
            fn = lambda: core.song_full(text, level)
        elif op == "search":
            fn = lambda: {"songs": core.search(text, limit, 0).get("songs", [])}
        elif op == "playlist":
            fn = lambda: core.playlist(text)
        elif op == "album":
            fn = lambda: core.album(text)
        elif op == "download":
            fn = lambda: core.download_song(
                text, level, core.settings.get("download_dir", "")
            )
        else:
            fn = lambda: core._parse(core.client.user_account())

        self.api_output.setPlainText("执行中 ...")
        run_async(fn, on_done=self.on_api_done, on_error=self.on_api_error)

    def on_api_done(self, result):
        self.api_last_result = result if isinstance(result, dict) else {"data": result}
        self.api_last_url = str(self.api_last_result.get("url") or "")
        self.api_output.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        if self.api_op_combo.currentData() == "account":
            self._update_api_account(self.api_last_result)
        self.statusBar().showMessage("执行完成", 3000)

    def on_api_error(self, message):
        self.api_output.setPlainText(f"执行失败: {message}")
        self.statusBar().showMessage(f"执行失败: {message}", 8000)

    def _update_api_account(self, account):
        if not account:
            return
        vip_names = {0: "无VIP", 11: "音乐包", 110: "黑胶VIP", 120: "黑胶SVIP"}
        vip_type = int(account.get("vip_type", 0) or 0)
        vip = vip_names.get(vip_type, f"VIP{vip_type}")
        self.api_account_label.setText(
            f"{account.get('nickname', '')} · uid {account.get('user_id', 0)} · {vip}"
        )

    def copy_api_json(self):
        QApplication.clipboard().setText(self.api_output.toPlainText())
        self.statusBar().showMessage("已复制 JSON", 3000)

    def copy_api_url(self):
        if not self.api_last_url:
            self.statusBar().showMessage("当前结果没有播放链接", 3000)
            return
        QApplication.clipboard().setText(self.api_last_url)
        self.statusBar().showMessage("已复制播放链接", 3000)

    def play_api_result(self):
        if not self.api_last_url:
            self.statusBar().showMessage("当前结果没有播放链接", 3000)
            return
        result = self.api_last_result
        self.current_track = result
        self.player.play_url(self.api_last_url, volume=self.volume_slider.value())
        self.bar_title.setText(result.get("name", ""))
        self.bar_artist.setText(result.get("ar_name") or result.get("artists", ""))
        self.load_cover(result.get("pic", ""), self.bar_cover, 56)
        self.statusBar().showMessage(f"正在播放: {result.get('name', '')}", 5000)

    def download_api_result(self):
        result = self.api_last_result
        if result.get("path"):
            self.statusBar().showMessage(
                f"已下载: {result.get('filename', result['path'])}", 8000
            )
            return
        if result.get("id"):
            self.start_download(
                {"id": result["id"], "name": result.get("name", ""), "source": "local"}
            )
            return
        if self.api_last_url:
            filename = (
                f"{result.get('name') or 'music'}.{result.get('type') or 'mp3'}"
            )
            run_async(
                self.core.client.download_url,
                self.api_last_url,
                self.core.settings.get("download_dir", ""),
                filename,
                referer_for(self.api_last_url),
                on_done=lambda info: self.statusBar().showMessage(
                    f"下载完成: {info.get('filename', '')}", 8000
                ),
                on_error=self.on_api_error,
            )
            return
        self.statusBar().showMessage("当前结果没有可下载内容", 3000)

    def _build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        general = QFrame()
        general.setObjectName("Panel")
        general_layout = QVBoxLayout(general)
        general_layout.addWidget(self._section_label("下载与音质"))

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("下载目录"))
        self.download_dir_edit = QLineEdit()
        dir_row.addWidget(self.download_dir_edit, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.browse_download_dir)
        dir_row.addWidget(browse_btn)
        general_layout.addLayout(dir_row)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("默认音质"))
        self.settings_quality_combo = QComboBox()
        for key, name in QUALITY_NAMES.items():
            self.settings_quality_combo.addItem(name, key)
        quality_row.addWidget(self.settings_quality_combo)
        quality_row.addWidget(QLabel("请求超时(秒)"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        quality_row.addWidget(self.timeout_spin)
        quality_row.addWidget(QLabel("在线解析冷却(秒)"))
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 600)
        self.cooldown_spin.setToolTip("在线 API 每次解析后的等待时间，0 表示不限制")
        quality_row.addWidget(self.cooldown_spin)
        quality_row.addStretch(1)
        general_layout.addLayout(quality_row)

        self.write_tags_check = QCheckBox("下载后写入音乐标签（标题/歌手/专辑/歌词/封面）")
        general_layout.addWidget(self.write_tags_check)
        layout.addWidget(general)

        playback = QFrame()
        playback.setObjectName("Panel")
        playback_layout = QVBoxLayout(playback)
        playback_layout.addWidget(self._section_label("下载与播放"))
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("命名模板"))
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("{artist}/{album}/{name} [{level}]")
        template_row.addWidget(self.template_edit, 1)
        playback_layout.addLayout(template_row)
        options_row = QHBoxLayout()
        self.skip_existing_check = QCheckBox("跳过已存在")
        self.resume_check = QCheckBox("播放进度记忆")
        self.tray_check = QCheckBox("最小化到托盘")
        self.topmost_check = QCheckBox("窗口置顶")
        self.sync_fav_check = QCheckBox("收藏同步网易云")
        self.fallback_check = QCheckBox("聚合失败自动换源")
        self.desktop_lyrics_check = QCheckBox("桌面歌词")
        self.desktop_lyrics_check.toggled.connect(self.toggle_desktop_lyrics)
        options_row.addWidget(self.skip_existing_check)
        options_row.addWidget(self.resume_check)
        options_row.addWidget(self.tray_check)
        options_row.addWidget(self.topmost_check)
        options_row.addWidget(self.sync_fav_check)
        options_row.addWidget(self.fallback_check)
        options_row.addWidget(self.desktop_lyrics_check)
        options_row.addWidget(QLabel("下载并发"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 8)
        options_row.addWidget(self.concurrency_spin)
        options_row.addStretch(1)
        playback_layout.addLayout(options_row)
        dirs_row = QHBoxLayout()
        dirs_row.addWidget(QLabel("额外音乐目录"))
        self.library_dirs_edit = QLineEdit()
        self.library_dirs_edit.setPlaceholderText("可选，多个目录用 ; 分隔")
        dirs_row.addWidget(self.library_dirs_edit, 1)
        playback_layout.addLayout(dirs_row)
        layout.addWidget(playback)

        account = QFrame()
        account.setObjectName("Panel")
        account_layout = QVBoxLayout(account)
        account_layout.addWidget(self._section_label("账号 Cookie"))
        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("粘贴网页版 Cookie，例如 MUSIC_U=xxxx; os=pc;")
        self.cookie_edit.setFixedHeight(110)
        account_layout.addWidget(self.cookie_edit)

        login_row = QHBoxLayout()
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("网易云邮箱 / 手机号")
        self.account_password_edit = QLineEdit()
        self.account_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.account_password_edit.setPlaceholderText("密码")
        self.account_login_btn = QPushButton("账号密码登录")
        self.account_login_btn.clicked.connect(self.account_login)
        login_row.addWidget(self.account_edit, 1)
        login_row.addWidget(self.account_password_edit, 1)
        login_row.addWidget(self.account_login_btn)
        account_layout.addLayout(login_row)

        account_row = QHBoxLayout()
        self.save_cookie_btn = QPushButton("保存 Cookie")
        self.save_cookie_btn.setObjectName("Accent")
        self.save_cookie_btn.clicked.connect(self.save_cookie)
        self.qr_btn = QPushButton("扫码登录")
        self.qr_btn.clicked.connect(self.qr_login)
        self.web_btn = QPushButton("网页登录")
        self.web_btn.clicked.connect(self.web_login)
        self.cookie_status = QLabel("")
        self.cookie_status.setObjectName("SubTitle")
        account_row.addWidget(self.save_cookie_btn)
        account_row.addWidget(self.qr_btn)
        account_row.addWidget(self.web_btn)
        account_row.addWidget(self.cookie_status, 1)
        account_layout.addLayout(account_row)
        layout.addWidget(account)

        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("Accent")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn, 0, Qt.AlignmentFlag.AlignRight)

        about = QFrame()
        about.setObjectName("Panel")
        about_layout = QVBoxLayout(about)
        about_layout.addWidget(self._section_label("关于"))
        edition_name = "公开精简版" if PUBLIC_EDITION else "完整版"
        about_label = QLabel(
            f"Neko.night - Music v{APP_VERSION}（{edition_name}）\n"
            "C++ 核心（WinHTTP + BCrypt）+ PyQt6 界面 · 仅供学习交流，请尊重版权"
        )
        about_label.setObjectName("SubTitle")
        about_label.setWordWrap(True)
        about_layout.addWidget(about_label)
        layout.addWidget(about)

        layout.addWidget(self._build_maintenance_panel())

        layout.addStretch(1)
        return page

    def _build_maintenance_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.addWidget(self._section_label("维护与更新"))

        cache_row = QHBoxLayout()
        self.cache_label = QLabel("")
        self.cache_label.setObjectName("SubTitle")
        cache_row.addWidget(self.cache_label, 1)
        clear_cache_btn = QPushButton("清理封面缓存")
        clear_cache_btn.clicked.connect(self._clear_cache)
        cache_row.addWidget(clear_cache_btn)
        panel_layout.addLayout(cache_row)

        update_row = QHBoxLayout()
        self.update_status = QLabel(f"当前版本 v{APP_VERSION}")
        self.update_status.setObjectName("SubTitle")
        update_row.addWidget(self.update_status, 1)
        self.update_download_btn = QPushButton("打开下载页")
        self.update_download_btn.setObjectName("Accent")
        self.update_download_btn.setVisible(False)
        self.update_download_btn.clicked.connect(self._open_releases_page)
        update_row.addWidget(self.update_download_btn)
        self.update_btn = QPushButton("检查更新")
        self.update_btn.clicked.connect(self._check_updates_manual)
        update_row.addWidget(self.update_btn)
        panel_layout.addLayout(update_row)

        self._refresh_cache_label()
        return panel

    def _cache_size(self):
        from library import COVERS_DIR

        total = 0
        if COVERS_DIR.is_dir():
            for path in COVERS_DIR.iterdir():
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _refresh_cache_label(self):
        if not hasattr(self, "cache_label"):
            return
        self.cache_label.setText(
            f"封面缓存 {format_size(self._cache_size()) or '0B'}"
        )

    def _clear_cache(self):
        from library import COVERS_DIR

        if (
            QMessageBox.question(
                self,
                "清理缓存",
                "确定清空封面缓存吗？下次浏览时会重新下载。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        removed = 0
        if COVERS_DIR.is_dir():
            for path in COVERS_DIR.iterdir():
                try:
                    if path.is_file():
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        self.cover_cache.clear()
        self._refresh_cache_label()
        self.statusBar().showMessage(f"已清理 {removed} 个封面缓存文件", 5000)

    def _check_updates_manual(self):
        self._manual_update_check = True
        self.update_status.setText("正在检查更新…")
        self.update_btn.setEnabled(False)
        self._update_checker.check()

    def _open_releases_page(self):
        QDesktopServices.openUrl(QUrl(self._update_url or RELEASES_URL))

    def _on_update_checked(self, tag, url):
        self.update_btn.setEnabled(True)
        if url:
            self._update_url = url
        manual = self._manual_update_check
        self._manual_update_check = False
        if not tag:
            if manual:
                self.update_status.setText("检查更新失败，请稍后再试")
            return
        if is_newer(tag, APP_VERSION):
            self.update_status.setText(
                f"发现新版本 v{tag}（当前 v{APP_VERSION}）"
            )
            self.update_download_btn.setVisible(True)
            if not manual:
                self.statusBar().showMessage(
                    f"发现新版本 v{tag}，可在设置页打开下载页", 12000
                )
        else:
            self.update_status.setText(f"已是最新版本 v{APP_VERSION}")
            self.update_download_btn.setVisible(False)

    def _section_label(self, text):
        label = QLabel(text)
        label.setObjectName("Section")
        return label

    def _build_player_bar(self):
        bar = QFrame()
        bar.setObjectName("Panel")
        bar.setFixedHeight(84)
        layout = QHBoxLayout(bar)
        layout.setSpacing(12)

        self.bar_cover = QLabel()
        self.bar_cover.setFixedSize(56, 56)
        self.bar_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar_cover.setText("♪")
        self.bar_cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bar_cover.setToolTip("点击打开大播放器")
        self.bar_cover.installEventFilter(self)
        layout.addWidget(self.bar_cover)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.bar_title = QLabel("未在播放")
        self.bar_title.setObjectName("Section")
        self.bar_artist = QLabel("")
        self.bar_artist.setObjectName("SubTitle")
        info.addWidget(self.bar_title)
        info.addWidget(self.bar_artist)
        layout.addLayout(info)
        layout.addSpacing(8)

        prev_btn = QPushButton("⏮")
        prev_btn.setObjectName("Round")
        prev_btn.clicked.connect(self.play_previous)
        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("Play")
        self.play_btn.clicked.connect(self.toggle_play)
        next_btn = QPushButton("⏭")
        next_btn.setObjectName("Round")
        next_btn.clicked.connect(self.play_next)
        layout.addWidget(prev_btn)
        layout.addWidget(self.play_btn)
        layout.addWidget(next_btn)

        self.mode_btn = QPushButton("🔁")
        self.mode_btn.setObjectName("Round")
        self.mode_btn.setToolTip("播放模式：列表循环 / 单曲循环 / 随机")
        self.mode_btn.clicked.connect(self._cycle_play_mode)
        queue_btn = QPushButton("☰")
        queue_btn.setObjectName("Round")
        queue_btn.setToolTip("播放队列")
        queue_btn.clicked.connect(self._show_queue)
        self.fav_btn = QPushButton("♡")
        self.fav_btn.setObjectName("Round")
        self.fav_btn.setToolTip("收藏")
        self.fav_btn.clicked.connect(self.toggle_current_favorite)
        self.speed_btn = QPushButton("1x")
        self.speed_btn.setObjectName("Round")
        self.speed_btn.setToolTip("倍速")
        self.speed_btn.clicked.connect(self._show_speed_menu)
        self.sleep_btn = QPushButton("🌙")
        self.sleep_btn.setObjectName("Round")
        self.sleep_btn.setToolTip("睡眠定时")
        self.sleep_btn.clicked.connect(self._show_sleep_menu)
        layout.addWidget(self.mode_btn)
        layout.addWidget(queue_btn)
        layout.addWidget(self.fav_btn)
        layout.addWidget(self.speed_btn)
        layout.addWidget(self.sleep_btn)

        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("SubTitle")
        layout.addWidget(self.time_label)
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 0)
        self.progress_slider.sliderPressed.connect(self.on_seek_start)
        self.progress_slider.sliderReleased.connect(self.on_seek_end)
        layout.addWidget(self.progress_slider, 1)
        self.duration_label = QLabel("00:00")
        self.duration_label.setObjectName("SubTitle")
        layout.addWidget(self.duration_label)

        volume_icon = QLabel("🔊")
        layout.addWidget(volume_icon)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        layout.addWidget(self.volume_slider)
        return bar

    def _connect_player(self):
        self.player.state_changed.connect(self.on_player_state)
        self.player.position_changed.connect(self.on_player_position)
        self.player.duration_changed.connect(self.on_player_duration)
        self.player.finished.connect(self.on_player_finished)
        self.player.error.connect(self.on_player_error)

    def _apply_settings(self):
        settings = self.core.settings
        self.download_dir_edit.setText(settings.get("download_dir", ""))
        self.timeout_spin.setValue(int(settings.get("timeout", 30)))
        quality = settings.get("quality", "lossless")
        index = self.quality_combo.findData(quality)
        self.quality_combo.setCurrentIndex(index if index >= 0 else 2)
        settings_index = self.settings_quality_combo.findData(quality)
        self.settings_quality_combo.setCurrentIndex(
            settings_index if settings_index >= 0 else 2
        )
        self.volume_slider.setValue(int(settings.get("volume", 70)))
        self.write_tags_check.setChecked(bool(settings.get("write_tags", True)))
        self.template_edit.setText(
            settings.get("download_template", "{name} [{level}]")
        )
        self.skip_existing_check.setChecked(bool(settings.get("skip_existing", True)))
        self.resume_check.setChecked(bool(settings.get("resume_playback", True)))
        self.tray_check.setChecked(bool(settings.get("minimize_to_tray", False)))
        self.topmost_check.setChecked(bool(settings.get("always_on_top", False)))
        self.sync_fav_check.setChecked(bool(settings.get("sync_favorites", False)))
        self.fallback_check.setChecked(bool(settings.get("jx_fallback", True)))
        self.desktop_lyrics_check.blockSignals(True)
        self.desktop_lyrics_check.setChecked(
            bool(settings.get("desktop_lyrics", False))
        )
        self.desktop_lyrics_check.blockSignals(False)
        self.concurrency_spin.setValue(int(settings.get("download_concurrency", 3)))
        get_download_pool(self.concurrency_spin.value())
        dirs = settings.get("library_dirs") or []
        if isinstance(dirs, str):
            dirs = [dirs]
        self.library_dirs_edit.setText(";".join(dirs))
        if settings.get("always_on_top"):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        mode = settings.get("play_mode", "list")
        if hasattr(self, "mode_btn"):
            self.mode_btn.setText(
                {"list": "🔁", "single": "🔂", "shuffle": "🔀"}.get(mode, "🔁")
            )
        source = settings.get("source", "local")
        platform = settings.get("jx_platform", "netease")
        target = (source, platform if source == "jx" else None)
        source_index = 0
        for index in range(self.source_combo.count()):
            if self.source_combo.itemData(index) == target:
                source_index = index
                break
        self.source_combo.setCurrentIndex(source_index)
        self.cooldown_spin.setValue(int(settings.get("parse_cooldown", 60)))
        self.on_source_changed()
        self.cookie_edit.setPlainText(self.core.cookie_text)

    def current_source(self):
        data = self.source_combo.currentData()
        if isinstance(data, (tuple, list)) and data:
            return data[0]
        return "local"

    def on_source_changed(self, *_):
        data = self.source_combo.currentData()
        if not data:
            return
        if isinstance(data, (tuple, list)):
            source, platform = data[0], data[1]
        else:
            source, platform = data, None
        self.core.settings["source"] = source
        if source == "jx" and platform:
            self.core.settings["jx_platform"] = platform
        self.core.save_settings()

    def current_quality(self):
        return self.quality_combo.currentData()

    def on_quality_changed(self):
        if self._syncing_quality:
            return
        self._syncing_quality = True
        index = self.settings_quality_combo.findData(self.current_quality())
        if index >= 0:
            self.settings_quality_combo.setCurrentIndex(index)
        self._syncing_quality = False
        self.core.settings["quality"] = self.current_quality()
        self.core.save_settings()

    def browse_download_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择下载目录", self.download_dir_edit.text() or str(Path.home())
        )
        if directory:
            self.download_dir_edit.setText(directory)

    def save_settings(self):
        self.core.settings["download_dir"] = self.download_dir_edit.text().strip()
        self.core.settings["quality"] = self.settings_quality_combo.currentData()
        self.core.settings["timeout"] = self.timeout_spin.value()
        self.core.settings["volume"] = self.volume_slider.value()
        self.core.settings["write_tags"] = self.write_tags_check.isChecked()
        self.core.settings["parse_cooldown"] = self.cooldown_spin.value()
        self.core.settings["download_template"] = (
            self.template_edit.text().strip() or "{name} [{level}]"
        )
        self.core.settings["skip_existing"] = self.skip_existing_check.isChecked()
        self.core.settings["resume_playback"] = self.resume_check.isChecked()
        self.core.settings["minimize_to_tray"] = self.tray_check.isChecked()
        self.core.settings["always_on_top"] = self.topmost_check.isChecked()
        self.core.settings["sync_favorites"] = self.sync_fav_check.isChecked()
        self.core.settings["jx_fallback"] = self.fallback_check.isChecked()
        self.core.settings["download_concurrency"] = self.concurrency_spin.value()
        self.core.settings["library_dirs"] = [
            part.strip()
            for part in self.library_dirs_edit.text().split(";")
            if part.strip()
        ]
        get_download_pool(self.concurrency_spin.value())
        always = self.topmost_check.isChecked()
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != always:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always)
            self.show()
        self.core.apply_settings()
        self.core.save_settings()
        self._syncing_quality = True
        index = self.quality_combo.findData(self.core.settings["quality"])
        if index >= 0:
            self.quality_combo.setCurrentIndex(index)
        self._syncing_quality = False
        self.statusBar().showMessage("设置已保存", 3000)

    def save_cookie(self):
        try:
            self.core.save_cookie(self.cookie_edit.toPlainText())
        except CoreError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_login_state()
        self.statusBar().showMessage("Cookie 已保存", 3000)

    def qr_login(self):
        dialog = QRLoginDialog(self.core, self)
        if dialog.exec():
            self.cookie_edit.setPlainText(self.core.cookie_text)
            self._refresh_login_state()
            QMessageBox.information(self, "登录成功", "扫码登录成功，Cookie 已保存。")

    def account_login(self):
        username = self.account_edit.text().strip()
        password = self.account_password_edit.text()
        if not username or not password:
            QMessageBox.warning(self, "提示", "请输入网易云账号（邮箱/手机号）和密码。")
            return
        self.account_login_btn.setEnabled(False)
        self.account_login_btn.setText("登录中...")
        run_async(
            self.core.login_password,
            username,
            password,
            on_done=self.on_account_login_done,
            on_error=self.on_account_login_error,
        )

    def on_account_login_done(self, result):
        self.account_login_btn.setEnabled(True)
        self.account_login_btn.setText("账号密码登录")
        if result.get("cookie"):
            self.account_password_edit.clear()
            self.cookie_edit.setPlainText(self.core.cookie_text)
            self._refresh_login_state()
            who = result.get("nickname") or result.get("user_id") or "账号"
            QMessageBox.information(self, "登录成功", f"已登录：{who}")
        else:
            QMessageBox.warning(
                self,
                "登录失败",
                f"{result.get('message') or '未知错误'}\n(code {result.get('code')})",
            )

    def on_account_login_error(self, message):
        self.account_login_btn.setEnabled(True)
        self.account_login_btn.setText("账号密码登录")
        self.on_error(message)

    def web_login(self):
        if getattr(self, "_web_login_running", False):
            return
        self._web_login_running = True
        self.web_btn.setEnabled(False)
        self.web_btn.setText("登录中...")

        result_path = Path(tempfile.gettempdir()) / "ncm_web_login.json"
        try:
            result_path.unlink()
        except OSError:
            pass
        script = Path(__file__).resolve().parent / "web_login.py"
        from paths import config_dir

        storage = str(config_dir() / "edge_profile")
        run_async(
            self._run_web_login,
            script,
            result_path,
            storage,
            on_done=self.on_web_login_done,
            on_error=self.on_web_login_error,
        )

    @staticmethod
    def _run_web_login(script, result_path, storage):
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--web-login", str(result_path), storage]
        else:
            check = subprocess.run(
                [sys.executable, "-c", "import websocket"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if check.returncode != 0:
                raise CoreError(
                    "网页登录需要 websocket-client，请安装：python -m pip install websocket-client"
                )
            command = [sys.executable, str(script), str(result_path), storage]
        subprocess.run(
            command,
            timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result_path.is_file():
            try:
                return json.loads(result_path.read_text(encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def on_web_login_done(self, data):
        self._web_login_running = False
        self.web_btn.setEnabled(True)
        self.web_btn.setText("网页登录")
        music_u = (data or {}).get("music_u", "")
        if not music_u:
            QMessageBox.information(self, "未完成", "登录窗口已关闭，没有检测到登录 Cookie。")
            return
        try:
            self.core.save_cookie(f"MUSIC_U={music_u};os=pc;appver=8.9.70;")
        except CoreError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.cookie_edit.setPlainText(self.core.cookie_text)
        self._refresh_login_state()
        QMessageBox.information(self, "登录成功", "网页登录成功，Cookie 已保存。")

    def on_web_login_error(self, message):
        self._web_login_running = False
        self.web_btn.setEnabled(True)
        self.web_btn.setText("网页登录")
        self.on_error(message)

    def _refresh_login_state(self):
        if self.core.has_cookie():
            self.login_label.setText("已配置 Cookie")
            self.cookie_status.setText("Cookie 已配置，可解析 VIP 音质")
        else:
            self.login_label.setText("未登录")
            self.cookie_status.setText("未配置 Cookie，仅能试听 / 标准音质")

    def do_search(self):
        keywords = self.search_input.text().strip()
        if not keywords:
            return
        limit = self.search_limit.value()
        self.statusBar().showMessage(f"搜索中: {keywords} ...")
        run_async(
            self.core.search_tracks,
            keywords,
            limit,
            0,
            self.current_source(),
            on_done=self.on_search_done,
            on_error=self.on_error,
        )

    def on_search_done(self, data):
        if isinstance(data, dict):
            tracks = data.get("songs") or data.get("tracks") or []
        else:
            tracks = list(data or [])
        self.fill_track_table(self.search_table, tracks)
        self.statusBar().showMessage(f"搜索完成，共 {len(tracks)} 条结果", 5000)

    def fill_track_table(self, table, tracks):
        table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            index_item = QTableWidgetItem(str(row + 1))
            index_item.setData(TRACK_ROLE, track)
            table.setItem(row, 0, index_item)
            table.setItem(row, 1, QTableWidgetItem(track.get("name", "")))
            table.setItem(row, 2, QTableWidgetItem(track.get("artists", "")))
            table.setItem(row, 3, QTableWidgetItem(track.get("album", "")))

    def selected_track(self, table):
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        if item is None:
            return None
        return item.data(TRACK_ROLE)

    def selected_tracks(self, table):
        tracks = []
        rows = sorted({index.row() for index in table.selectedIndexes()})
        for row in rows:
            item = table.item(row, 0)
            if item is not None:
                tracks.append(item.data(TRACK_ROLE))
        return tracks

    def play_selected_search(self):
        track = self.selected_track(self.search_table)
        if track is None:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        tracks = [
            self.search_table.item(row, 0).data(TRACK_ROLE)
            for row in range(self.search_table.rowCount())
        ]
        self.play_from_list(tracks, self.search_table.currentRow())

    def play_selected_collection(self):
        tracks = self.selected_tracks(self.collection_table)
        if not tracks:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        row = self.collection_table.currentRow()
        self.play_from_list(self.collection_tracks, max(0, row))

    def play_from_list(self, tracks, index):
        tracks = [track for track in tracks if track]
        if not tracks:
            return
        self.play_queue = tracks
        self.play_index = max(0, min(index, len(tracks) - 1))
        self._persist_queue()
        self.play_track(tracks[self.play_index])

    def play_track(self, track):
        quality = self.current_quality()
        self.statusBar().showMessage(f"正在获取播放链接: {track.get('name', '')} ...")
        run_async(
            self.core.playback_track,
            track,
            quality,
            on_done=self.on_song_ready,
            on_error=self.on_error,
        )

    def on_song_ready(self, data):
        self.current_track = data
        if not data.get("url"):
            self.statusBar().showMessage("获取播放链接失败", 5000)
            QMessageBox.warning(
                self,
                "无法播放",
                "未能获取播放链接。\n可能原因：版权限制、该音质不支持，或未配置 VIP Cookie。",
            )
            return
        if self.core.settings.get("resume_playback"):
            try:
                self._pending_resume = self.library.load_progress(data)
            except Exception:  # noqa: BLE001
                self._pending_resume = 0
        else:
            self._pending_resume = 0
        target_volume = self.volume_slider.value()
        self._fade_timer.stop()
        self.player.play_url(data["url"], volume=0)
        self._fade_volume(target_volume, 400)
        try:
            self.fav_btn.setText("❤" if self.library.is_favorite(data) else "♡")
            self.library.add_history(data)
        except Exception:  # noqa: BLE001
            pass
        self.bar_title.setText(data.get("name", ""))
        self.bar_artist.setText(data.get("ar_name", ""))
        self.detail_name.setText(data.get("name", ""))
        self.detail_artist.setText(data.get("ar_name", ""))
        self.detail_album.setText(data.get("al_name", ""))
        self.detail_meta.setText(
            f"{QUALITY_NAMES.get(data.get('level', ''), data.get('level', ''))} · "
            f"{data.get('size_formatted', '')} · {data.get('type', '').upper()}"
        )
        self.load_cover(data.get("pic", ""), self.detail_cover, 300)
        self.load_cover(data.get("pic", ""), self.bar_cover, 56)
        self.lyric_view.setPlainText(merge_lyrics(data.get("lyric", ""), data.get("tlyric", "")))
        self.statusBar().showMessage(f"正在播放: {data.get('name', '')}")
        self._fallback_tried = False
        if self.desktop_lyrics is not None and self.desktop_lyrics.isVisible():
            self.desktop_lyrics.refresh_track()
        if self.now_playing is not None and self.now_playing.isVisible():
            self.now_playing.refresh()

    def toggle_play(self):
        if self.current_track:
            self.player.toggle()
        else:
            self.play_selected_search()

    def play_previous(self):
        if not self.play_queue:
            return
        self.play_index = (self.play_index - 1) % len(self.play_queue)
        self.play_track(self.play_queue[self.play_index])

    def play_next(self):
        if not self.play_queue:
            return
        self.play_index = (self.play_index + 1) % len(self.play_queue)
        self.play_track(self.play_queue[self.play_index])

    def on_player_state(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer

        self.play_btn.setText(
            "⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶"
        )

    def on_player_position(self, position):
        if not self.seeking:
            self.progress_slider.setValue(int(position))
        self.time_label.setText(format_time(position))
        now = time.time()
        duration = self.progress_slider.maximum()
        if (
            self.current_track
            and duration > 0
            and now - self._last_progress_save >= 5
        ):
            self._last_progress_save = now
            try:
                self.library.save_progress(self.current_track, position, duration)
            except Exception:  # noqa: BLE001
                pass

    def on_player_duration(self, duration):
        self.progress_slider.setRange(0, max(0, int(duration)))
        self.duration_label.setText(format_time(duration))
        if self._pending_resume:
            if duration and self._pending_resume < duration - 2000:
                self.player.set_position(self._pending_resume)
                self.statusBar().showMessage(
                    f"从上次位置继续: {format_time(self._pending_resume)}", 4000
                )
            self._pending_resume = 0

    def on_player_finished(self):
        if self.current_track:
            try:
                self.library.clear_progress(self.current_track)
            except Exception:  # noqa: BLE001
                pass
            if self.current_track.get("source") == "file":
                self._refresh_local_tracks()
        mode = self.core.settings.get("play_mode", "list")
        if mode == "single" and self.current_track:
            self.player.set_position(0)
            self.player.player.play()
            return
        if mode == "shuffle" and len(self.play_queue) > 1:
            import random

            index = self.play_index
            while index == self.play_index:
                index = random.randrange(len(self.play_queue))
            self.play_index = index
            self.play_track(self.play_queue[index])
            return
        if self.play_queue:
            self.play_next()

    def on_player_error(self, message):
        track = self.current_track or {}
        if (
            track.get("source") == "jx"
            and self.core.settings.get("jx_fallback", True)
            and not self._fallback_tried
        ):
            self._fallback_tried = True
            fallback = self.core._find_fallback_track(track)
            if fallback:
                self.statusBar().showMessage(
                    f"播放失败，自动换源: {fallback.get('name', '')}", 5000
                )
                self.play_track(fallback)
                return
        self.statusBar().showMessage(f"播放器错误: {message}", 8000)

    def on_seek_start(self):
        self.seeking = True

    def on_seek_end(self):
        self.seeking = False
        self.player.set_position(self.progress_slider.value())

    def on_volume_changed(self, value):
        self.player.set_volume(value)
        self.core.settings["volume"] = value
        self.core.save_settings()

    def do_parse_collection(self):
        text = self.collection_input.text().strip()
        if not text:
            return
        kind = self.collection_type.currentData()
        self.statusBar().showMessage("解析中 ...")
        run_async(
            self.core.parse_collection,
            kind,
            text,
            self.current_source(),
            on_done=self.on_collection_done,
            on_error=self.on_error,
        )

    def on_collection_done(self, data):
        tracks = data.get("tracks", [])
        extra = data.get("extra", "")
        self.collection_tracks = tracks
        self.fill_track_table(self.collection_table, tracks)
        self.collection_info.setText(
            f"{data.get('name', '')} · {extra} · "
            f"共 {data.get('trackCount', len(tracks))} 首"
        )
        self.load_cover(data.get("coverImgUrl", ""), self.detail_cover, 300)
        self.statusBar().showMessage(f"解析完成，共 {len(tracks)} 首", 5000)

    def download_selected_search(self):
        track = self.selected_track(self.search_table)
        if track is None:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        self.start_download(track)

    def download_selected_collection(self):
        tracks = self.selected_tracks(self.collection_table)
        if not tracks:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        for track in tracks:
            self.start_download(track)

    def download_all_collection(self):
        if not self.collection_tracks:
            return
        for track in self.collection_tracks:
            self.start_download(track)

    def start_download(self, track):
        quality = self.current_quality()
        row = self.download_table.rowCount()
        self.download_table.insertRow(row)
        self.download_table.setItem(row, 0, QTableWidgetItem(track.get("name", "")))
        self.download_table.setItem(
            row, 1, QTableWidgetItem(QUALITY_NAMES.get(quality, quality))
        )
        status_item = QTableWidgetItem("排队中")
        self.download_table.setItem(row, 2, status_item)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        self.download_table.setCellWidget(row, 3, progress)
        action_btn = QPushButton("取消")
        self.download_table.setCellWidget(row, 4, action_btn)

        key = self.next_download_key
        self.next_download_key += 1
        self.download_rows[key] = {
            "row": row,
            "progress": progress,
            "status": status_item,
            "button": action_btn,
            "track": track,
            "quality": quality,
            "cancel": False,
        }
        action_btn.clicked.connect(
            lambda _checked=False, task_key=key: self._download_action(task_key)
        )
        self.nav.setCurrentRow(self.nav_downloads_index)
        self._run_download_task(key)

    def _download_action(self, key):
        task = self.download_rows.get(key)
        if not task:
            return
        if task["status"].text() in ("下载中", "排队中", "取消中..."):
            task["cancel"] = True
            task["status"].setText("取消中...")
            return
        task["cancel"] = False
        task["progress"].setValue(0)
        self._run_download_task(key)

    def _run_download_task(self, key):
        task = self.download_rows.get(key)
        if not task:
            return
        task["cancel"] = False
        task["status"].setText("下载中")
        task["button"].setText("取消")

        def on_progress(received, total):
            current = self.download_rows.get(key)
            if not current:
                return
            if total > 0:
                current["progress"].setValue(int(received * 100 / total))

        def on_done(result):
            current = self.download_rows.get(key)
            if not current:
                return
            current["progress"].setValue(100)
            current["status"].setText(
                "已存在" if result.get("skipped") else "已完成"
            )
            current["button"].setText("重试")
            self.statusBar().showMessage(
                f"下载完成: {result.get('filename', '')}", 8000
            )
            self._refresh_local_tracks()

        def on_error(message):
            current = self.download_rows.get(key)
            if not current:
                return
            if "取消" in str(message) or "aborted" in str(message).lower():
                current["status"].setText("已取消")
            else:
                current["status"].setText("失败")
            current["button"].setText("重试")
            self.statusBar().showMessage(f"下载失败: {message}", 8000)

        run_download(
            self.core.download_track,
            task["track"],
            task["quality"],
            self.core.settings.get("download_dir", ""),
            on_done=on_done,
            on_error=on_error,
            on_progress=on_progress,
            cancel=lambda: self.download_rows.get(key, {}).get("cancel", False),
        )

    def clear_finished_downloads(self):
        for row in range(self.download_table.rowCount() - 1, -1, -1):
            item = self.download_table.item(row, 2)
            if item is not None and item.text() in ("已完成", "已存在"):
                self.download_table.removeRow(row)
                updated = {}
                for key, task in self.download_rows.items():
                    if task["row"] == row:
                        continue
                    if task["row"] > row:
                        task["row"] -= 1
                    updated[key] = task
                self.download_rows = updated

    def show_selected_lyrics(self):
        track = self.selected_track(self.search_table)
        if track is None:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        self.lyric_view.setPlainText("歌词加载中 ...")
        run_async(
            self.core.playback_track,
            track,
            self.current_quality(),
            on_done=self.on_lyrics_ready,
            on_error=self.on_error,
        )

    def on_lyrics_ready(self, data):
        self.detail_name.setText(data.get("name", ""))
        self.detail_artist.setText(data.get("ar_name", ""))
        self.detail_album.setText(data.get("al_name", ""))
        self.detail_meta.setText(
            f"{QUALITY_NAMES.get(data.get('level', ''), data.get('level', ''))} · "
            f"{data.get('size_formatted', '')} · {data.get('type', '').upper()}"
        )
        self.load_cover(data.get("pic", ""), self.detail_cover, 300)
        self.lyric_view.setPlainText(
            merge_lyrics(data.get("lyric", ""), data.get("tlyric", ""))
        )

    def _scan_library(self):
        roots = [self.core.settings.get("download_dir", "")]
        extra = self.core.settings.get("library_dirs") or []
        if isinstance(extra, str):
            extra = [extra]
        roots = [item for item in [*roots, *extra] if item]
        if not roots:
            self.statusBar().showMessage("请先在设置页配置下载目录", 5000)
            return
        self.statusBar().showMessage("扫描本地音乐 ...")
        bridge = ProgressBridge()
        bridge.progress.connect(self._on_scan_progress)
        self._scan_bridge = bridge
        run_async(
            self.library.scan,
            roots,
            bridge.progress.emit,
            on_done=self._on_scan_done,
            on_error=self.on_error,
        )

    def _on_scan_progress(self, stats):
        self.statusBar().showMessage(
            f"扫描中: 已发现 {stats['seen']}，新增 {stats['added']}，"
            f"更新 {stats['updated']}"
        )

    def _on_scan_done(self, stats):
        self.statusBar().showMessage(
            f"扫描完成: {stats['seen']} 个文件，新增 {stats['added']}，"
            f"更新 {stats['updated']}，跳过 {stats['skipped']}",
            8000,
        )
        self._refresh_local_playlists()
        self._refresh_local_tracks()

    def _export_local_playlist(self):
        playlist_id = self.local_playlist_combo.currentData()
        if playlist_id is None:
            QMessageBox.information(
                self, "导出歌单", "请先在列表下拉框中选择一个本地歌单。"
            )
            return
        tracks = self.library.playlist_tracks(playlist_id)
        if not tracks:
            QMessageBox.information(self, "导出歌单", "该歌单还没有歌曲。")
            return
        name = self.local_playlist_combo.currentText().replace("/", "_")
        target, selected = QFileDialog.getSaveFileName(
            self,
            "导出歌单",
            str(Path.home() / f"{name}.m3u8"),
            "M3U8 播放列表 (*.m3u8);;文本清单 (*.txt)",
        )
        if not target:
            return
        lines = []
        if target.lower().endswith(".txt") or "txt" in selected:
            for index, track in enumerate(tracks, 1):
                lines.append(
                    f"{index}. {track.get('title', '')} - "
                    f"{track.get('artist', '')}"
                )
        else:
            lines.append("#EXTM3U")
            for track in tracks:
                duration = int(round((track.get("duration") or 0) / 1000))
                lines.append(
                    f"#EXTINF:{duration},{track.get('artist', '')} - "
                    f"{track.get('title', '')}"
                )
                lines.append(str(track.get("path", "")))
        try:
            Path(target).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self.statusBar().showMessage(f"已导出 {len(tracks)} 首：{target}", 8000)

    def _refresh_local_playlists(self):
        if not hasattr(self, "local_playlist_combo"):
            return
        current = self.local_playlist_combo.currentData()
        self.local_playlist_combo.blockSignals(True)
        self.local_playlist_combo.clear()
        self.local_playlist_combo.addItem("全部歌曲", None)
        for playlist in self.library.playlists():
            self.local_playlist_combo.addItem(playlist["name"], playlist["id"])
        index = self.local_playlist_combo.findData(current)
        self.local_playlist_combo.setCurrentIndex(index if index >= 0 else 0)
        self.local_playlist_combo.blockSignals(False)

    def _refresh_local_tracks(self):
        if not hasattr(self, "local_table"):
            return
        playlist_id = self.local_playlist_combo.currentData()
        if playlist_id:
            tracks = self.library.playlist_tracks(playlist_id)
        else:
            tracks = self.library.tracks(self.local_filter.text().strip())
        self.local_tracks = tracks
        self.fill_local_table(tracks)
        total = sum(int(track.get("size") or 0) for track in tracks)
        self.local_info_label.setText(f"{len(tracks)} 首 · {format_size(total)}")

    def fill_local_table(self, tracks):
        table = self.local_table
        table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            index_item = QTableWidgetItem(str(row + 1))
            index_item.setData(TRACK_ROLE, track)
            table.setItem(row, 0, index_item)
            name = track.get("name", "")
            if self.library.is_favorite(track):
                name += "  ♥"
            table.setItem(row, 1, QTableWidgetItem(name))
            table.setItem(row, 2, QTableWidgetItem(track.get("artists", "")))
            table.setItem(row, 3, QTableWidgetItem(track.get("album", "")))
            duration_text = track.get("duration_text") or format_time(
                track.get("duration", 0)
            )
            table.setItem(row, 4, QTableWidgetItem(duration_text))
            table.setItem(
                row, 5, QTableWidgetItem((track.get("format") or "").upper())
            )

    def _local_selected_tracks(self):
        tracks = []
        rows = sorted({index.row() for index in self.local_table.selectedIndexes()})
        for row in rows:
            item = self.local_table.item(row, 0)
            if item is not None:
                tracks.append(item.data(TRACK_ROLE))
        return tracks

    def play_selected_local(self):
        tracks = self._local_selected_tracks()
        if not tracks:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        self.play_from_list(self.local_tracks, max(0, self.local_table.currentRow()))

    def queue_selected_local(self):
        tracks = self._local_selected_tracks()
        if not tracks:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        self._append_to_queue(tracks)

    def favorite_selected_local(self):
        tracks = self._local_selected_tracks()
        if not tracks:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        favorite = not self.library.is_favorite(tracks[0])
        for track in tracks:
            if self.library.is_favorite(track) != favorite:
                self._toggle_favorite_track(track)
        self._refresh_local_tracks()
        self.statusBar().showMessage("已收藏" if favorite else "已取消收藏", 3000)

    def playlist_selected_local(self):
        tracks = self._local_selected_tracks()
        if not tracks:
            self.statusBar().showMessage("请先选择歌曲", 3000)
            return
        playlist_id = self._pick_playlist()
        if playlist_id:
            self._add_tracks_to_playlist(playlist_id, tracks)

    def remove_selected_from_playlist(self):
        playlist_id = self.local_playlist_combo.currentData()
        if not playlist_id:
            self.statusBar().showMessage("当前是全部歌曲视图", 3000)
            return
        for track in self._local_selected_tracks():
            item_id = track.get("_item_id")
            if item_id:
                self.library.remove_playlist_item(item_id)
        self._refresh_local_tracks()

    def _pick_playlist(self):
        playlists = self.library.playlists()
        names = [playlist["name"] for playlist in playlists]
        names.append("＋ 新建歌单…")
        name, ok = QInputDialog.getItem(self, "选择歌单", "歌单：", names, 0, False)
        if not ok or not name:
            return None
        if name == "＋ 新建歌单…":
            new_name, ok = QInputDialog.getText(self, "新建歌单", "歌单名称：")
            if not ok or not new_name.strip():
                return None
            return self.library.create_playlist(new_name.strip())
        return next(
            (playlist["id"] for playlist in playlists if playlist["name"] == name),
            None,
        )

    def _add_tracks_to_playlist(self, playlist_id, tracks):
        added = 0
        for track in tracks:
            if self.library.add_to_playlist(playlist_id, track):
                added += 1
        self._refresh_local_playlists()
        self.statusBar().showMessage(f"已加入歌单 {added} 首", 3000)

    def _create_local_playlist(self):
        name, ok = QInputDialog.getText(self, "新建歌单", "歌单名称：")
        if not ok or not name.strip():
            return
        self.library.create_playlist(name.strip())
        self._refresh_local_playlists()

    def _delete_local_playlist(self):
        playlist_id = self.local_playlist_combo.currentData()
        if not playlist_id:
            self.statusBar().showMessage("请先选择一个歌单", 3000)
            return
        name = self.local_playlist_combo.currentText()
        answer = QMessageBox.question(self, "删除歌单", f"确定删除歌单「{name}」？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_playlist(playlist_id)
        self._refresh_local_playlists()
        self._refresh_local_tracks()

    def _local_context_menu(self, pos):
        tracks = self._local_selected_tracks()
        if not tracks:
            return
        menu = QMenu(self)
        menu.addAction("播放", self.play_selected_local)
        menu.addAction("加入队列", self.queue_selected_local)
        favorite = self.library.is_favorite(tracks[0])
        menu.addAction("取消收藏" if favorite else "收藏", self.favorite_selected_local)
        submenu = menu.addMenu("加入歌单")
        playlists = self.library.playlists()
        if playlists:
            for playlist in playlists:
                action = submenu.addAction(playlist["name"])
                action.triggered.connect(
                    lambda _checked=False, pid=playlist["id"], items=tracks: self._add_tracks_to_playlist(pid, items)
                )
        else:
            placeholder = submenu.addAction("（暂无歌单，点击新建）")
            placeholder.triggered.connect(self.playlist_selected_local)
        if self.local_playlist_combo.currentData():
            menu.addAction("从歌单移除", self.remove_selected_from_playlist)
        menu.addSeparator()
        menu.addAction("在资源管理器中显示", lambda: self._show_in_explorer(tracks[0]))
        menu.addAction(
            "复制路径",
            lambda: QApplication.clipboard().setText(tracks[0].get("path", "")),
        )
        menu.addAction("刷新标签", lambda: self._refresh_tracks_metadata(tracks))
        menu.addAction("从本地库移除", lambda: self._remove_tracks_from_library(tracks))
        menu.exec(self.local_table.viewport().mapToGlobal(pos))

    def _show_in_explorer(self, track):
        path = track.get("path")
        if not path:
            return
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except OSError as exc:
            self.statusBar().showMessage(f"无法打开资源管理器: {exc}", 5000)

    def _refresh_tracks_metadata(self, tracks):
        for track in tracks:
            path = track.get("path")
            if path:
                self.library.add_file(path)
        self._refresh_local_tracks()
        self.statusBar().showMessage("标签已刷新", 3000)

    def _remove_tracks_from_library(self, tracks):
        answer = QMessageBox.question(
            self,
            "从本地库移除",
            f"从库中移除 {len(tracks)} 首（不会删除文件）？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for track in tracks:
            if track.get("path"):
                self.library.remove_track(track["path"])
        self._refresh_local_tracks()

    def _toggle_favorite_track(self, track):
        favorite = self.library.toggle_favorite(track)
        if (
            self.core.settings.get("sync_favorites")
            and track.get("source") == "local"
            and track.get("id")
            and hasattr(self.core, "like_song")
        ):
            run_async(
                self.core.like_song,
                track["id"],
                favorite,
                on_error=lambda message: self.statusBar().showMessage(
                    f"同步网易云失败: {message}", 5000
                ),
            )
        return favorite

    def toggle_current_favorite(self):
        if not self.current_track:
            self.statusBar().showMessage("没有正在播放的歌曲", 3000)
            return
        favorite = self._toggle_favorite_track(self.current_track)
        self.fav_btn.setText("❤" if favorite else "♡")
        if self.current_track.get("source") == "file":
            self._refresh_local_tracks()
        self.statusBar().showMessage("已收藏" if favorite else "已取消收藏", 3000)

    def _append_to_queue(self, tracks):
        tracks = [track for track in tracks if track]
        if not tracks:
            return
        self.play_queue.extend(tracks)
        self._persist_queue()
        self.statusBar().showMessage(f"已加入队列，共 {len(self.play_queue)} 首", 3000)

    def _persist_queue(self):
        try:
            self.library.save_queue(self.play_queue)
        except Exception:  # noqa: BLE001
            pass

    def _restore_queue(self):
        try:
            self.play_queue = self.library.load_queue()
        except Exception:  # noqa: BLE001
            self.play_queue = []
        self.play_index = -1

    def _show_queue(self):
        if getattr(self, "queue_dialog", None) is not None:
            self.queue_dialog.refresh()
            self.queue_dialog.show()
            self.queue_dialog.raise_()
            self.queue_dialog.activateWindow()
            return
        self.queue_dialog = QueueDialog(self)
        self.queue_dialog.finished.connect(
            lambda _result: setattr(self, "queue_dialog", None)
        )
        self.queue_dialog.show()

    def _cycle_play_mode(self):
        modes = [("list", "🔁"), ("single", "🔂"), ("shuffle", "🔀")]
        keys = [mode[0] for mode in modes]
        current = self.core.settings.get("play_mode", "list")
        index = (keys.index(current) + 1) % len(keys) if current in keys else 0
        self.core.settings["play_mode"] = keys[index]
        self.core.save_settings()
        self.mode_btn.setText(modes[index][1])
        self.mode_changed.emit(keys[index])
        labels = {"list": "列表循环", "single": "单曲循环", "shuffle": "随机播放"}
        self.statusBar().showMessage(labels[keys[index]], 3000)

    def _show_speed_menu(self):
        menu = QMenu(self)
        for rate in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            action = menu.addAction(f"{rate:g}x")
            action.setCheckable(True)
            action.setChecked(abs(self.player.playback_rate() - rate) < 1e-6)
            action.triggered.connect(
                lambda _checked=False, value=rate: self._set_speed(value)
            )
        menu.exec(self.speed_btn.mapToGlobal(self.speed_btn.rect().center()))

    def _set_speed(self, rate):
        self.player.set_playback_rate(rate)
        self.speed_btn.setText("1x" if rate == 1.0 else f"{rate:g}x")
        self.statusBar().showMessage(f"倍速 {rate:g}x", 3000)

    def _show_sleep_menu(self):
        menu = QMenu(self)
        for minutes in (15, 30, 60, 90):
            action = menu.addAction(f"{minutes} 分钟后暂停")
            action.triggered.connect(
                lambda _checked=False, value=minutes: self._start_sleep(value)
            )
        menu.addSeparator()
        menu.addAction("取消定时", self._cancel_sleep)
        menu.exec(self.sleep_btn.mapToGlobal(self.sleep_btn.rect().center()))

    def _start_sleep(self, minutes):
        self._sleep_timer.start(minutes * 60 * 1000)
        self.sleep_btn.setText(f"{minutes}m")
        self.statusBar().showMessage(f"睡眠定时：{minutes} 分钟后暂停", 5000)

    def _cancel_sleep(self):
        self._sleep_timer.stop()
        self.sleep_btn.setText("🌙")
        self.statusBar().showMessage("已取消睡眠定时", 3000)

    def _on_sleep_timeout(self):
        self.sleep_btn.setText("🌙")
        self._fade_volume(0, 1500)
        QTimer.singleShot(1600, self.player.pause)
        self.statusBar().showMessage("睡眠定时到，已暂停播放", 5000)

    def _fade_volume(self, target, duration_ms=300):
        target = max(0, min(100, int(target)))
        self._fade_start = self.player.volume()
        self._fade_target = target
        self._fade_steps = max(1, int(duration_ms / 30))
        self._fade_step = 0
        self._fade_timer.start(30)

    def _fade_tick(self):
        self._fade_step += 1
        ratio = self._fade_step / self._fade_steps
        value = self._fade_start + (self._fade_target - self._fade_start) * ratio
        self.player.set_volume(int(value))
        if self._fade_step >= self._fade_steps:
            self._fade_timer.stop()
            self.player.set_volume(self._fade_target)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#3d7bfd"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(4, 4, 56, 56)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(24, 16, 16, 32)
        painter.end()
        self.tray.setIcon(QIcon(pixmap))
        self.tray.setToolTip("Neko.night - Music")
        menu = QMenu(self)
        menu.addAction("显示 / 隐藏", self._toggle_visible)
        menu.addAction("播放 / 暂停", self.toggle_play)
        menu.addAction("上一首", self.play_previous)
        menu.addAction("下一首", self.play_next)
        menu.addSeparator()
        menu.addAction("桌面歌词", self.toggle_desktop_lyrics)
        menu.addAction("迷你模式", self.enter_mini_mode)
        menu.addSeparator()
        menu.addAction("退出", self._quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visible()

    def _toggle_visible(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _quit_app(self):
        self._really_quit = True
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        QApplication.quit()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._hotkeys_registered:
            self._register_media_keys()

    def _register_media_keys(self):
        try:
            import ctypes

            self._user32 = ctypes.windll.user32
            self._hwnd = int(self.winId())
            self._hotkey_ids = []
            for index, vk in enumerate((0xB3, 0xB0, 0xB1), start=1):
                if self._user32.RegisterHotKey(self._hwnd, index, 0x4000, vk):
                    self._hotkey_ids.append(index)
            self._hotkeys_registered = True
        except Exception:  # noqa: BLE001
            self._hotkeys_registered = False

    def _unregister_media_keys(self):
        try:
            for index in getattr(self, "_hotkey_ids", []):
                self._user32.UnregisterHotKey(self._hwnd, index)
        except Exception:  # noqa: BLE001
            pass
        self._hotkeys_registered = False

    def _handle_media_hotkey(self, hotkey_id):
        if hotkey_id == 1:
            self.toggle_play()
        elif hotkey_id == 2:
            self.play_next()
        elif hotkey_id == 3:
            self.play_previous()

    def nativeEvent(self, event_type, message):
        try:
            import ctypes
            from ctypes import wintypes

            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == 0x0312 and int(msg.wParam) in (1, 2, 3):
                self._handle_media_hotkey(int(msg.wParam))
                return True, 0
        except Exception:  # noqa: BLE001
            pass
        return False, 0

    def toggle_desktop_lyrics(self):
        enabled = not (
            self.desktop_lyrics is not None and self.desktop_lyrics.isVisible()
        )
        if enabled:
            if self.desktop_lyrics is None:
                from desktop_lyrics import DesktopLyrics

                self.desktop_lyrics = DesktopLyrics(self)
            self.desktop_lyrics.show_lyrics()
        elif self.desktop_lyrics is not None:
            self.desktop_lyrics.close_lyrics()
        self.core.settings["desktop_lyrics"] = enabled
        self.core.save_settings()
        if hasattr(self, "desktop_lyrics_check"):
            self.desktop_lyrics_check.blockSignals(True)
            self.desktop_lyrics_check.setChecked(enabled)
            self.desktop_lyrics_check.blockSignals(False)
        self.statusBar().showMessage(
            "桌面歌词已开启" if enabled else "桌面歌词已关闭", 3000
        )

    def enter_mini_mode(self):
        if self.mini_player is None:
            from mini_player import MiniPlayer

            self.mini_player = MiniPlayer(self)
        self.mini_player.show_mini()
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self.bar_cover and event.type() == QEvent.Type.MouseButtonRelease:
            self.show_now_playing()
            return True
        return super().eventFilter(obj, event)

    def show_now_playing(self):
        if self.now_playing is None:
            from now_playing import NowPlayingWindow

            self.now_playing = NowPlayingWindow(self)
        self.now_playing.slide_in()

    def closeEvent(self, event):
        self._unregister_media_keys()
        if self.now_playing is not None:
            self.now_playing.hide()
        if self.desktop_lyrics is not None:
            self.desktop_lyrics.hide()
        if self.mini_player is not None:
            self.mini_player.hide()
        self.core.save_settings()
        if (
            self.core.settings.get("minimize_to_tray")
            and getattr(self, "tray", None) is not None
            and self.tray.isVisible()
            and not getattr(self, "_really_quit", False)
        ):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "Neko.night - Music",
                "已最小化到托盘，双击图标恢复",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            return
        super().closeEvent(event)

    def load_cover(self, url, label, size, on_loaded=None):
        if not url:
            label.clear()
            return
        local = Path(str(url))
        if local.is_file():
            pixmap = QPixmap(str(local))
            if not pixmap.isNull():
                label.setPixmap(
                    pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )
                if on_loaded is not None:
                    on_loaded(pixmap)
            return
        cached = self.cover_cache.get(url)
        if cached is not None:
            label.setPixmap(
                cached.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            )
            if on_loaded is not None:
                on_loaded(cached)
            return

        def fetch():
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return response.content

        def done(data):
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return
            self.cover_cache[url] = pixmap
            label.setPixmap(
                pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            )
            if on_loaded is not None:
                on_loaded(pixmap)

        run_async(fetch, on_done=done, on_error=lambda _message: None)

    def on_error(self, message):
        self.statusBar().showMessage(f"操作失败: {message}", 8000)
        if "冷却" in str(message):
            QMessageBox.information(self, "请稍候", str(message))


def main():
    import sys

    from core_client import CoreClient

    app = QApplication(sys.argv)
    app.setApplicationName("Neko.night - Music")
    from fonts import apply_base_font
    from style import APP_QSS

    apply_base_font(app)
    app.setStyleSheet(APP_QSS)
    app.aboutToQuit.connect(shutdown)

    core = CoreClient()
    window = MainWindow(core)
    window.show()
    sys.exit(app.exec())
