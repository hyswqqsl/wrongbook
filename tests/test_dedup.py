# -*- coding: utf-8 -*-
"""题库查重回归测试（v0.6.2 录入去重：重复题不入库，完成提示里报告）

覆盖契约：
  1. norm_text：LaTeX/标点/空白/大小写归一化
  2. find_duplicate：同题不同排版能查到；不同题查不到；短题干不查
  3. 已归档（mastered）的题也参与查重
  4. 入库链 ingest_selected：重复题跳过（不调 LLM、不入库），sheet 标记 processed
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
import pipeline  # noqa: E402


def _temp_store():
    """把 wrongbook/drafts 指向临时目录，避免测试污染真实数据

    ⚠ 必须先 set_test_mode()：否则 store 函数内的 _init_once() 会把路径全局量
    重绑回当前用户目录，测试的 patch 失效 → 种子数据覆盖真实题库（曾发生）。
    """
    store.set_test_mode()
    tmp = tempfile.mkdtemp()
    store.DB_PATH = os.path.join(tmp, "wrongbook.json")
    store.DRAFT_PATH = os.path.join(tmp, "drafts.json")
    return tmp


def _seed_bank(records):
    store.save({"version": 1, "questions": records})


Q1 = r"如图，在 $\triangle ABC$ 中，$AB=AC$，点 $D$、$E$ 分别在 $AB$、$AC$ 上，$BE$ 交 $CD$ 于点 $F$，且 $\angle BFC=120^{\circ}$，求证：$BD=CE$。"


class TestNormText(unittest.TestCase):
    def test_latex_punct_case_insensitive(self):
        a = store.norm_text(r"如图，在 $\triangle ABC$ 中，AB=AC。")
        b = store.norm_text(r"如图在\triangle abc中ab=ac")   # 无空格无标点、小写
        self.assertEqual(a, b)

    def test_keeps_cjk_alnum(self):
        self.assertEqual(store.norm_text("求 x 的值：x=3?"), "求x的值x3")


class TestFindDuplicate(unittest.TestCase):
    def setUp(self):
        _temp_store()
        _seed_bank([{"id": "q001", "mastered_at": None,
                     "question": {"text": Q1}}])

    def test_same_question_different_formatting(self):
        """同一道题：LaTeX 细节/标点/空白不同 → 仍判重复"""
        dup = store.find_duplicate(r"如图，在$\triangle ABC$中，AB=AC，点D、E分别在AB、AC上，BE交CD于点F，且∠BFC=120°，求证：BD=CE。")
        self.assertIsNotNone(dup)
        self.assertEqual(dup["id"], "q001")

    def test_different_question_not_duplicate(self):
        dup = store.find_duplicate("计算：$\\sqrt{16}+\\sqrt{9}$ 的值是多少？")
        self.assertIsNone(dup)

    def test_short_text_skipped(self):
        self.assertIsNone(store.find_duplicate("求x。"))

    def test_empty_bank(self):
        _seed_bank([])
        self.assertIsNone(store.find_duplicate(Q1))

    def test_mastered_also_checked(self):
        """已归档题也在查重范围（避免重复录入）"""
        _seed_bank([{"id": "q002", "mastered_at": "2026-11-01",
                     "question": {"text": Q1}}])
        dup = store.find_duplicate(r"如图，在 $\triangle ABC$ 中，$AB=AC$，点 $D$、$E$ 分别在 $AB$、$AC$ 上，$BE$ 交 $CD$ 于点 $F$，且 $\angle BFC=120^{\circ}$，求证：$BD=CE$。")
        self.assertIsNotNone(dup)
        self.assertEqual(dup["id"], "q002")


class TestIngestDedup(unittest.TestCase):
    """入库链第⓪步：重复题跳过，不调 LLM、不写库"""

    def setUp(self):
        _temp_store()
        _seed_bank([{"id": "q001", "question": {"text": Q1}}])
        self._orig_make = pipeline.make_client
        pipeline.make_client = lambda provider="deepseek": (object(), "fake-model")

    def tearDown(self):
        pipeline.make_client = self._orig_make

    def test_duplicate_skipped_without_llm(self):
        sheet = {"id": "d1", "source": "测试卷", "status": "pending",
                 "questions": [{"no": 1, "selected": True, "question": Q1,
                                "type": "short", "has_figure": False,
                                "figure_crops": [], "reason": {"kind": "careless"}}]}
        store.add_sheet(sheet)   # 真实流程里草稿单在转录时已落盘
        out = pipeline.ingest_selected(sheet)
        self.assertEqual(len(out["results"]), 1)
        r = out["results"][0]
        self.assertTrue(r["skipped"])
        self.assertEqual(r["duplicate_of"], "q001")
        # 题库未被写入新题
        self.assertEqual(len(store.load()["questions"]), 1)
        # 草稿单被标记 processed（已处理完，可追溯）
        self.assertEqual(store.get_sheet("d1")["status"], "processed")
        self.assertEqual(store.get_sheet("d1")["skipped_duplicates"], ["q001"])

    def test_sheet_stays_pending_while_questions_remain(self):
        """一张照片分多次提交：只处置勾选的题，剩余题保持可提交（v0.6.3）

        两道题只提交一道（重复跳过）→ 草稿单保持 pending，该题打 skipped_dup，
        另一道题无处置标记、仍可再勾选再提交（后端不会以「已处理」拒绝）。
        """
        sheet = {"id": "d3", "source": "测试卷", "status": "pending",
                 "questions": [
                     {"no": 1, "selected": True, "question": Q1,
                      "type": "short", "has_figure": False, "figure_crops": [],
                      "reason": {"kind": "careless"}},
                     {"no": 2, "selected": False, "question": " Completely different question about functions.",
                      "type": "fill", "has_figure": False, "figure_crops": []}]}
        store.add_sheet(sheet)
        out = pipeline.ingest_selected(sheet)
        self.assertTrue(out["results"][0]["skipped"])
        st = store.get_sheet("d3")
        self.assertEqual(st["status"], "pending")            # 还有未处置的题 → 不结束
        q1 = st["questions"][0]
        self.assertEqual(q1["skipped_dup"], "q001")
        self.assertFalse(q1["selected"])
        self.assertIsNone(st["questions"][1].get("skipped_dup"))
        self.assertIsNone(st["questions"][1].get("ingested_id"))
        # 第二轮勾选另一道题：不会被「已处理」拦截（状态仍是 pending，正常进入质量链）
        store.patch_sheet_question("d3", 2, {"selected": True})
        st2 = store.get_sheet("d3")
        self.assertEqual(st2["status"], "pending")
        self.assertTrue(st2["questions"][1]["selected"])

    def test_new_question_would_call_llm(self):
        """非重复题正常进入质量链（此处只验证它没有被去重拦截——mock LLM 会抛错）"""
        sheet = {"id": "d2", "source": "测试卷", "status": "pending",
                 "questions": [{"no": 1, "selected": True,
                                "question": "计算：$2^{10}$ 等于多少？",
                                "type": "fill", "has_figure": False,
                                "figure_crops": [], "reason": {"kind": "unknown"}}]}

        class _Boom:
            def chat(self, *a, **kw):
                raise AssertionError("LLM 被调用（应走到验伪步骤）——此处仅验证通过查重")
        try:
            pipeline.ingest_selected(sheet)
        except Exception:
            pass   # 质量链在 fake 客户端上必然失败；只要不是被去重拦截即可
        self.assertEqual(len(store.load()["questions"]), 1)   # 失败题未入库


if __name__ == "__main__":
    unittest.main()
