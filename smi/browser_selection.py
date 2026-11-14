"""Choose a supported system-default browser and keep each login with its browser."""
from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import plistlib
import shutil
import sys
import tempfile


BINDING_FILE = '.ovideostats-browser.json'
LEGACY_BINDING_FILE = '.catchmedia-browser.json'
BROWSER_NAMES = {'chrome': 'Google Chrome', 'msedge': 'Microsoft Edge', 'chromium': 'Chromium'}
MAC_BUNDLES = {
    'com.google.chrome': ('chrome', 'Google Chrome.app'),
    'com.microsoft.edgemac': ('msedge', 'Microsoft Edge.app'),
    'org.chromium.chromium': ('chromium', 'Chromium.app'),
}


class BrowserSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class BrowserApp:
    id: str
    executable: Path
    app_path: object = None

    @property
    def name(self):
        return BROWSER_NAMES[self.id]


@dataclass(frozen=True)
class BrowserDiscovery:
    default_id: str
    default_name: str
    installed: tuple


@dataclass(frozen=True)
class BrowserSelection:
    browser: BrowserApp
    message: str
    is_default: bool = False


def _mac_preference_bundle():
    path = Path.home() / 'Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist'
    try:
        handlers = plistlib.loads(path.read_bytes()).get('LSHandlers', [])
        for scheme in ('https', 'http'):
            matches = [row for row in handlers if row.get('LSHandlerURLScheme') == scheme and row.get('LSHandlerRoleAll')]
            if matches:
                return str(max(matches, key=lambda row: row.get('LSHandlerModificationDate', 0))['LSHandlerRoleAll']).lower()
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        pass
    return ''


def _mac_app(path):
    try:
        path = Path(path)
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
        bundle = str(info.get('CFBundleIdentifier', '')).lower()
        if bundle in MAC_BUNDLES:
            executable = path / 'Contents/MacOS' / info['CFBundleExecutable']
            if executable.is_file():
                return BrowserApp(MAC_BUNDLES[bundle][0], executable, path)
    except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException):
        pass
    return None


def _discover_macos():
    default_path, bundle, workspace = None, '', None
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL
        workspace = NSWorkspace.sharedWorkspace()
        url = workspace.URLForApplicationToOpenURL_(NSURL.URLWithString_('https://example.com'))
        if url:
            default_path = Path(str(url.path()))
            bundle = str(plistlib.loads((default_path / 'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier', '')).lower()
    except Exception:
        pass
    bundle = bundle or _mac_preference_bundle()
    installed = {}
    for bundle_id, (browser_id, app_name) in MAC_BUNDLES.items():
        paths = []
        if workspace is not None:
            try:
                url = workspace.URLForApplicationWithBundleIdentifier_(bundle_id)
                if url:
                    paths.append(Path(str(url.path())))
            except Exception:
                pass
        paths.extend([Path('/Applications') / app_name, Path.home() / 'Applications' / app_name])
        for path in paths:
            app = _mac_app(path)
            if app and app.id == browser_id:
                installed[browser_id] = app
                break
    default_app = _mac_app(default_path) if default_path else None
    if default_app:
        installed[default_app.id] = default_app
    default_id = MAC_BUNDLES.get(bundle, ('', ''))[0]
    default_name = BROWSER_NAMES.get(default_id) or {
        'com.apple.safari': 'Safari', 'org.mozilla.firefox': 'Firefox',
    }.get(bundle, default_path.stem if default_path else ('其他浏览器' if bundle else ''))
    return BrowserDiscovery(default_id, default_name, tuple(installed.values()))


def _windows_association(kind):
    """Ask Windows' protocol association API rather than guessing from an old registry key."""
    import ctypes
    from ctypes import wintypes
    function = ctypes.WinDLL('shlwapi').AssocQueryStringW
    function.argtypes = [wintypes.DWORD, ctypes.c_int, wintypes.LPCWSTR,
                         wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    function.restype = ctypes.c_long
    ole32 = ctypes.WinDLL('ole32')
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    initialized = ole32.CoInitializeEx(None, 0) in (0, 1)
    try:
        count = wintypes.DWORD(0)
        function(0x1000, kind, 'https', 'open', None, ctypes.byref(count))  # ASSOCF_IS_PROTOCOL
        if not 0 < count.value < 32768:
            return ''
        buffer = ctypes.create_unicode_buffer(count.value)
        if function(0x1000, kind, 'https', 'open', buffer, ctypes.byref(count)) != 0:
            return ''
        return buffer.value
    finally:
        if initialized:
            ole32.CoUninitialize()


def _windows_app(path):
    filename = PureWindowsPath(str(path)).name.lower()
    browser_id = {'chrome.exe': 'chrome', 'msedge.exe': 'msedge', 'chromium.exe': 'chromium'}.get(filename)
    candidate = Path(path)
    return BrowserApp(browser_id, candidate) if browser_id and candidate.is_file() else None


def _discover_windows():
    executable, default_name = '', ''
    try:
        executable = _windows_association(2)  # ASSOCSTR_EXECUTABLE
        default_name = _windows_association(4)  # ASSOCSTR_FRIENDLYAPPNAME
    except Exception:
        pass
    installed = {}
    # Edge is normally available on Windows; neither brand is hard-coded as the default.
    for browser_id, relative in [('msedge', 'Microsoft/Edge/Application/msedge.exe'),
                                  ('chrome', 'Google/Chrome/Application/chrome.exe'),
                                  ('chromium', 'Chromium/Application/chromium.exe')]:
        for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            root = os.environ.get(key)
            if root:
                app = _windows_app(Path(root) / relative)
                if app:
                    installed[browser_id] = app
                    break
    default_app = _windows_app(executable) if executable else None
    if default_app:
        installed[default_app.id] = default_app
    return BrowserDiscovery(default_app.id if default_app else '',
                            default_app.name if default_app else (default_name or (PureWindowsPath(executable).stem if executable else '')),
                            tuple(installed.values()))


def discover_browsers():
    if sys.platform == 'darwin':
        return _discover_macos()
    if sys.platform == 'win32':
        return _discover_windows()
    installed = []
    for browser_id, commands in [('chrome', ('google-chrome', 'google-chrome-stable')),
                                  ('msedge', ('microsoft-edge', 'microsoft-edge-stable')),
                                  ('chromium', ('chromium', 'chromium-browser'))]:
        executable = next((shutil.which(name) for name in commands if shutil.which(name)), None)
        if executable:
            installed.append(BrowserApp(browser_id, Path(executable)))
    return BrowserDiscovery('', '', tuple(installed))


def profile_browser_id(profile):
    if profile is None:
        return None
    profile = Path(profile)
    marker = profile / BINDING_FILE
    if not marker.is_file():
        marker = profile / LEGACY_BINDING_FILE
    if marker.is_file():
        try:
            browser_id = json.loads(marker.read_text(encoding='utf-8'))['browser_id']
            if browser_id in BROWSER_NAMES:
                return browser_id
        except (OSError, ValueError, KeyError, TypeError):
            pass
        raise BrowserSelectionError('这个账号的登录浏览器记录无法读取，请重新连接原账号。')
    # Every pre-migration profile in this application was created with Chrome.
    if any((profile / name).exists() for name in ('Local State', 'Default', 'Last Version')):
        return 'chrome'
    return None


def profile_browser_name(profile):
    try:
        return BROWSER_NAMES.get(profile_browser_id(profile), '')
    except BrowserSelectionError:
        return '无法识别，请重新连接'


def select_browser(profile=None, channel=None, discovery=None):
    discovery = discovery or discover_browsers()
    installed = {app.id: app for app in discovery.installed}
    bound = profile_browser_id(profile)
    if channel and channel not in BROWSER_NAMES:
        raise BrowserSelectionError('不支持这个浏览器选项，请使用 Chrome、Edge 或 Chromium。')
    if bound and channel and bound != channel:
        raise BrowserSelectionError('这个登录会话属于 {}，不能直接交给另一种浏览器；请重新连接账号。'.format(BROWSER_NAMES[bound]))
    requested = bound or channel
    if requested:
        if requested not in installed:
            raise BrowserSelectionError('该账号使用 {}，但本机未找到它。请恢复安装，或点击“重新连接”用当前默认浏览器登录原账号。'.format(BROWSER_NAMES[requested]))
        app = installed[requested]
        return BrowserSelection(app, '该账号继续使用 {}。'.format(app.name), app.id == discovery.default_id)
    if discovery.default_id in installed:
        app = installed[discovery.default_id]
        return BrowserSelection(app, '新连接使用系统默认浏览器 {}。'.format(app.name), True)
    if installed:
        app = next(iter(installed.values()))
        reason = '默认浏览器 {} 暂不支持账号采集'.format(discovery.default_name) if discovery.default_name else '未能识别可用的系统默认浏览器'
        return BrowserSelection(app, '{}，将使用 {}。'.format(reason, app.name))
    raise BrowserSelectionError('未找到可用于账号登录与采集的浏览器，请安装 Google Chrome 或 Microsoft Edge。Safari 和 Firefox 暂不支持此采集流程。')


def bind_profile(profile, browser):
    """Bind before launch, including failed launches which may create browser files."""
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=profile, prefix='.ovideostats-browser-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'browser_id': browser.id}, handle)
        os.replace(temporary, profile / BINDING_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def browser_status():
    discovery = discover_browsers()
    try:
        selection = select_browser(discovery=discovery)
        return {'available': True, 'name': selection.browser.name, 'default_name': discovery.default_name,
                'is_default': selection.is_default, 'message': selection.message}
    except BrowserSelectionError as error:
        return {'available': False, 'name': '', 'default_name': discovery.default_name,
                'is_default': False, 'message': str(error)}
