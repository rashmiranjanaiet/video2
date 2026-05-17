from __future__ import annotations

import runpy
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent / 'yt-dlp-master'
sys.path.insert(0, str(APP_ROOT))
runpy.run_path(str(APP_ROOT / 'local_downloader_server.py'), run_name='__main__')
