"""Login checks distinguish expiration, uncertain failures and account changes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from smi import studio
from smi.browser_base import BrowserError


class LoginChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)
        for name, value in [('APP_DIR', self.root / 'studio'),
                            ('REGISTRY', self.root / 'studio/accounts.json')]:
            patcher = patch.object(studio, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.previous_cwd)

    def account(self, platform='douyin', identity='one', **kwargs):
        profile = self.root / (platform + '-' + identity)
        profile.mkdir()
        return studio._register(platform, identity, kwargs.pop('name', identity), profile=profile, **kwargs)

    def browser(self, logged_in=True, text='作品管理', url='https://creator.douyin.com/home'):
        browser = Mock()
        browser.__enter__ = Mock(return_value=browser)
        browser.__exit__ = Mock(return_value=False)
        browser.is_logged_in.return_value = logged_in
        browser.page.inner_text.return_value = text
        browser.url = url
        browser.count.return_value = 0
        return browser

    def test_checks_every_registered_account_including_two_on_one_platform(self):
        accounts = [self.account(platform, str(index)) for index, platform in enumerate(('bili', 'douyin', 'douyin', 'xhs', 'channels'))]
        with patch.object(studio, '_verify_session', side_effect=lambda row: (row['identity'], row['name'])) as verify:
            progress = Mock()
            results = studio.check_login_states(progress)
        self.assertEqual([call.args[0]['id'] for call in verify.call_args_list], [row['id'] for row in accounts])
        self.assertEqual([r['status'] for r in results], ['connected'] * 5)
        self.assertEqual(progress.call_args.args[1:], (5, 5))
        for account in studio.list_accounts():
            self.assertGreater(account['last_checked_at'], 0)
            self.assertIsNone(account['last_check_error'])

    def test_bili_network_failure_is_not_expiration_and_does_not_stop_other_accounts(self):
        first = self.account('bili', credential=self.root / 'credential.json')
        second = self.account('xhs')
        with patch.object(studio, 'load_credentials', return_value={'cookie': 'private-cookie'}), \
             patch.object(studio, 'BiliClient'), \
             patch.object(studio, 'check_login', return_value={'ok': False, 'code': None, 'message': '请求超时'}), \
             patch.object(studio, '_verify_browser_session', return_value=('one', '账号')):
            results = studio.check_login_states(Mock())
        self.assertEqual([r['status'] for r in results], ['error', 'connected'])
        self.assertIn('请求超时', results[0]['message'])
        self.assertEqual(studio.get_account(first['id'])['connection_status'], 'error')
        self.assertEqual(studio.get_account(second['id'])['connection_status'], 'connected')

    def test_bili_requires_live_platform_identity_not_only_saved_credentials(self):
        account = self.account('bili', credential=self.root / 'credential.json')
        for info, status in [({'ok': False, 'code': -101}, 'expired'),
                             ({'ok': True, 'mid': 'different'}, 'mismatch'),
                             ({'ok': True}, 'error'),
                             ({'ok': True, 'mid': 'one'}, 'connected')]:
            with self.subTest(info=info), patch.object(studio, 'load_credentials', return_value={'cookie': 'private-cookie'}), \
                 patch.object(studio, 'BiliClient'), patch.object(studio, 'check_login', return_value=info):
                result = studio.check_login_states(Mock())[0]
            self.assertEqual(result['status'], status)
            self.assertEqual(studio.get_account(account['id'])['identity'], 'one')

    def test_browser_success_verifies_identity_and_closes_browser(self):
        account = self.account()
        browser = self.browser()
        with patch.object(studio, 'DouyinBrowser', return_value=browser) as cls, \
             patch.object(studio, '_identity', return_value=('one', '登录名称')):
            result = studio.check_login_states(Mock())[0]
        cls.assert_called_once_with(profile_dir=studio.get_account(account['id'])['profile'], headless=True)
        browser.open.assert_called_once()
        browser.goto.assert_called_once_with(settle=1)
        browser.__exit__.assert_called_once()
        self.assertEqual(result['status'], 'connected')

    def test_blank_dashboard_is_error_but_explicit_login_screen_is_expired(self):
        account = self.account()
        for text, status in [('', 'error'), ('页面加载失败', 'error'), ('安全验证', 'error'), ('扫码登录', 'expired')]:
            browser = self.browser(logged_in=False, text=text)
            with self.subTest(text=text), patch.object(studio, 'DouyinBrowser', return_value=browser), \
                 patch.object(studio, '_identity') as identity:
                result = studio.check_login_states(Mock())[0]
            identity.assert_not_called()
            self.assertEqual(result['status'], status)
            self.assertEqual(studio.get_account(account['id'])['connection_status'], status)

    def test_browser_failure_replaces_stale_green_without_changing_configuration(self):
        account = self.account()
        studio.update_account(account['id'], ['title', 'play'], True)
        previous = studio.get_account(account['id'])
        browser = self.browser()
        browser.open.side_effect = BrowserError('cannot launch')
        with patch.object(studio, 'DouyinBrowser', return_value=browser):
            result = studio.check_login_states(Mock())[0]
        stored = studio.get_account(account['id'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('无法启动浏览器', stored['last_check_error'])
        for key in ('profile', 'identity', 'fields', 'include_private', 'connected_at'):
            self.assertEqual(stored[key], previous[key])

    def test_missing_profile_does_not_create_an_empty_replacement_session(self):
        account = studio._register('xhs', 'one', '账号', profile=self.root / 'missing')
        with patch.object(studio, 'XhsBrowser') as browser:
            result = studio.check_login_states(Mock())[0]
        browser.assert_not_called()
        self.assertFalse((self.root / 'missing').exists())
        self.assertEqual(result['status'], 'expired')

    def test_legacy_session_is_added_after_verification_and_failure_is_reported(self):
        for platform in ('douyin', 'xhs'):
            (self.root / 'data/browser' / platform).mkdir(parents=True)
        def verify(row):
            if row['platform'] == 'xhs':
                raise BrowserError('failed')
            return 'legacy-id', '旧账号'
        with patch.object(studio, '_verify_session', side_effect=verify):
            results = studio.check_login_states(Mock())
        self.assertEqual([r['status'] for r in results], ['connected', 'error'])
        self.assertTrue(results[0]['added'])
        self.assertEqual(len(studio.list_accounts()), 1)
        self.assertGreater(studio.list_accounts()[0]['last_checked_at'], 0)

    def test_existing_legacy_profile_is_only_checked_once(self):
        profile = self.root / 'data/browser/douyin'
        profile.mkdir(parents=True)
        account = studio._register('douyin', 'one', '账号', profile=profile)
        with patch.object(studio, '_verify_session', return_value=('one', '账号')) as verify:
            results = studio.check_login_states(Mock())
        self.assertEqual(len(results), 1)
        verify.assert_called_once()
        self.assertEqual(results[0]['id'], account['id'])

    def test_duplicate_legacy_identity_does_not_overwrite_failed_existing_connection(self):
        account = self.account()
        (self.root / 'data/browser/douyin').mkdir(parents=True)
        def verify(row):
            if row.get('id'):
                raise studio.LoginCheckFailure('expired', '原连接过期')
            return 'one', '旧会话里的账号'
        with patch.object(studio, '_verify_session', side_effect=verify):
            results = studio.check_login_states(Mock())
        self.assertEqual([r['status'] for r in results], ['expired', 'connected'])
        self.assertFalse(results[1]['added'])
        self.assertIsNone(results[1]['id'])
        self.assertEqual(studio.get_account(account['id'])['connection_status'], 'expired')
        self.assertEqual(len(studio.list_accounts()), 1)

    def test_empty_registry_returns_no_fabricated_success(self):
        self.assertEqual(studio.check_login_states(Mock()), [])

    def test_expired_bili_cannot_be_marked_connected_by_exporting_public_data(self):
        account = self.account('bili', credential=self.root / 'credential.json')
        with patch.object(studio, 'load_credentials', return_value={'cookie': 'private-cookie'}), \
             patch.object(studio, 'BiliClient') as client, \
             patch.object(studio, 'check_login', return_value={'ok': False, 'code': -101}):
            result = studio._export_one(studio.get_account(account['id']), self.root, Mock())
        self.assertFalse(result['ok'])
        client.return_value.space_videos.assert_not_called()
        self.assertEqual(studio.get_account(account['id'])['connection_status'], 'expired')

    def test_credential_access_denied_is_error_instead_of_expiration(self):
        account = self.account('bili', credential=self.root / 'credential.json')
        with patch.object(studio, 'load_credentials', side_effect=PermissionError('Permission denied')), \
             patch.object(studio, 'BiliClient') as client:
            result = studio.check_login_states(Mock())[0]
        client.assert_not_called()
        self.assertEqual(result['status'], 'error')
        self.assertIn('系统权限阻止', result['message'])
        self.assertEqual(studio.get_account(account['id'])['connection_status'], 'error')


if __name__ == '__main__':
    unittest.main()
