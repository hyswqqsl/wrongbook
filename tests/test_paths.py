# -*- coding: utf-8 -*-
"""R1 数据外置改造回归：paths 目录解析 / 原子写加固 / meta 版本钩子 / KB 播种"""
import os, sys, json, unittest, tempfile, shutil
from unittest import mock

import paths
import kbtree as kbtree_mod
import store


class TestPathsResolution(unittest.TestCase):
    """目录解析优先级：环境变量 > frozen 平台目录 > 项目内 data/"""

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("WRONGBOOK_DATA")
            try:
                os.environ["WRONGBOOK_DATA"] = d
                self.assertEqual(paths.data_home(), os.path.abspath(d))
            finally:
                if old is None:
                    os.environ.pop("WRONGBOOK_DATA", None)
                else:
                    os.environ["WRONGBOOK_DATA"] = old

    def test_frozen_platform_dirs(self):
        cases = [
            # 期望值用宿主 os.path.join 拼（测的是目录解析逻辑，不测平台分隔符风格）
            ("win32", {"APPDATA": r"D:\AppData"},
             os.path.join(r"D:\AppData", "WrongBook", "data")),
            ("darwin", {},
             os.path.join(os.path.expanduser("~"), "Library", "Application Support", "WrongBook", "data")),
        ]
        for plat, envs, expect in cases:
            old_env = {k: os.environ.get(k) for k in envs}
            try:
                os.environ.update(envs)
                with mock.patch.object(paths.sys, "platform", plat), \
                     mock.patch.object(paths.sys, "frozen", True, create=True):
                    self.assertEqual(paths.data_home(), expect)
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_dev_default_is_project_data(self):
        old = os.environ.pop("WRONGBOOK_DATA", None)
        try:
            with mock.patch.object(paths.sys, "frozen", False, create=True):
                self.assertEqual(paths.data_home(),
                                 os.path.join(paths.app_root(), "data"))
        finally:
            if old is not None:
                os.environ["WRONGBOOK_DATA"] = old


class TestAtomicWrite(unittest.TestCase):
    """atomic_write_json：fsync + .bak 三代轮换 + 原子替换"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = os.path.join(self.tmp, "sub", "data.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, n):
        paths.atomic_write_json(self.p, {"n": n})

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_creates_and_reads(self):
        self._write(1)
        self.assertEqual(self._read(self.p), {"n": 1})
        self.assertFalse(os.path.exists(self.p + ".tmp"))   # 无残留临时文件

    def test_bak_rotation_three_generations(self):
        versions = [{"version": 1, "questions": [{"id": "q%d" % i}]} for i in range(4)]
        for db in versions:   # 写 4 次
            paths.atomic_write_json(self.p, db)
        self.assertEqual(self._read(self.p)["questions"][0]["id"], "q3")       # 当前
        self.assertEqual(self._read(self.p + ".bak")["questions"][0]["id"], "q2")    # 上一版
        self.assertEqual(self._read(self.p + ".bak.1")["questions"][0]["id"], "q1")
        self.assertEqual(self._read(self.p + ".bak.2")["questions"][0]["id"], "q0")
        self.assertFalse(os.path.exists(self.p + ".bak.3"))   # 只留 3 代


class TestEnsureMeta(unittest.TestCase):
    """data/meta.json 版本钩子：首写、幂等、降级保护"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _meta(self):
        with open(os.path.join(self.tmp, "meta.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_first_write_and_idempotent(self):
        paths.ensure_meta(self.tmp)
        self.assertEqual(self._meta()["schema"], paths.DATA_SCHEMA_VERSION)
        paths.ensure_meta(self.tmp)
        self.assertEqual(self._meta()["schema"], paths.DATA_SCHEMA_VERSION)

    def test_upgrade_path(self):
        with open(os.path.join(self.tmp, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 0}, f)   # 模拟存量老目录（无 meta 文件时同样 v=0）
        paths.ensure_meta(self.tmp)
        self.assertEqual(self._meta()["schema"], paths.DATA_SCHEMA_VERSION)

    def test_downgrade_guard(self):
        with open(os.path.join(self.tmp, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 99}, f)
        m = paths.ensure_meta(self.tmp, version=paths.DATA_SCHEMA_VERSION)
        self.assertIn("schema_warn", m)      # 提示回退风险
        self.assertEqual(self._meta()["schema"], 99)   # 不写回降级


class TestStoreIntegration(unittest.TestCase):
    """store 写入链走 paths 收口后行为不变（set_test_data_dir 场景）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)

    def tearDown(self):
        store.set_test_data_dir(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_rotates_bak(self):
        store.set_test_mode()
        store.DB_PATH = os.path.join(self.tmp, "wrongbook.json")
        store.save({"version": 1, "questions": []})
        store.save({"version": 1, "questions": [{"id": "q1"}]})
        store.save({"version": 1, "questions": [{"id": "q1"}, {"id": "q2"}]})
        store.save({"version": 1, "questions": [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]})
        self.assertTrue(os.path.exists(store.DB_PATH + ".bak"))
        self.assertTrue(os.path.exists(store.DB_PATH + ".bak.1"))
        with open(store.DB_PATH + ".bak.1", encoding="utf-8") as f:   # 上上版 = [q1]
            self.assertEqual(json.load(f)["questions"][0]["id"], "q1")
        self.assertFalse(os.path.exists(store.DB_PATH + ".bak.3"))

    def test_meta_written_on_init(self):
        rec = store.add({"type": "single", "question": {"text": "R1 meta 钩子"}})
        self.assertTrue(rec["id"])
        with open(os.path.join(self.tmp, "meta.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["schema"], paths.DATA_SCHEMA_VERSION)


class TestKbSeeding(unittest.TestCase):
    """kbtree KB 目录：数据目录缺失时从随包种子播种（打包态场景模拟）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seed_copied_into_empty_data_home(self):
        src = os.path.join(paths.app_root(), "data", "kb")
        if not os.path.isdir(src):   # 无种子环境跳过（不伪失败）
            self.skipTest("项目内无 KB 种子")
        with unittest.mock.patch.object(paths, "DATA_HOME", self.tmp):
            d = kbtree_mod._kb_dir()
            self.assertEqual(d, os.path.join(self.tmp, "kb"))
            for f in os.listdir(src):
                self.assertTrue(os.path.exists(os.path.join(d, f)), f)
            # 幂等：文件已存在不覆盖（手编清单保留）
            kp = os.path.join(d, "knowledge_points.json")
            if os.path.exists(kp):
                with open(kp, "w", encoding="utf-8") as f:
                    json.dump({"grade": "手编标记"}, f)
                kbtree_mod._kb_dir()
                with open(kp, encoding="utf-8") as f:
                    self.assertEqual(json.load(f).get("grade"), "手编标记")


if __name__ == "__main__":
    unittest.main()
