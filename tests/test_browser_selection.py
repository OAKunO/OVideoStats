from pathlib import Path
import json
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from smi import browser_selection as selection
from smi import browser_base
from smi.douyin_browser import DouyinBrowser


class BrowserSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chrome = selection.BrowserApp('chrome', self.root / 'chrome.exe')
        self.edge = selection.BrowserApp('msedge', self.root / 'msedge.exe')
        self.profile = self.root / 'profile'

    def tearDown(self):
        self.temporary.cleanup()

    def discovery(self, default_id='msedge', default_name='Microsoft Edge', installed=None):
        return selection.BrowserDiscovery(default_id, default_name, tuple(installed or [self.chrome, self.edge]))

    def test_default_edge_wins_even_when_chrome_was_found_first(self):
        result = selection.select_browser(discovery=self.discovery())
        self.assertEqual(result.browser, self.edge)
        self.assertTrue(result.is_default)

    def test_default_chrome_wins_even_when_edge_was_found_first(self):
        result = selection.select_browser(discovery=self.discovery('chrome', 'Google Chrome', [self.edge, self.chrome]))
        self.assertEqual(result.browser, self.chrome)

    def test_unsupported_default_falls_back_with_an_explanation(self):
        result = selection.select_browser(discovery=self.discovery('', 'Safari'))
        self.assertEqual(result.browser, self.chrome)
        self.assertFalse(result.is_default)
        self.assertIn('Safari', result.message)
        self.assertIn('Google Chrome', result.message)

    def test_no_compatible_browser_is_an_actionable_error(self):
        with self.assertRaisesRegex(selection.BrowserSelectionError, 'Chrome.*Edge'):
            selection.select_browser(discovery=selection.BrowserDiscovery('', 'Firefox', ()))

    def test_existing_chrome_profile_is_never_opened_with_default_edge(self):
        (self.profile / 'Default').mkdir(parents=True)
        result = selection.select_browser(self.profile, discovery=self.discovery())
        self.assertEqual(result.browser, self.chrome)

    def test_bound_edge_profile_survives_a_default_browser_change(self):
        selection.bind_profile(self.profile, self.edge)
        result = selection.select_browser(self.profile, discovery=self.discovery('chrome', 'Google Chrome'))
        self.assertEqual(result.browser, self.edge)
        self.assertEqual(selection.profile_browser_name(self.profile), 'Microsoft Edge')

    def test_renamed_app_keeps_legacy_edge_binding(self):
        self.profile.mkdir()
        (self.profile / selection.LEGACY_BINDING_FILE).write_text('{"version":1,"browser_id":"msedge"}')
        result = selection.select_browser(self.profile, discovery=self.discovery('chrome', 'Google Chrome'))
        self.assertEqual(result.browser, self.edge)
        selection.bind_profile(self.profile, self.chrome)
        self.assertEqual(selection.profile_browser_id(self.profile), 'chrome')

    def test_missing_bound_browser_does_not_reuse_another_brands_session(self):
        selection.bind_profile(self.profile, self.edge)
        with self.assertRaisesRegex(selection.BrowserSelectionError, '重新连接'):
            selection.select_browser(self.profile, discovery=self.discovery('chrome', 'Google Chrome', [self.chrome]))

    def test_corrupt_binding_cannot_be_treated_as_an_empty_profile(self):
        self.profile.mkdir()
        (self.profile / selection.BINDING_FILE).write_text('{broken')
        with self.assertRaisesRegex(selection.BrowserSelectionError, '重新连接'):
            selection.select_browser(self.profile, discovery=self.discovery())

    def test_explicit_browser_cannot_override_an_existing_brand(self):
        selection.bind_profile(self.profile, self.edge)
        with self.assertRaisesRegex(selection.BrowserSelectionError, '另一种浏览器'):
            selection.select_browser(self.profile, channel='chrome', discovery=self.discovery())

    def test_binding_moves_with_the_profile_after_registration(self):
        selection.bind_profile(self.profile, self.edge)
        moved = self.root / 'registered'
        self.profile.rename(moved)
        self.assertEqual(selection.profile_browser_id(moved), 'msedge')
        self.assertEqual(json.loads((moved / selection.BINDING_FILE).read_text())['browser_id'], 'msedge')

    def test_macos_recognizes_an_app_by_bundle_identity(self):
        app = self.root / 'Microsoft Edge.app'
        (app / 'Contents/MacOS').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.microsoft.edgemac', 'CFBundleExecutable': 'Microsoft Edge'}))
        executable = app / 'Contents/MacOS/Microsoft Edge'
        executable.touch()
        found = selection._mac_app(app)
        self.assertEqual(found.id, 'msedge')
        self.assertEqual(found.app_path, app)
        self.assertEqual(found.executable, executable)

    def test_windows_uses_the_default_registered_executable_in_a_custom_directory(self):
        path = self.root / 'Custom Browser Location/msedge.exe'
        path.parent.mkdir()
        path.touch()
        with patch.object(selection, '_windows_association', side_effect=lambda kind: str(path) if kind == 2 else 'Microsoft Edge'), \
                patch.dict(selection.os.environ, {}, clear=True):
            discovered = selection._discover_windows()
        self.assertEqual(discovered.default_id, 'msedge')
        self.assertEqual(selection.select_browser(discovery=discovered).browser.executable, path)

    def test_windows_fallback_does_not_require_chrome(self):
        path = self.root / 'Microsoft/Edge/Application/msedge.exe'
        path.parent.mkdir(parents=True)
        path.touch()
        with patch.object(selection, '_windows_association', side_effect=lambda kind: 'Firefox' if kind == 4 else ''), \
                patch.dict(selection.os.environ, {'PROGRAMFILES': str(self.root)}, clear=True):
            discovered = selection._discover_windows()
        result = selection.select_browser(discovery=discovered)
        self.assertEqual(result.browser.id, 'msedge')
        self.assertIn('Firefox', result.message)

    def test_all_collectors_default_to_auto_selection(self):
        self.assertIsNone(DouyinBrowser(profile_dir=self.profile).channel)
        self.assertIsNone(browser_base.BrowserSession(self.profile).channel)

    def test_windows_launch_uses_the_selected_executable_and_pins_its_profile(self):
        playwright = Mock()
        factory = Mock()
        factory.return_value.start.return_value = playwright
        playwright.chromium.launch_persistent_context.return_value.pages = [Mock()]
        chosen = selection.BrowserSelection(self.edge, '使用 Edge', True)
        with patch.object(browser_base, 'select_browser', return_value=chosen), \
                patch.object(browser_base, 'require_playwright', return_value=factory), \
                patch.object(browser_base.sys, 'platform', 'win32'):
            with browser_base.BrowserSession(self.profile, headless=False) as browser:
                browser.open()
                self.assertEqual(browser.browser_message, '使用 Edge')
                call = playwright.chromium.launch_persistent_context.call_args
                self.assertEqual(call.kwargs['executable_path'], str(self.edge.executable))
                self.assertNotIn('channel', call.kwargs)
                self.assertEqual(selection.profile_browser_id(self.profile), 'msedge')
        playwright.stop.assert_called_once()

    def test_failed_first_launch_keeps_the_selected_brand_for_retry(self):
        playwright = Mock()
        factory = Mock()
        factory.return_value.start.return_value = playwright
        playwright.chromium.launch_persistent_context.side_effect = RuntimeError('launch blocked')
        with patch.object(browser_base, 'select_browser', return_value=selection.BrowserSelection(self.edge, 'Edge')), \
                patch.object(browser_base, 'require_playwright', return_value=factory), \
                patch.object(browser_base.sys, 'platform', 'win32'):
            with self.assertRaises(browser_base.BrowserError):
                browser_base.BrowserSession(self.profile).open()
        self.assertEqual(selection.profile_browser_id(self.profile), 'msedge')
        playwright.stop.assert_called_once()

    def test_macos_launch_services_receives_selected_edge_app(self):
        edge = selection.BrowserApp('msedge', self.edge.executable, self.root / 'Microsoft Edge.app')
        factory = Mock()
        chosen = selection.BrowserSelection(edge, '系统默认 Edge', True)
        with patch.object(browser_base, 'select_browser', return_value=chosen), \
                patch.object(browser_base, 'require_playwright', return_value=factory), \
                patch.object(browser_base.sys, 'platform', 'darwin'), \
                patch('smi.mac_chrome.MacChromeSession') as native:
            native.return_value.open.return_value.pages = [Mock()]
            with browser_base.BrowserSession(self.profile) as browser:
                browser.open()
            native.assert_called_once_with(app_path=edge.app_path, name='Microsoft Edge')
            factory.return_value.start.return_value.chromium.launch_persistent_context.assert_not_called()
            native.return_value.close.assert_called_once()
