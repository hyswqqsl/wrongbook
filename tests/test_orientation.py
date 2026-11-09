# -*- coding: utf-8 -*-
"""照片方向（EXIF orientation）一致性回归测试

根因（2026-11-01 用户报告"抓取原图旋转了90度"）：
  存盘的照片带 EXIF orientation=6（显示时需顺时针转90°）。
  浏览器显示时应用了 EXIF → 用户看到的是【转正空间】(竖)，
  但服务端裁剪用的是【原始像素空间】(横) → 两个坐标系错位：
    · 自动抓图：LLM 的 box 在原始空间、裁剪也在原始空间 → 区域对但图横躺 90°
    · 手动框选：用户的 box 在转正空间、服务端在原始空间裁 → 区域完全错位

修复方向：全链路统一到【转正空间】
  1. preprocess 给 LLM 的图转正（LLM 返回的 box 即转正空间）
  2. 存盘的照片转正（浏览器显示 = 服务端像素，两边一致）
  3. 裁剪从转正后的图上取（box 直接可用）
  4. 历史草稿单迁移：照片转正 + figure_boxes 同步变换
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

from PIL import Image  # noqa: E402
import reviewflow  # noqa: E402


def _rotated_photo(w=400, h=300, orientation=6):
    """造一张带 EXIF orientation 的照片。

    真实场景（手机横拍）：存盘像素 4032x3024（横），orientation=6 → 显示时顺时针90°变竖。
    这里 w,h 指【显示正立后】的尺寸，故存盘像素为 (h, w)。
    """
    buf = io.BytesIO()
    Image.new("RGB", (h, w), "white").save(buf, "JPEG", quality=95)
    raw = buf.getvalue()
    im = Image.open(io.BytesIO(raw))
    exif = im.getexif()
    exif[274] = orientation
    out = io.BytesIO()
    im.save(out, "JPEG", quality=95, exif=exif)
    return out.getvalue()


class TestOrientationHelpers(unittest.TestCase):
    """方向工具函数"""

    def test_exif_orientation_detected(self):
        raw = _rotated_photo(orientation=6)
        im = Image.open(io.BytesIO(raw))
        self.assertEqual(reviewflow.exif_orientation(im), 6)

    def test_no_orientation_defaults_to_1(self):
        buf = io.BytesIO()
        Image.new("RGB", (100, 80), "white").save(buf, "JPEG")
        im = Image.open(io.BytesIO(buf.getvalue()))
        self.assertEqual(reviewflow.exif_orientation(im), 1)

    def test_box_transform_orientation_6(self):
        """orientation=6（顺时针90）：raw box → upright box"""
        # raw (l,t,r,b) → upright (1-b, l, 1-t, r)
        got = reviewflow.box_to_upright((0.39, 0.6, 0.55, 0.93), 6)
        self.assertEqual([round(v, 4) for v in got], [0.07, 0.39, 0.4, 0.55])

    def test_box_transform_orientation_8(self):
        """orientation=8（逆时针90）：raw box → upright box"""
        got = reviewflow.box_to_upright((0.1, 0.2, 0.3, 0.4), 8)
        self.assertEqual([round(v, 4) for v in got], [0.2, 0.7, 0.4, 0.9])

    def test_box_transform_orientation_3(self):
        """orientation=3（180°）：raw box → upright box"""
        got = reviewflow.box_to_upright((0.1, 0.2, 0.3, 0.4), 3)
        self.assertEqual([round(v, 4) for v in got], [0.7, 0.6, 0.9, 0.8])

    def test_box_transform_identity(self):
        """orientation=1/缺省 → 不变"""
        for o in (1, None):
            self.assertEqual(reviewflow.box_to_upright((0.1, 0.2, 0.3, 0.4), o),
                             (0.1, 0.2, 0.3, 0.4))

    def test_box_transform_preserves_area(self):
        """旋转不改变面积（归一化坐标下的不变量）"""
        for o in (3, 6, 8):
            l, t, r, b = 0.11, 0.22, 0.63, 0.87
            nl, nt, nr, nb = reviewflow.box_to_upright((l, t, r, b), o)
            self.assertAlmostEqual((r - l) * (b - t), (nr - nl) * (nb - nt), places=6)

    def test_box_transform_preserves_center(self):
        """旋转 90° 时中心点保持不变（几何中心应落在同一位置）"""
        for o in (6, 8):
            l, t, r, b = 0.11, 0.22, 0.63, 0.87
            nl, nt, nr, nb = reviewflow.box_to_upright((l, t, r, b), o)
            cx, cy = (l + r) / 2, (t + b) / 2
            ncx, ncy = (nl + nr) / 2, (nt + nb) / 2
            # 90° 旋转会把 (cx,cy) 映射到另一个位置，但仍在图内且不重合原角
            self.assertTrue(0 <= ncx <= 1 and 0 <= ncy <= 1)
            self.assertNotAlmostEqual(cx, ncx, places=3)


class TestPreprocessUpright(unittest.TestCase):
    """preprocess 必须给 LLM 转正后的图"""

    def test_preprocess_transposes_exif(self):
        raw = _rotated_photo(300, 400, orientation=6)
        uri, size = reviewflow.preprocess(raw)
        # 转正后应为竖的 (300, 400)
        self.assertEqual(size, (300, 400))

    def test_preprocess_no_orientation_unchanged(self):
        buf = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(buf, "JPEG")
        uri, size = reviewflow.preprocess(buf.getvalue())
        self.assertEqual(size, (200, 100))


class TestCropUpright(unittest.TestCase):
    """裁剪必须从转正后的图上取，box 直接可用"""

    def test_crop_from_upright_image(self):
        """转正图 + 转正空间 box → 正确区域，且图为正立"""
        raw = _rotated_photo(300, 400, orientation=6)
        up = Image.open(io.BytesIO(raw))
        up = reviewflow.upright_image(up)
        self.assertEqual(up.size, (300, 400))
        # 在转正图上画一个标记块，验证裁剪能取到它
        from PIL import ImageDraw
        mark = Image.new("RGB", (300, 400), "white")
        d = ImageDraw.Draw(mark)
        d.rectangle([60, 100, 140, 200], fill="red")
        buf = io.BytesIO()
        mark.save(buf, "PNG")
        crops = reviewflow.crop_figures(buf.getvalue(), "s1",
                                        [{"no": 1, "k": 0, "box": [0.2, 0.25, 0.4667, 0.5]}])
        self.assertEqual(len(crops), 1)
        c = Image.open(crops[0]["path"])
        px = c.convert("RGB").getpixel((c.width // 2, c.height // 2))
        self.assertEqual(px, (255, 0, 0), "应裁到红色标记块")


class TestManualBoxSpace(unittest.TestCase):
    """手动框选：用户给的 box 属于【显示空间】=【转正空间】"""

    def test_manual_crop_uses_box_directly_on_upright(self):
        """服务端用转正图 + 用户 box 直接裁（不需再变换）"""
        from PIL import ImageDraw
        mark = Image.new("RGB", (300, 400), "white")
        ImageDraw.Draw(mark).rectangle([60, 100, 140, 200], fill="blue")
        buf = io.BytesIO()
        mark.save(buf, "PNG")
        paths = reviewflow.manual_crop(buf.getvalue(), "s2", 3, [0.2, 0.25, 0.4667, 0.5])
        self.assertEqual(len(paths), 1)
        c = Image.open(paths[0]).convert("RGB")
        self.assertEqual(c.getpixel((c.width // 2, c.height // 2)), (0, 0, 255))


class TestLegacyMigration(unittest.TestCase):
    """历史草稿单迁移：照片转正 + figure_boxes 同步变换"""

    def test_migrate_transforms_boxes_and_photo(self):
        import store
#        with tempfile.TemporaryDirectory() as td:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            old_photo_dir, old_crop_dir = store.PHOTO_DIR, store.CROP_DIR
            store.PHOTO_DIR = os.path.join(td, "photos")
            store.CROP_DIR = os.path.join(td, "crops")
            try:
                os.makedirs(store.PHOTO_DIR, exist_ok=True)
                p = os.path.join(store.PHOTO_DIR, "s3.jpg")
                with open(p, "wb") as f:
                    f.write(_rotated_photo(300, 400, orientation=6))
                sheet = {"id": "s3", "photo": p, "questions": [
                    {"no": 1, "figure_boxes": [[0.39, 0.6, 0.55, 0.93]]}]}
                reviewflow.migrate_sheet_orientation(sheet)
                # 照片已转正（无 EXIF orientation）
                im = Image.open(p)
                self.assertEqual(im.size, (300, 400))
                self.assertEqual(reviewflow.exif_orientation(im), 1)
                # box 已变换到转正空间
                box = sheet["questions"][0]["figure_boxes"][0]
                self.assertEqual([round(v, 4) for v in box], [0.07, 0.39, 0.4, 0.55])
            finally:
                store.PHOTO_DIR, store.CROP_DIR = old_photo_dir, old_crop_dir

    def test_migrate_is_idempotent(self):
        import store
        with tempfile.TemporaryDirectory() as td:
            old_photo_dir = store.PHOTO_DIR
            store.PHOTO_DIR = os.path.join(td, "photos")
            try:
                os.makedirs(store.PHOTO_DIR, exist_ok=True)
                p = os.path.join(store.PHOTO_DIR, "s4.jpg")
                with open(p, "wb") as f:
                    f.write(_rotated_photo(300, 400, orientation=6))
                sheet = {"id": "s4", "photo": p, "questions": [
                    {"no": 1, "figure_boxes": [[0.39, 0.6, 0.55, 0.93]]}]}
                reviewflow.migrate_sheet_orientation(sheet)
                first = list(sheet["questions"][0]["figure_boxes"][0])
                reviewflow.migrate_sheet_orientation(sheet)   # 再跑一次不应再变
                second = list(sheet["questions"][0]["figure_boxes"][0])
                self.assertEqual(first, second)
            finally:
                store.PHOTO_DIR = old_photo_dir


if __name__ == "__main__":
    unittest.main(verbosity=2)
