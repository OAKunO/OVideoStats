"""Cross-platform localhost UI for the existing Python collectors.

Run from the source checkout: .venv/bin/python studio_app.py
"""
import argparse
import json
import mimetypes
import secrets
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from smi import studio
from smi.browser_selection import browser_status

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
JOBS = {}
JOB_LOCK = threading.Lock()
NATIVE_FOLDER_CHOOSER = None
NATIVE_THEME_CHANGED = None


def choose_export_folder():
    """Use the host OS folder chooser; cancellation leaves the current path intact."""
    if NATIVE_FOLDER_CHOOSER is not None:
        return NATIVE_FOLDER_CHOOSER()
    if getattr(sys, 'frozen', False) and sys.platform != 'darwin':
        raise ValueError('文件夹选择器尚未就绪，请稍后重试。')
    previous = studio.get_export_folder()
    if previous and not Path(previous).is_dir():
        previous = ''
    if sys.platform == 'darwin':
        # Tk's macOS dialog is presented as a Python app. Standard Additions
        # opens the native Finder-style folder panel instead.
        if previous:
            script = ('on run argv\n'
                      'return POSIX path of (choose folder with prompt "选择 Excel 导出文件夹" '
                      'default location (POSIX file (item 1 of argv)))\nend run')
            command = ['/usr/bin/osascript', '-e', script, previous]
        else:
            command = ['/usr/bin/osascript', '-e',
                       'POSIX path of (choose folder with prompt "选择 Excel 导出文件夹")']
    else:
        script = ("import tkinter as tk; from tkinter import filedialog; "
                  "r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True); "
                  "print(filedialog.askdirectory(parent=r, title='选择 Excel 导出文件夹', "
                  "initialdir=__import__('sys').argv[1])); r.destroy()")
        command = [sys.executable, '-c', script, previous]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('选择文件夹超时，请重试。') from exc
    if result.returncode:
        if sys.platform == 'darwin' and '-128' in result.stderr:
            return ''  # Native panel's Cancel button.
        raise ValueError(result.stderr.strip()[-300:] or '无法打开文件夹选择器')
    folder = result.stdout.strip()
    if folder and not Path(folder).is_dir():
        raise ValueError('所选文件夹不存在，请重新选择。')
    return folder


def start_job(kind, fn):
    job_id = uuid.uuid4().hex
    job = {'id': job_id, 'kind': kind, 'state': 'running', 'message': '正在准备…'}
    if kind == 'export':
        job.update(completed=0, total=0)
    with JOB_LOCK:
        if any(value['state'] == 'running' for value in JOBS.values()):
            raise ValueError('已有任务正在运行，请等它完成。')
        JOBS[job_id] = job
    def run():
        def update(message, completed=None, total=None):
            with JOB_LOCK:
                job['message'] = message
                if completed is not None:
                    job['completed'] = completed
                if total is not None:
                    job['total'] = total
        try:
            result = fn(update)
            with JOB_LOCK:
                job.update(state='done', message='已完成', result=result)
        except Exception as exc:
            with JOB_LOCK:
                job.update(state='error', message=str(exc))
    threading.Thread(target=run, daemon=True).start()
    return job_id


class Handler(BaseHTTPRequestHandler):
    token = None
    def log_message(self, format, *args):
        pass

    def _json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        # The token is delivered in the localhost URL and required for every API call.
        return self.headers.get('X-Studio-Token') == self.token

    def _body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 65536:
            raise ValueError('请求过大')
        return json.loads(self.rfile.read(size) or b'{}')

    def do_GET(self):
        route = urlparse(self.path).path
        if route.startswith('/api/avatars/'):
            token = parse_qs(urlparse(self.path).query).get('token', [''])[0]
            if not (self._authorized() or (self.token and secrets.compare_digest(token, self.token))):
                return self._json(403, {'error': '未授权'})
            path = studio.avatar_path(route.rsplit('/', 1)[-1])
            if not path:
                return self._json(404, {'error': '头像不存在'})
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Cache-Control', 'private, max-age=3600')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route.startswith('/api/'):
            if not self._authorized():
                return self._json(403, {'error': '未授权'})
            if route == '/api/state':
                folder = studio.get_export_folder()
                return self._json(200, {'accounts': studio.list_accounts(), 'pending_sessions': studio.pending_sessions(), 'platforms': studio.PLATFORMS,
                                        'fields': {p: studio.field_options(p) for p in studio.PLATFORMS},
                                        'export_folder': folder, 'export_folder_available': bool(folder and Path(folder).is_dir()),
                                        'account_sort': studio.get_account_sort(), 'theme': studio.get_theme(),
                                        'login_browser': browser_status()})
            if route.startswith('/api/jobs/'):
                with JOB_LOCK:
                    job = JOBS.get(route.rsplit('/', 1)[-1])
                    result = dict(job) if job else None
                return self._json(200 if result else 404, result or {'error': '任务不存在'})
            return self._json(404, {'error': '接口不存在'})
        if route == '/':
            route = '/index.html'
        path = (DIST / route.lstrip('/')).resolve()
        if not path.is_relative_to(DIST.resolve()) or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        if route == '/index.html':
            theme = studio.get_theme()
            data = data.replace(b'data-appearance="light"', f'data-appearance="{theme}"'.encode())
            resolved = 'dark' if theme == 'dark' else 'light'
            data = data.replace(b'data-theme="light"', f'data-theme="{resolved}"'.encode())
            data = data.replace(b'content="#f8f5ef"', b'content="#201f1d"' if theme == 'dark' else b'content="#f8f5ef"')
            if NATIVE_THEME_CHANGED is not None:
                desktop = {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform)
                if desktop:
                    data = data.replace(b'<html ', f'<html data-desktop="{desktop}" '.encode())
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
        if route == '/index.html':
            self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._authorized():
            return self._json(403, {'error': '未授权'})
        route = urlparse(self.path).path
        try:
            data = self._body()
            if route == '/api/accounts':
                platform = data.get('platform')
                return self._json(202, {'job': start_job('login', lambda update: studio.add_account(platform, update))})
            if route in ('/api/check-logins', '/api/adopt'):
                return self._json(202, {'job': start_job('check-logins', studio.check_login_states)})
            if route == '/api/export':
                folder = data.get('folder') or studio.get_export_folder()
                account_ids = data.get('account_ids')
                if account_ids is None:
                    raise ValueError('请至少勾选一个账号')
                studio.export_accounts(account_ids)
                return self._json(202, {'job': start_job('export', lambda update: studio.export_all(folder, update, account_ids))})
            if route == '/api/export-folder':
                return self._json(200, {'folder': studio.set_export_folder(data.get('folder'))})
            if route == '/api/account-sort':
                return self._json(200, {'account_sort': studio.set_account_sort(data.get('key'), data.get('direction'))})
            if route == '/api/theme':
                theme = studio.set_theme(data.get('theme'))
                if NATIVE_THEME_CHANGED is not None:
                    NATIVE_THEME_CHANGED(theme)
                return self._json(200, {'theme': theme})
            if route.startswith('/api/accounts/') and route.endswith('/reconnect'):
                account_id = route.split('/')[3]
                return self._json(202, {'job': start_job('reconnect', lambda update: studio.reconnect_account(account_id, update))})
            if route == '/api/folder':
                # Desktop folder picker. The path remains local; browser never uploads files.
                return self._json(200, {'folder': choose_export_folder()})
            if route.startswith('/api/accounts/') and route.endswith('/settings'):
                account_id = route.split('/')[3]
                row = studio.update_account(account_id, data.get('fields'), data.get('include_private'))
                return self._json(200, {'account': row})
            return self._json(404, {'error': '接口不存在'})
        except (ValueError, OSError, ImportError) as exc:
            return self._json(400, {'error': str(exc)})

    def do_DELETE(self):
        if not self._authorized():
            return self._json(403, {'error': '未授权'})
        route = urlparse(self.path).path
        try:
            if route.startswith('/api/accounts/') and len(route.split('/')) == 4:
                account_id = route.split('/')[3]
                return self._json(200, {'account': studio.delete_account(account_id)})
            return self._json(404, {'error': '接口不存在'})
        except (ValueError, OSError) as exc:
            return self._json(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description='自媒体采集工作台')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    import os
    os.chdir(ROOT)
    if not (DIST / 'index.html').exists():
        parser.error('请先在 ui 目录运行 npm install && npm run build')
    token = secrets.token_urlsafe(32)
    Handler.token = token
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{server.server_port}/#token={token}'
    print('自媒体采集工作台：' + url, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
