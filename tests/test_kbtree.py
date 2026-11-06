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


class TestKnowledgePoints(unittest.TestCase):
    """v1.2i：精简细分知识点清单（从 KB 提炼落盘，之后直读不再解析大 json）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_kb(self.tmp, IDX)
        kbtree.set_kb_dir(self.tmp)

    def tearDown(self):
        kbtree.set_kb_dir(None)

    def test_extract_and_cache(self):
        out = kbtree.knowledge_points()
        self.assertEqual([c["title"] for c in out["chapters"]],
                         ["第一章 三角形", "第二章 特殊三角形"])
        all_kps = [k for c in out["chapters"] for k in c["kps"]]
        self.assertIn("三角形内角和", all_kps)   # 假 KB 里的知识点条目
        self.assertEqual(len(all_kps), len(set(all_kps)))   # 全局去重
        # 落盘 + 二次调用直读缓存文件（不再碰大 json）
        p = os.path.join(self.tmp, "knowledge_points.json")
        self.assertTrue(os.path.exists(p))
        self.assertEqual(os.path.getsize(p) < 10 * 1024, True)   # 精简（远小于大 json）
        out2 = kbtree.knowledge_points()
        self.assertEqual(out2, out)


class TestClassifyQuestions(unittest.TestCase):
    """classify_questions：LLM 输出归一化（不真调 LLM）—— v1.2b/i 专题大类 + KB 细分知识点"""

    CATS = [{"set_id": "ts1", "set_name": "初二中考必考专题", "cat_id": "tc1",
             "cat_label": "第1类 三边关系"},
            {"set_id": "ts1", "set_name": "初二中考必考专题", "cat_id": "tc2",
             "cat_label": "第6类 全等模型"}]

    def test_grade_fallback_and_cat_mapping(self):
        """v1.2k：kps 受控（内置 KB 细分知识点清单：精确/包含匹配回清单名，最多3个）"""
        import reviewflow as rf
        kb = [{"kp": "三角形内角和定理", "chapter": "第一章 三角形"},
              {"kp": "角平分线的性质", "chapter": "第一章 三角形"}]
        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = lambda *a, **kw: {"questions": [
            {"no": 1, "grade": "随便写的", "cat": "第1类 三边关系", "kps": ["三角形内角和定理"]},   # 精确命中
            {"no": 2, "grade": "九年级上册", "cat": "不存在的类别", "kps": ["角平分线的性质定理"]},  # 包含命中
            {"no": 3, "grade": "八年级上册", "cat": "第1类 三边关系", "kps": ["清单外的自由发挥"]},   # 未命中 → 置空（只用清单内知识点）
            {"no": 4, "kps": ["三角形内角和定理", "三角形内角和定理"]},   # 去重
            "非字典条目应被丢弃"]}
        try:
            out = rf.classify_questions(None, None,
                                        [{"no": i, "question": "q"} for i in (1, 2, 3, 4)],
                                        cats=self.CATS, kb_kps=kb)
        finally:
            rf.ask_json_vision = rf_ask
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["grade"], "八年级上册")   # 非法 grade 回落
        self.assertEqual(out[0]["set"], "ts1")            # 类别名精确匹配回 id
        self.assertEqual(out[0]["cat"], "tc1")
        self.assertEqual(out[0]["kps"], ["三角形内角和定理"])  # 精确匹配
        self.assertEqual(out[0]["chapter"], "第一章 三角形")    # v1.2o：kp 反查教材章
        self.assertEqual(out[1]["kps"], ["角平分线的性质"])    # 包含匹配回清单名
        self.assertEqual(out[1]["chapter"], "第一章 三角形")
        self.assertIsNone(out[1]["cat"])                  # 未知类别 → 置 None
        self.assertEqual(out[2]["kps"], [])  # 清单未覆盖 → 置空（前端显示「其他」）
        self.assertEqual(out[2]["chapter"], "")           # 无命中 kp → 无章
        self.assertEqual(out[3]["kps"], ["三角形内角和定理"])  # 重复输出去重

    def test_fuzzy_kp_convergence(self):
        """清单外名称 difflib 相似收敛到清单内条目（如「全等三角形的判定」→全等相关条目），
        完全无关则置空；绝不保留清单外原文（v1.2j）"""
        import reviewflow as rf
        kb = [{"kp": "三角形内角和定理", "chapter": "第一章 三角形"},
              {"kp": "三角形内角和", "chapter": "第一章 三角形"}]
        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = lambda *a, **kw: {"questions": [
            {"no": 1, "cat": "第1类 三边关系", "kps": ["三角形三个内角的和"]},   # 语义近 → difflib 收敛
            {"no": 2, "cat": "第1类 三边关系", "kps": ["完全无关乱写xyz"]}]}       # 无关 → 置空
        try:
            out = rf.classify_questions(None, None, [{"no": 1, "question": "q1"}, {"no": 2, "question": "q2"}],
                                        cats=self.CATS, kb_kps=kb)
        finally:
            rf.ask_json_vision = rf_ask
        self.assertIn(out[0]["kps"][0], ["三角形内角和定理", "三角形内角和"])   # 收敛到清单内
        self.assertEqual(out[1]["kps"], [])   # 完全无关 → 置空

    def test_kps_cap_three(self):
        """v1.2k：最多 3 个知识点（超出的丢弃），混合命中场景逐个归一化"""
        import reviewflow as rf
        kb = [{"kp": "k1", "chapter": "第一章 三角形"},
              {"kp": "k2", "chapter": "第一章 三角形"},
              {"kp": "k3", "chapter": "第二章 特殊三角形"},
              {"kp": "k4", "chapter": "第二章 特殊三角形"}]
        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = lambda *a, **kw: {"questions": [
            {"no": 1, "kps": ["k1", "k2 的变体", "k3", "k4"]}]}
        try:
            out = rf.classify_questions(None, None, [{"no": 1, "question": "q"}],
                                        cats=self.CATS, kb_kps=kb)
        finally:
            rf.ask_json_vision = rf_ask
        self.assertEqual(out[0]["kps"], ["k1", "k2", "k3"])   # 第4个被截断；「k2 的变体」包含匹配回 k2
        self.assertEqual(out[0]["chapter"], "第一章 三角形")     # v1.2o：首个命中 kp 所属章

    def test_prompt_contains_cat_labels(self):
        import reviewflow as rf
        seen = {}
        def fake_ask(client, model, prompt, uri, max_tokens=4000):
            seen["prompt"] = prompt
            return {"questions": []}
        rf_ask = rf.ask_json_vision
        rf.ask_json_vision = fake_ask
        try:
            rf.classify_questions(None, None, [{"no": 1, "question": "q"}],
                                  cats=self.CATS,
                                  kb_kps=[{"kp": "三角形内角和定理", "chapter": "第一章 三角形"}])
        finally:
            rf.ask_json_vision = rf_ask
        self.assertIn("第1类 三边关系", seen["prompt"])
        self.assertIn("第6类 全等模型", seen["prompt"])
        self.assertIn("三角形内角和定理", seen["prompt"])   # 细分知识点清单进入 prompt
        self.assertIn("第一章 三角形", seen["prompt"])


if __name__ == "__main__":
    unittest.main()
