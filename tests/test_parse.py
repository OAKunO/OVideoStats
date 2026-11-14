#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析助手测试。

这些助手原先在三个平台模块里各写一份、各测一遍。现在统一在 smi/parse.py，
测试也集中到这里——平台测试文件只保留各自特有的部分。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smi.parse import (  # noqa: E402
    as_text, cell, find_list, metric, pick, to_number, to_ts, unwrap,
)


class TestPick(unittest.TestCase):
    def test_first_non_empty_wins(self):
        self.assertEqual(pick({"b": 1, "a": 2}, "a", "b"), 2)

    def test_treats_empty_as_missing(self):
        """接口常用 ''/[]/{} 表示「没有」，不能当成有值。"""
        self.assertEqual(pick({"a": "", "b": 5}, "a", "b"), 5)
        self.assertEqual(pick({"a": [], "b": 5}, "a", "b"), 5)
        self.assertEqual(pick({"a": {}, "b": 5}, "a", "b"), 5)

    def test_default(self):
        self.assertIsNone(pick({}, "a"))
        self.assertEqual(pick({}, "a", default="x"), "x")

    def test_non_dict(self):
        self.assertIsNone(pick(None, "a"))


class TestToNumber(unittest.TestCase):
    def test_plain_and_separators(self):
        self.assertEqual(to_number(12), 12)
        self.assertEqual(to_number("1,234"), 1234)

    def test_chinese_units(self):
        self.assertEqual(to_number("1.2万"), 12000)
        self.assertEqual(to_number("1.5亿"), 150000000)

    def test_percent_becomes_fraction(self):
        self.assertAlmostEqual(to_number("12.5%"), 0.125)

    def test_floats_are_preserved(self):
        """点击率/完播率是小数，一律转 int 会归零——这是真踩过的静默 bug。"""
        self.assertEqual(to_number(0.08), 0.08)
        self.assertEqual(to_number(0.38), 0.38)
        self.assertIsInstance(to_number(5), int)
        self.assertIsInstance(to_number(5.0), int)

    def test_placeholders(self):
        for value in (None, "", "-", "--", "—", "暂无", "abc"):
            self.assertIsNone(to_number(value), repr(value))

    def test_bool_is_not_a_number(self):
        self.assertEqual(to_number(True), 1)


class TestToTs(unittest.TestCase):
    def test_seconds_passthrough(self):
        self.assertEqual(to_ts(1700000000), 1700000000)

    def test_milliseconds_downgraded(self):
        self.assertEqual(to_ts(1700000000000), 1700000000)

    def test_date_strings(self):
        self.assertIsInstance(to_ts("2026-09-04 12:30:00"), int)
        self.assertIsInstance(to_ts("2026-09-04"), int)
        self.assertIsInstance(to_ts("2026/09/04"), int)

    def test_unparseable_passthrough(self):
        self.assertEqual(to_ts("上周"), "上周")
        self.assertEqual(to_ts(None), None)


class TestAsText(unittest.TestCase):
    def test_string_stripped(self):
        self.assertEqual(as_text("  标题  "), "标题")

    def test_dict_extracts_known_keys(self):
        """实测抖音/小红书的标题字段有时是 dict。"""
        self.assertEqual(as_text({"title": "结构化标题"}), "结构化标题")
        self.assertEqual(as_text({"display_title": "d"}), "d")
        self.assertEqual(as_text({"desc": "d"}), "d")

    def test_nested_list(self):
        self.assertEqual(as_text([{"a": 1}, {"text": "命中"}]), "命中")

    def test_unusable_returns_empty(self):
        for value in (None, {}, {"foo": 1}, []):
            self.assertEqual(as_text(value), "", repr(value))


class TestUnwrap(unittest.TestCase):
    def test_single_layer(self):
        self.assertEqual(unwrap({"code": 0, "data": {"list": [1]}}), {"list": [1]})

    def test_double_layer(self):
        payload = {"code": 0, "data": {"result": {"notes": [1]}}}
        self.assertEqual(unwrap(payload), {"notes": [1]})

    def test_stops_when_not_dict(self):
        """data 直接是数组时不该被吃掉。"""
        payload = {"data": [1, 2]}
        self.assertEqual(unwrap(payload), payload)

    def test_non_dict_input(self):
        self.assertEqual(unwrap(None), {})
        self.assertEqual(unwrap([]), {})


class TestFindList(unittest.TestCase):
    def test_prefers_known_keys(self):
        for key in ("notes", "list", "post_list", "object_list", "items", "records"):
            self.assertEqual(find_list({key: [{"id": "1"}]}), [{"id": "1"}], key)

    def test_nested_envelope(self):
        payload = {"code": 0, "data": {"result": {"notes": [{"id": "x"}]}}}
        self.assertEqual(find_list(payload), [{"id": "x"}])

    def test_falls_back_to_id_bearing_list(self):
        self.assertEqual(find_list({"weird": [{"noteId": "9"}]}), [{"noteId": "9"}])

    def test_ignores_lists_without_ids(self):
        self.assertEqual(find_list({"rows": [{"a": 1}]}), [])

    def test_empty_and_missing(self):
        self.assertEqual(find_list({}), [])
        self.assertEqual(find_list({"list": []}), [])
        self.assertEqual(find_list(None), [])


class TestMetric(unittest.TestCase):
    KEYS = {"views": ("view_count", "viewCount", "view")}

    def test_nested_container(self):
        item = {"statistics": {"viewCount": 100}}
        self.assertEqual(metric(item, self.KEYS["views"]), 100)

    def test_flat(self):
        self.assertEqual(metric({"view_count": 55}, self.KEYS["views"]), 55)

    def test_alternate_container_names(self):
        for container in ("statistics", "stat", "stats", "metrics", "postStat"):
            self.assertEqual(metric({container: {"viewCount": 7}}, self.KEYS["views"]), 7, container)

    def test_missing(self):
        self.assertIsNone(metric({}, self.KEYS["views"]))


class TestCell(unittest.TestCase):
    def test_primitives_passthrough(self):
        for value in (None, "s", 1, 1.5, True):
            self.assertEqual(cell(value), value)

    def test_container_becomes_json(self):
        """Excel 只吃得下基本类型，dict/list 要转字符串而不是让导出崩掉。"""
        self.assertIn("duration", cell({"duration": 51}))
        self.assertIn("a", cell(["a", "b"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
