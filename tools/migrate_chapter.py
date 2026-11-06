# -*- coding: utf-8 -*-
"""章节二级迁移（v1.2n，一次性）：历史 analysis.chapter 为单名（如「全等的性质与判定」）
→ 升级为「专题集 · 类别」二级复合名（在 topics.json 中按类别名反查所属集）。

遍历 data/users/*/wrongbook.json 全部用户；幂等：含「 · 」的跳过、
topics 里找不到同名类别的保持原样（工作台归「未分组」）。
用法：.venv/bin/python tools/migrate_chapter.py
"""
import os
import sys
import json
import glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import topics  # noqa: E402


def main():
    # 类别名 → (集名, 类别) 反查表
    label_map = {}
    for s in topics.load_topics()["sets"]:
        for c in s["categories"]:
            label_map.setdefault(c["name"], (s["name"], c))

    total_changed = 0
    for fp in sorted(glob.glob(os.path.join(BASE, "data", "users", "*", "wrongbook.json"))):
        uid = fp.split(os.sep)[-2]
        db = json.load(open(fp, encoding="utf-8"))
        changed = 0
        for q in db.get("questions", []):
            an = q.get("analysis") or {}
            ch = (an.get("chapter") or "").strip()
            if not ch or " · " in ch:
                continue   # 已是二级或空
            hit = label_map.get(ch)
            if not hit:
                continue   # 清单里没有同名类别（如教材章节名），保持原样
            set_name, c = hit
            an["chapter"] = f"{set_name} · {c['name']}"
            an["kp_path"] = [set_name, "第{0}类 {1}".format(c["no"], c["name"]), ""]
            changed += 1
        if changed:
            json.dump(db, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  用户 {uid}：{changed} 题升级")
        total_changed += changed
    print(f"迁移完成：共 {total_changed} 题章节升级为二级（专题集 · 类别）")


if __name__ == "__main__":
    main()
