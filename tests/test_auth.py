#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录与凭据管理测试。不触网。

    python3 -m unittest discover -s tests -v
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import smi.auth as auth  # noqa: E402
from smi.auth import (  # noqa: E402
    CREDENTIAL_FILE, LoginError, _qr_matrix, check_login, clear_credentials,
    load_credentials, qrcode_login, render_qr_terminal, save_credentials,
    write_qr_file,
)

try:
    import qrcode  # noqa: F401
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="smi-cred-")
        self.path = os.path.join(self.tmp, "bilibili.json")

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def test_save_then_load(self):
        save_credentials("SESSDATA=abc; bili_jct=xyz", mid=123456789,
                         name="示例创作者", path=self.path)
        cred = load_credentials(self.path)
        self.assertEqual(cred["cookie"], "SESSDATA=abc; bili_jct=xyz")
        self.assertEqual(cred["mid"], 123456789)
        self.assertEqual(cred["name"], "示例创作者")
        self.assertIn("login_at", cred)

    def test_file_is_chmod_600(self):
        """服务器可能是多用户环境，凭据不能给别人读。"""
        save_credentials("SESSDATA=abc", path=self.path)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600, "凭据文件权限应为 600，实际 %o" % mode)

    def test_load_missing_returns_none(self):
        self.assertIsNone(load_credentials(os.path.join(self.tmp, "nope.json")))

    def test_load_corrupt_returns_none(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        self.assertIsNone(load_credentials(self.path))

    def test_load_without_cookie_field_returns_none(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"mid": 1}, fh)
        self.assertIsNone(load_credentials(self.path))

    def test_clear(self):
        save_credentials("SESSDATA=abc", path=self.path)
        self.assertTrue(clear_credentials(self.path))
        self.assertIsNone(load_credentials(self.path))
        self.assertFalse(clear_credentials(self.path), "重复删除应返回 False 而非抛异常")


class TestCredentialsAreNotCommitted(unittest.TestCase):
    """凭据文件绝不能被 git 追踪 —— 这是安全属性，必须由测试锁死。"""

    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_credentials_dir_is_gitignored(self):
        gi = os.path.join(self.root, ".gitignore")
        with open(gi, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("data/credentials/", content,
                      ".gitignore 必须排除 data/credentials/，否则账号凭据会被提交")

    def test_default_credential_path_is_inside_that_dir(self):
        self.assertTrue(str(CREDENTIAL_FILE).startswith("data/credentials/"),
                        "凭据默认路径应落在被忽略的目录内，实际 %s" % CREDENTIAL_FILE)

    def test_all_runtime_state_dirs_are_gitignored(self):
        """每一个会落盘「登录态或你自己的数据」的目录都必须被忽略。

        这类遗漏发生过两次：data/credentials/ 和 data/browser/（浏览器用户
        目录里存的就是登录态）。加这条测试，以后新增运行态目录时会立刻暴露。
        """
        gi = os.path.join(self.root, ".gitignore")
        with open(gi, encoding="utf-8") as fh:
            content = fh.read()
        for must in ("data/credentials/", "data/browser/", "data/downloads/",
                     "data/cache/", "reports/"):
            self.assertIn(must, content,
                          ".gitignore 缺少 %s —— 运行态数据会被提交" % must)


class TestLoadCookiePriority(unittest.TestCase):
    """load_cookie 优先级：显式参数 > 环境变量 > 扫码登录凭据。

    手写 cookie 文件那条 legacy 路径已删除——有了扫码登录它没有存在意义。
    """

    def setUp(self):
        import smi.bili as bili
        self.bili = bili
        self.tmp = tempfile.mkdtemp(prefix="smi-prio-")
        self.cred = os.path.join(self.tmp, "cred.json")

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def test_explicit_wins_over_everything(self):
        save_credentials("SESSDATA=from_file", path=self.cred)
        with mock.patch.object(auth, "CREDENTIAL_FILE", self.cred):
            self.assertEqual(self.bili.load_cookie("SESSDATA=explicit"),
                             "SESSDATA=explicit")

    def test_env_var_beats_credential_file(self):
        save_credentials("SESSDATA=from_file", path=self.cred)
        with mock.patch.object(auth, "CREDENTIAL_FILE", self.cred), \
             mock.patch.dict(os.environ, {"BILI_SESSDATA": "from_env"}, clear=False):
            self.assertEqual(self.bili.load_cookie(), "SESSDATA=from_env")

    def test_bare_sessdata_value_is_normalized(self):
        """只填 SESSDATA 的值也应能用。"""
        save_credentials("raw_value_without_key", path=self.cred)
        with mock.patch.object(auth, "CREDENTIAL_FILE", self.cred), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BILI_COOKIE", None)
            os.environ.pop("BILI_SESSDATA", None)
            self.assertEqual(self.bili.load_cookie(), "SESSDATA=raw_value_without_key")

    def test_credential_file_used(self):
        save_credentials("SESSDATA=from_file", path=self.cred)
        with mock.patch.object(auth, "CREDENTIAL_FILE", self.cred), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BILI_COOKIE", None)
            os.environ.pop("BILI_SESSDATA", None)
            self.assertEqual(self.bili.load_cookie(), "SESSDATA=from_file")

    def test_returns_none_when_nothing_available(self):
        with mock.patch.object(auth, "CREDENTIAL_FILE",
                               os.path.join(self.tmp, "missing.json")), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BILI_COOKIE", None)
            os.environ.pop("BILI_SESSDATA", None)
            self.assertIsNone(self.bili.load_cookie())


@unittest.skipUnless(HAS_QRCODE, "需要 qrcode 库（.venv/bin/pip install qrcode）")
class TestQrRendering(unittest.TestCase):
    """二维码渲染。

    这里有个真实踩过的坑：B站登录二维码点阵 53×53，若每个模块画两个字符
    需要 106 列，而标准终端只有 80 列——一旦自动换行，二维码就报废，
    而且从输出上完全看不出来。所以「绝不超出终端宽度」是必须守住的性质。
    """

    URL = ("https://account.bilibili.com/h5/account-h5/auth/scan-web"
           "?navhide=1&callback=close&qrcode_key=" + "0123456789abcdef" * 2)

    @staticmethod
    def _unrender(art, mode):
        """把字符画还原成点阵，用来验证映射没写反。"""
        if mode == "full":
            return [[ch == "\u2588" for ch in line[::2]] for line in art.splitlines()]
        out = []
        for line in art.splitlines():
            top, bottom = [], []
            for ch in line:
                top.append(ch in "\u2588\u2580")
                bottom.append(ch in "\u2588\u2584")
            out.append(top)
            out.append(bottom)
        return out

    def test_half_block_roundtrip_matches_matrix(self):
        art, mode = render_qr_terminal(self.URL, columns=80)
        self.assertEqual(mode, "half")
        matrix, _ = _qr_matrix(self.URL, border=2)
        back = self._unrender(art, mode)[:len(matrix)]
        self.assertEqual(back, matrix, "半块字符映射写错了，扫出来会是乱码")

    def test_full_block_roundtrip_matches_matrix(self):
        art, mode = render_qr_terminal(self.URL, columns=200)
        self.assertEqual(mode, "full")
        matrix, _ = _qr_matrix(self.URL, border=2)
        self.assertEqual(self._unrender(art, mode), matrix)

    def test_never_exceeds_terminal_width(self):
        for cols in (60, 80, 100, 130, 200):
            art, _mode = render_qr_terminal(self.URL, columns=cols)
            if art is None:
                continue
            widest = max(len(line) for line in art.splitlines())
            self.assertLessEqual(widest, cols,
                                 "cols=%d 时输出宽 %d，会换行并毁掉二维码" % (cols, widest))

    def test_too_narrow_reports_error_not_broken_qr(self):
        art, message = render_qr_terminal(self.URL, columns=20)
        self.assertIsNone(art, "宁可报错也不能输出一个会被换行毁掉的二维码")
        self.assertIn("列", message)

    def test_narrow_terminal_switches_to_half_block(self):
        narrow, mode_narrow = render_qr_terminal(self.URL, columns=80)
        wide, mode_wide = render_qr_terminal(self.URL, columns=200)
        self.assertEqual(mode_narrow, "half")
        self.assertEqual(mode_wide, "full")
        self.assertLess(len(narrow.splitlines()[0]), len(wide.splitlines()[0]))


@unittest.skipUnless(HAS_QRCODE, "需要 qrcode 库")
class TestWriteQrFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="smi-qr-")
        self.url = "https://example.com/login?qrcode_key=abc123"

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def test_svg(self):
        path = write_qr_file(self.url, os.path.join(self.tmp, "q.svg"))
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("<svg", content)
        self.assertIn("<path", content)

    def test_txt_keeps_raw_content(self):
        path = write_qr_file(self.url, os.path.join(self.tmp, "q.txt"))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.url)

    def test_creates_parent_directory(self):
        path = write_qr_file(self.url, os.path.join(self.tmp, "sub", "deep", "q.svg"))
        self.assertTrue(os.path.exists(path))
        os.remove(path)
        os.rmdir(os.path.join(self.tmp, "sub", "deep"))
        os.rmdir(os.path.join(self.tmp, "sub"))

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaises(LoginError) as ctx:
            write_qr_file(self.url, os.path.join(self.tmp, "q.gif"))
        self.assertIn("不支持", str(ctx.exception))


class TestCheckLogin(unittest.TestCase):
    class FakeClient:
        def __init__(self, response):
            self.response = response

        def _get(self, url, referer=None):
            if isinstance(self.response, Exception):
                raise self.response
            return 200, self.response

    def test_logged_in(self):
        c = self.FakeClient({"code": 0, "data": {"isLogin": True, "mid": 123456789,
                                                 "uname": "示例创作者"}})
        info = check_login(c)
        self.assertTrue(info["ok"])
        self.assertEqual(info["mid"], 123456789)
        self.assertEqual(info["name"], "示例创作者")

    def test_not_logged_in(self):
        c = self.FakeClient({"code": -101, "message": "账号未登录",
                             "data": {"isLogin": False}})
        info = check_login(c)
        self.assertFalse(info["ok"])
        self.assertEqual(info["code"], -101)

    def test_request_failure_is_not_an_exception(self):
        c = self.FakeClient(RuntimeError("网络断了"))
        info = check_login(c)
        self.assertFalse(info["ok"])
        self.assertIn("请求失败", info["message"])


class TestQrcodeLogin(unittest.TestCase):
    def _run(self, poll_sequence, **kw):
        """patch 掉网络，按顺序返回 poll 结果。"""
        generate = {"code": 0, "data": {"qrcode_key": "KEY123",
                                        "url": "https://example.com/qr"}}
        polls = list(poll_sequence)

        def fake_open(opener, url):
            if "generate" in url:
                return 200, generate
            return 200, polls.pop(0) if polls else polls

        with mock.patch.object(auth, "_open", fake_open), \
             mock.patch.object(auth.time, "sleep"):
            return qrcode_login(poll_interval=0, **kw)

    def test_success_reads_cookies_from_redirect_url(self):
        cookie, cookies = self._run([{
            "code": 0,
            "data": {"code": 0, "url": "https://www.bilibili.com/?SESSDATA=sess123"
                                     "&bili_jct=csrf456&DedeUserID=123456789"},
        }])
        self.assertIn("SESSDATA=sess123", cookie)
        self.assertIn("bili_jct=csrf456", cookie)
        self.assertEqual(cookies["DedeUserID"], "123456789")

    def test_polls_until_scanned(self):
        """先返回未扫码/待确认，最后一次成功——应继续轮询而不是提前放弃。"""
        cookie, _ = self._run([
            {"code": 0, "data": {"code": 86101}},                       # 未扫码
            {"code": 0, "data": {"code": 86090}},                       # 已扫码待确认
            {"code": 0, "data": {"code": 0, "url": "?SESSDATA=s"}},     # 成功
        ])
        self.assertIn("SESSDATA=s", cookie)

    def test_expired_qr_raises(self):
        with self.assertRaises(LoginError) as ctx:
            self._run([{"code": 0, "data": {"code": 86038}}])
        self.assertIn("过期", str(ctx.exception))

    def test_missing_sessdata_raises(self):
        with self.assertRaises(LoginError) as ctx:
            self._run([{"code": 0, "data": {"code": 0, "url": "https://x/?bili_jct=y"}}])
        self.assertIn("SESSDATA", str(ctx.exception))

    def test_generate_failure_raises(self):
        with mock.patch.object(auth, "_open",
                               lambda o, u: (200, {"code": -1, "message": "被拦了"})):
            with self.assertRaises(LoginError) as ctx:
                qrcode_login(poll_interval=0)
        self.assertIn("无法获取二维码", str(ctx.exception))

    def test_timeout_raises(self):
        with mock.patch.object(auth, "_open",
                               lambda o, u: (200, {"code": 0, "data": {"qrcode_key": "k",
                                                                       "url": "u"}}
                                             if "generate" in u
                                             else {"code": 0, "data": {"code": 86101}})), \
             mock.patch.object(auth.time, "sleep"), \
             mock.patch.object(auth.time, "time", side_effect=[0, 0, 999]):
            with self.assertRaises(LoginError) as ctx:
                qrcode_login(poll_interval=0, timeout=10)
        self.assertIn("超时", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
