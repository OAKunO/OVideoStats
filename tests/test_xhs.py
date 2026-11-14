#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书采集测试。不触网、不开浏览器。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smi.xhs_browser import (  # noqa: E402
    DATA_URL, LOGIN_URL, NOTE_LIST_MARK, XhsBrowser, normalize_note,
)







class TestNormalizeNote(unittest.TestCase):
    def test_permission_marks_private_notes(self):
        self.assertEqual(normalize_note({'permission_code': 1, 'permission_msg': '仅自己可见'})['visibility'], 'private')
        self.assertEqual(normalize_note({'permission_code': 0, 'permission_msg': ''})['visibility'], 'public')
    def test_basic(self):
        note = normalize_note({
            "id": "abc", "title": "我的笔记", "publishTime": 1700000000,
            "statistics": {"viewCount": 1000, "likeCount": 50, "commentCount": 8,
                           "collectCount": 12, "shareCount": 3, "exposure": 5000,
                           "coverClickRate": 0.08, "increaseFans": 4,
                           "avgWatchTime": 12.5, "danmaku": 2},
        })
        self.assertEqual(note["item_id"], "abc")
        self.assertEqual(note["title"], "我的笔记")
        self.assertEqual(note["created"], 1700000000)
        self.assertEqual(note["play"], 1000)
        self.assertEqual(note["exposure"], 5000)
        self.assertEqual(note["like"], 50)
        self.assertEqual(note["comment"], 8)
        self.assertEqual(note["collect"], 12)
        self.assertEqual(note["share"], 3)
        self.assertEqual(note["follow"], 4)
        self.assertAlmostEqual(note["cover_ctr"], 0.08)
        self.assertEqual(note["avg_watch_time"], 12.5)
        self.assertEqual(note["danmaku"], 2)

    def test_alternate_id_and_title(self):
        note = normalize_note({"noteId": "n1", "displayTitle": "t"})
        self.assertEqual(note["item_id"], "n1")
        self.assertEqual(note["title"], "t")

    def test_tolerates_empty(self):
        note = normalize_note({})
        self.assertEqual(note["bvid"], "")
        self.assertIsNone(note["play"])


class FakeLocator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class FakePage:
    def __init__(self, text="", url=DATA_URL):
        self._text = text
        self.url = url

    def inner_text(self, selector):
        return self._text

    def locator(self, selector):
        return FakeLocator(0)


class TestLoginDetection(unittest.TestCase):
    """小红书的登录表单嵌在页面里、登录成功后也不跳 URL，所以判定只能看内容。"""

    def _b(self, text, url=DATA_URL):
        b = XhsBrowser(headless=True)
        b.page = FakePage(text=text, url=url)
        return b

    def test_login_form_means_logged_out(self):
        b = self._b("创作服务平台 短信登录 发送验证码 登录即同意 用户协议")
        self.assertFalse(b.is_logged_in())

    def test_dashboard_means_logged_in(self):
        b = self._b("创作服务平台 发布笔记 首页 笔记管理 数据看板")
        self.assertTrue(b.is_logged_in())

    def test_does_not_use_url(self):
        """登录成功后 SPA 原地换内容，URL 可能还停在 /login——不能据此判未登录。"""
        b = self._b("发布笔记 笔记管理 数据看板",
                    url="https://creator.xiaohongshu.com/login")
        self.assertTrue(b.is_logged_in())

    def test_unknown_page_is_conservatively_logged_out(self):
        """都没匹配到时宁可让用户重试，也别误判成已登录。"""
        b = self._b("加载中...")
        self.assertFalse(b.is_logged_in())

    def test_marketing_copy_does_not_false_positive(self):
        """登录页的营销文案里有「创作服务平台」，不能当已登录特征。"""
        b = self._b("创作服务平台 解锁创作者专属功能 让创作发布、数据分析更高效 短信登录")
        self.assertFalse(b.is_logged_in())


class TestUrls(unittest.TestCase):
    def test_api_mark(self):
        # 主要来源是「笔记管理」页的接口（不需要数据权限）
        self.assertIn("creator/note/user/posted", NOTE_LIST_MARK)

    def test_length_reads_from_video_info(self):
        """实测 video_info 是 dict({duration: 51})，不能整个塞进表格。"""
        self.assertEqual(normalize_note({"video_info": {"duration": 51}})["length"], 51)
        self.assertIsNone(normalize_note({"video_info": {}})["length"])
        self.assertEqual(normalize_note({"duration": 30})["length"], 30)

    def test_urls_point_at_creator_site(self):
        self.assertTrue(LOGIN_URL.startswith("https://creator.xiaohongshu.com"))
        self.assertTrue(DATA_URL.startswith("https://creator.xiaohongshu.com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
