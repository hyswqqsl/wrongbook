# -*- coding: utf-8 -*-
"""删除题目连带图片清理回归测试（V1.0.3b → v1.2q 交叉引用契约）

覆盖契约：
  1. 裁剪图（crop）被【未处置草稿单】引用时保留（引用是活的，草稿可回看/继续提交）；
     无引用才删文件，并从草稿单同步清理坏引用（figure_crops[] / figure_crop）
  2. crop 被其他错题/专题例题引用时保留（to_book 与例题共享 topic_crops 文件，
     删其一不得丢另一方的图形——q20261106-111019 图形丢失事故根因）
  3. 原图（photo）被多题共享时：删其一，原图保留
  4. 最后一题删除后：原图无任何引用（含草稿单）→ 连带删除
  5. 原图仍被草稿单引用时保留（草稿单可回看整版）
  6. 删除不存在的题返回 False；返回值语义不变（True=已删）
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


def _mkfile(path, content=b"img"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _question(qid, photo, crop):
    return {"id": qid, "created_at": "2026-11-03 12:00:00", "mastered_at": None,
            "source": "测试卷", "type": "fill",
            "question": {"text": "题干", "choices": None, "figure_spec": None,
                         "figure_crop": crop},
            "printed_answer": None, "verified_answer": "42", "verify_status": "pass",
            "mistake_reason": {"kind": "careless", "note": ""},
            "analysis": {"chapter": "第一章 三角形", "knowledge_points": [], "pitfall": "",
                         "difficulty": 2},
            "similar": [], "practice": [], "photo": photo, "flags": {}}


class TestRemoveCleansImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)
        store.list_users()                      # 触发初始化（绑定路径到 tmp）
        self.photo = _mkfile(os.path.join(self.tmp, "photos", "sheetA.jpg"))
        self.crop_a = _mkfile(os.path.join(self.tmp, "crops", "sheetA_q1_0.png"))
        self.crop_b = _mkfile(os.path.join(self.tmp, "crops", "sheetA_q2_0.png"))
        store.add(_question("qa", self.photo, self.crop_a))
        store.add(_question("qb", self.photo, self.crop_b))
        store.add_sheet({"source": "测试卷", "photo_size": [100, 100], "provider_note": "",
                         "photo": self.photo,
                         "questions": [
                             {"no": 1, "type": "fill", "question": "题1", "choices": None,
                              "figure_boxes": [], "figure_crops": [self.crop_a, self.crop_b],
                              "printed_answer": None, "has_handwriting": False,
                              "selected": False, "reason": None, "edited": None,
                              "figure_mode": "crop", "figure_spec": None, "figure_svg": None,
                              "figure_note": None, "figure_manual_box": None,
                              "figure_crop": self.crop_a},
                             {"no": 2, "type": "fill", "question": "题2", "choices": None,
                              "figure_boxes": [], "figure_crops": [self.crop_b],
                              "printed_answer": None, "has_handwriting": False,
                              "selected": False, "reason": None, "edited": None,
                              "figure_mode": "crop", "figure_spec": None, "figure_svg": None,
                              "figure_note": None, "figure_manual_box": None,
                              "figure_crop": self.crop_b}]})

    def tearDown(self):
        store.set_test_data_dir(None)

    # ---------- 1+2+3：删 A（照片被 B 共享） ----------

    def test_crop_kept_while_pending_draft_references_it(self):
        """v1.2q：未处置草稿单仍引用 crop → 文件与引用都保留（引用是活的）"""
        self.assertTrue(store.remove("qa"))
        self.assertTrue(os.path.exists(self.crop_a))             # 草稿引用中 → 保留
        self.assertTrue(os.path.exists(self.photo))              # 原图被 qb 共享 → 保留
        self.assertTrue(os.path.exists(self.crop_b))
        sh = store.load_sheets()["sheets"][0]
        q1, q2 = sh["questions"]
        self.assertIn(self.crop_a, q1["figure_crops"])           # 引用不清（文件还在）
        self.assertEqual(q1["figure_crop"], self.crop_a)
        self.assertEqual([q["id"] for q in store.load()["questions"]], ["qb"])

    def test_crop_removed_when_no_reference(self):
        """v1.2q：草稿引用已不在（/已处置）→ 删题连带删 crop 并清理坏引用"""
        sh = store.load_sheets()["sheets"][0]
        sh["questions"][0]["figure_crops"] = [c for c in sh["questions"][0]["figure_crops"] if c != self.crop_a]
        sh["questions"][0]["figure_crop"] = None
        store.save_sheets({"sheets": [sh]})
        self.assertTrue(store.remove("qa"))
        self.assertFalse(os.path.exists(self.crop_a))            # 无任何引用 → 删文件
        self.assertTrue(os.path.exists(self.crop_b))             # 无关 crop 不受影响
        self.assertTrue(os.path.exists(self.photo))              # 原图被 qb 共享 → 保留

    def test_crop_kept_when_draft_question_disposed(self):
        """v1.2q：草稿题已处置（ingested_id）→ 其引用不阻塞删除；坏引用同步清理"""
        sh = store.load_sheets()["sheets"][0]
        sh["questions"][0]["ingested_id"] = "qa"                 # 已处置
        store.save_sheets({"sheets": [sh]})
        self.assertTrue(store.remove("qa"))
        self.assertFalse(os.path.exists(self.crop_a))            # 已处置引用不阻塞 → 删文件
        sh = store.load_sheets()["sheets"][0]
        self.assertNotIn(self.crop_a, sh["questions"][0]["figure_crops"])   # 坏引用已清
        self.assertIsNone(sh["questions"][0]["figure_crop"])

    # ---------- 4：删最后一题 → 原图连带删除 ----------

    def test_delete_last_question_removes_photo(self):
        store.remove("qa")
        sh = store.load_sheets()["sheets"][0]
        sh["photo"] = None                        # 移除草稿单引用（模拟草稿已不存在）
        for q in sh["questions"]:
            q["figure_crops"] = []
            q["figure_crop"] = None               # 同步移除全部 crop 引用
        store.save_sheets({"sheets": [sh]})
        self.assertTrue(store.remove("qb"))
        self.assertFalse(os.path.exists(self.crop_b))
        self.assertFalse(os.path.exists(self.photo))             # 原图无引用 → 连带删除

    # ---------- 5：原图被草稿引用时保留（多题删完后草稿还在的场景） ----------

    def test_photo_kept_while_draft_references_it(self):
        store.remove("qa")
        # 删 qb 前先把草稿单的 photo 引用改到独立副本模拟"草稿仍引用另一张原图"：
        sh = store.load_sheets()["sheets"][0]
        sh["photo"] = self.photo                 # 草稿引用 photo（q2 不再引用 crop_b 以外）
        store.save_sheets({"sheets": [sh]})
        store.remove("qb")
        self.assertTrue(os.path.exists(self.photo))              # 草稿仍引用 → 保留

    # ---------- 6：边界 ----------

    def test_remove_missing_returns_false(self):
        self.assertFalse(store.remove("nope"))
        self.assertEqual(len(store.load()["questions"]), 2)


if __name__ == "__main__":
    unittest.main()
