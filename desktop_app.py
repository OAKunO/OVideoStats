"""Native desktop window around the existing localhost collector API."""

import os
import secrets
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import webview

from smi import studio
import studio_app
from studio_app import DIST, Handler


APP_NAME = 'OVideoStats'
LEGACY_APP_NAME = 'Catch Media'
BUNDLE_ROOT = Path(__file__).resolve().parent


def state_override():
    return os.environ.get('OVIDEOSTATS_STATE_DIR') or os.environ.get('CATCH_MEDIA_STATE_DIR')


def state_directory(legacy=False):
    override = state_override()
    if override:
        return Path(override).expanduser().resolve()
    name = LEGACY_APP_NAME if legacy else APP_NAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / name
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / name
    return Path.home() / '.local' / 'share' / ('catch_media' if legacy else 'ovideostats')


def prepare_state():
    """Copy existing local data on first launch; keep the original and never overwrite."""
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True)
    target = state / 'data'
    if not target.exists():
        source = None if state_override() else state_directory(legacy=True) / 'data'
        if source and source.is_dir():
            staging = state / 'data.migrating'
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source, staging, symlinks=True,
                            ignore=shutil.ignore_patterns('Singleton*', 'DevToolsActivePort'))
            staging.rename(target)
        else:
            (target / 'studio').mkdir(parents=True)
    os.chdir(state)
    return state


def desktop_backend():
    return 'edgechromium' if sys.platform == 'win32' else ('cocoa' if sys.platform == 'darwin' else None)


def system_theme():
    """Read the application appearance, independent of an explicit window theme."""
    if sys.platform != 'darwin':
        return None
    from AppKit import NSApplication, NSAppearanceNameAqua, NSAppearanceNameDarkAqua
    appearance = NSApplication.sharedApplication().effectiveAppearance()
    match = appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
    return 'dark' if match == NSAppearanceNameDarkAqua else 'light'


def theme_background(theme):
    resolved = system_theme() if theme == 'system' else theme
    return '#201f1d' if resolved == 'dark' else '#f8f5ef'


def configure_macos_window(window):
    """Use OAnyImort's hidden-title layout, retaining the native window controls."""
    from AppKit import NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden, NSColor
    native = window.native
    native.setStyleMask_(native.styleMask() | NSWindowStyleMaskFullSizeContentView)
    native.setTitleVisibility_(NSWindowTitleHidden)
    native.setTitlebarAppearsTransparent_(True)
    # Undo pywebview's explicit system-color paint. The web workspace now
    # extends under this transparent view, including the sidebar's own color.
    native.contentView().superview().subviews().lastObject().setBackgroundColor_(NSColor.clearColor())


def apply_window_theme(window, theme):
    """Sync native controls; the full-size workspace paints and animates the top."""
    if sys.platform != 'darwin' or window is None:
        return
    from AppKit import NSAppearance, NSAppearanceNameAqua, NSAppearanceNameDarkAqua, NSColor
    from Foundation import NSThread
    from PyObjCTools import AppHelper

    def apply():
        native = window.native
        if native is None:
            return
        dark = (system_theme() if theme == 'system' else theme) == 'dark'
        appearance = NSAppearanceNameDarkAqua if dark else NSAppearanceNameAqua
        color = '#201f1d' if dark else '#f8f5ef'
        red, green, blue = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        native.setAppearance_(None if theme == 'system' else NSAppearance.appearanceNamed_(appearance))
        background = NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1.0)
        native.setBackgroundColor_(background)

    # HTTP theme changes arrive on a worker; AppKit updates belong on the GUI thread.
    if NSThread.isMainThread():
        apply()
    else:
        AppHelper.callAfter(apply)


class DesktopApi:
    def __init__(self):
        # pywebview recursively inspects public attributes of js_api. A public
        # Window leaks the native WinForms object into that traversal and can
        # block the Windows message loop while reading its properties.
        self._window = None

    def choose_export_folder(self):
        previous = studio.get_export_folder()
        if not previous or not Path(previous).is_dir():
            previous = ''
        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=previous)
        return selected[0] if selected else ''

    def sync_theme(self, theme=None):
        apply_window_theme(self._window, theme if theme is not None else studio.get_theme())

    def system_theme(self):
        return system_theme()

    def _prepare_window(self):
        configure_macos_window(self._window)
        self.sync_theme()


def main():
    if not (DIST / 'index.html').is_file():
        raise RuntimeError('缺少已构建的界面文件。')
    prepare_state()
    Handler.token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api = DesktopApi()
    studio_app.NATIVE_FOLDER_CHOOSER = api.choose_export_folder
    studio_app.NATIVE_THEME_CHANGED = api.sync_theme
    url = 'http://127.0.0.1:{}/#token={}'.format(server.server_port, Handler.token)
    try:
        api._window = webview.create_window('OVideoStats · 自媒体采集工作台', url, js_api=api,
                                       width=1280, height=760, min_size=(1050, 650),
                                       background_color=theme_background(studio.get_theme()))
        if sys.platform == 'darwin':
            api._window.events.before_show += api._prepare_window
        options = {'gui': desktop_backend()}
        icon = BUNDLE_ROOT / 'app-icon.ico'
        if sys.platform == 'win32' and icon.is_file():
            options['icon'] = str(icon)
        webview.start(**options)
    finally:
        studio_app.NATIVE_FOLDER_CHOOSER = None
        studio_app.NATIVE_THEME_CHANGED = None
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('com.oakuno.ovideostats')
    try:
        main()
    except Exception:
        import traceback
        log_dir = state_directory()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / 'startup-error.log'
        log_file.write_text(traceback.format_exc(), encoding='utf-8')
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(None,
                'OVideoStats 启动失败。请确认已安装 Microsoft Edge WebView2 Runtime。\n'
                '错误详情：' + str(log_file), 'OVideoStats', 0x10)
        raise
