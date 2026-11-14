import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

import desktop_app
import studio_app


class DesktopPortTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        dist = Path(directory.name)
        (dist / 'index.html').write_text('<html></html>')
        patcher = patch.object(desktop_app, 'DIST', dist)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_native_backends(self):
        for platform, expected in [('win32', 'edgechromium'), ('darwin', 'cocoa'), ('linux', None)]:
            with patch.object(sys, 'platform', platform):
                self.assertEqual(desktop_app.desktop_backend(), expected)

    def test_windows_state_separate_from_install(self):
        with patch.object(sys, 'platform', 'win32'), patch.dict('os.environ', {'LOCALAPPDATA': '/tmp/windows-user'}, clear=True):
            self.assertEqual(desktop_app.state_directory(), Path('/tmp/windows-user/OVideoStats'))

    def test_upgrade_preserves_accounts_preferences_and_browser_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            base = home / 'Library/Application Support'
            old_data = base / 'Catch Media/data'
            studio = old_data / 'studio'
            studio.mkdir(parents=True)
            (studio / 'accounts.json').write_text('[{"id":"existing-account"}]')
            (studio / 'preferences.json').write_text('{"theme":"dark","export_folder":"/reports"}')
            profile = studio / 'profiles/account'
            profile.mkdir(parents=True)
            (profile / 'Cookies').write_bytes(b'existing-browser-data')
            (profile / 'SingletonLock').write_text('stale-process')
            (profile / 'RunningChromeVersion').symlink_to('142.0.0.0')
            bundle = home / 'bundle'
            bundle.mkdir()
            repo = home / 'repo'
            (repo / 'data/studio').mkdir(parents=True)
            (repo / 'data/studio/accounts.json').write_text('[]')
            (bundle / 'migration_source.txt').write_text(str(repo))
            with patch.object(sys, 'platform', 'darwin'), patch.dict('os.environ', {}, clear=True), \
                    patch.object(Path, 'home', return_value=home), \
                    patch.object(desktop_app, 'BUNDLE_ROOT', bundle), patch.object(desktop_app.os, 'chdir'):
                state = desktop_app.prepare_state()
                self.assertEqual(state, base / 'OVideoStats')
                for relative in ['studio/accounts.json', 'studio/preferences.json', 'studio/profiles/account/Cookies']:
                    self.assertEqual((state / 'data' / relative).read_bytes(), (old_data / relative).read_bytes())
                self.assertFalse((state / 'data/studio/profiles/account/SingletonLock').exists())
                self.assertTrue((state / 'data/studio/profiles/account/RunningChromeVersion').is_symlink())
                # Reopening must keep updates made in the renamed application.
                (state / 'data/studio/accounts.json').write_text('[{"id":"new-account"}]')
                desktop_app.prepare_state()
                self.assertIn('new-account', (state / 'data/studio/accounts.json').read_text())
                self.assertIn('existing-account', (old_data / 'studio/accounts.json').read_text())

    def test_state_override_supports_existing_launchers_and_new_name_wins(self):
        with patch.dict('os.environ', {'CATCH_MEDIA_STATE_DIR': '/tmp/old-override'}, clear=True):
            self.assertEqual(desktop_app.state_directory(), Path('/tmp/old-override').resolve())
            with patch.dict('os.environ', {'OVIDEOSTATS_STATE_DIR': '/tmp/new-override'}):
                self.assertEqual(desktop_app.state_directory(), Path('/tmp/new-override').resolve())

    def test_clean_install_never_imports_developer_source_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'data/studio').mkdir(parents=True)
            (source / 'data/studio/accounts.json').write_text('[{"id":"private-development-account"}]')
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'migration_source.txt').write_text(str(source))
            state = root / 'clean-state'
            with patch.dict('os.environ', {'OVIDEOSTATS_STATE_DIR': str(state)}, clear=True), \
                    patch.object(desktop_app, 'BUNDLE_ROOT', bundle), patch.object(desktop_app.os, 'chdir'):
                desktop_app.prepare_state()
            self.assertTrue((state / 'data/studio').is_dir())
            self.assertFalse((state / 'data/studio/accounts.json').exists())

    def test_native_folder_api_success_and_cancel(self):
        api = desktop_app.DesktopApi()
        api._window = Mock()
        with patch.object(desktop_app.studio, 'get_export_folder', return_value='/does-not-exist'):
            api._window.create_file_dialog.return_value = ['/tmp']
            self.assertEqual(api.choose_export_folder(), '/tmp')
            self.assertEqual(api._window.create_file_dialog.call_args.kwargs['directory'], '')
            api._window.create_file_dialog.return_value = None
            self.assertEqual(api.choose_export_folder(), '')

    def test_real_bridge_never_traverses_native_window_properties(self):
        from webview import util
        touched = []

        class NativeWindow:
            @property
            def accessibility_object(self):
                # A native property may wait on the GUI thread. Record access
                # instead of blocking so this regression fails deterministically.
                touched.append(True)
                raise RuntimeError('Native window property was traversed')

        class ImmediateThread:
            def __init__(self, target):
                self.target = target

            def start(self):
                self.target()

        with patch.object(desktop_app, 'prepare_state'), \
                patch.object(desktop_app, 'ThreadingHTTPServer'), \
                patch.object(desktop_app.threading, 'Thread'), \
                patch.object(desktop_app.webview, 'create_window', return_value=NativeWindow()) as create, \
                patch.object(desktop_app.webview, 'start'), \
                patch.object(desktop_app.studio, 'get_theme', return_value='light'), \
                patch.object(sys, 'platform', 'win32'):
            desktop_app.main()
        api = create.call_args.kwargs['js_api']
        window = SimpleNamespace(_js_api=api, _functions={},
                                 _expose_lock=threading.Lock(), run_js=Mock(), events=Mock())
        with patch.object(util, 'Thread', ImmediateThread), \
                patch.object(util, 'load_js_files', return_value=('bootstrap', '%(functions)s')):
            util.inject_pywebview('edgechromium', window)
        import json
        functions = json.loads(window.run_js.call_args.args[0])
        self.assertEqual({entry['func'] for entry in functions},
                         {'choose_export_folder', 'sync_theme', 'system_theme'})
        self.assertEqual(touched, [])
        window.events._pywebviewready.set.assert_called_once_with()

    def test_windows_document_marks_native_renderer_before_first_paint(self):
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<html data-theme="light"></html>')
            handler = object.__new__(studio_app.Handler)
            handler.path = '/'
            handler.wfile = io.BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            with patch.object(studio_app, 'DIST', root), \
                    patch.object(studio_app, 'NATIVE_THEME_CHANGED', Mock()), \
                    patch.object(studio_app.studio, 'get_theme', return_value='light'), \
                    patch.object(sys, 'platform', 'win32'):
                handler.do_GET()
            self.assertIn(b'<html data-desktop="windows" ', handler.wfile.getvalue())

    def test_http_folder_uses_same_native_dialog(self):
        with patch.object(studio_app, 'NATIVE_FOLDER_CHOOSER', return_value='/tmp') as chooser:
            self.assertEqual(studio_app.choose_export_folder(), '/tmp')
            chooser.assert_called_once_with()

    def test_frozen_fallback_never_relaunches_exe(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), patch.object(studio_app.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '尚未就绪'):
                studio_app.choose_export_folder()
            run.assert_not_called()

    def test_window_contract_and_server_cleanup(self):
        with patch.object(desktop_app, 'prepare_state'), patch.object(desktop_app, 'ThreadingHTTPServer') as server_type, patch.object(desktop_app.threading, 'Thread'), patch.object(desktop_app.webview, 'create_window') as window, patch.object(desktop_app.webview, 'start') as start, patch.object(desktop_app.studio, 'get_theme', return_value='light'), patch.object(sys, 'platform', 'win32'):
            desktop_app.main()
            start.assert_called_once_with(gui='edgechromium')
            self.assertEqual(window.call_args.kwargs['width'], 1280)
            self.assertEqual(window.call_args.kwargs['height'], 760)
            self.assertEqual(window.call_args.kwargs['background_color'], '#f8f5ef')
            server_type.return_value.shutdown.assert_called_once()
            server_type.return_value.server_close.assert_called_once()
            self.assertIsNone(studio_app.NATIVE_FOLDER_CHOOSER)
            self.assertIsNone(studio_app.NATIVE_THEME_CHANGED)

    def test_macos_title_bar_follows_workspace_theme(self):
        appkit = SimpleNamespace(NSAppearance=Mock(), NSColor=Mock(),
                                NSAppearanceNameAqua='Aqua', NSAppearanceNameDarkAqua='DarkAqua')
        foundation = SimpleNamespace(NSThread=Mock())
        foundation.NSThread.isMainThread.return_value = True
        helper = Mock()
        modules = {'AppKit': appkit, 'Foundation': foundation,
                   'PyObjCTools': SimpleNamespace(AppHelper=helper)}
        window = Mock()
        with patch.object(sys, 'platform', 'darwin'), patch.dict(sys.modules, modules):
            for theme, appearance, rgb in [('light', 'Aqua', (248 / 255, 245 / 255, 239 / 255)),
                                            ('dark', 'DarkAqua', (32 / 255, 31 / 255, 29 / 255))]:
                desktop_app.apply_window_theme(window, theme)
                appkit.NSAppearance.appearanceNamed_.assert_called_with(appearance)
                appkit.NSColor.colorWithSRGBRed_green_blue_alpha_.assert_called_with(*rgb, 1.0)
                window.native.setAppearance_.assert_called_with(appkit.NSAppearance.appearanceNamed_.return_value)
                window.native.setBackgroundColor_.assert_called_with(appkit.NSColor.colorWithSRGBRed_green_blue_alpha_.return_value)
            with patch.object(desktop_app, 'system_theme', return_value='dark'):
                desktop_app.apply_window_theme(window, 'system')
                window.native.setAppearance_.assert_called_with(None)
                appkit.NSColor.colorWithSRGBRed_green_blue_alpha_.assert_called_with(32 / 255, 31 / 255, 29 / 255, 1.0)
        helper.callAfter.assert_not_called()

    def test_macos_hidden_title_keeps_native_window_controls(self):
        appkit = SimpleNamespace(NSWindowStyleMaskFullSizeContentView=32768,
                                NSWindowTitleHidden=1, NSColor=Mock())
        window = Mock()
        window.native.styleMask.return_value = 15
        with patch.dict(sys.modules, {'AppKit': appkit}):
            desktop_app.configure_macos_window(window)
        window.native.setStyleMask_.assert_called_once_with(15 | 32768)
        window.native.setTitleVisibility_.assert_called_once_with(1)
        window.native.setTitlebarAppearsTransparent_.assert_called_once_with(True)
        window.native.standardWindowButton_.assert_not_called()

    def test_macos_title_bar_updates_on_gui_thread(self):
        foundation = SimpleNamespace(NSThread=Mock())
        foundation.NSThread.isMainThread.return_value = False
        helper = Mock()
        modules = {'AppKit': Mock(), 'Foundation': foundation,
                   'PyObjCTools': SimpleNamespace(AppHelper=helper)}
        window = Mock()
        with patch.object(sys, 'platform', 'darwin'), patch.dict(sys.modules, modules):
            desktop_app.apply_window_theme(window, 'light')
            window.native.setAppearance_.assert_not_called()
            helper.callAfter.assert_called_once()
            helper.callAfter.call_args.args[0]()
            window.native.setAppearance_.assert_called_once()

    def test_theme_api_notifies_native_window_after_saving(self):
        handler = object.__new__(studio_app.Handler)
        handler.path = '/api/theme'
        handler._authorized = Mock(return_value=True)
        handler._body = Mock(return_value={'theme': 'light'})
        handler._json = Mock()
        with patch.object(studio_app.studio, 'set_theme', return_value='light') as save, patch.object(studio_app, 'NATIVE_THEME_CHANGED') as changed:
            changed.side_effect = lambda theme: save.assert_called_once_with(theme)
            handler.do_POST()
            changed.assert_called_once_with('light')
            handler._json.assert_called_once_with(200, {'theme': 'light'})

    def test_windows_builder_rejects_mac(self):
        path = Path(__file__).resolve().parents[1]/'packaging'/'build_windows.py'
        spec = importlib.util.spec_from_file_location('catch_windows_build', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(sys, 'platform', 'darwin'), patch.object(sys, 'argv', ['build_windows.py']), patch.object(module.subprocess, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Windows'):
                module.main()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
