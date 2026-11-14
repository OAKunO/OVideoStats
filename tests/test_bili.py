#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线单元测试：不触网，用假响应验证解析、重试与错误处理。

    python3 -m unittest discover -s tests -v

真实接口的行为由 probes/ 下的探针负责验证，两者互补。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import smi.bili as _bili  # noqa: E402

# 测试必须隔离：把账号名缓存指向临时文件，绝不写进真实 data/ 目录
_TEST_TMP = tempfile.mkdtemp(prefix="smi-test-")
_bili.NAME_CACHE_FILE = os.path.join(_TEST_TMP, "account_names.json")

from smi.bili import (  # noqa: E402
    BiliClient, BiliError, NeedLogin, RISK_CODES, mixin_key, normalize_cookie,
    parse_bvid, parse_mid, strip_tags, to_int,
)

NAV_RESPONSE = {
    "code": -101, "message": "账号未登录",
    "data": {"isLogin": False, "wbi_img": {
        "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
    }},
}
SPI_RESPONSE = {"code": 0, "message": "0", "data": {"b_3": "BUV3-TEST", "b_4": "BUV4-TEST"}}
CARD_RESPONSE = {"code": 0, "data": {"card": {"mid": 123456789, "name": "示例创作者",
                                              "fans": 2875}}}
TOPARCS_RESPONSE = {"code": 0, "data": {"bvid": "BV1rGFszHEtb", "title": "置顶",
                                        "owner": {"mid": 123456789, "name": "示例创作者"}}}
NAVNUM_RESPONSE = {"code": 0, "data": {"video": 41, "article": 0}}


def make_video(i):
    return {
        "bvid": "BV1%09d" % i,
        "title": "第 %d 个视频" % i,
        "created": 1700000000 + i,
        "length": "03:12",
        "stat": {"view": 1000 * i, "comment": i, "like": 0},
    }


def make_page(videos, count):
    return {
        "code": 0, "message": "OK",
        "data": {"list": {"vlist": videos},
                 "page": {"count": count, "num": 1, "size": 30}},
    }


def make_search_item(i, mid=123456789, bvid=None):
    """故意把数值字段做成字符串，验证 to_int 的转换。"""
    return {
        "mid": mid,
        "bvid": bvid or "BV1search%03d" % i,
        "title": '<em class="keyword">示例创作者</em> 第 %d 条' % i,
        "pubdate": 1700000000 + i,
        "play": str(1000 * i),
        "like": i,
        "review": i * 2,
        "video_review": i * 3,
        "favorites": i * 4,
        "duration": "03:12",
    }


def make_search_page(items, num_results=None):
    return {"code": 0, "data": {"result": items,
                                "numResults": num_results if num_results is not None else len(items)}}


MOBILE_HTML = ("<html><head><title>示例创作者的个人空间-示例创作者个人主页-哔哩哔哩视频"
               "</title></head></html>").encode("utf-8")
EMPTY_HTML = b"<html><head><title></title></head></html>"

BASE_ROUTES = {
    "finger/spi": SPI_RESPONSE,
    "/x/web-interface/nav": NAV_RESPONSE,
    "m.bilibili.com/space": MOBILE_HTML,
}


class FakeClient(BiliClient):
    """按 URL 路由到预设响应，完全不触网。"""

    def __init__(self, routes):
        super().__init__(cookie="SESSDATA=fake", delay=(0, 0))
        self.routes = dict(BASE_ROUTES)
        self.routes.update(routes)
        self.calls = []

    def _resolve(self, url):
        self.calls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                if callable(resp):
                    return resp(url, len(self.calls))
                if isinstance(resp, int):
                    return resp, {"code": resp, "message": "blocked"}
                return 200, resp
        raise AssertionError("未预设的 URL: %s" % url)

    def _get(self, url, referer=None):
        return self._resolve(url)

    def _get_raw(self, url, referer=None, ua=None):
        """必须一起覆盖，否则测试会打到真实网络。"""
        status, payload = self._resolve(url)
        if isinstance(payload, bytes):
            return status, payload
        return status, json.dumps(payload).encode("utf-8")


class TestParse(unittest.TestCase):
    def test_parse_mid(self):
        for text in ("https://space.bilibili.com/987654321",
                     "https://space.bilibili.com/987654321/video?spm_id_from=333.999",
                     "987654321"):
            self.assertEqual(parse_mid(text), 987654321)

    def test_parse_mid_invalid(self):
        self.assertIsNone(parse_mid("https://www.bilibili.com/video/BV1nSa36nEMs/"))

    def test_parse_bvid(self):
        self.assertEqual(parse_bvid("BV1nSa36nEMs"), "BV1nSa36nEMs")
        self.assertEqual(
            parse_bvid("https://www.bilibili.com/video/BV1nSa36nEMs/?spm=1"),
            "BV1nSa36nEMs")

    def test_normalize_cookie(self):
        self.assertEqual(normalize_cookie("abc"), "SESSDATA=abc")
        self.assertEqual(normalize_cookie("buvid3=x; SESSDATA=abc"),
                         "buvid3=x; SESSDATA=abc")
        self.assertIsNone(normalize_cookie(None))

    def test_mixin_key_is_32_chars(self):
        mk = mixin_key("7cd084941338484aae1ad9425b84077c",
                       "4932caff0ff746eab6f01bf08b70ac45")
        self.assertEqual(mk, "ea1db124af3c7062474693fa704f4ff8")

    def test_strip_tags(self):
        self.assertEqual(strip_tags('<em class="keyword">示例创作者</em> 日常'),
                         "示例创作者 日常")
        self.assertEqual(strip_tags("a &amp; b"), "a & b")
        self.assertEqual(strip_tags(None), "")

    def test_to_int_handles_strings_and_dashes(self):
        self.assertEqual(to_int("12345"), 12345)
        self.assertEqual(to_int(12345), 12345)
        self.assertIsNone(to_int("--"))
        self.assertIsNone(to_int(None))


class TestVisitorCookie(unittest.TestCase):
    def test_visitor_cookie_acquired_automatically(self):
        c = FakeClient({})
        c.ensure_visitor()
        self.assertIn("buvid3=BUV3-TEST", c._cookie_header())

    def test_visitor_cookie_only_fetched_once(self):
        c = FakeClient({})
        c.ensure_visitor()
        c.ensure_visitor()
        self.assertEqual(sum("finger/spi" in u for u in c.calls), 1)

    def test_login_cookie_and_visitor_are_merged(self):
        c = FakeClient({})
        c.ensure_visitor()
        header = c._cookie_header()
        self.assertIn("buvid3=", header)
        self.assertIn("SESSDATA=fake", header)


class TestRetry(unittest.TestCase):
    def test_retries_on_risk_code_then_succeeds(self):
        state = {"n": 0}

        def flaky(url, n):
            state["n"] += 1
            if state["n"] < 3:
                return 200, {"code": -412, "message": "request was banned"}
            return 200, {"code": 0, "data": {"ok": True}}

        c = FakeClient({"target": flaky})
        with mock.patch("smi.bili.time.sleep"):
            _, data = c._get_retry("target")
        self.assertEqual(data.get("code"), 0)
        self.assertEqual(state["n"], 3, "应该重试到第 3 次成功")

    def test_gives_up_after_tries(self):
        c = FakeClient({"target": -412})
        with mock.patch("smi.bili.time.sleep"):
            _, data = c._get_retry("target", tries=3)
        self.assertEqual(data.get("code"), -412)
        self.assertEqual(sum("target" in u for u in c.calls), 3)

    def test_no_retry_for_normal_error(self):
        c = FakeClient({"target": {"code": -404, "message": "啥都木有"}})
        _, data = c._get_retry("target")
        self.assertEqual(data.get("code"), -404)
        self.assertEqual(len(c.calls), 1, "非风控错误不该重试")

    def test_risk_codes_include_the_ones_we_actually_hit(self):
        for code in (-412, -352, -799, -403):
            self.assertIn(code, RISK_CODES)


class TestAccountName(unittest.TestCase):
    def test_from_card(self):
        c = FakeClient({"/x/web-interface/card": CARD_RESPONSE})
        self.assertEqual(c.account_name(123456789), "示例创作者")

    def test_falls_back_to_top_arc_when_card_blocked(self):
        """card 被风控时，应改用置顶视频的作者名，而不是直接失败。"""
        c = FakeClient({"/x/web-interface/card": -412,
                        "/x/space/top/arc": TOPARCS_RESPONSE})
        with mock.patch("smi.bili.time.sleep"):
            self.assertEqual(c.account_name(123456789), "示例创作者")

    def test_returns_empty_when_all_paths_fail(self):
        _bili.save_name_cache({}, _bili.NAME_CACHE_FILE)   # 清掉用例间的缓存
        c = FakeClient({"/x/web-interface/card": -412, "/x/space/top/arc": -412,
                        "m.bilibili.com/space": EMPTY_HTML})
        with mock.patch("smi.bili.time.sleep"):
            self.assertEqual(c.account_name(123456789), "")

    def test_falls_back_to_mobile_page_html(self):
        """card 被单独限流时，移动端空间页的 HTML 里能抠出昵称。"""
        _bili.save_name_cache({}, _bili.NAME_CACHE_FILE)
        c = FakeClient({"/x/web-interface/card": -352})
        with mock.patch("smi.bili.time.sleep"):
            self.assertEqual(c.account_name(123456789), "示例创作者")
        self.assertTrue(any("m.bilibili.com/space" in u for u in c.calls))

    def test_html_fallback_ignores_bogus_name_field(self):
        """HTML 里第一个 "name" 往往是 Bilibili 之类，标题才是可靠来源。"""
        html = ('<html><head><title>oil欧呦的个人空间-oil欧呦个人主页</title>'
                '</head><body>"name":"Bilibili"</body></html>').encode("utf-8")
        _bili.save_name_cache({}, _bili.NAME_CACHE_FILE)
        c = FakeClient({"/x/web-interface/card": -352, "m.bilibili.com/space": html})
        with mock.patch("smi.bili.time.sleep"):
            self.assertEqual(c.account_name(13030183), "oil欧呦")

    def test_success_is_cached_and_reused(self):
        """拿到一次昵称就写缓存，之后不再去赌那个概率性风控的接口。"""
        _bili.save_name_cache({}, _bili.NAME_CACHE_FILE)
        c1 = FakeClient({"/x/web-interface/card": CARD_RESPONSE})
        self.assertEqual(c1.account_name(123456789), "示例创作者")
        self.assertEqual(_bili.load_name_cache().get("123456789"), "示例创作者")

        c2 = FakeClient({"/x/web-interface/card": -412})   # 这次接口是坏的
        self.assertEqual(c2.account_name(123456789), "示例创作者",
                         "应直接命中缓存，不该因接口风控而失败")
        self.assertEqual(len(c2.calls), 0, "命中缓存时不应发起任何请求")

    def test_no_pinned_video_is_not_a_failure(self):
        """账号没有置顶视频时 top/arc 返回 53016，这是正常情况而非风控。"""
        _bili.save_name_cache({}, _bili.NAME_CACHE_FILE)
        c = FakeClient({"/x/web-interface/card": -412,
                        "m.bilibili.com/space": EMPTY_HTML,
                        "/x/space/top/arc": {"code": 53016, "message": "没有置顶视频"}})
        with mock.patch("smi.bili.time.sleep"):
            self.assertEqual(c.account_name(123456789), "")


class TestSearchAccountVideos(unittest.TestCase):
    """不登录方案的主力路径。"""

    def test_parses_fields_and_filters_by_mid(self):
        items = [make_search_item(i) for i in range(1, 4)]
        items.append(make_search_item(99, mid=999))          # 别人的视频，应被过滤
        c = FakeClient({"/x/web-interface/search/type": make_search_page(items)})
        videos, num = c.search_account_videos(123456789, name="示例创作者")
        self.assertEqual(len(videos), 3, "应过滤掉不属于该 mid 的视频")
        self.assertEqual(videos[0]["play"], 1000, "字符串数值应转成 int")
        self.assertEqual(videos[0]["like"], 1)
        self.assertEqual(videos[0]["danmaku"], 3)
        self.assertEqual(videos[0]["favorite"], 4)
        self.assertEqual(videos[0]["title"], "示例创作者 第 1 条", "应去掉高亮标签")

    def test_pagination_and_dedup(self):
        page1 = make_search_page([make_search_item(i) for i in range(1, 21)], 25)
        page2 = make_search_page([make_search_item(i) for i in range(18, 26)], 25)
        state = {"n": 0}

        def route(url, n):
            state["n"] += 1
            return 200, (page1 if state["n"] == 1 else page2)

        c = FakeClient({"/x/web-interface/search/type": route})
        videos, num = c.search_account_videos(123456789, name="示例创作者")
        self.assertEqual(len(videos), 25, "重叠的应去重")
        self.assertEqual(num, 25)

    def test_stops_on_short_page(self):
        c = FakeClient({"/x/web-interface/search/type":
                        make_search_page([make_search_item(i) for i in range(1, 6)], 5)})
        videos, _ = c.search_account_videos(123456789, name="示例创作者")
        self.assertEqual(len(videos), 5)
        self.assertEqual(sum("search/type" in u for u in c.calls), 1, "不足一页就应停止")

    def test_raises_on_risk_control(self):
        c = FakeClient({"/x/web-interface/search/type": -412})
        with mock.patch("smi.bili.time.sleep"):
            with self.assertRaises(BiliError) as ctx:
                c.search_account_videos(123456789, name="示例创作者")
        self.assertIn("风控", str(ctx.exception))

    def test_not_flagged_truncated_when_naturally_exhausted(self):
        """结果自然翻完（最后一页不满 20）时，不能误报为「被截断」。"""
        page1 = make_search_page([make_search_item(i) for i in range(1, 21)], 39)
        page2 = make_search_page([make_search_item(i) for i in range(21, 40)], 39)
        state = {"n": 0}

        def route(url, n):
            state["n"] += 1
            return 200, (page1 if state["n"] == 1 else page2)

        c = FakeClient({"/x/web-interface/search/type": route})
        videos, _ = c.search_account_videos(123456789, name="示例创作者", expect=41)
        self.assertEqual(len(videos), 39)
        self.assertFalse(c.last_search_truncated,
                         "39 条是搜索的自然上限，不是翻页截断，不该告警")

    def test_flagged_truncated_when_hitting_page_cap(self):
        """通用名账号：每页只有零星几条是自己的，一直满页直到撞上限。"""
        def make_mixed_page(offset):
            items = [make_search_item(offset + j, mid=999) for j in range(17)]
            items += [make_search_item(offset + j) for j in range(3)]
            return make_search_page(items, 1000)

        state = {"n": 0}

        def route(url, n):
            state["n"] += 1
            return 200, make_mixed_page(state["n"] * 100)

        c = FakeClient({"/x/web-interface/search/type": route})
        videos, _ = c.search_account_videos(123456789, name="通用名",
                                            max_pages=3, expect=1248)
        self.assertEqual(len(videos), 9)
        self.assertTrue(c.last_search_truncated,
                        "满页却远没找齐、又撞上翻页上限，应告警结果不完整")

    def test_refuses_without_name(self):
        _bili.save_name_cache({}, _bili.NAME_CACHE_FILE)
        c = FakeClient({"/x/web-interface/card": -412, "/x/space/top/arc": -412,
                        "m.bilibili.com/space": EMPTY_HTML})
        with mock.patch("smi.bili.time.sleep"):
            with self.assertRaises(BiliError):
                c.search_account_videos(123456789)


class TestSpaceVideos(unittest.TestCase):
    def test_pagination_stops_at_total(self):
        """35 条 / 每页 30 条 → 应当取两页就停，而不是多打一页。"""
        pages = [make_page([make_video(i) for i in range(1, 31)], 35),
                 make_page([make_video(i) for i in range(31, 36)], 35)]
        state = {"n": 0}

        def route(url, n):
            idx = min(state["n"], len(pages) - 1)
            state["n"] += 1
            return 200, pages[idx]

        c = FakeClient({"/x/space/wbi/arc/search": route})
        videos, total = c.space_videos(987654321)
        self.assertEqual(total, 35)
        self.assertEqual(len(videos), 35)
        self.assertEqual(state["n"], 2, "应当只请求两页")

    def test_fields_mapped(self):
        c = FakeClient({"/x/space/wbi/arc/search": make_page([make_video(7)], 1)})
        videos, _ = c.space_videos(1)
        self.assertEqual(videos[0]["bvid"], "BV1000000007")
        self.assertEqual(videos[0]["play"], 7000)
        self.assertIsNone(videos[0]["like"], "列表接口不返回点赞量，应为 None")

    def test_412_becomes_need_login(self):
        c = FakeClient({"/x/space/wbi/arc/search": -412})
        with mock.patch("smi.bili.time.sleep"):
            with self.assertRaises(NeedLogin):
                c.space_videos(1)

    def test_nonzero_code_raises(self):
        c = FakeClient({"/x/space/wbi/arc/search": {"code": -404, "message": "打挂了"}})
        with self.assertRaises(BiliError) as ctx:
            c.space_videos(1)
        self.assertNotIsInstance(ctx.exception, NeedLogin)

    def test_empty_page_terminates(self):
        c = FakeClient({"/x/space/wbi/arc/search": make_page([], 0)})
        videos, total = c.space_videos(1)
        self.assertEqual(videos, [])
        self.assertEqual(total, 0)


class TestFillLikes(unittest.TestCase):
    def test_fill_likes_merges_play_and_like(self):
        detail = {"code": 0, "data": {
            "bvid": "BV1", "title": "t", "pubdate": 1,
            "owner": {"name": "up", "mid": 9},
            "stat": {"view": 500, "like": 50, "reply": 5, "favorite": 1,
                     "coin": 2, "share": 3, "danmaku": 4},
        }}
        c = BiliClient(cookie=None, delay=(0, 0))
        c._get = lambda url, referer=None: (200, detail)
        videos = [{"bvid": "BV1", "play": 999, "like": None}]
        c.fill_likes(videos)
        self.assertEqual(videos[0]["like"], 50)
        self.assertEqual(videos[0]["play"], 500, "应以 view 的权威值覆盖列表值")

    def test_error_does_not_abort_batch(self):
        c = BiliClient(cookie=None, delay=(0, 0))
        calls = {"n": 0}

        def fake_get(url, referer=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return 200, {"code": -404, "message": "啥都木有"}
            return 200, {"code": 0, "data": {"stat": {"view": 10, "like": 1}}}

        c._get = fake_get
        videos = [{"bvid": "A", "like": None}, {"bvid": "B", "like": None}]
        c.fill_likes(videos)
        self.assertIsNone(videos[0]["like"])
        self.assertIn("error", videos[0], "失败的条目应记录原因而不是抛出")
        self.assertEqual(videos[1]["like"], 1, "后续条目应继续处理")


if __name__ == "__main__":
    unittest.main(verbosity=2)
