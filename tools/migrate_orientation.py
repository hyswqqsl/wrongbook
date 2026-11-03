# -*- coding: utf-8 -*-
"""一次性迁移脚本：把历史草稿单的照片转正 + figure_boxes 同步变换。

用法（在 wrongbook/ 下）：  ../.venv/bin/python tools/migrate_orientation.py [--dry-run]

背景：见 tests/test_orientation.py —— 照片带 EXIF orientation=6 时，
浏览器显示方向与服务端像素不一致，导致「抓取原图」转 90°、手动框选错位。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)
os.chdir(PKG)

import reviewflow  # noqa: E402
from store import load_sheets, save_sheets  # noqa: E402


def main():
    dry = "--dry-run" in sys.argv
    db = load_sheets()
    changed = []
    for s in db["sheets"]:
        photo = s.get("photo")
        if not photo or not os.path.exists(photo):
            continue
        from PIL import Image
        o = reviewflow.exif_orientation(Image.open(photo))
        if o == 1:
            continue
        before = [list(b) for q in s.get("questions", []) for b in (q.get("figure_boxes") or [])]
        if dry:
            print("  [dry] %s orientation=%s → 需转正（%d 个 box）" % (s["id"], o, len(before)))
            changed.append(s["id"])
            continue
        if reviewflow.migrate_sheet_orientation(s):
            changed.append(s["id"])
            print("  ✓ %s orientation=%s 已转正，%d 个 box 已变换" % (
                s["id"], o, len(before)))
    if dry:
        print("\n[dry-run] 将迁移 %d 张草稿单：%s" % (len(changed), changed))
        return
    save_sheets(db)
    print("\n完成：迁移 %d 张草稿单" % len(changed))


if __name__ == "__main__":
    main()
