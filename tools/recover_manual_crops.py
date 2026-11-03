# -*- coding: utf-8 -*-
"""恢复旧代码产出的「手动框选」裁剪图。

背景（2026-11-01 第二次方向 bug 报告）：
  方向修复前的旧代码，把用户在【显示空间(=转正空间)】框的 box 直接用到
  【原始像素图】上裁剪 → 裁出的图横躺 90°、且区域也可能不对。

  这批旧产物里**用户的框选意图**仍然可以从像素找回：
    old_m = raw.crop(U)        U = 用户在显示图上的归一化 box
    raw   = upright.transpose(ROTATE_270)
  → 在 raw 上用模板匹配定位 old_m → 得到 U → 再用 upright.crop(U) 重新裁
  → 拿到与用户原意一致、方向正确的图。

用法（在 wrongbook/ 下）：
  ../.venv/bin/python tools/recover_manual_crops.py [--dry-run]

注意：模板匹配是启发式，恢复后应人工核对（脚本会打印每张恢复图供检查）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)
os.chdir(PKG)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import reviewflow  # noqa: E402
from store import load_sheets, save_sheets  # noqa: E402


def locate_template(tpl, big):
    """在 big 中定位 tpl（归一化互相关）→ (x, y, w, h) 或 None"""
    from scipy import signal
    t = np.asarray(tpl, dtype=float)
    b = np.asarray(big, dtype=float)
    th, tw = t.shape
    bh, bw = b.shape
    if th > bh or tw > bw:
        return None
    t = (t - t.mean()) / (t.std() + 1e-6)
    res = signal.correlate(b, t, mode="valid")
    idx = np.unravel_index(np.argmax(res), res.shape)
    return int(idx[1]), int(idx[0]), tw, th


def recover_sheet(sheet, dry=False):
    """恢复单张草稿单的手动裁剪图 → 返回 [(no, old_path, new_path, U)]"""
    photo = sheet.get("photo")
    if not photo or not os.path.exists(photo):
        return []
    up = Image.open(photo).convert("RGB")
    # 旧代码从原始像素图裁；原始像素图 = 转正图逆时针 90
    raw = up.transpose(Image.ROTATE_270)
    results = []
    for q in sheet.get("questions", []):
        for c in list(q.get("figure_crops") or []):
            if not (c.endswith("_m.png") and os.path.exists(c)):
                continue
            if q.get("figure_manual_box"):     # 新版已存档 box → 无需恢复
                continue
            old = Image.open(c).convert("L")
            r = locate_template(old, raw.convert("L"))
            if not r:
                print("  ✗ %s Q%s 定位失败（尺寸不匹配）" % (sheet["id"][-6:], q["no"]))
                continue
            x, y, w, h = r
            U = [x / raw.width, y / raw.height, (x + w) / raw.width, (y + h) / raw.height]
            new = up.crop((int(U[0] * up.width), int(U[1] * up.height),
                           int(U[2] * up.width), int(U[3] * up.height)))
            new_path = c[:-4] + "_fixed.png"
            if not dry:
                new.save(new_path)
                q["figure_manual_box"] = [float(v) for v in U]
                # 恢复后的图排在前面（作为该题当前权威原图）
                q["figure_crops"] = [new_path] + [p for p in q["figure_crops"] if p != c]
            results.append((q["no"], c, new_path, U))
            print("  ✓ %s Q%s → 恢复 U=%s  尺寸=%s" % (
                sheet["id"][-6:], q["no"], [round(v, 4) for v in U], new.size))
    return results


def main():
    dry = "--dry-run" in sys.argv
    db = load_sheets()
    total = 0
    for s in db["sheets"]:
        res = recover_sheet(s, dry=dry)
        if res:
            total += len(res)
    if dry:
        print("\n[dry-run] 将恢复 %d 张手动裁剪图" % total)
        return
    save_sheets(db)
    print("\n完成：恢复 %d 张手动裁剪图" % total)


if __name__ == "__main__":
    main()
