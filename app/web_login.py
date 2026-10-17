# -*- coding: utf-8 -*-
"""Standalone real-browser login helper for the Netease app.

Usage:
    python web_login.py <result_json> [profile_dir]

Launches a real Chromium-family browser (Edge on Windows, Chrome/Chromium on
Linux) with a dedicated profile and CDP enabled, opens the Netease login page
and polls cookies through the DevTools protocol. When MUSIC_U appears it
writes {"music_u": "..."} and closes the browser.

Exit codes: 0 success, 2 closed without login, 3 browser not found.
"""
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
LINUX_BROWSERS = (
    "microsoft-edge",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
)

LOGIN_URL = (
    "https://music.163.com/login?targetUrl=https%3A%2F%2Fmusic.163.com%2F"
)
LOGIN_TIMEOUT = 900


def find_browser():
    if sys.platform == "win32":
        for path in EDGE_CANDIDATES:
            if Path(path).is_file():
                return path
        return ""
    for name in LINUX_BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    return ""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def first_page_ws(port):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/list",
        headers={"Host": f"127.0.0.1:{port}"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        targets = json.loads(response.read().decode("utf-8"))
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return target["webSocketDebuggerUrl"]
    return ""


def find_music_u(cookies):
    for cookie in cookies or []:
        if cookie.get("name") == "MUSIC_U" and cookie.get("value"):
            return cookie["value"]
    return ""


def main():
    result_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("web_login_result.json")
    )
    if len(sys.argv) > 2:
        profile = sys.argv[2]
    else:
        try:
            from paths import config_dir

            profile = str(config_dir() / "edge_profile")
        except ImportError:
            profile = str(
                Path(__file__).resolve().parent.parent / "config" / "edge_profile"
            )

    browser = find_browser()
    if not browser:
        result_path.write_text(json.dumps({"music_u": ""}), encoding="utf-8")
        return 3

    port = free_port()
    proc = subprocess.Popen(
        [
            browser,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            LOGIN_URL,
        ]
    )

    music_u = ""
    try:
        import websocket

        ws_url = ""
        deadline = time.time() + 45
        while time.time() < deadline and proc.poll() is None:
            try:
                ws_url = first_page_ws(port)
            except Exception:  # noqa: BLE001
                ws_url = ""
            if ws_url:
                break
            time.sleep(0.6)

        if ws_url:
            connection = websocket.create_connection(ws_url, timeout=8)
            message_id = [0]

            def call(method, params=None):
                message_id[0] += 1
                current = message_id[0]
                connection.send(
                    json.dumps({"id": current, "method": method, "params": params or {}})
                )
                while True:
                    data = json.loads(connection.recv())
                    if data.get("id") == current:
                        return data.get("result", {})

            call("Network.enable")
            deadline = time.time() + LOGIN_TIMEOUT
            while time.time() < deadline and proc.poll() is None:
                result = call(
                    "Network.getCookies",
                    {"urls": ["https://music.163.com/", "https://music.163.com"]},
                )
                music_u = find_music_u(result.get("cookies"))
                if music_u:
                    break
                time.sleep(1.5)
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        music_u = ""
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:  # noqa: BLE001
            pass

    result_path.write_text(
        json.dumps({"music_u": music_u}, ensure_ascii=False), encoding="utf-8"
    )
    return 0 if music_u else 2


if __name__ == "__main__":
    raise SystemExit(main())
