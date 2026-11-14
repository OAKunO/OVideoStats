#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号采集测试。不触网、不开浏览器。

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smi.channels_browser import (  # noqa: E402
    AUTH_API_MARK, IMAGE_POST_LIST_URL, POST_API_MARK, POST_LIST_URL,
    ChannelsBrowser, normalize_post,
)






class TestNormalizePost(unittest.TestCase):
    def test_visible_type_marks_non_public_posts(self):
        self.assertEqual(normalize_post({'visibleType': 1})['visibility'], 'public')
        self.assertEqual(normalize_post({'visibleType': 2})['visibility'], 'private')
        self.assertEqual(normalize_post({'visibleType': 3})['visibility'], 'private')
        self.assertEqual(normalize_post({})['visibility'], 'unknown')
    def test_statistics_nested(self):
        post = normalize_post({
            "objectId": "abc123", "title": "我的第一个视频",
            "createTime": 1700000000,
            "statistics": {"playCount": 1000, "likeCount": 50,
                           "commentCount": 8, "forwardCount": 3, "favCount": 12},
        })
        self.assertEqual(post["item_id"], "abc123")
        self.assertEqual(post["bvid"], "abc123")
        self.assertEqual(post["title"], "我的第一个视频")
        self.assertEqual(post["created"], 1700000000)
        self.assertEqual(post["play"], 1000)
        self.assertEqual(post["like"], 50)
        self.assertEqual(post["comment"], 8)
        self.assertEqual(post["share"], 3)
        self.assertEqual(post["collect"], 12)

    def test_statistics_flat(self):
        """统计也可能平铺在顶层，不能只认 statistics。"""
        post = normalize_post({"objectId": "1", "play_count": 7, "like_count": 2})
        self.assertEqual((post["play"], post["like"]), (7, 2))

    def test_alternate_id_keys(self):
        for key in ("objectId", "object_id", "exportId", "export_id", "id"):
            post = normalize_post({key: "x"})
            self.assertEqual(post["item_id"], "x", key)

    def test_play_field_aliases(self):
        for key in ("playCount", "readCount", "viewCount", "exposureCount"):
            self.assertEqual(normalize_post({"statistics": {key: 5}})["play"], 5, key)

    def test_millisecond_timestamp(self):
        self.assertEqual(normalize_post({"createTime": 1700000000000})["created"],
                         1700000000)

    def test_whitespace_title_stripped(self):
        self.assertEqual(normalize_post({"title": "  标题  "})["title"], "标题")

    def test_tolerates_empty(self):
        post = normalize_post({})
        self.assertEqual(post["bvid"], "")
        self.assertIsNone(post["play"])
        self.assertEqual(post["title"], "")


class FakeLocator:
    def __init__(self, count, texts=None):
        self._count = count
        self._texts = texts or []

    def count(self):
        return self._count

    def all_inner_texts(self):
        return self._texts


class FakePage:
    """够用的假页面：按选择器返回命中数。基类的 count() 走的就是这条路。"""

    def __init__(self, counts=None, text="", url="https://channels.weixin.qq.com/platform"):
        self._counts = counts or {}
        self._text = text
        self.url = url
        self.closed = False

    def inner_text(self, selector):
        return self._text

    def locator(self, selector):
        return FakeLocator(self._counts.get(selector, 0), [self._text] if selector == 'body' else [])

    def is_closed(self):
        return self.closed

    def evaluate(self, script):
        return 'signed-in-account' if getattr(self, 'signed_in', False) else None


class TestChannelsLoginDetection(unittest.TestCase):
    """不能用「正文里出现『登录』」判断——助手导航里本来就有这类文案。"""

    def _browser(self, url, counts=None, text=""):
        b = ChannelsBrowser(headless=True)
        b.page = FakePage(counts=counts, text=text, url=url)
        return b

    def test_login_page_url_means_logged_out(self):
        b = self._browser("https://channels.weixin.qq.com/login.html")
        self.assertFalse(b.is_logged_in())

    def test_oauth_callback_is_not_yet_the_creator_dashboard(self):
        b = self._browser("https://channels.weixin.qq.com/platform/oauth-callback.html")
        self.assertFalse(b.is_logged_in())

    def test_platform_dashboard_markers_mean_logged_in(self):
        b = self._browser("https://channels.weixin.qq.com/platform",
                          text="作品管理 内容管理 数据中心 账号设置")
        self.assertTrue(b.is_logged_in())

    def test_current_account_summary_means_logged_in(self):
        b = self._browser("https://channels.weixin.qq.com/platform",
                          text="视频号 · 助手\n示例创作者\n视频号ID:\nsph123\n视频32\n关注者2342\n昨日数据")
        self.assertTrue(b.is_logged_in())

    def test_loading_page_is_not_assumed_logged_in(self):
        b = self._browser("https://channels.weixin.qq.com/platform")
        self.assertFalse(b.is_logged_in())

    def test_login_prompt_text_means_logged_out(self):
        b = self._browser("https://channels.weixin.qq.com/platform",
                          counts={"text=微信扫码登录": 1})
        self.assertFalse(b.is_logged_in())

    def test_navigation_word_alone_does_not_break_detection(self):
        """助手首页导航里有「登录视频号助手」，不该因此判定未登录。"""
        b = self._browser("https://channels.weixin.qq.com/platform",
                          text="视频号助手 登录视频号助手 内容管理 数据中心 账号设置")
        self.assertTrue(b.is_logged_in())

    def test_marketing_landing_page_is_not_logged_in(self):
        b = self._browser("https://channels.weixin.qq.com/platform",
                          text="一站式服务，让创作更简单。登录视频号助手 加载失败，点击重试 内容管理 数据中心")
        self.assertFalse(b.is_logged_in())

    def test_oauth_and_lookalike_hosts_cannot_supply_dashboard_markers(self):
        for url in ('https://open.weixin.qq.com/connect/qrconnect',
                    'https://channels.weixin.qq.com.example.com/platform',
                    'https://example.com/platform',
                    'https://channels.weixin.qq.com/login.html',
                    'http://channels.weixin.qq.com/platform'):
            self.assertFalse(self._browser(url, text='内容管理 数据中心').is_logged_in(), url)

    def test_wujie_multiple_bodies_and_video_manager_are_supported(self):
        b = self._browser('https://channels.weixin.qq.com/platform/post/list',
                          counts={'.post-feed-item': 3, 'text="视频管理"': 1}, text='视频管理')
        locator = FakeLocator(2, ['视频号助手', '视频管理 作品列表'])
        original = b.page.locator
        b.page.locator = lambda selector: locator if selector == 'body' else original(selector)
        self.assertEqual(b.body_text(), '视频号助手\n视频管理 作品列表')
        self.assertTrue(b.is_logged_in())


class TestChannelsLoginWait(unittest.TestCase):
    def browser(self, authorize_at=None):
        clock = {'now': 0}
        browser = ChannelsBrowser()
        page = FakePage(url='https://channels.weixin.qq.com/login.html', text='微信扫码登录')
        def wait(milliseconds):
            clock['now'] += milliseconds / 1000
            if authorize_at is not None and clock['now'] >= authorize_at:
                page.url = 'https://channels.weixin.qq.com/platform'
                page._text = '内容管理 数据中心'
        page.wait_for_timeout = wait
        page.evaluate = Mock(return_value='stale-finder-username')
        browser.page = page
        browser._ctx = SimpleNamespace(pages=[page], cookies=Mock(return_value=[{'name': 'sessionid'}, {'name': 'wxuin'}]))
        browser.goto = Mock()
        return browser, clock

    def test_phone_confirmation_completes_without_refreshing_qr(self):
        browser, clock = self.browser(authorize_at=12)
        qr = browser.page
        with patch('smi.channels_browser.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(browser.wait_for_login(timeout=30, poll=2))
        self.assertEqual(clock['now'], 12)
        self.assertIs(browser.page, qr)
        browser.goto.assert_not_called()
        browser._ctx.cookies.assert_not_called()
        qr.evaluate.assert_not_called()

    def test_stale_cookies_and_storage_do_not_redirect_or_complete_login(self):
        from smi.browser_base import NeedLogin
        browser, clock = self.browser()
        with patch('smi.channels_browser.time.monotonic', side_effect=lambda: clock['now']):
            with self.assertRaisesRegex(NeedLogin, '扫码登录超时'):
                browser.wait_for_login(timeout=8, poll=2)
        browser.goto.assert_not_called()
        browser._ctx.cookies.assert_not_called()
        browser.page.evaluate.assert_not_called()

    def test_new_authenticated_channels_tab_completes_without_touching_qr(self):
        browser, clock = self.browser()
        dashboard = FakePage(text='内容管理 数据中心')
        browser._ctx.pages.append(dashboard)
        with patch('smi.channels_browser.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(browser.wait_for_login(timeout=10))
        self.assertIs(browser.page, dashboard)
        browser.goto.assert_not_called()

    def test_oauth_popup_cannot_be_accepted_as_the_logged_in_dashboard(self):
        browser, clock = self.browser(authorize_at=4)
        qr = browser.page
        browser._ctx.pages.append(FakePage(url='https://open.weixin.qq.com/connect/qrconnect', text='内容管理 数据中心'))
        with patch('smi.channels_browser.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(browser.wait_for_login(timeout=10, poll=2))
        self.assertEqual(clock['now'], 4)
        self.assertIs(browser.page, qr)
        browser.goto.assert_not_called()

    def test_closed_qr_window_exits_with_an_actionable_message(self):
        from smi.browser_base import NeedLogin
        browser, _ = self.browser()
        browser.page.closed = True
        with self.assertRaisesRegex(NeedLogin, '扫码窗口已关闭'):
            browser.wait_for_login()

    def test_official_login_entry_switches_only_to_qr_without_confirming(self):
        from smi.channels_browser import LOGIN_URL
        browser, _ = self.browser()
        browser.page = Mock()
        browser.page.get_by_text.return_value.count.return_value = 1
        browser.page.get_by_text.return_value.first.is_visible.return_value = True
        browser.open_login()
        browser.goto.assert_called_once_with(LOGIN_URL, settle=2)
        browser.page.get_by_text.assert_called_once_with('使用其他头像、昵称或账号', exact=True)
        browser.page.get_by_text.return_value.first.click.assert_called_once_with(timeout=5000)


class TestUrls(unittest.TestCase):
    def test_both_list_pages_configured(self):
        """视频和图文是两套列表，只开一个会漏掉一半作品。"""
        from smi.channels_browser import LIST_URLS
        self.assertEqual(len(LIST_URLS), 2)
        self.assertIn(POST_LIST_URL, LIST_URLS)
        self.assertIn(IMAGE_POST_LIST_URL, LIST_URLS)

    def test_api_marks(self):
        self.assertIn("post_list", POST_API_MARK)
        self.assertIn("statistic", AUTH_API_MARK.replace("auth", "statistic"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
