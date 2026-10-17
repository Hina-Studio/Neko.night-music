# -*- coding: utf-8 -*-
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import ncm_core

try:
    from edition import PUBLIC_EDITION
except ImportError:  # pragma: no cover
    PUBLIC_EDITION = False

from paths import config_dir

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = config_dir()
SETTINGS_FILE = CONFIG_DIR / "settings.json"
COOKIE_FILE = CONFIG_DIR / "cookie.txt"
COOKIE_ENC_FILE = CONFIG_DIR / "cookie.enc"

DEFAULT_SETTINGS = {
    "download_dir": str(Path.home() / "Music" / "NeteaseApp"),
    "quality": "lossless",
    "timeout": 30,
    "volume": 70,
    "write_tags": True,
    "source": "local",
    "jx_platform": "netease",
    "parse_cooldown": 60,
    "download_template": "{name} [{level}]",
    "skip_existing": True,
    "download_concurrency": 3,
    "play_mode": "list",
    "always_on_top": False,
    "minimize_to_tray": False,
    "resume_playback": True,
    "sync_favorites": False,
    "jx_fallback": True,
    "desktop_lyrics": False,
    "lyrics_font_size": 28,
    "lyrics_pos": [120, 120],
    "lyrics_locked": False,
    "mini_mode": False,
}

QUALITY_NAMES = {
    "standard": "标准音质",
    "exhigh": "极高音质",
    "lossless": "无损音质",
    "hires": "Hi-Res",
    "jyeffect": "高清环绕声",
    "sky": "沉浸环绕声",
    "jymaster": "超清母带",
    "dolby": "杜比全景声",
}

QUALITY_KEYS = list(QUALITY_NAMES.keys())

SOURCE_NAMES = {
    "local": "本地 Cookie",
    "online": "在线 API",
    "jx": "聚合搜索",
}

JX_PLATFORMS = {
    "netease": "网易云",
    "qq": "QQ 音乐",
    "kugou": "酷狗",
    "kuwo": "酷我",
    "xiami": "虾米",
    "baidu": "百度",
    "1ting": "一听",
    "migu": "咪咕",
    "lizhi": "荔枝",
    "qingting": "蜻蜓",
    "ximalaya": "喜马拉雅",
    "kg": "全民K歌",
    "5singyc": "5sing原创",
    "5singfc": "5sing翻唱",
}

SEARCH_PLATFORMS = (
    {}
    if PUBLIC_EDITION
    else {key: JX_PLATFORMS[key] for key in ("netease", "qq", "kugou")}
)

AUDIO_EXTENSIONS = {"mp3", "flac", "m4a", "mp4", "aac", "ogg", "opus", "wav"}

REFERER_RULES = (
    ("music.163.com", "https://music.163.com/"),
    ("music.126.net", "https://music.163.com/"),
    ("qqmusic.qq.com", "https://y.qq.com/"),
    ("kugou.com", "https://www.kugou.com/"),
    ("kuwo.cn", "https://www.kuwo.cn/"),
    ("migu.cn", "https://music.migu.cn/"),
    ("ximalaya.com", "https://www.ximalaya.com/"),
    ("lizhi.fm", "https://www.lizhi.fm/"),
    ("qingting.fm", "https://www.qingting.fm/"),
    ("5sing.kugou.com", "https://5sing.kugou.com/"),
    ("baidu.com", "https://music.baidu.com/"),
)


def referer_for(url):
    for marker, referer in REFERER_RULES:
        if marker in str(url):
            return referer
    return ""


class CoreError(RuntimeError):
    pass


def format_size(size):
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.2f}{unit}"
        value /= 1024.0
    return ""


class CoreClient:
    def __init__(self):
        self.client = ncm_core.Client()
        self.online = None if PUBLIC_EDITION else ncm_core.NextMusicClient()
        self.jx = None if PUBLIC_EDITION else ncm_core.MusicJxClient()
        self.settings = dict(DEFAULT_SETTINGS)
        self._last_online_parse = 0.0
        self._last_extended_call = 0.0
        self._account_uid = ""
        self._lock_password = ""
        self.library = None
        self.load_settings()
        self.apply_settings()
        self.cookie_text = self.load_cookie()

    def attach_library(self, library):
        self.library = library

    def load_settings(self):
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            self.settings.update(data)
        return self.settings

    def save_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def apply_settings(self):
        try:
            timeout = int(self.settings.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(5, timeout)
        self.client.set_timeout(timeout)
        if self.online is not None:
            self.online.set_timeout(timeout)
        if self.jx is not None:
            self.jx.set_timeout(max(60, timeout))

    def load_cookie(self):
        if COOKIE_ENC_FILE.is_file():
            self.client.set_cookie("")
            return ""
        try:
            text = COOKIE_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        self.client.set_cookie(text)
        return text

    def save_cookie(self, text):
        text = (text or "").strip()
        if text and not text.endswith(";"):
            text += ";"
        if COOKIE_ENC_FILE.is_file():
            if not self._lock_password:
                raise CoreError("Cookie 已锁定，请先解锁后再保存")
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            COOKIE_ENC_FILE.write_text(
                ncm_core.encrypt_secret(self._lock_password, text),
                encoding="utf-8",
            )
        else:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            COOKIE_FILE.write_text(text, encoding="utf-8")
        self.client.set_cookie(text)
        self.cookie_text = text
        return text

    def is_locked(self):
        return COOKIE_ENC_FILE.is_file()

    def is_unlocked(self):
        return bool(self._lock_password)

    def unlock(self, password):
        if not COOKIE_ENC_FILE.is_file():
            return False
        if not password:
            return False
        try:
            blob = COOKIE_ENC_FILE.read_text(encoding="utf-8").strip()
            text = ncm_core.decrypt_secret(password, blob)
        except Exception:  # noqa: BLE001
            return False
        self._lock_password = password
        self.client.set_cookie(text)
        self.cookie_text = text
        return True

    def enable_lock(self, password, cookie_text=None):
        password = (password or "").strip()
        if len(password) < 4:
            raise CoreError("密码至少需要 4 位")
        text = cookie_text if cookie_text is not None else self.cookie_text
        text = (text or "").strip()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        COOKIE_ENC_FILE.write_text(
            ncm_core.encrypt_secret(password, text), encoding="utf-8"
        )
        try:
            COOKIE_FILE.unlink()
        except OSError:
            pass
        self._lock_password = password
        self.client.set_cookie(text)
        self.cookie_text = text
        return True

    def disable_lock(self, password=""):
        if COOKIE_ENC_FILE.is_file() and not self._lock_password:
            if not password or not self.unlock(password):
                return False
        text = self.cookie_text or ""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(text, encoding="utf-8")
        try:
            COOKIE_ENC_FILE.unlink()
        except OSError:
            pass
        self._lock_password = ""
        return True

    def change_lock_password(self, old_password, new_password):
        if len((new_password or "").strip()) < 4:
            raise CoreError("密码至少需要 4 位")
        if not self.unlock(old_password):
            return False
        COOKIE_ENC_FILE.write_text(
            ncm_core.encrypt_secret(new_password.strip(), self.cookie_text),
            encoding="utf-8",
        )
        self._lock_password = new_password.strip()
        return True

    def has_cookie(self):
        return bool(self.client.cookie())

    def current_source(self):
        return self.settings.get("source", "local")

    # ---------------- local (direct Netease with cookie) ----------------

    def search(self, keywords, limit=30, offset=0):
        return self._parse(self.client.search(str(keywords), int(limit), int(offset)))

    def song_full(self, id_or_url, quality):
        return self._parse(self.client.song_full(str(id_or_url), str(quality)))

    def song_url(self, id_or_url, quality):
        return self._parse(
            self.client.song_url(self.resolve_id(id_or_url), str(quality))
        )

    def song_detail(self, id_or_url):
        return self._parse(self.client.song_detail(self.resolve_id(id_or_url)))

    def lyric(self, id_or_url):
        return self._parse(self.client.lyric(self.resolve_id(id_or_url)))

    def playlist(self, id_or_url):
        return self._parse(self.client.playlist(self.resolve_id(id_or_url)))

    def album(self, id_or_url):
        return self._parse(self.client.album(self.resolve_id(id_or_url)))

    def qr_unikey(self):
        return self._parse(self.client.qr_unikey())

    def qr_check(self, unikey):
        return self._parse(self.client.qr_check(str(unikey)))

    def login_password(self, username, password):
        result = self._parse(
            self.client.login_password(str(username), str(password))
        )
        if result.get("cookie"):
            self.save_cookie(result["cookie"])
        return result

    # ---------------- Netease extended (cookie required) ----------------

    def _throttle(self, gap=0.4):
        now = time.monotonic()
        wait = self._last_extended_call + gap - now
        if wait > 0:
            time.sleep(wait)
        self._last_extended_call = time.monotonic()

    def account_uid(self, refresh=False):
        if self._account_uid and not refresh:
            return self._account_uid
        data = self._parse(self.client.user_account())
        self._account_uid = str(data.get("user_id") or "")
        return self._account_uid

    def user_account(self):
        return self._parse(self.client.user_account())

    def recommend_songs(self):
        self._throttle()
        return self._parse(self.client.recommend_songs())

    def personal_fm(self):
        self._throttle()
        return self._parse(self.client.personal_fm())

    def toplists(self):
        self._throttle()
        return self._parse(self.client.toplists())

    def liked_song_ids(self):
        self._throttle()
        return set(self._parse(self.client.liked_song_ids()).get("ids") or [])

    def like_song(self, song_id, like):
        self._throttle()
        return self._parse(self.client.like_song(str(song_id), bool(like)))

    def user_playlists(self, uid=None, limit=50, offset=0):
        self._throttle()
        target = str(uid or self.account_uid())
        if not target:
            raise CoreError("无法获取账号 UID，请先登录")
        return self._parse(
            self.client.user_playlists(target, int(limit), int(offset))
        )

    def comments(self, song_id, limit=20, offset=0):
        self._throttle()
        return self._parse(
            self.client.comments(self.resolve_id(song_id), int(limit), int(offset))
        )

    def artist_detail(self, artist_id):
        self._throttle()
        return self._parse(self.client.artist_detail(str(artist_id)))

    def artist_songs(self, artist_id, limit=50, offset=0):
        self._throttle()
        return self._parse(
            self.client.artist_songs(str(artist_id), int(limit), int(offset))
        )

    def artist_albums(self, artist_id, limit=30, offset=0):
        self._throttle()
        return self._parse(
            self.client.artist_albums(str(artist_id), int(limit), int(offset))
        )

    def new_songs(self, area_id=0):
        self._throttle()
        return self._parse(self.client.new_songs(int(area_id)))

    # ---------------- unified search / collection / playback ----------------

    def search_tracks(self, keywords, limit=30, offset=0, source=None):
        source = source or self.current_source()
        if PUBLIC_EDITION and source in ("online", "jx"):
            raise CoreError("当前版本不包含在线 / 聚合音源")
        if source == "online":
            response = self._parse(self.online.search(str(keywords), int(limit), int(offset)))
            self._check_online(response, "在线 API 搜索失败")
            songs = (response.get("data") or {}).get("songs") or []
            return [self._online_track(song) for song in songs]
        if source == "jx":
            platform = self.settings.get("jx_platform", "netease")
            limit = max(1, int(limit))
            tracks = []
            seen = set()
            page = 1
            max_pages = min(15, max(3, (limit + 9) // 10 + 3))
            empty_pages = 0
            while len(tracks) < limit and page <= max_pages and empty_pages < 2:
                response = self._parse(
                    self.jx.search(str(keywords), "name", platform, page)
                )
                if response.get("code") != 200:
                    if page == 1:
                        message = response.get("error") or "单平台搜索失败"
                        raise CoreError(str(message))
                    break
                page_tracks = [
                    self._jx_track(item) for item in (response.get("data") or [])
                ]
                if not page_tracks:
                    empty_pages += 1
                    page += 1
                    continue
                empty_pages = 0
                for track in page_tracks:
                    key = (track.get("platform"), track.get("id"), track.get("name"))
                    if key in seen:
                        continue
                    seen.add(key)
                    tracks.append(track)
                    if len(tracks) >= limit:
                        break
                page += 1
            return tracks[:limit]
        songs = self.search(keywords, limit, offset).get("songs", [])
        for song in songs:
            song["source"] = "local"
        return songs

    def parse_collection(self, kind, text, source=None):
        source = source or self.current_source()
        if PUBLIC_EDITION and source in ("online", "jx"):
            raise CoreError("当前版本不包含在线 / 聚合音源")
        text = str(text).strip()
        if source == "local":
            data = self.playlist(text) if kind == "playlist" else self.album(text)
            tracks = data.get("tracks") or data.get("songs") or []
            for track in tracks:
                track["source"] = "local"
            return {
                "name": data.get("name", ""),
                "coverImgUrl": data.get("coverImgUrl", ""),
                "extra": data.get("creator") or data.get("artist") or "",
                "trackCount": data.get("trackCount", len(tracks)),
                "description": data.get("description", ""),
                "tracks": tracks,
            }

        resolved = self.resolve_id(text)
        if source == "online":
            self._enforce_online_cooldown()
            if kind == "playlist":
                response = self._parse(self.online.playlist_trackall(resolved))
                self._check_online(response, "在线 API 获取歌单失败")
                data = response.get("data") or {}
            else:
                response = self._parse(self.online.album(resolved))
                self._check_online(response, "在线 API 获取专辑失败")
                data = response.get("data") or {}
            songs = data.get("songs") or []
            artist = data.get("artist", "")
            if isinstance(artist, dict):
                artist = artist.get("name", "")
            creator = data.get("creator", "")
            if isinstance(creator, dict):
                creator = creator.get("nickname", "")
            return {
                "name": data.get("name", ""),
                "coverImgUrl": data.get("coverImage") or data.get("picUrl") or "",
                "extra": creator or artist,
                "trackCount": data.get("songCount", len(songs)),
                "description": data.get("description", ""),
                "tracks": [self._online_track(song) for song in songs],
            }

        response = self._parse(self.jx.search(text, "url", "_", 1))
        if response.get("code") != 200:
            raise CoreError(response.get("error") or "聚合解析失败")
        tracks = [self._jx_track(item) for item in (response.get("data") or [])]
        return {
            "name": "聚合解析",
            "coverImgUrl": tracks[0].get("picUrl", "") if tracks else "",
            "extra": JX_PLATFORMS.get(self.settings.get("jx_platform", ""), ""),
            "trackCount": len(tracks),
            "description": "",
            "tracks": tracks,
        }

    def playback_track(self, track, quality):
        source = track.get("source") or self.current_source()
        if PUBLIC_EDITION and source in ("online", "jx"):
            raise CoreError("当前版本不包含在线 / 聚合音源")
        if source == "file":
            path = Path(str(track.get("path") or ""))
            if not path.is_file():
                raise CoreError("本地文件不存在")
            lyric = ""
            sidecar = path.with_suffix(".lrc")
            if sidecar.is_file():
                try:
                    lyric = sidecar.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    lyric = ""
            size = track.get("size") or path.stat().st_size
            return {
                "id": track.get("id", ""),
                "name": track.get("name", path.stem),
                "ar_name": track.get("artists", ""),
                "al_name": track.get("album", ""),
                "pic": track.get("cover_path", ""),
                "level": "local",
                "url": path.as_uri(),
                "type": track.get("format") or path.suffix.lstrip("."),
                "br": 0,
                "size": size,
                "size_formatted": format_size(size),
                "lyric": lyric,
                "tlyric": "",
                "duration": track.get("duration", 0),
                "source": "file",
                "path": str(path),
            }
        if source == "local":
            data = self.song_full(track.get("id"), quality)
            data.setdefault("source", "local")
            return data

        if source == "online":
            self._enforce_online_cooldown()
            song_id = str(track.get("id"))
            info = {}
            try:
                info_response = self._parse(self.online.song_info(song_id))
                if info_response.get("code") == 200 and isinstance(
                    info_response.get("data"), dict
                ):
                    info = info_response["data"]
            except Exception:  # noqa: BLE001
                info = {}
            url_response = self._parse(self.online.song_url(song_id, quality))
            self._check_online(url_response, "在线 API 获取播放链接失败")
            url_data = url_response.get("data") or {}
            url = url_data.get("url", "")
            if not url:
                raise CoreError("在线 API 未返回播放链接（可能音质不支持或版权限制）")
            lyric = {}
            try:
                lyric_response = self._parse(self.online.song_lyric(song_id))
                if lyric_response.get("code") == 200 and lyric_response.get("data"):
                    lyric = lyric_response["data"]
            except Exception:  # noqa: BLE001
                lyric = {}
            size = url_data.get("size", 0)
            return {
                "id": song_id,
                "name": info.get("name") or track.get("name", ""),
                "ar_name": info.get("singer") or track.get("artists", ""),
                "al_name": info.get("album") or track.get("album", ""),
                "pic": info.get("picimg") or track.get("picUrl", ""),
                "level": url_data.get("level", quality),
                "url": url,
                "type": self._ext_from_url(url) or "mp3",
                "br": url_data.get("br", 0),
                "size": size,
                "size_formatted": format_size(size),
                "lyric": lyric.get("lrc", ""),
                "tlyric": lyric.get("tlyric", ""),
                "duration": 0,
                "source": "online",
            }

        url = track.get("url", "")
        if not url:
            raise CoreError("聚合结果没有可用的播放链接")
        return {
            "id": str(track.get("id", "")),
            "name": track.get("name", ""),
            "ar_name": track.get("artists", ""),
            "al_name": track.get("album", ""),
            "pic": track.get("picUrl", ""),
            "level": "jx",
            "url": url,
            "type": self._ext_from_url(url) or "mp3",
            "br": 0,
            "size": 0,
            "size_formatted": "",
            "lyric": track.get("lrc", ""),
            "tlyric": "",
            "duration": 0,
            "source": "jx",
        }

    def download_track(self, track, quality, directory, progress=None):
        source = track.get("source") or self.current_source()
        if source == "file":
            raise CoreError("本地文件不需要下载")
        try:
            return self._download_track_once(track, quality, directory, progress)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if (
                source == "jx"
                and self.settings.get("jx_fallback", True)
                and "取消" not in message
            ):
                fallback = self._find_fallback_track(track)
                if fallback:
                    return self._download_track_once(
                        fallback, quality, directory, progress
                    )
            raise

    def _find_fallback_track(self, track):
        query = f"{track.get('name', '')} {track.get('artists', '')}".strip()
        if not query:
            return None
        for source in ("local", "online"):
            try:
                results = self.search_tracks(query, 5, 0, source)
            except Exception:  # noqa: BLE001
                continue
            for item in results:
                if item.get("id"):
                    return item
        return None

    def _download_track_once(self, track, quality, directory, progress=None):
        data = self.playback_track(track, quality)
        url = data.get("url") or ""
        if not url:
            raise CoreError("没有可用的下载链接（版权限制或音质不支持）")
        name = data.get("name") or track.get("name") or "music"
        artists = data.get("ar_name") or track.get("artists") or ""
        album = data.get("al_name") or track.get("album") or ""
        level = data.get("level") or quality
        extension = data.get("type") or self._ext_from_url(url) or "mp3"
        target = self._download_target(
            directory, name, artists, album, level, extension
        )

        if (
            self.settings.get("skip_existing", True)
            and target.is_file()
            and target.stat().st_size > 0
        ):
            size = target.stat().st_size
            result = {
                "path": str(target),
                "filename": target.name,
                "name": name,
                "artists": artists,
                "album": album,
                "pic": data.get("pic", ""),
                "type": extension,
                "level": level,
                "size": size,
                "size_formatted": format_size(size),
                "duration": data.get("duration", 0),
                "skipped": True,
            }
            self._register_download(result)
            return result

        target.parent.mkdir(parents=True, exist_ok=True)
        info = self._download_with_retry(url, target, referer_for(url), progress)
        size = int(info.get("size") or data.get("size") or target.stat().st_size)
        result = {
            "path": str(target),
            "filename": target.name,
            "name": name,
            "artists": artists,
            "album": album,
            "pic": data.get("pic", ""),
            "type": extension,
            "level": level,
            "size": size,
            "size_formatted": format_size(size),
            "duration": data.get("duration", 0),
            "skipped": False,
        }
        if self.settings.get("write_tags", True):
            try:
                from tagger import write_tags

                result["tags_written"] = write_tags(
                    str(target), result, data.get("lyric", ""), data.get("tlyric", "")
                )
            except Exception:  # noqa: BLE001
                result["tags_written"] = False
        self._register_download(result)
        return result

    def download_song(self, id_or_url, quality, directory, progress=None):
        return self.download_track(
            {"id": id_or_url, "source": "local"}, quality, directory, progress
        )

    def _download_with_retry(self, url, target, referer, progress):
        part = Path(str(target) + ".part")
        last_error = ""
        for attempt in range(3):
            try:
                info = self._parse(
                    self.client.download_url(
                        url, str(part.parent), part.name, referer, progress, True
                    )
                )
                os.replace(info.get("path", str(part)), str(target))
                info["path"] = str(target)
                info["filename"] = target.name
                return info
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if "aborted" in last_error.lower():
                    raise CoreError("下载已取消")
                time.sleep(1.5 * (attempt + 1))
        raise CoreError(last_error or "下载失败")

    def _register_download(self, result):
        if self.library is None or not result.get("path"):
            return
        try:
            self.library.add_file(result["path"])
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _sanitize_segment(text):
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text or "")).strip(" .")
        return text or "未命名"

    def _download_target(self, directory, name, artists, album, level, extension):
        template = str(self.settings.get("download_template") or "{name} [{level}]")
        parts = {
            "name": self._sanitize_segment(name or "music"),
            "artist": self._sanitize_segment(artists or "未知歌手"),
            "artists": self._sanitize_segment(artists or "未知歌手"),
            "album": self._sanitize_segment(album or "未知专辑"),
            "level": level,
        }
        try:
            relative = template.format(**parts)
        except (KeyError, ValueError):
            relative = f"{parts['name']} [{level}]"
        relative = relative.replace("\\", "/").strip("/ ")
        if not relative:
            relative = f"{parts['name']} [{level}]"
        relative_path = Path(relative)
        filename = (relative_path.name or parts["name"]) + "." + extension
        return Path(directory) / relative_path.parent / filename

    def resolve_id(self, text):
        return self.client.resolve_id(str(text))

    # ---------------- helpers ----------------

    @staticmethod
    def _online_track(song):
        return {
            "id": song.get("id"),
            "name": song.get("name", ""),
            "artists": song.get("singer", ""),
            "album": song.get("album", ""),
            "picUrl": song.get("picimg", ""),
            "duration": song.get("duration", ""),
            "copyright": song.get("copyright", 1),
            "source": "online",
        }

    @staticmethod
    def _jx_track(item):
        return {
            "id": item.get("songid"),
            "name": item.get("title", ""),
            "artists": item.get("author", ""),
            "album": JX_PLATFORMS.get(item.get("type", ""), item.get("type", "")),
            "platform": item.get("type", ""),
            "picUrl": item.get("pic", ""),
            "url": item.get("url", ""),
            "lrc": item.get("lrc", ""),
            "link": item.get("link", ""),
            "source": "jx",
        }

    @staticmethod
    def _ext_from_url(url):
        path = urllib.parse.urlparse(str(url)).path
        suffix = Path(path).suffix.lower().lstrip(".")
        return suffix if suffix in AUDIO_EXTENSIONS else ""

    @staticmethod
    def _check_online(response, fallback):
        code = response.get("code")
        if code == 429:
            raise CoreError("当前 IP 请求频率过快，请稍后再试")
        if code != 200:
            raise CoreError(response.get("message") or fallback)

    def _enforce_online_cooldown(self):
        try:
            cooldown = int(self.settings.get("parse_cooldown", 60))
        except (TypeError, ValueError):
            cooldown = 60
        if cooldown <= 0:
            return
        now = time.monotonic()
        if self._last_online_parse and now - self._last_online_parse < cooldown:
            wait = int(cooldown - (now - self._last_online_parse)) + 1
            raise CoreError(f"在线解析冷却中，请 {wait} 秒后再试")
        self._last_online_parse = now

    @staticmethod
    def _parse(text):
        return json.loads(text)
