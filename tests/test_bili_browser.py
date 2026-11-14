#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站浏览器登录测试。不触网、不开浏览器。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smi.bili_browser import SKIP_COOKIES, BiliBrowser  # noqa: E402


class FakeLocator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n

    def is_visible(self):
        return self._n > 0

    def click(self, **kw):
        if self._n == 0:
            raise RuntimeError("not clickable")


class FakePage:
    def __init__(self, counts=None, text="", url="https://www.bilibili.com/"):
        self._counts = counts or {}
        self._text = text
        self.url = url

    def inner_text(self, selector):
        return self._text

    def locator(self, selector):
        return FakeLocator(self._counts.get(selector, 0))


class FakeCtx:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


def browser_with(cookies=None, counts=None, text=""):
    b = BiliBrowser(headless=True)
    b.page = FakePage(counts=counts, text=text)
    b._ctx = FakeCtx(cookies or [])
    return b


class TestLoginDetection(unittest.TestCase):
    def test_avatar_means_logged_in(self):
        self.assertTrue(browser_with(counts={".header-avatar-wrap": 1}).is_logged_in())

    def test_no_avatar_means_logged_out(self):
        self.assertFalse(browser_with(counts={}).is_logged_in())

    def test_text_with_login_word_does_not_false_positive(self):
        """B站首页导航里本来就有「登录」字样，不能据此判定。"""
        b = browser_with(counts={}, text="首页 番剧 直播 登录 注册")
        self.assertFalse(b.is_logged_in())


class TestCookieString(unittest.TestCase):
    def test_builds_header(self):
        b = browser_with(cookies=[
            {"name": "SESSDATA", "value": "abc", "domain": ".bilibili.com"},
            {"name": "buvid3", "value": "xyz", "domain": ".bilibili.com"},
        ])
        ck = b.cookie_string()
        self.assertIn("SESSDATA=abc", ck)
        self.assertIn("buvid3=xyz", ck)

    def test_skips_other_domains(self):
        b = browser_with(cookies=[
            {"name": "SESSDATA", "value": "abc", "domain": ".bilibili.com"},
            {"name": "OTHER", "value": "nope", "domain": ".example.com"},
        ])
        ck = b.cookie_string()
        self.assertIn("SESSDATA=abc", ck)
        self.assertNotIn("OTHER", ck)

    def test_skips_noisy_cookies(self):
        b = browser_with(cookies=[
            {"name": "SESSDATA", "value": "abc", "domain": ".bilibili.com"},
            {"name": "bmg_af_sc", "value": "big", "domain": ".bilibili.com"},
        ])
        ck = b.cookie_string()
        self.assertNotIn("bmg_af_sc", ck)
        self.assertTrue(SKIP_COOKIES)

    def test_empty(self):
        self.assertEqual(browser_with(cookies=[]).cookie_string(), "")

    def test_cookie_names_sorted(self):
        b = browser_with(cookies=[
            {"name": "b", "value": "1", "domain": ".bilibili.com"},
            {"name": "a", "value": "2", "domain": ".bilibili.com"},
        ])
        self.assertEqual(b.cookie_names(), ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
