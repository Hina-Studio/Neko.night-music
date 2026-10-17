# -*- coding: utf-8 -*-
"""Packaged entry point.

Lives inside ``app/`` so Nuitka's static analysis finds the sibling modules.
For source runs ``run.py`` remains the entry.
"""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--web-login":
        del sys.argv[1]
        from web_login import main as web_login_main

        return web_login_main()
    from main_window import main as app_main

    app_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
