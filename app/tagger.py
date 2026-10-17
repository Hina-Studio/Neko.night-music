# -*- coding: utf-8 -*-
from pathlib import Path

import requests


def _combined_lyrics(lyric, tlyric):
    text = (lyric or "").strip()
    translation = (tlyric or "").strip()
    if text and translation:
        return f"{text}\n\n[翻译]\n{translation}"
    return text or translation


def _fetch_cover(url):
    if not url:
        return None
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None


def write_tags(path, info, lyric="", tlyric=""):
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return False

    target = Path(path)
    if not target.is_file():
        return False

    suffix = target.suffix.lower()
    cover = _fetch_cover(info.get("pic", ""))
    text = _combined_lyrics(lyric, tlyric)

    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, APIC, USLT

            try:
                audio = ID3(str(target))
            except ID3NoHeaderError:
                audio = ID3()
            if info.get("name"):
                audio.add(TIT2(encoding=3, text=info["name"]))
            if info.get("artists"):
                audio.add(TPE1(encoding=3, text=info["artists"]))
            if info.get("album"):
                audio.add(TALB(encoding=3, text=info["album"]))
            if text:
                audio.add(USLT(encoding=3, lang="eng", desc="Lyrics", text=text))
            if cover:
                audio.add(
                    APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover",
                         data=cover)
                )
            audio.save(str(target), v2_version=3)
            return True

        if suffix == ".flac":
            from mutagen.flac import FLAC, Picture

            audio = FLAC(str(target))
            if info.get("name"):
                audio["TITLE"] = info["name"]
            if info.get("artists"):
                audio["ARTIST"] = info["artists"]
            if info.get("album"):
                audio["ALBUM"] = info["album"]
            if text:
                audio["LYRICS"] = text
            if cover:
                picture = Picture()
                picture.type = 3
                picture.mime = "image/jpeg"
                picture.desc = "Cover"
                picture.data = cover
                audio.clear_pictures()
                audio.add_picture(picture)
            audio.save()
            return True

        if suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(str(target))
            if info.get("name"):
                audio["\xa9nam"] = [info["name"]]
            if info.get("artists"):
                audio["\xa9ART"] = [info["artists"]]
            if info.get("album"):
                audio["\xa9alb"] = [info["album"]]
            if text:
                audio["\xa9lyr"] = [text]
            if cover:
                audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
            return True

        audio = MutagenFile(str(target))
        if audio is None:
            return False
        if info.get("name"):
            audio["TITLE"] = info["name"]
        if info.get("artists"):
            audio["ARTIST"] = info["artists"]
        if info.get("album"):
            audio["ALBUM"] = info["album"]
        audio.save()
        return True
    except Exception:  # noqa: BLE001
        return False
