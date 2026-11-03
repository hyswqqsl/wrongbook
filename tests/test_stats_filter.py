# -*- coding: utf-8 -*-
"""工作台筛选回归测试（v0.7.3：章节/难度/知识点/错因/时间段 → 统计与列表联动）

覆盖契约：
  1. 各维度独立过滤生效（章节/难度/知识点/错因/时间段）
  2. 组合过滤（AND）
  3. reason="none" 只命中未标注题
  4. 知识点为子串匹配（kp_path 叶子 + knowledge_points 都参与）
  5. facets 始终按全量计算（不受筛选影响）
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import store  # noqa: E402
import stats  # noqa: E402


def _seed():
    store.set_test_mode()
    tmp = tempfile.mkdtemp()
    store.DB_PATH = os.path.join(tmp, "wrongbook.json")
    store.save({"version": 1, "questions": [
        {"id": "qa", "created_at": "2026-11-01 10:00:00", "mastered_at": None,
         "type": "single",
         "mistake_reason": {"kind": "careless"},
         "analysis": {"chapter": "第一章 三角形", "difficulty": 1,
                      "kp_path": ["第一章 三角形", "1.1 认识三角形", "三角形内角和"],
                      "knowledge_points": ["三角形内角和"]}},
        {"id": "qb", "created_at": "2026-11-15 10:00:00", "mastered_at": None,
         "type": "fill",
         "mistake_reason": {"kind": "unknown"},
         "analysis": {"chapter": "第五章 一次函数", "difficulty": 3,
                      "kp_path": ["第五章 一次函数", "5.2 一次函数", "一次函数的图象"],
                      "knowledge_points": ["待定系数法"]}},
        {"id": "qc", "created_at": "2026-12-01 10:00:00", "mastered_at": "2026-12-02",
         "type": "short",
         "mistake_reason": {},
         "analysis": {"chapter": "第五章 一次函数", "difficulty": 2,
                      "kp_path": [], "knowledge_points": []}},
    ]})
    return tmp


class TestDashboardFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _seed()

    def test_no_filter_returns_all(self):
        d = stats.dashboard()
        self.assertEqual(d["total"], 3)
        self.assertEqual(len(d["practice_overview"]), 2)   # 只含在库题

    def test_filter_chapter(self):
        d = stats.dashboard(chapter="第五章 一次函数")
        self.assertEqual(d["total"], 2)
        self.assertEqual({r["id"] for r in d["practice_overview"]}, {"qb"})

    def test_filter_difficulty(self):
        d = stats.dashboard(difficulty=3)
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["practice_overview"][0]["id"], "qb")

    def test_filter_kp_substring(self):
        d = stats.dashboard(kp="内角和")          # kp_path 叶子子串
        self.assertEqual({r["id"] for r in d["practice_overview"]}, {"qa"})
        d2 = stats.dashboard(kp="待定系数")        # knowledge_points 子串
        self.assertEqual({r["id"] for r in d2["practice_overview"]}, {"qb"})

    def test_filter_kp_by_section_title(self):
        """知识点下拉含节标题选项（KB 树）→ 命中 kp_path[1]"""
        d = stats.dashboard(kp="5.2 一次函数")
        self.assertEqual({r["id"] for r in d["practice_overview"]}, {"qb"})

    def test_filter_reason_and_none(self):
        d = stats.dashboard(reason="careless")
        self.assertEqual({r["id"] for r in d["practice_overview"]}, {"qa"})
        d2 = stats.dashboard(reason="none")        # 仅未标注
        self.assertEqual({r["id"] for r in d2["practice_overview"]}, set())  # qc 已归档

    def test_filter_date_range(self):
        d = stats.dashboard(date_from="2026-11-10", date_to="2026-11-30")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["practice_overview"][0]["id"], "qb")

    def test_combined_and(self):
        d = stats.dashboard(chapter="第五章 一次函数", difficulty=3, date_from="2026-11-01")
        self.assertEqual(d["total"], 1)

    def test_facets_always_full(self):
        d = stats.dashboard(chapter="第一章 三角形")
        self.assertEqual(len(d["facets"]["chapters"]), 2)   # 不受筛选影响
        self.assertIn("三角形内角和", d["facets"]["kps"])
        self.assertIn("none", d["facets"]["reasons"])

    def test_kp_candidates_grouped_by_chapter_from_bank(self):
        """知识点下拉候选：题库标注汇总（与列表同源），按章分组，与搜索结果无关"""
        d = stats.dashboard(kp="内角和")            # 命中 1 题
        kbc = d["facets"]["kp_by_chapter"]
        self.assertIn("三角形内角和", kbc["第一章 三角形"])
        self.assertIn("一次函数的图象", " ".join(
            k for ch, kks in kbc.items() for k in kks))         # 全库知识点仍在候选
        self.assertIn("待定系数法", kbc.get("第五章 一次函数", []))
        # 候选总数不随筛选变化：筛选前后 kps 集合一致
        d_all = stats.dashboard()
        self.assertEqual(set(d["facets"]["kps"]), set(d_all["facets"]["kps"]))

    def test_overview_rows_carry_meta(self):
        d = stats.dashboard()
        row = next(r for r in d["practice_overview"] if r["id"] == "qa")
        self.assertEqual(row["chapter"], "第一章 三角形")
        self.assertEqual(row["difficulty"], 1)
        self.assertEqual(row["reason"], "马虎")
        self.assertEqual(row["kp"], "三角形内角和")


if __name__ == "__main__":
    unittest.main()
