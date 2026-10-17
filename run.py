# -*- coding: utf-8 -*-
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

if len(sys.argv) > 1 and sys.argv[1] == "--web-login":
    del sys.argv[1]
    from web_login import main as web_login_main

    raise SystemExit(web_login_main())

from main_window import main

if __name__ == "__main__":
    main()
