# -*- coding: utf-8 -*-
"""拍摄方向自动扶正回归（v1.3）：照片内容横躺 90°（EXIF 无标记）→ 转录前检测并扶正

覆盖契约：
  1. CV 预筛：正立文字图 → upright（零 token 直通）；横躺文字图 → rotated
  2. LLM 确认：CV 判躺 → 小图视觉调用返回角度；非法/失败 → 0（不旋转保持现状）
  3. transcribe_photo 集成：横躺照片自动扶正后转录——存盘照片方向正立、
     figure 裁剪在扶正图上、sheet.auto_rotated 记录角度
  4. EXIF（v0.5.1）与拍摄扶正（v1.3）叠加：两种旋转都存在时先 EXIF 再内容扶正
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


def _doc_photo(rot=0, w=900, h=640):
    """合成多行"文字"文档照片（用横条模拟文本行，行剖面特征明显）；rot=顺时针预转角度"""
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = 40
    while y < h - 60:
        d.rectangle([50, y, w - 80, y + 16], fill=(20, 20, 20))    # 一"行"文字
        d.rectangle([50, y + 20, w // 2, y + 26], fill=(60, 60, 60))  # 行内次级结构
        y += 48                                                    # 行距
    d.rectangle([w - 90, h - 50, w - 50, h - 20], fill=(10, 10, 10))  # 页码块
    if rot:
        img = img.rotate(-rot, expand=True)   # 顺时针 rot 度（模拟拍摄横躺）
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class TestCvDetect(unittest.TestCase):
    def test_upright_photo_zero_token_pass(self):
        im = Image.open(io.BytesIO(_doc_photo(rot=0)))
        self.assertEqual(rf.detect_rotation_cv(im), "upright")

    def test_rotated_photo_detected(self):
        im = Image.open(io.BytesIO(_doc_photo(rot=90)))
        self.assertEqual(rf.detect_rotation_cv(im), "rotated")
        im2 = Image.open(io.BytesIO(_doc_photo(rot=270)))
        self.assertEqual(rf.detect_rotation_cv(im2), "rotated")

    def test_blank_photo_unsure(self):
        im = Image.new("RGB", (600, 400), "white")
        self.assertEqual(rf.detect_rotation_cv(im), "unsure")


class TestLlmConfirm(unittest.TestCase):
    def setUp(self):
        self.lied = Image.open(io.BytesIO(_doc_photo(rot=90)))

    def test_llm_angle_applied(self):
        """四宫格指认契约：LLM 答 n=2（右上正立）→ 需顺时针 90°"""
        real = rf.make_client
        real_ask = rf.ask_json_vision
        rf.make_client = lambda *a, **kw: (object(), "stub")
        rf.ask_json_vision = lambda *a, **kw: {"n": 2}
        try:
            self.assertEqual(rf.detect_rotation_llm(self.lied), 90)
        finally:
            rf.make_client, rf.ask_json_vision = real, real_ask

    def test_llm_failure_returns_zero(self):
        real = rf.make_client
        rf.make_client = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no key"))
        try:
            self.assertEqual(rf.detect_rotation_llm(self.lied), 0)
        finally:
            rf.make_client = real

    def test_detect_routing(self):
        """CV 判正立 → 0 且不调 LLM；CV 判躺 → LLM 角度"""
        real_ask = rf.ask_json_vision
        calls = {"n": 0}

        def fake_ask(*a, **kw):
            calls["n"] += 1
            return {"n": 4}   # 右下(顺270)正立 → rotate=270

        rf.ask_json_vision = fake_ask
        real_mc = rf.make_client
        rf.make_client = lambda *a, **kw: (object(), "stub")
        try:
            self.assertEqual(rf.detect_rotation(Image.open(io.BytesIO(_doc_photo(rot=0)))), 0)
            self.assertEqual(calls["n"], 0)                     # 正立零调用
            self.assertEqual(rf.detect_rotation(self.lied), 270)  # 躺倒 → LLM
            self.assertEqual(calls["n"], 1)
        finally:
            rf.ask_json_vision, rf.make_client = real_ask, real_mc


class TestRotateSheetPhoto(unittest.TestCase):
    """v1.3 人审兜底：rotate_sheet_photo 顺时针转 90° + 图形框同步变换 + 整单重裁（可连点）"""

    def setUp(self):
        store.set_test_mode()
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)

    def tearDown(self):
        store.set_test_data_dir(None)

    def _mk_sheet_with_figure(self):
        from PIL import ImageDraw
        img = Image.new("RGB", (900, 640), "white")
        d = ImageDraw.Draw(img)
        y = 40
        while y < 500:
            d.rectangle([50, y, 700, y + 14], fill=(20, 20, 20))
            y += 46
        d.rectangle([620, 520, 800, 620], fill=(10, 10, 60))   # "图形"区域
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=90)
        # stub 全部 LLM 调用（转录/方向检测/归类）——离线快速
        real_mc, real_ask = rf.make_client, rf.ask_json_vision
        rf.make_client = lambda *a, **kw: (object(), "stub")

        def fake_ask(client, model, prompt, uri, max_tokens=16000):
            if "试卷数字化专家" in prompt:
                return {"source_guess": "", "questions": [
                    {"no": 1, "type": "fill", "question": "如图", "choices": None,
                     "has_figure": True, "figure_boxes": [[0.68, 0.8, 0.9, 0.97]],
                     "printed_answer": None, "has_handwriting": False}]}
            return {"questions": []}

        rf.ask_json_vision = fake_ask
        try:
            sheet = rf.transcribe_photo(buf.getvalue())
        finally:
            rf.make_client, rf.ask_json_vision = real_mc, real_ask
        return sheet

    def test_rotate_transforms_boxes_and_recrops(self):
        sheet = self._mk_sheet_with_figure()
        # 转录后图形框存在（自动抓图）
        q = sheet["questions"][0]
        boxes0 = q["figure_boxes"]
        crop0 = q["figure_crops"][0] if q.get("figure_crops") else None
        self.assertTrue(boxes0, "转录应产出图形框")
        # 旋转一次：box 坐标按 (l,t,r,b)->(1-b,l,1-t,r) 变换、裁剪图重生成
        out = rf.rotate_sheet_photo(sheet["id"])
        self.assertEqual(out["auto_rotated"], 90)
        q1 = out["questions"][0]
        (l0, t0, r0, b0), (l1, t1, r1, b1) = boxes0[0], q1["figure_boxes"][0]
        self.assertAlmostEqual(l1, 1 - b0, places=5)
        self.assertAlmostEqual(t1, l0, places=5)
        self.assertAlmostEqual(r1, 1 - t0, places=5)
        self.assertAlmostEqual(b1, r0, places=5)
        if crop0:
            self.assertTrue(os.path.exists(q1["figure_crops"][0]))
        # 连点：再转 3 次 = 回到 360°，box 应回到初始值
        for _ in range(3):
            out = rf.rotate_sheet_photo(sheet["id"])
        self.assertEqual(out["auto_rotated"], 0)
        b_back = out["questions"][0]["figure_boxes"][0]
        self.assertAlmostEqual(b_back[0], boxes0[0][0], places=5)
        self.assertAlmostEqual(b_back[1], boxes0[0][1], places=5)
        self.assertAlmostEqual(b_back[2], boxes0[0][2], places=5)
        self.assertAlmostEqual(b_back[3], boxes0[0][3], places=5)

    def test_rotate_missing_sheet_404(self):
        with self.assertRaises(ValueError):
            rf.rotate_sheet_photo("nonexistent")


class TestTranscribeIntegration(unittest.TestCase):
    def setUp(self):
        store.set_test_mode()
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)

    def tearDown(self):
        store.set_test_data_dir(None)

    def test_lying_photo_autorotated_before_transcribe(self):
        """横躺照片：检测 90° → 扶正后转录；存盘照片正立、sheet.auto_rotated=90"""
        raw = _doc_photo(rot=90)
        real_mc = rf.make_client
        real_ask = rf.ask_json_vision

        def fake_ask(client, model, prompt, uri, max_tokens=16000):
            if "四宫格" in prompt:
                return {"n": 4}   # 顺躺(90)样本 → 右下(顺270)正立 → rotate=270
            if "试卷数字化专家" in prompt:
                return {"source_guess": "", "questions": [
                    {"no": 1, "type": "fill", "question": "如图，三角形纸片对折。",
                     "choices": None, "has_figure": False, "figure_boxes": [],
                     "printed_answer": None, "has_handwriting": False}]}
            if "教研专家" in prompt:
                return {"questions": []}
            raise AssertionError("未知 prompt 路由")

        rf.make_client = lambda *a, **kw: (object(), "stub")
        rf.ask_json_vision = fake_ask
        try:
            sheet = rf.transcribe_photo(raw)
            self.assertEqual(sheet["auto_rotated"], 270)   # 顺躺90样本 → 扶正需顺270
            with Image.open(sheet["photo"]) as im:
                w, h = im.size
                self.assertGreater(w, h)   # 横躺照片扶正后应为横向（原图 900x640 顺转 90 后仍横…）
        finally:
            rf.make_client, rf.ask_json_vision = real_mc, real_ask

    def test_exif_plus_content_rotation_combined(self):
        """EXIF 转正后再检测内容方向：正立文档打 EXIF=6 → exif_transpose 后内容躺 → CV 判 rotated
        （验证 v0.5.1 EXIF 与 v1.3 内容扶正两层检测叠加生效）"""
        buf = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.open(io.BytesIO(_doc_photo(rot=0))).save(buf, "JPEG", quality=90, exif=exif)
        up = rf.upright_image(Image.open(io.BytesIO(buf.getvalue()))).convert("RGB")   # EXIF 转正 → 内容躺
        self.assertEqual(up.size[0] < up.size[1], True)                                 # 转正后变竖版
        self.assertEqual(rf.detect_rotation_cv(up), "rotated")                          # 检测到躺倒


if __name__ == "__main__":
    unittest.main()
