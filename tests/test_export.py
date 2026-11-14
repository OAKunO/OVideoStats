#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出层测试：真写文件再读回来校验，不触网。

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smi.export import write_csv, write_xlsx  # noqa: E402

VIDEOS = [
    {"bvid": "BV1aaaaaaaaa", "title": "第一条：中文标题对齐",
     "created": 1700000000, "play": 1234567, "like": 98765,
     "comment": 321, "length": "03:12"},
    {"bvid": "BV1bbbbbbbbb", "title": "第二条",
     "created": 1700086400, "play": 892, "like": 12,
     "comment": 1, "length": "10:05"},
    {"bvid": "BV1cccccccccc", "title": "第三条 新发布",
     "created": 1700172800, "play": 0, "like": 0,
     "comment": 0, "length": "00:45"},
]


class TestXlsx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "out.xlsx")
        write_xlsx(VIDEOS, self.path, mid=987654321, total=3)

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def _load(self):
        from openpyxl import load_workbook
        return load_workbook(self.path)

    def test_file_is_real_xlsx(self):
        """xlsx 本质是 zip，能被 openpyxl 打开才算数。"""
        self.assertTrue(os.path.getsize(self.path) > 3000)
        wb = self._load()
        self.assertIn("视频明细", wb.sheetnames)
        self.assertIn("总览", wb.sheetnames)

    def test_headers_and_row_count(self):
        ws = self._load()["视频明细"]
        self.assertEqual(ws.cell(1, 1).value, "序号")
        self.assertEqual(ws.cell(1, 4).value, "播放量")
        self.assertEqual(ws.cell(1, 5).value, "点赞量")
        self.assertEqual(ws.max_row, 4, "1 行表头 + 3 行数据")
        self.assertEqual(ws.cell(2, 2).value, "第一条：中文标题对齐")

    def test_numbers_are_numbers_not_strings(self):
        """必须存成数字，否则在 Excel 里没法排序求和。"""
        ws = self._load()["视频明细"]
        for row in (2, 3, 4):
            self.assertIsInstance(ws.cell(row, 4).value, (int, float))
            self.assertIsInstance(ws.cell(row, 5).value, (int, float))
        self.assertEqual(ws.cell(2, 4).value, 1234567)
        self.assertEqual(ws.cell(2, 4).number_format, "#,##0")

    def test_rate_is_fraction_with_percent_format(self):
        ws = self._load()["视频明细"]
        rate = ws.cell(2, 6).value
        self.assertAlmostEqual(rate, 98765 / 1234567, places=6)
        self.assertEqual(ws.cell(2, 6).number_format, "0.00%")

    def test_zero_play_gives_blank_rate_not_crash(self):
        ws = self._load()["视频明细"]
        self.assertIsNone(ws.cell(4, 6).value, "0 播放不应算出点赞率")

    def test_hyperlink_present(self):
        ws = self._load()["视频明细"]
        cell = ws.cell(2, 10)
        self.assertIn("BV1aaaaaaaaa", cell.value)
        self.assertIsNotNone(cell.hyperlink)

    def test_freeze_and_filter(self):
        ws = self._load()["视频明细"]
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.auto_filter.ref, "A1:J4")

    def test_overview_stats(self):
        ov = self._load()["总览"]
        got = {}
        for r in range(1, ov.max_row + 1):
            k = ov.cell(r, 1).value
            if k:
                got[k] = ov.cell(r, 2).value
        self.assertEqual(got["账号 mid"], 987654321)
        self.assertEqual(got["视频总数"], 3)
        self.assertEqual(got["播放量 合计"], 1234567 + 892)
        self.assertEqual(got["播放量 中位数"], 892)
        self.assertEqual(got["播放量 最高"], 1234567)
        self.assertEqual(got["点赞量 合计"], 98765 + 12)


class TestExtraColumnsAndStats(unittest.TestCase):
    """抖音导出要带完播率等额外列，且统计口径可以和明细不同。"""

    def test_extra_columns_appended(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.xlsx")
            vids = [dict(VIDEOS[0], finish_rate=0.3, status="公开")]
            write_xlsx(vids, path, extra_columns=[("审核状态", "status"),
                                                  ("完播率", "finish_rate")])
            ws = load_workbook(path)["视频明细"]
            headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
            self.assertIn("审核状态", headers)
            self.assertIn("完播率", headers)
            self.assertEqual(ws.cell(2, headers.index("审核状态") + 1).value, "公开")
            self.assertEqual(ws.cell(2, headers.index("完播率") + 1).value, 0.3)

    def test_stats_videos_overrides_overview(self):
        """明细 101 条但只有 26 条公开时，中位数必须按 26 条算。"""
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.xlsx")
            videos = ([{"title": "公开", "play": 100 * i, "like": i} for i in range(1, 6)]
                      + [{"title": "自见", "play": 0, "like": 0} for _ in range(15)])
            public = videos[:5]
            write_xlsx(videos, path, stats_videos=public)
            ov = load_workbook(path)["总览"]
            got = {}
            for r in range(1, ov.max_row + 1):
                k = ov.cell(r, 1).value
                if k:
                    got[k] = ov.cell(r, 2).value
            self.assertEqual(got["本次统计条数"], 20)
            self.assertEqual(got["参与统计的条数"], 5)
            self.assertEqual(got["其中非公开"], 15)
            self.assertEqual(got["播放量 中位数"], 300)
            self.assertNotEqual(got["播放量 中位数"], 0)


class TestCsv(unittest.TestCase):
    def test_csv_has_bom_for_excel(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.csv")
            write_csv(VIDEOS, p)
            with open(p, "rb") as fh:
                self.assertTrue(fh.read(3) == b"\xef\xbb\xbf",
                                "带 BOM，Excel 打开中文才不乱码")
            with open(p, encoding="utf-8-sig") as fh:
                lines = fh.read().splitlines()
            self.assertIn("播放量", lines[0])
            self.assertEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
