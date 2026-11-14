#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨模块契约检查。

起因：两次重构都靠线上实跑才发现问题——
  * `click_any(required=False)`：调用点按旧签名传参，基类没有这个参数
  * `self._downloads`：属性在改造 __init__ 时丢了，用时才 AttributeError

单元测试覆盖不到这些，因为它们只在特定代码路径上炸。这里用 AST 做静态检查，
让这类错误在跑测试时就暴露，而不是等实跑。
"""

import ast
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SMIDIR = pathlib.Path(__file__).resolve().parent.parent / "smi"


def method_names(class_node):
    """类里定义的方法名（self.count 这类访问指向的就是它们）。"""
    return {n.name for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def class_attrs(tree, class_node):
    """收集类里所有 self.<name> 用到的属性名（读或写）。"""
    used = set()
    for node in ast.walk(class_node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self":
            used.add(node.attr)
    return used


def assigned_in_init(class_node, names):
    """哪些属性在 __init__ 里被赋过值（含 self.x = ... 和 super().__init__ 之后）。"""
    assigned = set()
    for node in ast.walk(class_node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self" and isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.attr)
    return assigned & names


class TestBrowserSubclassAttributes(unittest.TestCase):
    """每个平台客户端用到的 self.<attr> 必须有着落。

    否则会拖到运行时才 AttributeError——这正是上次 `_downloads` 的翻版。
    """

    def test_all_self_attributes_are_defined(self):
        base = ast.parse((SMIDIR / "browser_base.py").read_text(encoding="utf-8"))
        base_cls = next(n for n in base.body
                        if isinstance(n, ast.ClassDef) and n.name == "BrowserSession")
        base_attrs = class_attrs(base, base_cls) | method_names(base_cls)

        problems = []
        for path in sorted(SMIDIR.glob("*_browser.py")):
            if path.name == "browser_base.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
                if not any(getattr(b, "id", getattr(b, "attr", "")) == "BrowserSession"
                           for b in cls.bases):
                    continue
                used = class_attrs(tree, cls)
                defined = (assigned_in_init(cls, used) | base_attrs
                           | method_names(cls))
                missing = sorted(used - defined)
                # 方法名和 Python 内置属性不算
                missing = [m for m in missing
                           if not m.startswith("__") and m not in ("page",)]
                if missing:
                    problems.append("%s.%s 用到但没定义: %s"
                                    % (path.name, cls.name, ", ".join(missing)))
        self.assertEqual(problems, [], "\n".join(problems))


class TestBaseMethodSignatures(unittest.TestCase):
    """子类覆盖基类方法时，签名要兼容，别把基类的关键字参数弄丢。"""

    def test_overrides_accept_base_parameters(self):
        import inspect
        from smi import browser_base
        import importlib

        base_methods = {
            name: inspect.signature(getattr(browser_base.BrowserSession, name))
            for name in ("goto", "click_any", "wait_for_login", "grab_login_qr",
                         "is_logged_in", "count", "body_text")
        }
        problems = []
        for mod_name in ("bili_browser", "channels_browser", "douyin_browser",
                         "xhs_browser"):
            mod = importlib.import_module("smi.%s" % mod_name)
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if not (inspect.isclass(obj) and issubclass(obj, browser_base.BrowserSession)):
                    continue
                if obj is browser_base.BrowserSession:
                    continue
                for mname, base_sig in base_methods.items():
                    if mname not in obj.__dict__:
                        continue
                    sub_sig = inspect.signature(getattr(obj, mname))
                    base_params = set(base_sig.parameters) - {"self"}
                    sub_params = set(sub_sig.parameters) - {"self"}
                    lost = base_params - sub_params
                    if lost:
                        problems.append("%s.%s 丢了基类参数: %s"
                                        % (obj.__name__, mname, ", ".join(sorted(lost))))
        self.assertEqual(problems, [], "\n".join(problems))


class TestSharedHelperImports(unittest.TestCase):
    """从 parse 用到的助手必须真的导入。

    重构时把 `_pick(` 批量改成 `pick(`，很容易漏掉导入——今天连中三次
    （unwrap / to_number / as_text），都是跑到线上才炸。
    """

    HELPERS = {"pick", "to_number", "to_ts", "as_text", "unwrap",
               "find_list", "metric", "cell"}

    def test_no_helper_used_without_import(self):
        problems = []
        for path in sorted(SMIDIR.glob("*.py")):
            if path.name == "parse.py":
                continue      # 定义处本身
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "parse":
                    imported |= {a.asname or a.name for a in node.names}
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            used |= {n.func.id for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            missing = sorted((used & self.HELPERS) - imported)
            if missing:
                problems.append("%s 用了但没导入: %s" % (path.name, ", ".join(missing)))
        self.assertEqual(problems, [], "\n".join(problems))


class TestNoStaleNames(unittest.TestCase):
    """模块之间引用的名字必须真实存在（重构改名后最容易漏）。"""

    def test_main_imports_resolve(self):
        import importlib
        main = importlib.import_module("smi.__main__")
        self.assertTrue(main)   # 导入成功即说明所有 from ... import 都解析得到

    def test_probes_and_cli_do_not_reference_removed_modules(self):
        removed = ("douyin_creator", "CreatorClient", "NeedCookie")
        hits = []
        for path in list(SMIDIR.glob("*.py")) + list((SMIDIR.parent / "tests").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name in ("parse.py", "test_module_contracts.py"):
                continue          # 这两个文件里提到这些名字是说明性文字
            for name in removed:
                if name in text:
                    hits.append("%s 仍引用 %s" % (path.name, name))
        self.assertEqual(hits, [], "\n".join(hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
