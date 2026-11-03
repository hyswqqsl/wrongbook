# -*- coding: utf-8 -*-
"""手动框选 box 存档 + 整单重裁 回归测试

背景（2026-11-01 用户报告第二次）：照片方向修好后，旧代码产出的
「手动框选」裁剪图仍是横躺的 —— 因为它是旧代码从【旋转图】上裁的，
而 box 本身（在浏览器显示空间）其实是对的。

修复要点：
  1. 手动框选的 box 存档（figure_manual_box，转正空间）→ 可重放
  2. recrop_all()：按当前照片重裁所有图（自动 + 手动），必然与图源一致
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
import reviewflow  # noqa: E402


def _photo_with_mark(size=(300, 400), box=(60, 100, 140, 200), color="red"):
    """造一张(正立竖图)，在 box 区域画标记"""
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).rectangle(list(box), fill=color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), size, box


class TestManualBoxArchive(unittest.TestCase):
    """手动 box 存档（app 层字段）"""

    def test_manual_box_is_stored_in_upright_space(self):
        """存档的 box 应原样保留（转正空间），不做任何变换"""
        _, size, box = _photo_with_mark()
        l, t, r, b = box
        norm = [l / size[0], t / size[1], r / size[0], b / size[1]]
        self.assertEqual(list(norm), list(reviewflow.box_to_upright(norm, 1)))  # 不变

    def test_manual_crop_and_archive_produce_same_region(self):
        """存档的 box 重放一次 → 裁出完全相同的图（可复现）"""
        import store
        raw, size, box = _photo_with_mark(color="green")
        l, t, r, b = box
        norm = [l / size[0], t / size[1], r / size[0], b / size[1]]
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = td
            try:
                p1 = reviewflow.manual_crop(raw, "s", 1, norm)[0]
                p2 = reviewflow.manual_crop(raw, "s", 1, norm)[0]
            finally:
                store.CROP_DIR = old
            self.assertEqual(Image.open(p1).size, Image.open(p2).size)
            self.assertEqual(Image.open(p1).tobytes(), Image.open(p2).tobytes())


class TestRecropAll(unittest.TestCase):
    """整单重裁"""

    def _sheet(self, td, sid="sx"):
        im = Image.new("RGB", (300, 400), "white")
        ImageDraw.Draw(im).rectangle([60, 100, 140, 200], fill="red")
        p = os.path.join(td, sid + ".jpg")
        im.save(p, "JPEG")
        return {"id": sid, "photo": p, "questions": [
            {"no": 1, "figure_boxes": [[0.2, 0.25, 0.4667, 0.5]],
             "figure_manual_box": [0.05, 0.05, 0.2, 0.2]}]}

    def test_recrop_all_produces_both_auto_and_manual(self):
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = os.path.join(td, "crops")
            try:
                sheet = self._sheet(td)
                out = reviewflow.recrop_all(sheet)
                self.assertEqual(len(out["auto_paths"]), 1)
                self.assertEqual(len(out["manual_paths"]), 1)
                self.assertTrue(out["auto_paths"][0].endswith("_q1_0.png"))
                self.assertTrue(out["manual_paths"][0].endswith("_q1_m.png"))
                # 草稿单里的 figure_crops 被更新为两条
                self.assertEqual(len(sheet["questions"][0]["figure_crops"]), 2)
            finally:
                store.CROP_DIR = old

    def test_recrop_all_auto_crop_hits_mark(self):
        """自动图应正好裁到标记块（验证坐标空间一致）"""
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = os.path.join(td, "crops")
            try:
                sheet = self._sheet(td)
                out = reviewflow.recrop_all(sheet)
                c = Image.open(out["auto_paths"][0]).convert("RGB")
                px = c.getpixel((c.width // 2, c.height // 2))
                # JPEG 有损 → 红通道应接近 255（G/B 接近 0）
                self.assertGreater(px[0], 240)
                self.assertLess(px[1], 20)
                self.assertLess(px[2], 20)
            finally:
                store.CROP_DIR = old

    def test_recrop_all_is_idempotent(self):
        """重跑一次结果相同（图源唯一权威 → 必然幂等）"""
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = os.path.join(td, "crops")
            try:
                sheet = self._sheet(td)
                a = reviewflow.recrop_all(sheet)["auto_paths"]
                b = reviewflow.recrop_all(sheet)["auto_paths"]
                self.assertEqual(a, b)
            finally:
                store.CROP_DIR = old

    def test_recrop_all_without_manual_box(self):
        """没有手动 box 时只产自动图"""
        import store
        with tempfile.TemporaryDirectory() as td:
            old = store.CROP_DIR
            store.CROP_DIR = os.path.join(td, "crops")
            try:
                sheet = self._sheet(td)
                sheet["questions"][0].pop("figure_manual_box")
                out = reviewflow.recrop_all(sheet)
                self.assertEqual(len(out["auto_paths"]), 1)
                self.assertEqual(len(out["manual_paths"]), 0)
            finally:
                store.CROP_DIR = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
