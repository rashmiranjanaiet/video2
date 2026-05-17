from __future__ import annotations

import json
import hmac
import mimetypes
import os
import posixpath
import shutil
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.version import __version__


ROOT = Path(__file__).resolve().parent
IS_RENDER = os.environ.get('RENDER') == 'true'
HOST = os.environ.get('HOST') or ('0.0.0.0' if IS_RENDER else '127.0.0.1')
PORT = int(os.environ.get('PORT') or '8000')
ACCESS_TOKEN = os.environ.get('DOWNLOAD_TOKEN', '').strip()
REQUIRE_TOKEN = IS_RENDER or os.environ.get('REQUIRE_DOWNLOAD_TOKEN', '').lower() in {'1', 'true', 'yes'} or bool(ACCESS_TOKEN)

DEFAULT_DOWNLOAD_DIR = Path('/tmp/yt-dlp-downloads') if IS_RENDER else ROOT / 'local-downloads'
DOWNLOAD_DIR_VALUE = os.environ.get('DOWNLOAD_DIR')
if DOWNLOAD_DIR_VALUE:
    configured_download_dir = Path(DOWNLOAD_DIR_VALUE).expanduser()
    DOWNLOAD_DIR = configured_download_dir if configured_download_dir.is_absolute() else ROOT / configured_download_dir
else:
    DOWNLOAD_DIR = DEFAULT_DOWNLOAD_DIR
DOWNLOAD_DIR = DOWNLOAD_DIR.resolve()

ALLOWED_HOSTS_VALUE = os.environ.get(
    'ALLOWED_HOSTS',
    'youtube.com,youtu.be,youtube-nocookie.com',
)
ALLOWED_HOSTS = {
    host.strip().lower().removeprefix('www.')
    for host in ALLOWED_HOSTS_VALUE.split(',')
    if host.strip()
}
ALLOW_ALL_HOSTS = '*' in ALLOWED_HOSTS
MAX_DOWNLOAD_MB = int(os.environ.get('MAX_DOWNLOAD_MB') or '512')
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024 if MAX_DOWNLOAD_MB > 0 else 0
MAX_ACTIVE_DOWNLOADS = max(1, int(os.environ.get('MAX_ACTIVE_DOWNLOADS') or '1'))

ACTIVE_STATUSES = {'queued', 'starting', 'downloading', 'processing'}

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def json_bytes(payload: dict, status: int = HTTPStatus.OK) -> tuple[bytes, int, str]:
    return json.dumps(payload, ensure_ascii=False).encode('utf-8'), status, 'application/json; charset=utf-8'


def safe_relative_path(raw_path: str) -> Path | None:
    path = unquote(raw_path.split('?', 1)[0].split('#', 1)[0])
    path = posixpath.normpath(path.lstrip('/'))
    if path in ('', '.'):
        path = 'index.html'

    target = (ROOT / Path(path)).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        return None
    return target


def safe_download_path(raw_path: str) -> Path | None:
    path = unquote(raw_path.split('?', 1)[0].split('#', 1)[0])
    path = posixpath.normpath(path.lstrip('/'))
    target = (DOWNLOAD_DIR / Path(path)).resolve()
    try:
        target.relative_to(DOWNLOAD_DIR)
    except ValueError:
        return None
    return target


def is_allowed_host(hostname: str) -> bool:
    normalized = hostname.lower().removeprefix('www.').rstrip('.')
    if ALLOW_ALL_HOSTS:
        return True
    return any(normalized == host or normalized.endswith(f'.{host}') for host in ALLOWED_HOSTS)


def active_download_count() -> int:
    with JOBS_LOCK:
        return sum(1 for job in JOBS.values() if job.get('status') in ACTIVE_STATUSES)


def list_downloads() -> list[dict]:
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    files = []
    for path in DOWNLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        files.append({
            'name': path.name,
            'size': path.stat().st_size,
            'url': f'/local-downloads/{quote(path.name, safe="")}',
            'modified': path.stat().st_mtime,
        })
    files.sort(key=lambda item: item['modified'], reverse=True)
    return files[:12]


def update_job(job_id: str, **changes) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(changes)


def create_job(url: str, fmt: str) -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            'id': job_id,
            'url': url,
            'format': fmt,
            'status': 'queued',
            'label': 'Queued',
            'message': 'Waiting to start...',
            'percent': 0,
            'file_url': '',
            'filename': '',
            'error': '',
            'created_at': time.time(),
        }
    return job_id


def find_newest_download(before: set[Path]) -> Path | None:
    candidates = [path for path in DOWNLOAD_DIR.iterdir() if path.is_file() and path not in before]
    if not candidates:
        candidates = [path for path in DOWNLOAD_DIR.iterdir() if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def content_disposition_for(path: Path) -> str:
    fallback = path.name.encode('ascii', 'ignore').decode('ascii')
    fallback = fallback.replace('\\', '_').replace('/', '_').replace('"', '').strip()
    fallback = fallback or 'download'
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(path.name, safe="")}'


def run_download(job_id: str) -> None:
    with JOBS_LOCK:
        job = dict(JOBS[job_id])

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    before = set(DOWNLOAD_DIR.iterdir())
    output_template = str(DOWNLOAD_DIR / '%(title).200B [%(id)s].%(ext)s')
    selected_format = 'best[ext=mp4]/best' if job['format'] == 'mp4' else 'best'

    def progress_hook(data: dict) -> None:
        status = data.get('status')
        filename = data.get('filename') or ''

        if status == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
            downloaded = data.get('downloaded_bytes') or 0
            percent = (downloaded / total * 100) if total else 0
            speed = data.get('_speed_str') or ''
            eta = data.get('_eta_str') or ''
            message_parts = [part.strip() for part in (speed, f'ETA {eta}' if eta else '') if part]
            update_job(
                job_id,
                status='downloading',
                label='Downloading',
                message=' | '.join(message_parts) or 'Downloading...',
                percent=percent,
                filename=filename,
            )
        elif status == 'finished':
            update_job(
                job_id,
                status='processing',
                label='Processing',
                message='Finishing file...',
                percent=100,
                filename=filename,
            )

    options = {
        'format': selected_format,
        'outtmpl': {'default': output_template},
        'noplaylist': True,
        'windowsfilenames': True,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
    }
    if MAX_DOWNLOAD_BYTES:
        options['max_filesize'] = MAX_DOWNLOAD_BYTES

    try:
        update_job(job_id, status='starting', label='Starting', message='Reading video information...')
        with YoutubeDL(options) as ydl:
            ydl.extract_info(job['url'], download=True)

        newest = find_newest_download(before)
        if not newest:
            raise RuntimeError('Download finished, but no output file was found.')

        update_job(
            job_id,
            status='finished',
            label='Complete',
            message=f'Saved as {newest.name}',
            percent=100,
            filename=newest.name,
            file_url=f'/local-downloads/{quote(newest.name, safe="")}',
        )
    except Exception as error:  # noqa: BLE001 - keep the local UI readable for any yt-dlp failure
        update_job(
            job_id,
            status='error',
            label='Error',
            message='Download failed.',
            error=str(error),
            traceback=traceback.format_exc(),
        )


class LocalDownloaderHandler(BaseHTTPRequestHandler):
    server_version = 'yt-dlp-local/1.0'

    def log_message(self, format, *args):  # noqa: A002
        return

    def send_payload(self, payload: bytes, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        self.send_payload(*json_bytes(payload, status))

    def request_token(self) -> str:
        authorization = self.headers.get('Authorization', '')
        if authorization.lower().startswith('bearer '):
            return authorization.split(' ', 1)[1].strip()

        header_token = self.headers.get('X-Download-Token', '').strip()
        if header_token:
            return header_token

        query = parse_qs(urlparse(self.path).query)
        return (query.get('token') or [''])[0].strip()

    def require_auth(self) -> bool:
        if not REQUIRE_TOKEN:
            return True

        if not ACCESS_TOKEN:
            self.send_json({
                'error': 'Server access token is not configured. Set DOWNLOAD_TOKEN on Render.',
            }, HTTPStatus.SERVICE_UNAVAILABLE)
            return False

        if hmac.compare_digest(self.request_token(), ACCESS_TOKEN):
            return True

        self.send_json({'error': 'Access token required.'}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == '/api/health':
            self.send_json({
                'ok': True,
                'version': __version__,
                'auth_required': REQUIRE_TOKEN,
                'auth_configured': bool(ACCESS_TOKEN),
                'allowed_hosts': sorted(ALLOWED_HOSTS),
                'max_download_mb': MAX_DOWNLOAD_MB,
                'downloads_dir': str(DOWNLOAD_DIR),
            })
            return

        if parsed.path == '/api/files':
            if not self.require_auth():
                return
            self.send_json({'files': list_downloads()})
            return

        if parsed.path.startswith('/api/jobs/'):
            if not self.require_auth():
                return
            job_id = parsed.path.rsplit('/', 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.send_json({'error': 'Job not found.'}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(job)
            return

        if parsed.path.startswith('/local-downloads/'):
            if not self.require_auth():
                return
            target = safe_download_path(parsed.path.removeprefix('/local-downloads/'))
            if target and target.is_file():
                self.serve_file(target)
            else:
                self.serve_download_listing()
            return

        target = safe_relative_path(parsed.path)
        if target and target.is_file():
            self.serve_file(target)
            return

        self.send_json({'error': 'Not found.'}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != '/api/download':
            self.send_json({'error': 'Not found.'}, HTTPStatus.NOT_FOUND)
            return

        if not self.require_auth():
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, json.JSONDecodeError):
            self.send_json({'error': 'Invalid request body.'}, HTTPStatus.BAD_REQUEST)
            return

        url = str(payload.get('url') or '').strip()
        fmt = str(payload.get('format') or 'mp4').strip()
        parsed_url = urlparse(url)

        if parsed_url.scheme not in {'http', 'https'} or not parsed_url.netloc:
            self.send_json({'error': 'Paste a valid http or https video URL.'}, HTTPStatus.BAD_REQUEST)
            return

        if not is_allowed_host(parsed_url.hostname or ''):
            allowed = 'any host' if ALLOW_ALL_HOSTS else ', '.join(sorted(ALLOWED_HOSTS))
            self.send_json({'error': f'This server only allows downloads from: {allowed}.'}, HTTPStatus.BAD_REQUEST)
            return

        if fmt not in {'mp4', 'best'}:
            fmt = 'mp4'

        if active_download_count() >= MAX_ACTIVE_DOWNLOADS:
            self.send_json({'error': 'A download is already running. Try again when it finishes.'}, HTTPStatus.TOO_MANY_REQUESTS)
            return

        job_id = create_job(url, fmt)
        thread = threading.Thread(target=run_download, args=(job_id,), daemon=True)
        thread.start()
        self.send_json({'job_id': job_id}, HTTPStatus.ACCEPTED)

    def serve_file(self, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(path.stat().st_size))
        if path.resolve().is_relative_to(DOWNLOAD_DIR):
            self.send_header('Content-Disposition', content_disposition_for(path))
        self.end_headers()
        with path.open('rb') as file:
            shutil.copyfileobj(file, self.wfile)

    def serve_download_listing(self) -> None:
        files = list_downloads()
        links = '\n'.join(
            f'<li><a href="{item["url"]}">{item["name"]}</a></li>'
            for item in files
        ) or '<li>No downloads yet.</li>'
        payload = f'<!doctype html><title>Downloads</title><ul>{links}</ul>'.encode('utf-8')
        self.send_payload(payload, HTTPStatus.OK, 'text/html; charset=utf-8')


def main() -> None:
    os.chdir(ROOT)
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), LocalDownloaderHandler)
    print(f'yt-dlp local downloader running at http://{HOST}:{PORT}/')
    server.serve_forever()


if __name__ == '__main__':
    main()
