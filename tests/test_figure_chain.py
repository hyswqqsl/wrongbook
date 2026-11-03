# -*- coding: utf-8 -*-
"""错题本图形链回归测试（stdlib unittest，无外部依赖）

覆盖 A 方案的核心契约：
  1. 图形来源分层：几何题走 SVG，表格/图象题只保留原图裁剪，纯文字题无图
  2. SVG 生成失败时回落裁剪图（不阻塞入库）
  3. 转录 prompt 必须索取 figure_boxes（否则无从裁剪）
  4. spec → SVG 渲染可用
  5. 入库记录同时保留 figure_spec（权威）与 figure_crop（兜底）
  6. v0.6 默认图形来源：转录期不向量化（crop 兜底），SVG 按需生成
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import figureflow  # noqa: E402
import pipeline  # noqa: E402
import reviewflow  # noqa: E402


GEOM_Q = "如图，在 $\\triangle ABC$ 中，$AB$ 的垂直平分线交 $AB$ 于点 $E$，交 $BC$ 于点 $D$。"
TABLE_Q = "根据下表中的信息，回答问题："
PLAIN_Q = "某商店购进一批文具，每件售价 12 元时每天可卖 80 件。"


class TestFigureRouting(unittest.TestCase):
    """图形来源分层：svg / crop / none"""

    def test_geometry_uses_svg(self):
        self.assertEqual(reviewflow.classify_figure(GEOM_Q, True), "svg")

    def test_table_question_uses_crop_only(self):
        """表格/函数图象类题 SVG 原理上画不出 → 只保留原图裁剪"""
        self.assertEqual(reviewflow.classify_figure(TABLE_Q, True), "crop")

    def test_plain_question_has_no_figure(self):
        self.assertEqual(reviewflow.classify_figure(PLAIN_Q, False), "none")

    def test_has_figure_false_never_svg(self):
        """模型未判定有图时，即使题干含"如图"也不进图形链"""
        self.assertEqual(reviewflow.classify_figure(GEOM_Q, False), "none")


class TestTranscribePrompt(unittest.TestCase):
    """转录 prompt 必须索取 figure_boxes"""

    def test_prompt_asks_for_figure_boxes(self):
        self.assertIn("figure_boxes", reviewflow.TRANSCRIBE_PROMPT)

    def test_prompt_keeps_has_figure_flag(self):
        self.assertIn("has_figure", reviewflow.TRANSCRIBE_PROMPT)


class TestCropDefault(unittest.TestCase):
    """v0.6 默认图形来源：转录期不向量化，配图题一律 crop（省 token + 提速）

    向量化是全链路最费 token 的一步（每题 1~3 次 LLM 调用 + geocheck 重试），
    改为人审期按需点【🔄 重新生成SVG】（regen_figure 对 svg 类题仍可用）。
    """

    def test_geometry_defaults_to_crop_with_hint(self):
        mode, note = reviewflow.default_figure_fields(GEOM_Q, True)
        self.assertEqual(mode, "crop")
        self.assertIn("原图", note)   # 提示以原图为准

    def test_table_defaults_to_crop(self):
        mode, note = reviewflow.default_figure_fields(TABLE_Q, True)
        self.assertEqual(mode, "crop")
        self.assertIn("表格", note)

    def test_no_figure_stays_none(self):
        mode, note = reviewflow.default_figure_fields(PLAIN_Q, False)
        self.assertEqual(mode, "none")
        self.assertEqual(note, "")

    def test_no_figure_even_if_geometry_wording(self):
        """模型判无图时，几何措辞也不进图形链（与 classify_figure 同口径）"""
        mode, _ = reviewflow.default_figure_fields(GEOM_Q, False)
        self.assertEqual(mode, "none")

    def test_regen_still_allows_vectorize_for_geometry(self):
        """crop 默认不影响按需向量化：几何题 regen_figure 可正常走图形链
        （注入假的 vectorize/render，不真调 LLM）"""
        spec = {"points": [{"name": "A", "x": 1, "y": 1},
                           {"name": "B", "x": 8, "y": 1},
                           {"name": "C", "x": 1, "y": 6}],
                "segments": [{"a": "A", "b": "B"}]}
        out = reviewflow.regen_figure(GEOM_Q, True, [],
                                      vectorize=lambda *a, **kw: (spec, ["ok"]),
                                      render=lambda s: "<svg/>")
        self.assertTrue(out["ok"])
        self.assertEqual(out["figure_mode"], "svg")

    def test_regen_refuses_table_question(self):
        """表格类题 regen_figure 仍拒绝（SVG 表达不了）"""
        out = reviewflow.regen_figure(TABLE_Q, True, [],
                                      vectorize=lambda *a, **kw: (None, ["!"]),
                                      render=lambda s: "")
        self.assertFalse(out["ok"])
        self.assertEqual(out["figure_mode"], "crop")

    def test_ingest_respects_crop_default(self):
        """入库链：figure_mode=crop（新默认）→ 不再向量化，返回 None"""
        called = []
        spec = pipeline.resolve_figure_spec(
            {"figure_mode": "crop", "figure_spec": None, "has_figure": True},
            GEOM_Q, vectorize=lambda *a, **kw: called.append(1) or (None, []))
        self.assertIsNone(spec)
        self.assertFalse(called)   # 向量化未被调用


class TestFallback(unittest.TestCase):
    """SVG 生成失败 → 回落裁剪图，不阻塞入库"""

    def test_failed_vectorize_keeps_crop(self):
        crops = ["/tmp/x_q1_0.png"]
        spec, svg, note = reviewflow.build_figure_fields(
            GEOM_Q, has_figure=True, crops=crops, spec=None, svg=None, msgs=["生成失败"])
        self.assertIsNone(spec)
        self.assertIsNone(svg)
        self.assertTrue(note)

    def test_successful_vectorize_drops_crop_from_display(self):
        """SVG 成功时，图形字段以 SVG 为准"""
        spec = {"points": [{"name": "A", "x": 1, "y": 1}], "segments": []}
        out_spec, out_svg, note = reviewflow.build_figure_fields(
            GEOM_Q, has_figure=True, crops=["/tmp/x_q1_0.png"], spec=spec, svg="<svg/>", msgs=["ok"])
        self.assertIs(out_spec, spec)
        self.assertEqual(out_svg, "<svg/>")
        self.assertFalse(note)


class TestRender(unittest.TestCase):
    """spec → SVG 渲染"""

    def test_render_produces_svg(self):
        spec = {"points": [{"name": "A", "x": 1.5, "y": 1.5},
                           {"name": "B", "x": 8.5, "y": 1.5},
                           {"name": "C", "x": 1.0, "y": 6.4}],
                "segments": [{"a": "A", "b": "B"}, {"a": "A", "b": "C"}, {"a": "B", "b": "C"}]}
        svg = figureflow.render(spec)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_render_empty_spec_is_empty_string(self):
        self.assertEqual(figureflow.render({"points": [], "segments": []}), "")


class TestIngestPayload(unittest.TestCase):
    """入库记录：figure_spec（权威）+ figure_crop（兜底）都要留档"""

    def test_payload_keeps_both_spec_and_crop(self):
        q = pipeline.build_question_payload(
            text=GEOM_Q, choices=None,
            spec={"points": [{"name": "A", "x": 1, "y": 1}], "segments": []},
            crops=["/tmp/x_q1_0.png"])
        self.assertIsNotNone(q["figure_spec"])
        self.assertEqual(q["figure_crop"], "/tmp/x_q1_0.png")

    def test_payload_without_spec_keeps_crop_as_only_figure(self):
        q = pipeline.build_question_payload(
            text=TABLE_Q, choices=None, spec=None, crops=["/tmp/x_q1_0.png"])
        self.assertIsNone(q["figure_spec"])
        self.assertEqual(q["figure_crop"], "/tmp/x_q1_0.png")

    def test_payload_without_any_figure(self):
        q = pipeline.build_question_payload(text=PLAIN_Q, choices=None, spec=None, crops=[])
        self.assertIsNone(q["figure_spec"])
        self.assertIsNone(q["figure_crop"])


class TestCropHelper(unittest.TestCase):
    """裁剪框 → 归一化坐标裁剪"""

    def test_crop_figures_writes_png(self):
        import tempfile
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (400, 300), "white").save(buf, "JPEG")
        photo = buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            import store
            old = store.CROP_DIR
            store.CROP_DIR = td
            try:
                out = reviewflow.crop_figures(
                    photo, "sheet_test",
                    [{"no": 1, "k": 0, "box": [0.1, 0.1, 0.5, 0.5]}])
            finally:
                store.CROP_DIR = old
            self.assertEqual(len(out), 1)
            self.assertTrue(os.path.exists(out[0]["path"]))
        self.assertGreater(out[0]["w"], 0)

    def test_crop_figures_skips_degenerate_box(self):
        out = reviewflow.crop_figures(b"", "s", [{"no": 1, "k": 0, "box": [0.5, 0.5, 0.5, 0.5]}])
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
