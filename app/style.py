# -*- coding: utf-8 -*-
FONT_STACK = (
    '"Inter", "HarmonyOS Sans SC", "MiSans", "Noto Sans SC", '
    '"Microsoft YaHei UI", "Segoe UI", sans-serif'
)
MONO_STACK = '"JetBrains Mono", "Consolas", "Cascadia Mono", monospace'

APP_QSS = """
* {
    font-family: %(font)s;
    font-size: 13px;
    color: #f4f7fb;
}
#Root {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #04060c, stop:0.45 #0c1424, stop:1 #150b28);
}
QFrame#Panel, QFrame#Card {
    background: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 14px;
}
QFrame#Overlay {
    background: #0e1424;
    border-top: 1px solid rgba(255, 255, 255, 0.20);
    border-top-left-radius: 16px;
    border-top-right-radius: 16px;
}
QLabel#Title {
    font-size: 18px;
    font-weight: 500;
    color: #ffffff;
}
QLabel#SubTitle {
    color: rgba(244, 247, 251, 0.72);
    font-size: 12px;
}
QLabel#Section {
    font-size: 14px;
    font-weight: 500;
    color: #ffffff;
}
QListWidget#Nav {
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 14px;
    padding: 8px;
    outline: none;
}
QListWidget#Nav::item {
    height: 38px;
    border-radius: 9px;
    padding-left: 12px;
    color: rgba(244, 247, 251, 0.88);
}
QListWidget#Nav::item:hover {
    background: rgba(255, 255, 255, 0.14);
}
QListWidget#Nav::item:selected {
    background: rgba(79, 140, 255, 0.45);
    color: #ffffff;
    font-weight: 500;
}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background: rgba(10, 14, 24, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.24);
    border-radius: 9px;
    padding: 7px 10px;
    selection-background-color: #3d7bfd;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(111, 157, 255, 0.95);
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background: #131b2e;
    border: 1px solid rgba(255, 255, 255, 0.24);
    border-radius: 8px;
    selection-background-color: rgba(79, 140, 255, 0.55);
    outline: none;
}
QPushButton {
    background: rgba(255, 255, 255, 0.12);
    border: 1px solid rgba(255, 255, 255, 0.24);
    border-radius: 9px;
    padding: 7px 16px;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 0.22);
    border: 1px solid rgba(255, 255, 255, 0.34);
}
QPushButton:pressed {
    background: rgba(255, 255, 255, 0.30);
}
QPushButton:disabled {
    color: rgba(244, 247, 251, 0.45);
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.10);
}
QPushButton#Accent {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2f6df6, stop:1 #6a5cff);
    border: none;
    font-weight: 500;
}
QPushButton#Accent:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4a86ff, stop:1 #8377ff);
}
QPushButton#TabButton {
    background: rgba(255, 255, 255, 0.10);
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 12px;
    padding: 6px 15px;
}
QPushButton#TabButton:hover {
    background: rgba(255, 255, 255, 0.20);
}
QPushButton#TabButton:checked {
    background: rgba(79, 140, 255, 0.50);
    border: 1px solid rgba(111, 157, 255, 0.85);
    font-weight: 500;
}
QPushButton#Round {
    border-radius: 19px;
    min-width: 38px;
    max-width: 38px;
    min-height: 38px;
    max-height: 38px;
    padding: 0;
    font-size: 15px;
}
QPushButton#Play {
    border-radius: 23px;
    min-width: 46px;
    max-width: 46px;
    min-height: 46px;
    max-height: 46px;
    padding: 0;
    font-size: 18px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2f6df6, stop:1 #6a5cff);
    border: none;
}
QTableWidget {
    background: transparent;
    border: none;
    gridline-color: rgba(255, 255, 255, 0.10);
    outline: none;
}
QTableWidget::item {
    padding: 6px;
    border: none;
    color: #f4f7fb;
}
QTableWidget::item:selected {
    background: rgba(79, 140, 255, 0.48);
    border-radius: 6px;
    color: #ffffff;
}
QHeaderView::section {
    background: rgba(255, 255, 255, 0.10);
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.22);
    padding: 8px;
    color: rgba(244, 247, 251, 0.88);
    font-weight: 500;
}
QTableCornerButton::section {
    background: rgba(255, 255, 255, 0.10);
    border: none;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.30);
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.48);
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 0.30);
    border-radius: 5px;
    min-width: 30px;
}
QSlider::groove:horizontal {
    height: 5px;
    background: rgba(255, 255, 255, 0.28);
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4a86ff, stop:1 #8377ff);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 6px;
}
QProgressBar {
    background: rgba(255, 255, 255, 0.18);
    border: none;
    border-radius: 6px;
    height: 10px;
    text-align: center;
    font-size: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2f6df6, stop:1 #6a5cff);
    border-radius: 6px;
}
QTabWidget::pane {
    border: none;
}
QTabBar::tab {
    background: transparent;
    padding: 8px 16px;
    border-radius: 8px;
    color: rgba(244, 247, 251, 0.75);
}
QTabBar::tab:selected {
    background: rgba(79, 140, 255, 0.40);
    color: #ffffff;
}
QCheckBox {
    spacing: 8px;
}
QLabel#Lyric {
    color: rgba(244, 247, 251, 0.92);
    line-height: 170%%;
}
QToolTip {
    background: #101728;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 6px;
    padding: 6px;
}
QMenu {
    background: #101728;
    border: 1px solid rgba(255, 255, 255, 0.24);
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 22px;
    border-radius: 6px;
}
QMenu::item:selected {
    background: rgba(79, 140, 255, 0.55);
}
""" % {"font": FONT_STACK}
