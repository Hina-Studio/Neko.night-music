# -*- coding: utf-8 -*-
"""SQLite-backed local music library, playlists, queue, history and favorites.

Used by the PyQt6 UI; all heavy work (scan) runs in worker threads, so every
public method takes an internal lock around the connection.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from paths import config_dir

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = config_dir()
DB_FILE = CONFIG_DIR / "library.db"
COVERS_DIR = CONFIG_DIR / "covers"

MEDIA_EXTENSIONS = {"mp3", "flac", "m4a", "mp4", "ogg", "opus", "wav"}

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS tracks (
    path TEXT PRIMARY KEY,
    title TEXT,
    artist TEXT,
    album TEXT,
    duration INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    format TEXT,
    cover TEXT,
    added_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS playlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL,
    track_key TEXT NOT NULL,
    track_json TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at REAL,
    FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS queue (
    position INTEGER PRIMARY KEY,
    track_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_key TEXT,
    track_json TEXT,
    played_at REAL
);
CREATE TABLE IF NOT EXISTS progress (
    track_key TEXT PRIMARY KEY,
    position INTEGER,
    duration INTEGER,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS favorites (
    track_key TEXT PRIMARY KEY,
    track_json TEXT,
    added_at REAL
);
"""


def track_key(track):
    if not track:
        return ""
    path = track.get("path")
    if path:
        digest = hashlib.sha1(str(path).encode("utf-8", "ignore")).hexdigest()
        return "file:" + digest[:24]
    track_id = track.get("id")
    if track_id:
        return f"netease:{track_id}"
    payload = json.dumps(track, sort_keys=True, ensure_ascii=False)
    return "hash:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def format_duration(ms):
    try:
        seconds = int(ms) // 1000
    except (TypeError, ValueError):
        return "00:00"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _first(tags, key, default=""):
    if not tags:
        return default
    try:
        value = tags.get(key)
    except Exception:  # noqa: BLE001
        return default
    if not value:
        return default
    if isinstance(value, (list, tuple)):
        return str(value[0])
    return str(value)


class Library:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ---------------- local tracks ----------------

    def scan(self, roots, on_progress=None, should_cancel=None):
        stats = {"seen": 0, "added": 0, "updated": 0, "skipped": 0, "errors": 0}
        roots = [Path(root) for root in roots if root]
        for root in roots:
            if not root.exists():
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                if should_cancel and should_cancel():
                    return stats
                for filename in filenames:
                    suffix = Path(filename).suffix.lower().lstrip(".")
                    if suffix not in MEDIA_EXTENSIONS:
                        continue
                    path = str(Path(dirpath) / filename)
                    stats["seen"] += 1
                    try:
                        stat = os.stat(path)
                        existing = self.track_by_path(path)
                        if (
                            existing
                            and existing.get("size") == stat.st_size
                            and abs(existing.get("mtime", 0) - stat.st_mtime) < 0.5
                        ):
                            stats["skipped"] += 1
                        else:
                            info = self._read_file(path, stat)
                            self._upsert_track(info)
                            if existing:
                                stats["updated"] += 1
                            else:
                                stats["added"] += 1
                    except Exception:  # noqa: BLE001
                        stats["errors"] += 1
                    if on_progress and stats["seen"] % 20 == 0:
                        on_progress(dict(stats))
        if on_progress:
            on_progress(dict(stats))
        return stats

    def _read_file(self, path, stat):
        suffix = Path(path).suffix.lower().lstrip(".")
        title = Path(path).stem
        artist = ""
        album = ""
        duration = 0
        key = track_key({"path": path})
        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(path, easy=True)
            if audio is not None:
                tags = audio.tags
                title = _first(tags, "title", title)
                artist = _first(tags, "artist", "")
                album = _first(tags, "album", "")
                if getattr(audio, "info", None) is not None:
                    duration = int((audio.info.length or 0) * 1000)
        except Exception:  # noqa: BLE001
            pass
        cover = self._extract_cover(path, suffix, key)
        return {
            "path": path,
            "title": title,
            "artist": artist,
            "album": album,
            "duration": duration,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "format": suffix,
            "cover": cover,
            "added_at": time.time(),
        }

    def _extract_cover(self, path, suffix, key):
        for extension in (".jpg", ".png"):
            existing = COVERS_DIR / f"{key}{extension}"
            if existing.is_file():
                return str(existing)
        data = None
        mime = ""
        try:
            if suffix == "mp3":
                from mutagen.id3 import ID3

                tags = ID3(path)
                for frame in tags.getall("APIC"):
                    data = frame.data
                    mime = frame.mime or ""
                    break
            elif suffix == "flac":
                from mutagen.flac import FLAC

                audio = FLAC(path)
                if audio.pictures:
                    data = audio.pictures[0].data
                    mime = audio.pictures[0].mime or ""
            elif suffix in ("m4a", "mp4"):
                from mutagen.mp4 import MP4

                audio = MP4(path)
                covers = audio.tags.get("covr") if audio.tags else None
                if covers:
                    data = bytes(covers[0])
                    mime = "image/png" if covers[0].imageformat == 14 else "image/jpeg"
        except Exception:  # noqa: BLE001
            data = None
        if not data:
            return ""
        extension = ".png" if "png" in (mime or "").lower() else ".jpg"
        target = COVERS_DIR / f"{key}{extension}"
        try:
            target.write_bytes(data)
        except OSError:
            return ""
        return str(target)

    def _upsert_track(self, info):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tracks(path, title, artist, album, duration, size,
                                   mtime, format, cover, added_at)
                VALUES(:path, :title, :artist, :album, :duration, :size,
                       :mtime, :format, :cover, :added_at)
                ON CONFLICT(path) DO UPDATE SET
                    title=excluded.title, artist=excluded.artist,
                    album=excluded.album, duration=excluded.duration,
                    size=excluded.size, mtime=excluded.mtime,
                    format=excluded.format, cover=excluded.cover
                """,
                info,
            )
            self._conn.commit()

    def track_by_path(self, path):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tracks WHERE path=?", (str(path),)
            ).fetchone()
        return self._row_to_track(row) if row else None

    def add_file(self, path):
        path = str(path)
        if not Path(path).is_file():
            return None
        stat = os.stat(path)
        info = self._read_file(path, stat)
        self._upsert_track(info)
        return self.track_by_path(path)

    def tracks(self, filter_text="", limit=5000):
        sql = "SELECT * FROM tracks"
        params = []
        if filter_text:
            sql += " WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?"
            like = f"%{filter_text}%"
            params = [like, like, like]
        sql += " ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, title COLLATE NOCASE"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_track(row) for row in rows]

    def track_count(self):
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()
        return int(row["count"]) if row else 0

    def remove_track(self, path):
        with self._lock:
            self._conn.execute("DELETE FROM tracks WHERE path=?", (str(path),))
            self._conn.commit()

    @staticmethod
    def _row_to_track(row):
        prefix = Path(row["path"]).suffix.lower().lstrip(".")
        title = row["title"] or Path(row["path"]).stem
        return {
            "path": row["path"],
            "name": title,
            "artists": row["artist"] or "",
            "album": row["album"] or "",
            "duration": int(row["duration"] or 0),
            "duration_text": format_duration(row["duration"] or 0),
            "size": int(row["size"] or 0),
            "format": prefix,
            "cover_path": row["cover"] or "",
            "mtime": row["mtime"] or 0,
            "source": "file",
        }

    # ---------------- playlists ----------------

    def playlists(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name FROM playlists ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [{"id": row["id"], "name": row["name"]} for row in rows]

    def create_playlist(self, name):
        name = (name or "").strip()
        if not name:
            return None
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO playlists(name, created_at) VALUES(?, ?)",
                    (name, time.time()),
                )
                self._conn.commit()
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    "SELECT id FROM playlists WHERE name=?", (name,)
                ).fetchone()
                return row["id"] if row else None

    def rename_playlist(self, playlist_id, name):
        name = (name or "").strip()
        if not name:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE playlists SET name=? WHERE id=?", (name, int(playlist_id))
            )
            self._conn.commit()

    def delete_playlist(self, playlist_id):
        with self._lock:
            self._conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id=?", (int(playlist_id),)
            )
            self._conn.execute("DELETE FROM playlists WHERE id=?", (int(playlist_id),))
            self._conn.commit()

    def playlist_tracks(self, playlist_id):
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, track_json FROM playlist_items "
                "WHERE playlist_id=? ORDER BY position",
                (int(playlist_id),),
            ).fetchall()
        tracks = []
        for row in rows:
            try:
                track = json.loads(row["track_json"])
            except ValueError:
                continue
            track["_item_id"] = row["id"]
            tracks.append(track)
        return tracks

    def add_to_playlist(self, playlist_id, track):
        key = track_key(track)
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM playlist_items WHERE playlist_id=? AND track_key=?",
                (int(playlist_id), key),
            ).fetchone()
            if exists:
                return False
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next "
                "FROM playlist_items WHERE playlist_id=?",
                (int(playlist_id),),
            ).fetchone()
            self._conn.execute(
                "INSERT INTO playlist_items(playlist_id, track_key, track_json, "
                "position, added_at) VALUES(?, ?, ?, ?, ?)",
                (
                    int(playlist_id),
                    key,
                    json.dumps(track, ensure_ascii=False),
                    row["next"],
                    time.time(),
                ),
            )
            self._conn.commit()
        return True

    def remove_playlist_item(self, item_id):
        with self._lock:
            self._conn.execute(
                "DELETE FROM playlist_items WHERE id=?", (int(item_id),)
            )
            self._conn.commit()

    # ---------------- queue ----------------

    def save_queue(self, tracks):
        with self._lock:
            self._conn.execute("DELETE FROM queue")
            for position, track in enumerate(tracks or []):
                self._conn.execute(
                    "INSERT INTO queue(position, track_json) VALUES(?, ?)",
                    (position, json.dumps(track, ensure_ascii=False)),
                )
            self._conn.commit()

    def load_queue(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_json FROM queue ORDER BY position"
            ).fetchall()
        tracks = []
        for row in rows:
            try:
                tracks.append(json.loads(row["track_json"]))
            except ValueError:
                continue
        return tracks

    # ---------------- history / progress / favorites ----------------

    def add_history(self, track):
        with self._lock:
            self._conn.execute(
                "INSERT INTO history(track_key, track_json, played_at) VALUES(?, ?, ?)",
                (track_key(track), json.dumps(track, ensure_ascii=False), time.time()),
            )
            self._conn.execute(
                "DELETE FROM history WHERE id NOT IN "
                "(SELECT id FROM history ORDER BY id DESC LIMIT 1000)"
            )
            self._conn.commit()

    def history(self, limit=200):
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_json, played_at FROM history ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        items = []
        for row in rows:
            try:
                track = json.loads(row["track_json"])
            except ValueError:
                continue
            track["_played_at"] = row["played_at"]
            items.append(track)
        return items

    def save_progress(self, track, position, duration):
        with self._lock:
            self._conn.execute(
                "INSERT INTO progress(track_key, position, duration, updated_at) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(track_key) DO UPDATE SET "
                "position=excluded.position, duration=excluded.duration, "
                "updated_at=excluded.updated_at",
                (track_key(track), int(position), int(duration), time.time()),
            )
            self._conn.commit()

    def load_progress(self, track):
        with self._lock:
            row = self._conn.execute(
                "SELECT position, duration FROM progress WHERE track_key=?",
                (track_key(track),),
            ).fetchone()
        if not row:
            return 0
        position = int(row["position"] or 0)
        duration = int(row["duration"] or 0)
        if duration and position > duration - 5000:
            return 0
        return position

    def clear_progress(self, track):
        with self._lock:
            self._conn.execute(
                "DELETE FROM progress WHERE track_key=?", (track_key(track),)
            )
            self._conn.commit()

    def is_favorite(self, track):
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM favorites WHERE track_key=?", (track_key(track),)
            ).fetchone()
        return bool(row)

    def toggle_favorite(self, track):
        key = track_key(track)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM favorites WHERE track_key=?", (key,)
            ).fetchone()
            if row:
                self._conn.execute("DELETE FROM favorites WHERE track_key=?", (key,))
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO favorites(track_key, track_json, added_at) VALUES(?, ?, ?)",
                (key, json.dumps(track, ensure_ascii=False), time.time()),
            )
            self._conn.commit()
        return True

    def favorites(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_json FROM favorites ORDER BY added_at DESC"
            ).fetchall()
        items = []
        for row in rows:
            try:
                items.append(json.loads(row["track_json"]))
            except ValueError:
                continue
        return items
