"""Studio account isolation and selected export tests (no platform network calls)."""
import tempfile
import io
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from openpyxl import load_workbook
from smi import studio
from smi.avatars import normalized_png, fetch_avatar


class StudioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app_patch = patch.object(studio, 'APP_DIR', self.root / 'studio')
        self.registry_patch = patch.object(studio, 'REGISTRY', self.root / 'studio' / 'accounts.json')
        self.preferences_patch = patch.object(studio, 'PREFERENCES', self.root / 'studio' / 'preferences.json')
        self.app_patch.start()
        self.registry_patch.start()
        self.preferences_patch.start()

    def tearDown(self):
        self.registry_patch.stop()
        self.preferences_patch.stop()
        self.app_patch.stop()
        self.tmp.cleanup()

    def test_export_folder_is_remembered_until_another_valid_choice(self):
        first = self.root / '第一次导出'
        second = self.root / '第二次导出'
        first.mkdir()
        second.mkdir()
        self.assertEqual(studio.get_export_folder(), '')
        self.assertEqual(studio.set_export_folder(str(first)), str(first.resolve()))
        self.assertEqual(studio.get_export_folder(), str(first.resolve()))
        with self.assertRaisesRegex(ValueError, '不存在'):
            studio.set_export_folder(str(self.root / 'missing'))
        self.assertEqual(studio.get_export_folder(), str(first.resolve()))
        studio.set_export_folder(str(second))
        self.assertEqual(studio.get_export_folder(), str(second.resolve()))

    def test_same_platform_identity_rejected_but_different_identity_added(self):
        a = studio._register('douyin', 'uid-a', '账号 A', profile=self.root / 'profile-a')
        with self.assertRaisesRegex(ValueError, '已经添加过'):
            studio._register('douyin', 'uid-a', '账号 A 重新登录', profile=self.root / 'new-session')
        b = studio._register('douyin', 'uid-b', '账号 B', profile=self.root / 'profile-b')
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(len(studio.list_accounts()), 2)
        self.assertEqual([x['profile'] for x in studio._load()],
                         [str(self.root / 'profile-a'), str(self.root / 'profile-b')])

    def test_sort_and_destination_preferences_preserve_each_other(self):
        self.assertEqual(studio.get_account_sort(), {'key': 'connected_at', 'direction': 'asc'})
        studio.set_export_folder(str(self.root))
        studio.set_account_sort('platform', 'desc')
        self.assertEqual(studio.get_export_folder(), str(self.root.resolve()))
        studio.set_export_folder(str(self.root / 'studio'))
        self.assertEqual(studio.get_account_sort(), {'key': 'platform', 'direction': 'desc'})
        with self.assertRaisesRegex(ValueError, '无效'):
            studio.set_account_sort('invalid', 'asc')
        self.assertEqual(studio.get_account_sort(), {'key': 'platform', 'direction': 'desc'})

    def test_theme_persists_without_overwriting_other_preferences(self):
        self.assertEqual(studio.get_theme(), 'light')
        studio.set_export_folder(str(self.root))
        studio.set_account_sort('platform', 'desc')
        studio.set_theme('dark')
        self.assertEqual(studio.get_theme(), 'dark')
        self.assertEqual(studio.get_export_folder(), str(self.root.resolve()))
        self.assertEqual(studio.get_account_sort(), {'key': 'platform', 'direction': 'desc'})
        studio.set_account_sort('name', 'asc')
        self.assertEqual(studio.get_theme(), 'dark')
        studio.set_theme('system')
        self.assertEqual(studio.get_theme(), 'system')
        self.assertEqual(studio.get_export_folder(), str(self.root.resolve()))
        with self.assertRaisesRegex(ValueError, '无效'):
            studio.set_theme('invalid')
        self.assertEqual(studio.get_theme(), 'system')
        studio.set_theme('light')
        self.assertEqual(studio.get_theme(), 'light')

    def test_avatars_stay_with_their_account_and_are_removed_on_delete(self):
        from PIL import Image
        raw = io.BytesIO()
        Image.new('RGB', (60, 30), 'red').save(raw, format='JPEG')
        png = normalized_png(raw.getvalue())
        with Image.open(io.BytesIO(png)) as avatar:
            self.assertEqual(avatar.size, (128, 128))
            self.assertEqual(avatar.format, 'PNG')
        a = studio._register('douyin', 'a', '同名', avatar=png)
        b = studio._register('douyin', 'b', '同名', avatar=png)
        self.assertIn(a['id'], a['avatar_url'])
        self.assertNotEqual(a['avatar_url'], b['avatar_url'])
        self.assertEqual(studio.avatar_path(a['id']).read_bytes(), png)
        self.assertIsNone(studio.avatar_path('../accounts.json'))
        self.assertNotIn('avatar_file', a)
        path = studio.avatar_path(a['id'])
        studio.delete_account(a['id'])
        self.assertFalse(path.exists())
        self.assertIsNotNone(studio.avatar_path(b['id']))

    def test_avatar_failure_preserves_existing_image_and_connection(self):
        a = studio._register('xhs', 'a', '账号')
        self.assertIsNone(a['avatar_url'])
        studio._store_avatar(a['id'], b'existing')
        previous = studio.get_account(a['id'])['avatar_version']
        studio._store_avatar(a['id'], None)
        self.assertEqual(studio.get_account(a['id'])['avatar_version'], previous)
        with patch.object(studio, '_write_avatar', side_effect=OSError('read only')):
            b = studio._register('xhs', 'b', '账号 B', avatar=b'optional')
        self.assertIsNone(b['avatar_url'])
        self.assertEqual(fetch_avatar('file:///etc/passwd'), None)

    def test_avatar_endpoint_requires_token_and_only_serves_owned_image(self):
        from email.message import Message
        from studio_app import Handler
        a = studio._register('bili', 'a', '账号', avatar=b'cached-image')
        handler = Handler.__new__(Handler)
        handler.token = 'avatar-test-token'
        handler.headers = Message()
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._json = Mock()
        handler.path = a['avatar_url']
        handler.do_GET()
        self.assertEqual(handler._json.call_args.args[0], 403)
        handler.path += '&token=avatar-test-token'
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call('Content-Type', 'image/png')
        self.assertEqual(handler.wfile.getvalue(), b'cached-image')
        handler.path = '/api/avatars/unknown?token=avatar-test-token'
        handler.do_GET()
        self.assertEqual(handler._json.call_args.args[0], 404)

    def test_connection_time_is_recorded_and_legacy_order_is_preserved(self):
        studio._save([{'id': 'old-a', 'platform': 'bili', 'identity': 'old-a'},
                      {'id': 'old-b', 'platform': 'bili', 'identity': 'old-b'}])
        with patch('smi.studio.time.time', return_value=1234567890):
            account = studio._register('bili', 'new', '新账号')
        self.assertEqual(account['connected_at'], 1234567890)
        rows = studio.list_accounts()
        self.assertEqual([row['id'] for row in rows], ['old-a', 'old-b', account['id']])
        self.assertIsNone(rows[0]['connected_at'])

    def test_two_accounts_export_separately_and_private_filter(self):
        a = studio._register('douyin', 'uid-a', '同名账号', profile=self.root / 'a')
        b = studio._register('douyin', 'uid-b', '同名账号', profile=self.root / 'b')
        studio.update_account(a['id'], ['title', 'play', 'like_rate', 'status', 'finish_rate'], False)
        studio.update_account(b['id'], ['title', 'play', 'like_rate', 'status'], True)
        rows = [{'title': '公开', 'play': 100, 'like': 10, 'status': '已发布'},
                {'title': '自见', 'play': 0, 'like': 0, 'status': '自见'}]
        out = self.root / 'exports'
        out.mkdir()
        progress = []
        with patch.object(studio, '_collect', side_effect=lambda row, update: rows):
            results = studio.export_all(str(out), lambda message, completed=None, total=None: progress.append((completed, total)) if completed is not None else None)
        self.assertEqual(progress[0], (0, 2))
        self.assertEqual(progress[-1], (2, 2))
        self.assertEqual([r['count'] for r in results], [1, 2])
        self.assertTrue(all(r['ok'] for r in results))
        self.assertNotEqual(results[0]['path'], results[1]['path'])
        for result in results:
            self.assertRegex(Path(result['path']).name,
                             r'^同名账号-抖音视频数据-\d{12}(?:-\d+)?\.xlsx$')
        first = load_workbook(results[0]['path'])['作品明细']
        second = load_workbook(results[1]['path'])['作品明细']
        self.assertEqual(first.max_row, 2)
        self.assertEqual(second.max_row, 3)
        self.assertEqual(first.cell(2, 4).value, 0.1)
        self.assertEqual(first.cell(2, 4).number_format, '0.00%')
        self.assertEqual(first.cell(1, 6).value, '完播率')
        self.assertIsNone(first.cell(2, 6).value)

    def test_export_only_selected_accounts_and_progress(self):
        first = studio._register('douyin', 'first', '未选择的账号')
        selected = studio._register('douyin', 'selected', '选中的账号')
        other = studio._register('xhs', 'other', '另一平台账号')
        progress = []
        def update(message, completed=None, total=None):
            if completed is not None:
                progress.append((completed, total))
        with patch.object(studio, '_collect', return_value=[{'title': '公开作品', 'status': '已发布'}]) as collect:
            results = studio.export_all(str(self.root), update, [selected['id'], selected['id']])
        self.assertEqual([call.args[0]['id'] for call in collect.call_args_list], [selected['id']])
        self.assertEqual([result['account'] for result in results], ['选中的账号'])
        self.assertEqual(progress, [(0, 1), (1, 1)])
        self.assertTrue(results[0]['ok'])
        self.assertEqual(len(list(self.root.rglob('*.xlsx'))), 1)
        self.assertIsNone(studio.get_account(first['id'])['last_export'])
        self.assertIsNone(studio.get_account(other['id'])['last_export'])

    def test_empty_or_invalid_selection_never_collects(self):
        account = studio._register('bili', 'one', '账号')
        with patch.object(studio, '_collect') as collect:
            for selection in ([], 'all', [None], ['missing'], [account['id'], 'missing']):
                with self.subTest(selection=selection), self.assertRaises(ValueError):
                    studio.export_all(str(self.root), lambda *args: None, selection)
            collect.assert_not_called()
        self.assertEqual(list(self.root.rglob('*.xlsx')), [])

    def test_xhs_and_channels_can_filter_private_items(self):
        account = studio._register('xhs', 'user-a', '笔记号', profile=self.root / 'xhs')
        studio.update_account(account['id'], ['title'], True)
        works = [{'visibility': 'public'}, {'visibility': 'private'}]
        self.assertEqual(len(studio._public_videos('xhs', works)), 1)
        self.assertEqual(len(studio._public_videos('channels', works)), 1)
        with self.assertRaisesRegex(ValueError, '未返回可见性标记'):
            studio._public_videos('channels', [{'visibility': 'unknown'}])

    def test_delete_connection_cleans_only_owned_login_state(self):
        owned = self.root / 'studio' / 'profiles' / 'xhs' / 'session'
        owned.mkdir(parents=True)
        legacy = self.root / 'legacy'
        legacy.mkdir()
        a = studio._register('xhs', 'a', '账号 A', profile=owned)
        b = studio._register('xhs', 'b', '账号 B', profile=legacy)
        studio.delete_account(a['id'])
        self.assertFalse(owned.exists())
        studio.delete_account(b['id'])
        self.assertTrue(legacy.exists())
        self.assertEqual(studio.list_accounts(), [])

    def test_expired_export_updates_connection_status(self):
        account = studio._register('channels', 'finder-a', '视频号 A', profile=self.root / 'profile')
        with patch.object(studio, '_collect', side_effect=ValueError('视频号登录已过期，请重新登录。')):
            result = studio._export_one(studio.get_account(account['id']), self.root, lambda _: None)
        self.assertFalse(result['ok'])
        self.assertEqual(studio.list_accounts()[0]['connection_status'], 'expired')

    def test_new_channels_connection_opens_official_qr_and_waits_for_confirmation(self):
        browser = Mock()
        browser.__enter__ = Mock(return_value=browser)
        browser.__exit__ = Mock(return_value=False)
        # Even an older authenticated page must not skip an explicit connection.
        browser.is_logged_in.return_value = True
        with patch.object(studio, 'ChannelsBrowser', return_value=browser), \
                patch.object(studio, '_identity', return_value=('finder-new', '视频号 A')), \
                patch.object(studio, 'browser_avatar', return_value=None):
            account = studio.add_account('channels', Mock())
        browser.open_login.assert_called_once_with()
        browser.wait_for_login.assert_called_once_with(timeout=300)
        browser.goto.assert_not_called()
        browser.__exit__.assert_called_once()
        self.assertEqual(account['identity'], 'finder-new')

    def test_channels_reconnect_rejects_wrong_account_and_keeps_original_session(self):
        profile = self.root / 'original-profile'
        profile.mkdir()
        (profile / 'keep.txt').write_text('original session')
        account = studio._register('channels', 'finder-original', '原视频号', profile=profile)
        before = studio.get_account(account['id'])
        browser = Mock()
        browser.__enter__ = Mock(return_value=browser)
        browser.__exit__ = Mock(return_value=False)
        browser.is_logged_in.return_value = True
        with patch.object(studio, 'ChannelsBrowser', return_value=browser), \
                patch.object(studio, '_identity', return_value=('finder-other', '其他账号')), \
                patch.object(studio, 'browser_avatar', return_value=None):
            with self.assertRaisesRegex(ValueError, '原视频号'):
                studio.reconnect_account(account['id'], Mock())
        browser.open_login.assert_called_once_with()
        browser.wait_for_login.assert_called_once_with(timeout=300)
        self.assertEqual(studio.get_account(account['id']), before)
        self.assertEqual((profile / 'keep.txt').read_text(), 'original session')

    def test_unverified_legacy_session_is_visible_as_pending(self):
        legacy = self.root / 'data' / 'browser' / 'channels' / 'Default'
        legacy.mkdir(parents=True)
        with patch('smi.studio.Path', wraps=Path) as path_class:
            # pending_sessions uses relative paths just like the CLI project.
            path_class.side_effect = lambda value: self.root / value if str(value) == 'data/browser' else Path(value)
            pending = studio.pending_sessions()
        self.assertEqual([item['platform'] for item in pending], ['channels'])


if __name__ == '__main__':
    unittest.main()
