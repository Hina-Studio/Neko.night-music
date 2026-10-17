# -*- coding: utf-8 -*-
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer


class Player(QObject):
    state_changed = pyqtSignal(object)
    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.playbackStateChanged.connect(self.state_changed.emit)
        self._player.positionChanged.connect(self.position_changed.emit)
        self._player.durationChanged.connect(self.duration_changed.emit)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)

    @property
    def player(self):
        return self._player

    def play_url(self, url, volume=None):
        self._player.setSource(QUrl(url))
        if volume is not None:
            self.set_volume(volume)
        self._player.play()

    def play_file(self, path, volume=None):
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if volume is not None:
            self.set_volume(volume)
        self._player.play()

    def set_playback_rate(self, rate):
        self._player.setPlaybackRate(max(0.25, min(4.0, float(rate))))

    def playback_rate(self):
        return self._player.playbackRate()

    def is_playing(self):
        return (
            self._player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )

    def toggle(self):
        if self.is_playing():
            self._player.pause()
        else:
            self._player.play()

    def pause(self):
        self._player.pause()

    def stop(self):
        self._player.stop()

    def set_position(self, position_ms):
        self._player.setPosition(max(0, int(position_ms)))

    def position(self):
        return self._player.position()

    def duration(self):
        return self._player.duration()

    def set_volume(self, value):
        self._audio.setVolume(max(0, min(100, int(value))) / 100.0)

    def volume(self):
        return int(round(self._audio.volume() * 100))

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.finished.emit()

    def _on_error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.error.emit(message or str(error))
