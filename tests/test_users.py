# -*- coding: utf-8 -*-
"""多用户回归测试（v0.7：按用户隔离题库/草稿单/照片）

覆盖契约：
  1. 首次初始化：默认用户「测试账号」，题库为空
  2. 旧数据迁移：data/wrongbook.json 等迁移进默认用户目录，JSON 内绝对路径同步改写
  3. 新建用户：题库为空并自动切换
  4. 隔离：A 写入的题对 B 不可见；切回 A 数据还在
  5. switch 不存在的用户 → ValueError
"""
import os
import sys
import json
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import store  # noqa: E402


class TestUsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)   # 之后首次 store 调用会在 tmp 重新初始化

    def tearDown(self):
        store.set_test_data_dir(None)

    # ---------- 初始化与迁移 ----------

    def test_first_init_creates_default_empty_user(self):
        u = store.list_users()
        self.assertEqual(u["current"], "test")
        self.assertEqual([x["name"] for x in u["users"]], ["测试账号"])
        self.assertEqual(store.load()["questions"], [])

    def test_legacy_data_migrated_with_path_rewrite(self):
        """旧全局 wrongbook.json/photos 迁入 users/test/，绝对路径同步改写"""
        legacy_photo = os.path.join(self.tmp, "photos", "abc.jpg")
        os.makedirs(os.path.join(self.tmp, "photos"), exist_ok=True)
        open(legacy_photo, "wb").write(b"jpeg")
        legacy_crop = os.path.join(self.tmp, "crops", "abc_q1_0.png")
        os.makedirs(os.path.join(self.tmp, "crops"), exist_ok=True)
        open(legacy_crop, "wb").write(b"png")
        store._save_users_raw  # noqa  仅确认函数存在
        # 伪造旧全局数据（迁移前的形态）
        os.makedirs(os.path.join(self.tmp, "users"), exist_ok=True)
        json.dump({"version": 1, "questions": [
            {"id": "q1", "photo": os.path.join(self.tmp, "photos", "abc.jpg"),
             "question": {"figure_crop": os.path.join(self.tmp, "crops", "abc_q1_0.png")}}]},
            open(os.path.join(self.tmp, "wrongbook.json"), "w", encoding="utf-8"),
            ensure_ascii=False)
        json.dump({"version": 1, "sheets": []},
                  open(os.path.join(self.tmp, "drafts.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        # 触发初始化
        store.set_test_data_dir(self.tmp)   # 重置 _INIT
        u = store.list_users()
        self.assertEqual(u["current"], "test")
        # 文件已迁入用户目录
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "users", "test", "wrongbook.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "users", "test", "photos", "abc.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wrongbook.json")))
        # 记录里的绝对路径已改写且指向存在的文件
        q = store.load()["questions"][0]
        self.assertIn(os.path.join("users", "test", "photos"), q["photo"])
        self.assertTrue(os.path.exists(q["photo"]))
        self.assertTrue(os.path.exists(q["question"]["figure_crop"]))

    # ---------- 新建与隔离 ----------

    def test_add_user_empty_bank_and_switch(self):
        store.list_users()
        store.add({"id": "qa1", "question": {"text": "甲用户的题"}})
        u = store.add_user("乙用户")
        self.assertEqual(u["current"].startswith("u"), True)
        self.assertEqual(store.load()["questions"], [])          # 新用户题库为空
        self.assertEqual(store.current_uid(), u["current"])

    def test_isolation_between_users(self):
        store.list_users()
        store.add({"id": "qa1", "question": {"text": "甲用户的题"}})
        store.add_user("乙用户")
        self.assertIsNone(store.get("qa1"))                     # 乙看不到甲的题
        first = store.list_users()["users"][0]["id"]
        store.switch_user(first)
        self.assertIsNotNone(store.get("qa1"))                  # 切回甲，数据还在

    def test_switch_unknown_user_raises(self):
        store.list_users()
        with self.assertRaises(ValueError):
            store.switch_user("no-such-uid")

    def test_sheet_store_routed_per_user(self):
        store.list_users()
        store.add_sheet({"id": "dA", "questions": []})
        store.add_user("乙用户")
        self.assertIsNone(store.get_sheet("dA"))
        self.assertEqual(store.load_sheets()["sheets"], [])


if __name__ == "__main__":
    unittest.main()
