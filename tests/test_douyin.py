#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音开放平台客户端测试。不触网。

    python3 -m unittest discover -s tests -v
"""

import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import smi.douyin as dy  # noqa: E402
from smi.douyin import (  # noqa: E402
    DouyinClient, DouyinError, NeedAuthorize, _envelope, _raise_if_error,
    authorize_url, load_config, parse_redirect, save_config,
)


def ok(data):
    return {"data": dict(data, error_code=0, description=""), "extra": {"logid": "x"}}


def err(code, desc=""):
    return {"data": {"error_code": code, "description": desc}, "extra": {}}


def route_client(routes, config=None):
    """构造一个 _get 被替换掉的客户端。"""
    cfg = dict(config or {"client_key": "k", "access_token": "tok",
                          "open_id": "oid", "refresh_token": "rt"})
    client = DouyinClient(cfg)

    def fake_get(url):
        for key, resp in routes.items():
            if key in url:
                return resp(url) if callable(resp) else resp
        raise AssertionError("未预设的 URL: %s" % url)

    return client, fake_get


class TestEnvelope(unittest.TestCase):
    def test_data_wrapped_error(self):
        code, desc, data = _envelope({"data": {"error_code": 2190008,
                                               "description": "过期"}})
        self.assertEqual(code, 2190008)
        self.assertEqual(desc, "过期")
        self.assertEqual(data["error_code"], 2190008)

    def test_top_level_error(self):
        code, desc, _ = _envelope({"err_no": 10008, "err_msg": "无效"})
        self.assertEqual((code, desc), (10008, "无效"))

    def test_plain_data_is_treated_as_success(self):
        code, _desc, data = _envelope({"data": {"list": [1, 2], "cursor": 5}})
        self.assertEqual(code, 0)
        self.assertEqual(data["cursor"], 5)

    def test_error_mapping(self):
        for code in (2190008, 10008, 10007):
            with self.assertRaises(NeedAuthorize):
                _raise_if_error(code, "x")
        with self.assertRaises(DouyinError) as ctx:
            _raise_if_error(2190002, "缺权限")
        self.assertNotIsInstance(ctx.exception, NeedAuthorize)
        _raise_if_error(0, "")          # 不应抛
        _raise_if_error(None, "")

    def test_error_message_includes_hint(self):
        with self.assertRaises(DouyinError) as ctx:
            _raise_if_error(2190002, "缺权限")
        self.assertIn("scope", str(ctx.exception))


class TestOAuthHelpers(unittest.TestCase):
    def test_authorize_url(self):
        url = authorize_url("KEY", "https://x/cb", ["user_info", "video.list"], "st")
        self.assertIn("client_key=KEY", url)
        self.assertIn("response_type=code", url)
        self.assertIn("scope=user_info%2Cvideo.list", url)
        self.assertIn("state=st", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fx%2Fcb", url)

    def test_parse_redirect_accepts_three_shapes(self):
        self.assertEqual(parse_redirect("https://x/cb?code=A&state=S"), ("A", "S"))
        self.assertEqual(parse_redirect("code=A&state=S"), ("A", "S"))
        self.assertEqual(parse_redirect("A"), ("A", None))
        self.assertEqual(parse_redirect("  'A'  "), ("A", None))
        self.assertEqual(parse_redirect(""), (None, None))
        self.assertEqual(parse_redirect(None), (None, None))

    def test_parse_redirect_with_extra_params(self):
        code, state = parse_redirect(
            "https://x/cb?scopes=user_info&code=ABC&state=st&extra=1")
        self.assertEqual((code, state), ("ABC", "st"))


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="smi-dy-")
        self.path = os.path.join(self.tmp, "douyin.json")

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def test_roundtrip(self):
        save_config({"client_key": "k", "access_token": "t"}, self.path)
        self.assertEqual(load_config(self.path)["access_token"], "t")

    def test_permissions_600(self):
        """client_secret 等同应用凭据，不能给别人读。"""
        save_config({"client_key": "k", "client_secret": "s"}, self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_missing_and_corrupt_return_empty(self):
        self.assertEqual(load_config(os.path.join(self.tmp, "nope.json")), {})
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(load_config(self.path), {})


class TestTokenLifecycle(unittest.TestCase):
    def test_absorb_token_sets_everything(self):
        c = DouyinClient({"client_key": "k"})
        c._absorb_token({"access_token": "a", "refresh_token": "r", "open_id": "o",
                         "expires_in": 86400 * 15, "refresh_expires_in": 86400 * 30})
        self.assertEqual(c.access_token, "a")
        self.assertEqual(c.open_id, "o")
        self.assertEqual(c.config["refresh_token"], "r")
        self.assertAlmostEqual(c.config["expires_at"], c.config["updated_at"] + 86400 * 15,
                               delta=2)

    def test_token_expired(self):
        c = DouyinClient({"access_token": "a", "expires_at": 0})
        self.assertFalse(c.token_expired(), "没有到期时间时不该误判为过期")
        c = DouyinClient({"access_token": "a", "expires_at": 1000})
        self.assertTrue(c.token_expired(), "早就过期了")

    def test_expiring_soon_counts_as_expired(self):
        import time as _t
        c = DouyinClient({"access_token": "a", "expires_at": int(_t.time()) + 60})
        self.assertTrue(c.token_expired(margin=3600), "1 小时内到期应提前刷新")

    def test_ensure_token_raises_without_any_token(self):
        with self.assertRaises(NeedAuthorize) as ctx:
            DouyinClient({"client_key": "k"}).ensure_token()
        self.assertIn("dy-login", str(ctx.exception))

    def test_refresh_updates_tokens(self):
        c = DouyinClient({"client_key": "k", "access_token": "old",
                          "refresh_token": "rt", "expires_at": 1})
        with mock.patch.object(dy, "_get", lambda url: ok(
                {"access_token": "new", "refresh_token": "rt2", "expires_in": 100,
                 "open_id": "o"})):
            self.assertTrue(c.ensure_token())
        self.assertEqual(c.access_token, "new")
        self.assertEqual(c.config["refresh_token"], "rt2")


class TestCallWithRefresh(unittest.TestCase):
    def test_retries_once_after_refresh(self):
        """接口报令牌过期 → 刷新 → 重试一次，不该直接失败。"""
        state = {"n": 0}

        def fake_get(url):
            if "refresh_token" in url:
                return ok({"access_token": "new", "refresh_token": "rt2",
                           "expires_in": 100, "open_id": "o"})
            state["n"] += 1
            if state["n"] == 1:
                return err(2190008, "access_token过期")
            return ok({"list": [], "cursor": 0, "has_more": False})

        c = DouyinClient({"client_key": "k", "access_token": "old",
                          "refresh_token": "rt", "open_id": "o", "expires_at": 0})
        with mock.patch.object(dy, "_get", fake_get):
            data = c._call("/video/list/", cursor=0, count=20)
        self.assertEqual(state["n"], 2, "应当重试一次")
        self.assertIn("list", data)

    def test_gives_up_after_second_failure(self):
        def fake_get(url):
            if "refresh_token" in url:
                return ok({"access_token": "new", "refresh_token": "rt2",
                           "expires_in": 100})
            return err(2190008, "还是过期")

        c = DouyinClient({"client_key": "k", "access_token": "old",
                          "refresh_token": "rt", "open_id": "o", "expires_at": 0})
        with mock.patch.object(dy, "_get", fake_get):
            with self.assertRaises(NeedAuthorize):
                c._call("/video/list/", cursor=0, count=20)


class TestVideoList(unittest.TestCase):
    def _pages(self, pages):
        state = {"n": 0}

        def fake_get(url):
            if "refresh_token" in url:
                return ok({})
            idx = min(state["n"], len(pages) - 1)
            state["n"] += 1
            return ok(pages[idx])

        return fake_get, state

    def test_pagination_follows_has_more(self):
        pages = [
            {"list": [{"item_id": "1", "title": "a", "statistics": {"play_count": 10,
                                                                   "digg_count": 1}}],
             "cursor": 1, "has_more": True},
            {"list": [{"item_id": "2", "title": "b", "statistics": {"play_count": 20,
                                                                   "digg_count": 2}}],
             "cursor": 2, "has_more": False},
        ]
        fake_get, state = self._pages(pages)
        c = DouyinClient({"client_key": "k", "access_token": "t", "open_id": "o"})
        with mock.patch.object(dy, "_get", fake_get), \
             mock.patch.object(dy.time, "sleep"):
            videos, total = c.video_list()
        self.assertEqual(total, 2)
        self.assertEqual([v["bvid"] for v in videos], ["1", "2"])
        self.assertEqual(state["n"], 2)

    def test_stops_at_max_pages(self):
        page = {"list": [{"item_id": "x", "statistics": {}}],
                "cursor": 1, "has_more": True}
        fake_get, state = self._pages([page])
        c = DouyinClient({"client_key": "k", "access_token": "t", "open_id": "o"})
        with mock.patch.object(dy, "_get", fake_get), \
             mock.patch.object(dy.time, "sleep"):
            c.video_list(max_pages=3)
        self.assertEqual(state["n"], 3, "应当正好取 3 页就停")

    def test_normalize_maps_fields(self):
        item = {"item_id": "99", "title": "  带空格的标题  ", "create_time": 1700000000,
                "is_top": True, "share_url": "https://v.douyin.com/x/",
                "statistics": {"play_count": 111, "digg_count": 22,
                               "comment_count": 3, "share_count": 4,
                               "forward_count": 5}}
        v = DouyinClient._normalize(item)
        self.assertEqual(v["bvid"], "99")
        self.assertEqual(v["title"], "带空格的标题")
        self.assertEqual(v["play"], 111)
        self.assertEqual(v["like"], 22)
        self.assertEqual(v["comment"], 3)
        self.assertEqual(v["url"], "https://v.douyin.com/x/")
        self.assertTrue(v["is_top"])

    def test_normalize_tolerates_missing_statistics(self):
        v = DouyinClient._normalize({"item_id": "1"})
        self.assertIsNone(v["play"])
        self.assertIsNone(v["like"])
        self.assertEqual(v["title"], "")


class TestVideoData(unittest.TestCase):
    def test_batches_of_twenty(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            return ok({"list": [{"item_id": "x"}]})

        c = DouyinClient({"client_key": "k", "access_token": "t", "open_id": "o"})
        with mock.patch.object(dy, "_get", fake_get), \
             mock.patch.object(dy.time, "sleep"):
            out = c.video_data(["%d" % i for i in range(45)])
        self.assertEqual(len(calls), 3, "45 个 id 应分成 3 批（20/20/5）")
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
