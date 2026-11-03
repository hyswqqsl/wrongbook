# -*- coding: utf-8 -*-
"""入库时图形来源的尊重（回归测试）

人审期用户可显式选择图形来源（SVG 或原图）。入库时必须尊重用户选择：
  - 用户选了原图（figure_mode="crop"）→ 不得再自动向量化覆盖用户选择
  - SVG 生成失败回落（figure_mode="svg" 但无 spec）→ 入库时补向量化一次（合理）
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import pipeline  # noqa: E402

GEOM_Q = "如图，在 $\\triangle ABC$ 中，$AD$ 是等腰三角形 $ABC$ 的底边 $BC$ 上的中线。"


class TestFigureSourceRespect(unittest.TestCase):
    """入库阶段：图形来源决策"""

    def test_explicit_crop_choice_skips_vectorize(self):
        """用户显式选原图 → 不再向量化（否则会覆盖用户选择）"""
        calls = []

        def fake_vec(*a, **kw):
            calls.append(a)
            return {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}, ["ok"]

        q = {"question": GEOM_Q, "has_figure": True,
             "figure_mode": "crop", "figure_spec": None,
             "figure_crops": ["/tmp/c.png"]}
        spec = pipeline.resolve_figure_spec(q, GEOM_Q, vectorize=fake_vec)
        self.assertIsNone(spec)
        self.assertEqual(len(calls), 0)

    def test_svg_mode_without_spec_retries_vectorize(self):
        """SVG 模式但 spec 缺失（生成失败回落）→ 入库补向量化一次"""
        calls = []

        def fake_vec(*a, **kw):
            calls.append(a)
            return {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}, ["ok"]

        q = {"question": GEOM_Q, "has_figure": True,
             "figure_mode": "svg", "figure_spec": None, "figure_crops": ["/tmp/c.png"]}
        spec = pipeline.resolve_figure_spec(q, GEOM_Q, vectorize=fake_vec)
        self.assertIsNotNone(spec)
        self.assertEqual(len(calls), 1)

    def test_existing_spec_is_kept_verbatim(self):
        """排版机已生成且过 geocheck 的 spec → 直接采纳，不再调用模型"""
        calls = []

        def fake_vec(*a, **kw):
            calls.append(a)
            return None, ["x"]

        existing = {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}
        q = {"question": GEOM_Q, "has_figure": True,
             "figure_mode": "svg", "figure_spec": existing, "figure_crops": []}
        spec = pipeline.resolve_figure_spec(q, GEOM_Q, vectorize=fake_vec)
        self.assertIs(spec, existing)
        self.assertEqual(len(calls), 0)

    def test_table_question_never_vectorizes(self):
        """表格/图象类题 → 跳过向量化"""
        calls = []

        def fake_vec(*a, **kw):
            calls.append(a)
            return None, ["x"]

        q = {"question": "根据下表中的信息：", "has_figure": True,
             "figure_mode": None, "figure_spec": None, "figure_crops": ["/tmp/c.png"]}
        spec = pipeline.resolve_figure_spec(q, "根据下表中的信息：", vectorize=fake_vec)
        self.assertIsNone(spec)
        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
