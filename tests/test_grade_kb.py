# -*- coding: utf-8 -*-
"""前置年级选择回归（v1.4.1）：录入页选年级 → 服务端按年级加载受控知识点清单

覆盖契约：
  1. grade_slug：年级名 → kp_<slug>.json 文件名 slug（四年级上册→4_1 / 九年级下册→9_2）
  2. grade_options：年级全集 json（小学+初中）key 序即候选序
  3. knowledge_points_for_grade：首次从全集 json 提取落盘 kp_*.json，之后直读缓存；
     未知年级/「其他」返回 None（调用方回落内置清单）
  4. classify_questions(grade=…)：LLM 输出 grade 被强制覆盖为前置年级（不再盲猜）；
     kps 仍受控收敛；无 grade 参数保留旧行为（非法 grade 回落八年级上册）
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

import kbtree  # noqa: E402
import reviewflow as rf  # noqa: E402

PRIMARY = {
    "四年级上册": {"grade": "四年级上册", "chapters": [
        {"title": "第一单元 大数的认识", "kps": ["亿以内数的读写", "数位与计数单位"]},
        {"title": "第三单元 角的度量", "kps": ["线段、直线、射线", "角的度量与分类"]}]},
    "四年级下册": {"grade": "四年级下册", "chapters": [
        {"title": "第五单元 三角形", "kps": ["三角形的特性", "三角形的内角和"]}]},
}
JUNIOR = {
    "八年级上册": {"grade": "八年级上册", "chapters": [
        {"title": "第一章 三角形", "kps": ["三角形的三边关系", "全等三角形的判定（SSS）"]}]},
    "九年级下册": {"grade": "九年级下册", "chapters": [
        {"title": "第二章 相似形", "kps": ["相似三角形的判定"]}]},
}


def _write_sources(d):
    json.dump(PRIMARY, open(os.path.join(d, kbtree.GRADE_SOURCES[0]), "w",
                            encoding="utf-8"), ensure_ascii=False)
    json.dump(JUNIOR, open(os.path.join(d, kbtree.GRADE_SOURCES[1]), "w",
                           encoding="utf-8"), ensure_ascii=False)


class TestGradeSlug(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(kbtree.grade_slug("四年级上册"), "4_1")
        self.assertEqual(kbtree.grade_slug("四年级下册"), "4_2")
        self.assertEqual(kbtree.grade_slug("一年级上册"), "1_1")
        self.assertEqual(kbtree.grade_slug("八年级上册"), "8_1")
        self.assertEqual(kbtree.grade_slug("九年级下册"), "9_2")

    def test_invalid(self):
        self.assertIsNone(kbtree.grade_slug("其他"))
        self.assertIsNone(kbtree.grade_slug(""))
        self.assertIsNone(kbtree.grade_slug("三年级第X册"))


class TestGradeKb(unittest.TestCase):
    """grade_options / knowledge_points_for_grade：全集 json → 按年级分文件"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_sources(self.tmp)
        kbtree.set_kb_dir(self.tmp)

    def tearDown(self):
        kbtree.set_kb_dir(None)

    def test_grade_options_order(self):
        self.assertEqual(kbtree.grade_options(),
                         ["四年级上册", "四年级下册", "八年级上册", "九年级下册"])

    def test_extract_and_cache_file(self):
        out = kbtree.knowledge_points_for_grade("四年级上册")
        self.assertEqual(out["grade"], "四年级上册")
        self.assertEqual([c["title"] for c in out["chapters"]],
                         ["第一单元 大数的认识", "第三单元 角的度量"])
        p = os.path.join(self.tmp, "kp_4_1.json")   # 按年级分文件落盘
        self.assertTrue(os.path.exists(p))
        self.assertEqual(json.load(open(p, encoding="utf-8")), out)
        # 二次调用直读缓存文件（把全集 json 拿走也不影响）
        os.remove(os.path.join(self.tmp, kbtree.GRADE_SOURCES[0]))
        self.assertEqual(kbtree.knowledge_points_for_grade("四年级上册"), out)

    def test_unknown_grade_returns_none(self):
        self.assertIsNone(kbtree.knowledge_points_for_grade("其他"))
        self.assertIsNone(kbtree.knowledge_points_for_grade(""))
        self.assertIsNone(kbtree.knowledge_points_for_grade("七年级上册"))   # 全集未收录
        self.assertIsNone(kbtree.knowledge_points_for_grade("乱写的年级"))

    def test_corrupt_source_json_skipped(self):
        """v1.4.1d：单个全集 json 损坏（编码坏/截断）→ 跳过不抛异常，另一份仍可用
        （曾致 grade_options 抛 UnicodeDecodeError → /api/grades 500 → 前端年级下拉全空）"""
        # 初中全集损坏 → 回落小学候选，不抛异常
        with open(os.path.join(self.tmp, kbtree.GRADE_SOURCES[1]), "wb") as f:
            f.write(b"\xff\xfe broken bytes")
        kbtree.grade_options(force=True)
        self.assertEqual(kbtree.grade_options(),
                         ["四年级上册", "四年级下册"])
        self.assertIsNone(kbtree.knowledge_points_for_grade("九年级下册"))
        self.assertIsNotNone(kbtree.knowledge_points_for_grade("四年级上册"))
        # 两份全坏 → 空列表（endpoint 会回落初中候选），仍不抛异常
        with open(os.path.join(self.tmp, kbtree.GRADE_SOURCES[0]), "wb") as f:
            f.write(b"{ truncated")
        self.assertEqual(kbtree.grade_options(force=True), [])


class TestClassifyWithGrade(unittest.TestCase):
    """classify_questions：前置年级固定（不再盲猜）；无 grade 保留旧行为"""

    CATS = [{"set_id": "ts1", "set_name": "初二中考必考专题", "cat_id": "tc1",
             "cat_label": "第1类 三边关系"}]
    KB = [{"kp": "三角形的三边关系", "chapter": "第一章 三角形"},
          {"kp": "三角形的内角和", "chapter": "第五单元 三角形"}]

    def test_grade_forced(self):
        captured = {}

        def fake_ask(client, model, prompt, uri, max_tokens=0):
            captured["prompt"] = prompt
            return {"questions": [
                {"no": 1, "grade": "九年级上册", "cat": "第1类 三边关系",
                 "kps": ["三角形的三边关系"]},          # LLM 瞎猜的年级必须被覆盖
                {"no": 2, "cat": "不存在的类别", "kps": []}]}

        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = fake_ask
        try:
            out = rf.classify_questions(None, None,
                                        [{"no": i, "question": "q"} for i in (1, 2)],
                                        cats=self.CATS, kb_kps=self.KB,
                                        grade="四年级下册")
        finally:
            rf.ask_json_vision = rf_ask
        self.assertEqual([c["grade"] for c in out], ["四年级下册", "四年级下册"])
        self.assertEqual(out[0]["kps"], ["三角形的三边关系"])   # kps 仍受控收敛
        # prompt：grade 规则固定为前置年级，不再列候选词表
        self.assertIn("固定为「四年级下册」", captured["prompt"])
        self.assertNotIn("九年级上册/其他", captured["prompt"])

    def test_legacy_no_grade(self):
        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = lambda *a, **kw: {"questions": [
            {"no": 1, "grade": "随便写的", "cat": "第1类 三边关系", "kps": []}]}
        try:
            out = rf.classify_questions(None, None, [{"no": 1, "question": "q"}],
                                        cats=self.CATS, kb_kps=self.KB)
        finally:
            rf.ask_json_vision = rf_ask
        self.assertEqual(out[0]["grade"], "八年级上册")   # 旧行为：非法 grade 回落八年级上册


if __name__ == "__main__":
    unittest.main()
