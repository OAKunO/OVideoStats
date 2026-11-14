"""Launch an owned Chrome/Edge instance through macOS Launch Services.

Chrome then runs with its own application identity. Direct child-process launch
attributes Chrome's app-bundle maintenance to OVideoStats and causes TCC's App
Management notification. Profiles remain isolated and CDP stays on loopback.
"""
import os
import time
from pathlib import Path


class MacChromeSession:
    def __init__(self, app_path=None, name='Google Chrome'):
        self.app_path = Path(app_path) if app_path else None
        self.name = name
        self.application = None
        self.browser = None

    def open(self, playwright, profile, headless, user_agent, timeout, slow_mo):
        from AppKit import (NSWorkspace, NSWorkspaceLaunchNewInstance,
                            NSWorkspaceLaunchWithoutActivation,
                            NSWorkspaceLaunchConfigurationArguments)
        from Foundation import NSURL

        app_path = self.app_path or next((path for path in (Path('/Applications/Google Chrome.app'),
                                          Path.home() / 'Applications/Google Chrome.app')
                         if path.is_dir()), None)
        if not app_path:
            raise RuntimeError('未找到 {}，请安装后重试。'.format(self.name))
        profile = Path(profile).resolve()
        active_port = profile / 'DevToolsActivePort'
        active_port.unlink(missing_ok=True)
        arguments = [
            '--user-data-dir=' + str(profile),
            '--remote-debugging-port=0',
            '--remote-debugging-address=127.0.0.1',
            '--user-agent=' + user_agent,
            '--window-size=1440,900',
            '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking', '--disable-component-update',
            '--disable-sync', '--disable-extensions', '--disable-default-apps',
            '--disable-popup-blocking', '--disable-search-engine-choice-screen',
            '--enable-automation', '--password-store=basic', '--use-mock-keychain',
            'about:blank',
        ]
        if headless:
            arguments[0:0] = ['--headless=new', '--hide-scrollbars', '--mute-audio']
        options = NSWorkspaceLaunchNewInstance
        if headless:
            options |= NSWorkspaceLaunchWithoutActivation
        application, error = NSWorkspace.sharedWorkspace().launchApplicationAtURL_options_configuration_error_(
            NSURL.fileURLWithPath_(str(app_path)), options,
            {NSWorkspaceLaunchConfigurationArguments: arguments}, None)
        if error or not application:
            raise RuntimeError('macOS 无法启动 {}：{}'.format(self.name, error or '未返回浏览器进程'))
        self.application = application
        deadline = time.monotonic() + timeout / 1000
        try:
            while time.monotonic() < deadline:
                if not self._running():
                    raise RuntimeError('{} 启动后已退出，请确认该账号没有在其他窗口中运行。'.format(self.name))
                try:
                    lines = active_port.read_text().splitlines()
                except FileNotFoundError:
                    lines = []
                if len(lines) >= 2 and lines[0].isdigit() and 0 < int(lines[0]) <= 65535 and lines[1].startswith('/devtools/browser/'):
                    endpoint = 'ws://127.0.0.1:' + lines[0] + lines[1]
                    self.browser = playwright.chromium.connect_over_cdp(
                        endpoint, timeout=max(1, int((deadline - time.monotonic()) * 1000)), slow_mo=slow_mo)
                    context = self.browser.contexts[0]
                    # This covers context.request as well as browser navigation.
                    context.set_extra_http_headers({'User-Agent': user_agent})
                    self.browser.new_browser_cdp_session().send('Browser.setDownloadBehavior', {'behavior': 'deny'})
                    return context
                time.sleep(.1)
            raise RuntimeError('等待 {} 启动超时；请确认浏览器能正常打开，且未禁用远程调试。'.format(self.name))
        except Exception:
            self.close()
            raise

    def _running(self):
        if not self.application:
            return False
        try:
            os.kill(self.application.processIdentifier(), 0)
            return True
        except ProcessLookupError:
            return False

    def close(self):
        # Browser.close flushes the persistent profile. Never close a user's
        # ordinary Chrome window; only this newly launched process is owned.
        if self.browser:
            try:
                self.browser.new_browser_cdp_session().send('Browser.close')
            except Exception:
                pass
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.application:
            if self._running():
                self.application.terminate()
            deadline = time.monotonic() + 5
            while self._running() and time.monotonic() < deadline:
                time.sleep(.05)
            if self._running():
                self.application.forceTerminate()
                deadline = time.monotonic() + 2
                while self._running() and time.monotonic() < deadline:
                    time.sleep(.05)
            self.application = None
