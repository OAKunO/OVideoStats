#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音浏览器路线测试。不触网、不开浏览器。

    python3 -m unittest discover -s tests -v
"""

import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smi.douyin_browser import (  # noqa: E402
    DouyinBrowser, DouyinBrowserError, is_public, parse_export_file,
)




class TestPublicVisibility(unittest.TestCase):
    """抖音对「自见」作品不计播放量，导出里就是 0。

    实测某个账号 101 条里 75 条是自见、播放全为 0。统计时必须排除，
    否则播放量中位数会被拉成 0，看着像程序坏了。
    """

    def test_public(self):
        self.assertTrue(is_public({"status": "公开"}))
        self.assertTrue(is_public({"status": ""}))

    def test_non_public(self):
        for s in ("自见", "私密", "仅自己可见", "审核中", "未通过", "已删除"):
            self.assertFalse(is_public({"status": s}), s)

    def test_missing_status_is_treated_as_public(self):
        self.assertTrue(is_public({}))

    def test_observed_distribution(self):
        """复现实测分布：公开的有播放，自见的一律 0。"""
        videos = ([{"status": "公开", "play": 100 * i} for i in range(1, 6)]
                  + [{"status": "自见", "play": 0} for _ in range(15)])
        public = [v for v in videos if is_public(v)]
        self.assertEqual(len(public), 5)
        self.assertTrue(all(v["play"] > 0 for v in public))


HEADERS = ["作品名称", "发布时间", "播放量", "点赞量", "评论量", "分享量", "收藏量",
           "完播率", "5s完播率", "封面点击率", "2s跳出率", "平均播放时长", "体裁"]
ROW1 = ["我的第一条视频", "2026-09-04 12:30:00", "1.2万", "3456", "12", "8", "20",
        "35.5%", "50.2%", "6.1%", "28%", "00:12", "视频"]
ROW2 = ["第二条", "2026-09-03", "500", "10", "-", "0", "1",
        "10%", "20%", "3%", "40%", "00:05", "图文"]


class TestParseExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="smi-dyx-")

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def _write_xlsx(self, rows, name="export.xlsx"):
        from openpyxl import Workbook
        path = os.path.join(self.tmp, name)
        wb = Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        wb.save(path)
        return path

    def _write_csv(self, rows, name="export.csv"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)
        return path

    def test_xlsx_basic(self):
        videos = parse_export_file(self._write_xlsx([HEADERS, ROW1, ROW2]))
        self.assertEqual(len(videos), 2)
        v = videos[0]
        self.assertEqual(v["title"], "我的第一条视频")
        self.assertEqual(v["play"], 12000)
        self.assertEqual(v["like"], 3456)
        self.assertEqual(v["comment"], 12)
        self.assertEqual(v["share"], 8)
        self.assertEqual(v["collect"], 20)
        self.assertAlmostEqual(v["finish_rate"], 0.355)
        self.assertAlmostEqual(v["cover_ctr"], 0.061)
        self.assertEqual(v["genre"], "视频")
        self.assertIsInstance(v["created"], int)

    def test_missing_value_becomes_none(self):
        videos = parse_export_file(self._write_xlsx([HEADERS, ROW2]))
        self.assertIsNone(videos[0]["comment"], "'-' 应转成 None 而不是 0")

    def test_header_not_on_first_row(self):
        """导出表前面常有说明文字，表头不一定在第一行。"""
        rows = [["数据导出时间：2026-09-28"], [], HEADERS, ROW1]
        videos = parse_export_file(self._write_xlsx(rows))
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["title"], "我的第一条视频")

    def test_blank_rows_skipped(self):
        videos = parse_export_file(self._write_xlsx([HEADERS, ROW1, [None] * len(HEADERS), ROW2]))
        self.assertEqual(len(videos), 2)

    def test_csv_supported(self):
        videos = parse_export_file(self._write_csv([HEADERS, ROW1]))
        self.assertEqual(videos[0]["play"], 12000)

    def test_unknown_columns_are_kept_raw(self):
        headers = HEADERS + ["某个新指标"]
        row = ROW1 + ["42"]
        videos = parse_export_file(self._write_xlsx([headers, row]))
        self.assertEqual(videos[0]["_raw"]["某个新指标"], "42")

    def test_no_recognizable_header_raises(self):
        with self.assertRaises(DouyinBrowserError) as ctx:
            parse_export_file(self._write_xlsx([["甲", "乙"], ["1", "2"]]))
        self.assertIn("表头", str(ctx.exception))

    def test_empty_file_raises(self):
        with self.assertRaises(DouyinBrowserError):
            parse_export_file(self._write_xlsx([]))

    def test_file_without_extension_is_sniffed(self):
        """抖音下载下来的文件没有扩展名，必须按内容识别。"""
        from openpyxl import Workbook
        path = os.path.join(self.tmp, "64a32e89-840e-4b64-ba5f-eb4e13d8b360")
        wb = Workbook()
        ws = wb.active
        for r in (HEADERS, ROW1):
            ws.append(r)
        wb.save(path)
        videos = parse_export_file(path)
        self.assertEqual(videos[0]["play"], 12000)

    def test_binary_garbage_raises(self):
        path = os.path.join(self.tmp, "junk.bin")
        with open(path, "wb") as fh:
            fh.write(b"\x00\x01\x02not a table at all")
        with self.assertRaises(DouyinBrowserError):
            parse_export_file(path)

    def test_new_columns_mapped(self):
        rows = [HEADERS + ["主页访问量", "粉丝增量"],
                ROW1 + ["12312", "64"]]
        videos = parse_export_file(self._write_xlsx(rows))
        self.assertEqual(videos[0]["profile_visits"], 12312)
        self.assertEqual(videos[0]["fans_delta"], 64)

    def test_bvid_falls_back_to_title(self):
        videos = parse_export_file(self._write_xlsx([HEADERS, ROW1]))
        self.assertEqual(videos[0]["bvid"], "我的第一条视频")


class FakePage:
    """够用的假页面：可配置选择器命中数、正文、evaluate 结果。"""

    def __init__(self, text="", counts=None, evaluate_result=None, url="https://x/"):
        self._text = text
        self._counts = counts or {}
        self._eval = evaluate_result if evaluate_result is not None else []
        self.url = url

    def inner_text(self, selector):
        return self._text

    def locator(self, selector):
        n = self._counts.get(selector, 0)
        return FakeLocator(n)

    def evaluate(self, script, *args):
        return self._eval

    def goto(self, *a, **kw):
        return None


class FakeLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._count > 0

    def click(self, **kw):
        if self._count == 0:
            raise RuntimeError("not clickable")


class TestLoginDetection(unittest.TestCase):
    def _browser(self, page):
        b = DouyinBrowser()
        b.page = page
        return b

    def test_logged_out_when_login_words_present(self):
        page = FakePage(text="扫码登录 验证码登录 密码登录",
                        counts={"text=作品管理": 1})
        self.assertFalse(self._browser(page).is_logged_in(),
                         "登录页也有导航文字，必须先看未登录特征")

    def test_logged_in_when_workspace_markers_present(self):
        page = FakePage(text="投稿作品 作品管理 数据总览",
                        counts={"text=投稿作品": 1})
        self.assertTrue(self._browser(page).is_logged_in())

    def test_not_logged_in_without_markers(self):
        page = FakePage(text="加载中...", counts={})
        self.assertFalse(self._browser(page).is_logged_in())

    def test_need_login_is_inverse(self):
        page = FakePage(text="扫码登录")
        self.assertTrue(self._browser(page).need_login())


class TestGrabLoginQr(unittest.TestCase):
    PNG_B64 = "data:image/png;base64," + (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def test_decodes_base64_png(self):
        b = DouyinBrowser()
        b.page = FakePage(evaluate_result=[{"kind": "img", "data": self.PNG_B64}])
        raw = b.grab_login_qr()
        self.assertTrue(raw.startswith(b"\x89PNG"), "应解出真正的 PNG 字节")

    def test_saves_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "qr.png")
            b = DouyinBrowser()
            b.page = FakePage(evaluate_result=[{"kind": "img", "data": self.PNG_B64}])
            b.grab_login_qr(save_to=path)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 50)

    def test_raises_when_no_qr(self):
        b = DouyinBrowser()
        b.page = FakePage(evaluate_result=[])
        with self.assertRaises(DouyinBrowserError) as ctx:
            b.grab_login_qr()
        self.assertIn("二维码", str(ctx.exception))


class TestWaitForLogin(unittest.TestCase):
    def test_returns_immediately_when_logged_in(self):
        b = DouyinBrowser()
        b.page = FakePage(text="投稿作品", counts={"text=投稿作品": 1})
        with mock.patch("smi.douyin_browser.time.sleep"):
            self.assertTrue(b.wait_for_login(timeout=5))

    def test_times_out_with_actionable_message(self):
        b = DouyinBrowser()
        b.page = FakePage(text="扫码登录")
        with mock.patch("smi.douyin_browser.time.sleep"), \
             mock.patch("smi.douyin_browser.time.time", side_effect=[0, 0, 999]):
            with self.assertRaises(DouyinBrowserError) as ctx:
                b.wait_for_login(timeout=5)
        self.assertIn("超时", str(ctx.exception))


class TestWaitDownloads(unittest.TestCase):
    def test_returns_new_stable_file(self):
        with tempfile.TemporaryDirectory() as d:
            b = DouyinBrowser(download_dir=d)
            b.page = FakePage()
            before = set()
            path = os.path.join(d, "作品数据.xlsx")
            with open(path, "wb") as fh:
                fh.write(b"x" * 5000)
            with mock.patch("smi.douyin_browser.time.sleep"):
                got = b._wait_downloads(before, timeout=5)
            self.assertEqual([os.path.basename(p) for p in got], ["作品数据.xlsx"])

    def test_ignores_hidden_files(self):
        with tempfile.TemporaryDirectory() as d:
            b = DouyinBrowser(download_dir=d)
            b.page = FakePage()
            with open(os.path.join(d, ".DS_Store"), "wb") as fh:
                fh.write(b"x")
            with mock.patch("smi.douyin_browser.time.sleep"), \
                 mock.patch("smi.douyin_browser.time.time", side_effect=[0, 0, 0, 999]):
                with self.assertRaises(DouyinBrowserError):
                    b._wait_downloads(set(), timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
