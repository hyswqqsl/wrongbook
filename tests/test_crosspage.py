# -*- coding: utf-8 -*-
"""跨页题拼接回归（v1.3.1）：两页照片同一道题 → 前端拼长图一次转录

覆盖契约：
  1. preprocess 长图放宽：H/W≥1.5 的拼接长图最长边放宽到 2400（普通照片仍 1600）
  2. 转录 prompt 含跨页拼接条款（跨页截断题合并为一题完整转录）
  3. transcribe_photo 集成：长图走 2400（通过 photo_size 间接断言）
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

from PIL import Image, ImageDraw  # noqa: E402
import reviewflow as rf  # noqa: E402
import store  # noqa: E402


def _jpeg(w, h):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = 30
    while y < h - 30:
        d.rectangle([40, y, w - 40, y + 12], fill=(20, 20, 20))
        y += 44
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class TestLongPhotoPreprocess(unittest.TestCase):
    def test_normal_photo_capped_1600(self):
        _, (w, h) = rf.preprocess(_jpeg(4000, 3000))
        self.assertEqual(max(w, h), 1600)

    def test_stitched_long_photo_relaxed_2400(self):
        """拼接长图（H/W≥1.5）→ 最长边放宽 2400，两页文字保持可读"""
        _, (w, h) = rf.preprocess(_jpeg(1200, 3200), max_side=2400)
        self.assertEqual(max(w, h), 2400)
        self.assertEqual(h > w, True)

    def test_default_call_still_1600(self):
        _, (w, h) = rf.preprocess(_jpeg(1200, 3200))   # 不传参 = 普通调用
        self.assertEqual(max(w, h), 1600)


class TestTranscribePromptCrossPage(unittest.TestCase):
    def test_prompt_has_stitch_clause(self):
        self.assertIn("两页上下拼接", rf.TRANSCRIBE_PROMPT)
        self.assertIn("合并为**一题完整转录**", rf.TRANSCRIBE_PROMPT)

    def test_transcribe_long_photo_uses_relaxed_max_side(self):
        """集成：拼接长图转录 → 传给视觉模型的图最长边 2400（经 photo_size 断言）"""
        store.set_test_mode()
        tmp = tempfile.mkdtemp()
        store.set_test_data_dir(tmp)
        try:
            raw = _jpeg(1200, 3200)
            real_mc, real_ask = rf.make_client, rf.ask_json_vision
            seen = {}
            rf.make_client = lambda *a, **kw: (object(), "stub")

            def fake_ask(client, model, prompt, uri, max_tokens=16000):
                seen["uri_head"] = uri[:30]
                # data URI 头部之后是 base64——直接解码头部太重；用返回的 (W,H) 断言即可
                if "试卷数字化专家" in prompt:
                    return {"source_guess": "", "questions": [
                        {"no": 1, "type": "fill", "question": "跨页完整题干", "choices": None,
                         "has_figure": False, "figure_boxes": [],
                         "printed_answer": None, "has_handwriting": False}]}
                return {"questions": []}

            rf.ask_json_vision = fake_ask
            try:
                sheet = rf.transcribe_photo(raw)
            finally:
                rf.make_client, rf.ask_json_vision = real_mc, real_ask
            self.assertEqual(max(sheet["photo_size"]), 2400)   # 长图放宽生效
            self.assertEqual(len(sheet["questions"]), 1)
        finally:
            store.set_test_data_dir(None)


if __name__ == "__main__":
    unittest.main()
