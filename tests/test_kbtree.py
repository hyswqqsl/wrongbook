# -*- coding: utf-8 -*-
"""知识树回归测试（v0.7.1：教材 KB → 章/节/知识点树 + 转录期归类契约）

覆盖契约：
  1. build_tree：章/节/kp 三层结构，章序稳定，节须带编号
  2. 噪声过滤：非正式小节（课内练习/作业题等）不进树；kp 标题黑名单/前缀/长度
  3. kp 兜底：无有效知识点的节用节名自身作可选项
  4. classify_questions：LLM 输出归一化（非法 grade 回落八年级上册）
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
import reviewflow  # noqa: E402


def _write_kb(d, index):
    """按 kbtree.KB_FILES 的真实文件名写合成 KB（build_tree 按固定名加载）"""
    json.dump({"source_pdf": "test", "index": index, "chapters": []},
              open(os.path.join(d, kbtree.KB_FILES[0]), "w", encoding="utf-8"),
              ensure_ascii=False)


IDX = [
    {"chapter": "第一章 三角形", "section": "1.1 认识三角形", "kind": "knowledge",
     "title": "三角形内角和", "page": 3},
    {"chapter": "第一章 三角形", "section": "1.1 认识三角形", "kind": "knowledge",
     "title": "解", "page": 3},                       # 噪声：黑名单
    {"chapter": "第一章 三角形", "section": "课内练习", "kind": "knowledge",
     "title": "不应出现的小节", "page": 4},            # 噪声：非编号节
    {"chapter": "第一章 三角形", "section": "1.5 三角形全等的判定", "kind": "knowledge",
     "title": "例题解答（续）", "page": 8},            # 噪声：前缀
    {"chapter": "第二章 特殊三角形", "section": "2.2 等腰三角形", "kind": "knowledge",
     "title": "", "page": 12},                        # 空 title → 节名兜底
]


class TestKbTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_kb(self.tmp, IDX)
        kbtree.set_kb_dir(self.tmp)

    def tearDown(self):
        kbtree.set_kb_dir(None)

    def test_tree_structure(self):
        t = kbtree.build_tree()
        self.assertEqual([c["title"] for c in t["chapters"]],
                         ["第一章 三角形", "第二章 特殊三角形"])
        c1 = t["chapters"][0]
        self.assertEqual([s["title"] for s in c1["sections"]],
                         ["1.1 认识三角形", "1.5 三角形全等的判定"])

    def test_noise_filtered(self):
        t = kbtree.build_tree()
        kps = t["chapters"][0]["sections"][0]["kps"]
        self.assertEqual(kps, ["三角形内角和"])   # "解" 与非编号节都被过滤

    def test_section_name_fallback(self):
        """空 title 的节 → 节名自身作 kp 兜底"""
        t = kbtree.build_tree()
        s22 = t["chapters"][1]["sections"][0]
        self.assertEqual(s22["kps"], ["2.2 等腰三角形"])

    def test_compact_summary_contains_sections(self):
        text = kbtree.compact_summary()
        self.assertIn("1.1 认识三角形", text)
        self.assertIn("《第一章 三角形》", text)


class TestClassifyQuestions(unittest.TestCase):
    """classify_questions：LLM 输出归一化（不真调 LLM）"""

    def test_invalid_grade_falls_back(self):
        import reviewflow as rf
        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = lambda *a, **kw: {"questions": [
            {"no": 1, "grade": "随便写的", "chapter": "第一章 三角形",
             "section": "1.1 认识三角形", "kp": "三角形内角和"},
            {"no": 2, "grade": "九年级上册", "chapter": "", "section": "", "kp": ""},
            "非字典条目应被丢弃"]}
        try:
            out = rf.classify_questions(None, None,
                                        [{"no": 1, "question": "q1"}, {"no": 2, "question": "q2"}])
        finally:
            rf.ask_json_vision = rf_ask
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["grade"], "八年级上册")   # 非法 grade 回落
        self.assertEqual(out[1]["grade"], "九年级上册")
        self.assertEqual(out[1]["kp"], "")


if __name__ == "__main__":
    unittest.main()
