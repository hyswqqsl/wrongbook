# -*- coding: utf-8 -*-
"""图形纠正（重新生成 SVG / 切换为原图裁剪）回归测试（stdlib unittest）

覆盖人审期的两个纠正入口：
  1. regen_figure：题干(+裁剪图) → 重跑向量化 → 覆盖 spec/svg；失败不破坏现状
  2. use_crop：把该题权威图形切回原图裁剪（清空 spec，figure_mode=crop）
  3. 手动框选：用户给的 bbox → 裁剪出新图 → 作为该题裁剪图
  4. 自动重定位：复用 figure_boxes 重新裁剪
"""
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import reviewflow  # noqa: E402


GEOM_Q = "如图，在 $\\triangle ABC$ 中，$AD$ 是等腰三角形 $ABC$ 的底边 $BC$ 上的中线。"


def _photo(w=400, h=300):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


class TestRegenFigure(unittest.TestCase):
    """重新生成 SVG"""

    def test_regen_success_overwrites_spec_and_svg(self):
        spec = {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}
        out = reviewflow.regen_figure(
            question=GEOM_Q, has_figure=True, crops=[],
            vectorize=lambda **kw: (spec, ["图形向量化通过（第1轮）"]),
            render=lambda s: "<svg>new</svg>")
        self.assertTrue(out["ok"])
        self.assertIs(out["figure_spec"], spec)
        self.assertEqual(out["figure_svg"], "<svg>new</svg>")
        self.assertEqual(out["figure_mode"], "svg")
        self.assertEqual(out["figure_crops"], [])

    def test_regen_failure_keeps_previous_figure(self):
        """重生成失败 → 不破坏现状（返回 ok=False，调用方保留旧图形）"""
        out = reviewflow.regen_figure(
            question=GEOM_Q, has_figure=True, crops=["/tmp/a.png"],
            vectorize=lambda **kw: (None, ["图形向量化失败：xxx"]),
            render=lambda s: "<svg/>")
        self.assertFalse(out["ok"])
        self.assertIn("figure_spec", out)
        self.assertIn("figure_svg", out)
        self.assertTrue(out["note"])

    def test_regen_passes_crop_to_vectorize(self):
        """重生成时要把裁剪图喂给模型（纯文字猜坐标必然出错）"""
        import tempfile
        from PIL import Image
        seen = {}

        def fake_vec(**kw):
            seen.update(kw)
            return {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}, ["ok"]

        with tempfile.TemporaryDirectory() as td:
            crop = os.path.join(td, "c.png")
            Image.new("RGB", (40, 30), "white").save(crop)
            reviewflow.regen_figure(question=GEOM_Q, has_figure=True, crops=[crop],
                                    vectorize=fake_vec, render=lambda s: "<svg/>")
        self.assertEqual(seen.get("crop_path"), crop)
        self.assertIsNotNone(seen.get("uri"))
        self.assertTrue(seen["uri"].startswith("data:image/png;base64,"))

    def test_regen_missing_crop_file_still_passes_path(self):
        """裁剪图文件不存在时不应崩，uri 留空、crop_path 照传"""
        seen = {}

        def fake_vec(**kw):
            seen.update(kw)
            return {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}, ["ok"]

        reviewflow.regen_figure(question=GEOM_Q, has_figure=True, crops=["/tmp/nope.png"],
                                vectorize=fake_vec, render=lambda s: "<svg/>")
        self.assertEqual(seen.get("crop_path"), "/tmp/nope.png")
        self.assertIsNone(seen.get("uri"))

    def test_regen_for_table_question_is_refused(self):
        """表格/图象类题不适用 SVG → 明确拒绝，而不是白跑一趟"""
        out = reviewflow.regen_figure(
            question="根据下表中的信息：", has_figure=True, crops=[],
            vectorize=lambda **kw: (None, ["x"]), render=lambda s: "")
        self.assertFalse(out["ok"])

    def test_regen_default_vectorize_accepts_question_kwarg(self):
        """默认 vectorize 必须能用 question= 关键字调用（回归：
        vectorize_figure 原本要求 client/model 位置参数，直接调用会 TypeError）"""
        import inspect
        import figureflow
        sig = inspect.signature(figureflow.vectorize_figure)
        self.assertIn("client", sig.parameters)
        self.assertIn("model", sig.parameters)
        # client/model 有默认值（None）→ 可省略，函数内部自建 client
        empty = inspect.Parameter.empty
        for name in ("client", "model", "question"):
            self.assertNotEqual(sig.parameters[name].default, empty, name)
        # 关键字调用可用（question 传参名正确）
        sig.bind(question="如图", crop_path=None, rounds=1, uri=None)

    def test_regen_missing_question_raises_clear_error(self):
        import figureflow
        with self.assertRaises(ValueError):
            figureflow.vectorize_figure()


class TestUseCrop(unittest.TestCase):
    """切换为原图裁剪（权威图形换成原图，SVG 不再入库）"""

    def test_use_crop_clears_spec_and_sets_mode(self):
        q = {"figure_spec": {"points": [{"name": "A", "x": 1, "y": 1}]},
             "figure_svg": "<svg/>", "figure_crops": ["/tmp/a.png"],
             "figure_mode": "svg"}
        out = reviewflow.use_crop(q)
        self.assertIsNone(out["figure_spec"])
        self.assertIsNone(out["figure_svg"])
        self.assertEqual(out["figure_mode"], "crop")
        self.assertEqual(out["figure_crops"], ["/tmp/a.png"])

    def test_use_crop_without_any_crop_still_switches(self):
        q = {"figure_spec": None, "figure_svg": None, "figure_crops": [],
             "figure_mode": "svg"}
        out = reviewflow.use_crop(q)
        self.assertEqual(out["figure_mode"], "crop")


class TestManualCrop(unittest.TestCase):
    """手动框选：在原图上拖拽 → 裁剪出该题的新图"""

    def test_manual_crop_saves_new_file(self):
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = td
            try:
                paths = reviewflow.manual_crop(
                    _photo(), "sheet_x", 6, [0.1, 0.1, 0.6, 0.6])
            finally:
                store.CROP_DIR = old
            self.assertEqual(len(paths), 1)
            self.assertTrue(os.path.exists(paths[0]))
            self.assertIn("_q6_", os.path.basename(paths[0]))

    def test_manual_crop_does_not_overwrite_auto_crop(self):
        """手动框选必须用独立文件名（_m），不覆盖自动抓取的 _0（保留原始证据）"""
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = td
            try:
                auto = reviewflow.crop_figures(
                    _photo(), "sheet_z", [{"no": 6, "k": 0, "box": [0.1, 0.1, 0.6, 0.6]}])
                manual = reviewflow.manual_crop(
                    _photo(), "sheet_z", 6, [0.2, 0.2, 0.7, 0.7])
            finally:
                store.CROP_DIR = old
            self.assertNotEqual(auto[0]["path"], manual[0])
            self.assertTrue(os.path.exists(auto[0]["path"]))
            self.assertTrue(os.path.exists(manual[0]))
            self.assertTrue(auto[0]["path"].endswith("_q6_0.png"))
            self.assertTrue(manual[0].endswith("_q6_m.png"))

    def test_manual_crop_rejects_degenerate_box(self):
        self.assertEqual(reviewflow.manual_crop(_photo(), "s", 6, [0.5, 0.5, 0.5, 0.5]), [])


class TestAutoRelocate(unittest.TestCase):
    """自动重定位：复用该题 figure_boxes 重新裁剪"""

    def test_auto_relocate_uses_figure_boxes(self):
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = td
            try:
                paths = reviewflow.auto_relocate(
                    _photo(), "sheet_y", 7, [[0.2, 0.2, 0.7, 0.7]])
            finally:
                store.CROP_DIR = old
            self.assertEqual(len(paths), 1)
            self.assertTrue(os.path.exists(paths[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
