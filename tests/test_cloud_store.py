# -*- coding: utf-8 -*-
"""云端多租户存储层回归（Plan B B1-T1/T2）

覆盖契约：
  1. 双租户隔离：A 写题 B 不可见；同户不同成员（大宝/二宝）互相隔离
  2. 草稿单隔离与回读（data_json 整存整取，结构与桌面 JSON 一致）
  3. 专题树每户独立克隆（种子幂等）；练习记录按 (code, user_id) 隔离
  4. 未绑定租户直接抛错（防串户：宁可不服务）
  5. 索引列随保存维护（chapter/difficulty/reason_kind/mastered/norm_text）
"""
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import tenant_ctx  # noqa: E402
import store  # noqa: E402
import topics  # noqa: E402
import cloud_store  # noqa: E402

Q1 = "如图，在 $\\triangle ABC$ 中，AB=AC，求证∠B=∠C。"
Q2 = "计算：(2x+3)(x-1) 的展开结果。"


def _question(text, chapter="第二章 特殊三角形", difficulty=2):
    return {"type": "fill", "question": {"text": text, "choices": None},
            "analysis": {"chapter": chapter, "kp_path": [], "knowledge_points": [],
                         "difficulty": difficulty},
            "mistake_reason": {"kind": "careless"}}


class CloudTestBase(unittest.TestCase):
    def setUp(self):
        self._old_cloud = tenant_ctx.CLOUD
        tenant_ctx.CLOUD = True
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)
        cloud_store.ensure_account("WB-AA", "A家")
        cloud_store.ensure_account("WB-BB", "B家")

    def tearDown(self):
        tenant_ctx.CLOUD = self._old_cloud
        store.set_test_data_dir(None)


class TestQuestionsIsolation(CloudTestBase):

    def test_dual_tenant_isolation(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            rec = store.add(_question(Q1))
            self.assertEqual(len(store.load()["questions"]), 1)
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            self.assertEqual(store.load()["questions"], [])
            self.assertIsNone(store.get(rec["id"]))   # B 取不到 A 的题
        with tenant_ctx.bind_tenant("WB-AA", "u9"):   # 同户不同成员（二宝）
            self.assertEqual(store.load()["questions"], [])

    def test_update_remove_scoped(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            rec = store.add(_question(Q1))
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            self.assertIsNone(store.update(rec["id"], source="hack"))   # 越权更新无效
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            self.assertIsNone(store.get(rec["id"]).get("source"))
            self.assertTrue(store.remove(rec["id"]))

    def test_roundtrip_preserves_shape(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            rec = store.add(_question(Q1))
            rec2 = store.get(rec["id"])
            self.assertEqual(rec2["question"]["text"], Q1)
            self.assertEqual(rec2["analysis"]["chapter"], "第二章 特殊三角形")

    def test_find_duplicate_scoped(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            store.add(_question(Q1))
            dup = store.find_duplicate("如图，在 △ABC 中，AB=AC，求证∠B=∠C。")   # 去 LaTeX 后归一化相同
            self.assertIsNotNone(dup)
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            self.assertIsNone(store.find_duplicate("如图，在 △ABC 中，AB=AC，求证∠B=∠C。"))

    def test_unbound_tenant_raises(self):
        """cloud_store 数据函数未绑定租户直接抛错（store.load 未绑定时回落桌面 JSON，属 D-B2 设计）"""
        with self.assertRaises(RuntimeError):
            cloud_store.load_questions()

    def test_index_columns_maintained(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            rec = store.add(_question(Q2))
            cloud_store.save_questions(store.load())   # 触发整表重建
            import json
            from contextlib import closing
            with closing(cloud_store.connect()) as conn:
                row = conn.execute("SELECT * FROM questions WHERE qid=?", (rec["id"],)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["chapter"], "第二章 特殊三角形")
            self.assertEqual(row["difficulty"], 2)
            self.assertEqual(row["reason_kind"], "careless")
            self.assertEqual(row["mastered"], 0)
            self.assertTrue(row["norm_text"])


class TestSheetsIsolation(CloudTestBase):

    def test_sheet_scoped_and_roundtrip(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            sh = store.add_sheet({"source": "期中卷", "questions": [
                {"no": 1, "question": Q1, "selected": False}]})
            sid = sh["id"]
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            self.assertIsNone(store.get_sheet(sid))
            self.assertEqual(store.load_sheets()["sheets"], [])
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            got = store.get_sheet(sid)
            self.assertEqual(got["questions"][0]["question"], Q1)
            store.patch_sheet_question(sid, 1, {"selected": True})
            self.assertTrue(store.get_sheet(sid)["questions"][0]["selected"])
            store.set_sheet_status(sid, "processed")
            self.assertEqual(store.get_sheet(sid)["status"], "processed")


class TestTopicsPerAccount(CloudTestBase):

    def test_seed_cloned_independently(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            db_a = topics.load_topics()
            self.assertEqual(len(db_a["sets"]), 1)
            self.assertEqual(len(db_a["sets"][0]["categories"]), 10)   # 初二中考必考 10 类
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            db_b = topics.load_topics()
            self.assertNotEqual(db_a["sets"][0]["id"], db_b["sets"][0]["id"])   # 独立克隆
        # A 的修改不溢出到 B
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            topics.add_example(db_a["sets"][0]["id"], db_a["sets"][0]["categories"][0]["id"],
                               Q1, answer="B")
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            self.assertEqual(topics.load_topics()["sets"][0]["categories"][0]["examples"], [])

    def test_topic_crops_dir_per_code(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            d_a = topics._crops_dir()
        with tenant_ctx.bind_tenant("WB-BB", "u2"):
            d_b = topics._crops_dir()
        self.assertIn("WB-AA", d_a)
        self.assertIn("WB-BB", d_b)
        self.assertNotEqual(d_a, d_b)


class TestPracticePerUser(CloudTestBase):

    def test_practice_scoped_by_code_and_user(self):
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            db = topics.load_topics()
            ex = topics.add_example(db["sets"][0]["id"], db["sets"][0]["categories"][0]["id"],
                                    Q1, answer="B")
            r1 = topics.record_practice(ex["id"], True)
            self.assertEqual(r1["streak"], 1)
        with tenant_ctx.bind_tenant("WB-AA", "u2"):   # 二宝：未练过
            self.assertEqual(topics._load_practice()["practice"], {})
        with tenant_ctx.bind_tenant("WB-BB", "u1"):   # 他户同成员号：也未练过
            self.assertEqual(topics._load_practice()["practice"], {})


class TestSubThreadTenant(CloudTestBase):

    def test_background_thread_bind_tenant(self):
        """后台线程显式 bind_tenant → store 读写与主线程同租户（D-B2 核心）"""
        results = {}

        def work():
            with tenant_ctx.bind_tenant("WB-AA", "u1"):
                store.add(_question(Q1))
                results["n"] = len(store.load()["questions"])

        th = threading.Thread(target=work)
        th.start()
        th.join()
        self.assertEqual(results["n"], 1)
        with tenant_ctx.bind_tenant("WB-AA", "u1"):
            self.assertEqual(len(store.load()["questions"]), 1)


if __name__ == "__main__":
    unittest.main()
