# -*- coding: utf-8 -*-
"""考点标题行清洗回归（v1.2m）：转录 LLM 把卷面「【考点N】xxx（N分）」栏头并进题干，
造成同一题带/不带前缀两份转录查重不中 → 重复入库。转录后统一剥掉。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import reviewflow as rf  # noqa: E402
import store  # noqa: E402


class TestStripKaoqian(unittest.TestCase):
    def test_strip_with_prefix(self):
        self.assertEqual(rf.strip_kaoqian_title("【考点4】角平分线模型（8分） 在 ∠MAN 中，点 B"),
                         "在 ∠MAN 中，点 B")

    def test_strip_multiline(self):
        self.assertEqual(rf.strip_kaoqian_title("【考点3】内角和与外角性质(3分)\n如图，三角形纸片"),
                         "如图，三角形纸片")

    def test_keep_normal_question_with_score(self):
        """普通题干自带的（8分）分值标注不受影响（无【考点】栏头）"""
        self.assertEqual(rf.strip_kaoqian_title("如图，普通题干（8分）不剥"),
                         "如图，普通题干（8分）不剥")

    def test_keep_incomplete_pattern(self):
        """【】后无（N分）分值 → 不匹配，原样保留"""
        self.assertEqual(rf.strip_kaoqian_title("【考点】只有标记无分值 保留原样"),
                         "【考点】只有标记无分值 保留原样")

    def test_dedup_after_strip(self):
        """清洗后两份转录（都经清洗）题干一致 → 查重命中，不再重复入库"""
        store.set_test_mode()
        import tempfile
        tmp = tempfile.mkdtemp()
        store.set_test_data_dir(tmp)
        raw = "【考点4】角平分线模型（8分） 在 ∠MAN 中，点 B、C 分别是 AM、AN 上一点，求角。"
        store.add({"question": {"text": rf.strip_kaoqian_title(raw)}})   # 第一次转录（已清洗）
        dup = store.find_duplicate(rf.strip_kaoqian_title(raw))          # 第二次转录（同样清洗）
        self.assertIsNotNone(dup)


if __name__ == "__main__":
    unittest.main()
