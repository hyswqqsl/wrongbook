# -*- coding: utf-8 -*-
"""章节回归教材章迁移（v1.2o，一次性）：历史 analysis.chapter 为专题结构
（「专题集 · 类别」二级名 / v1.2l 产物，或更早的类别单名）
→ 迁回【教材章名】（data/kb/knowledge_points.json 的 chapters 结构）。

映射链（依次尝试，都不中保持原样 → 工作台归「未分类」）：
  1. 章名「 · 」后的类别名（人审确认过的专题大类，语义最强）对 KB kp 名匹配
     （精确/包含 → difflib 0.6；如「全等的性质与判定」→「全等三角形的性质」→ 第一章）
  2. 题的 knowledge_points / kp_path 中的 kp 逐个匹配后按章投票（同阈值），多者胜
同时重写 kp_path = [章, "", ""]（知识点筛选由 knowledge_points 驱动，不把
清单外旧名写进 kp_path 叶子）。幂等：chapter 已是 KB 章名则跳过。用法：
  .venv/bin/python tools/migrate_chapter.py          # 预览（dry-run）
  .venv/bin/python tools/migrate_chapter.py --apply  # 落盘（自动 .bak）
"""
import os
import sys
import json
import glob
import difflib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import kbtree  # noqa: E402


def build_kp_index():
    """KB 精简清单 → kp→章 反查表 + kp 名列表"""
    kp2ch, names = {}, []
    for c in kbtree.knowledge_points().get("chapters", []):
        for k in c.get("kps", []):
            kp2ch[k] = c["title"]
            names.append(k)
    return kp2ch, names


def match_chapter(text, kp2ch, names, cutoff=0.6):
    """一个名称（类别名或 kp）→ KB 章；精确/包含 → difflib；不中返回 None"""
    text = (text or "").strip()
    if not text:
        return None
    if text in kp2ch:
        return kp2ch[text]
    for k in kp2ch:
        if k in text or text in k:
            return kp2ch[k]
    close = difflib.get_close_matches(text, names, n=1, cutoff=cutoff)
    return kp2ch[close[0]] if close else None


def vote_chapter(cands, kp2ch, names):
    """多个名称逐个匹配后按章投票；多者胜，平票取首个命中章"""
    votes = []
    for c in cands:
        ch = match_chapter(c, kp2ch, names)
        if ch:
            votes.append(ch)
    if not votes:
        return None
    return max(set(votes), key=votes.count)


def migrate(fp, apply=False):
    db = json.load(open(fp, encoding="utf-8"))
    kp2ch, names = build_kp_index()
    kb_chapters = set(kp2ch.values())
    changed = []
    for q in db.get("questions", []):
        an = q.get("analysis") or {}
        ch = (an.get("chapter") or "").strip()
        if not ch or ch in kb_chapters:
            continue   # 空 / 已是教材章（幂等）
        # ① 类别名（人审确认的专题大类，语义最强信号）
        cat_name = ch.split("·")[-1].strip() if "·" in ch else ch
        hit_ch = match_chapter(cat_name, kp2ch, names)
        # ② 题的 kp 逐个匹配后按章投票
        if not hit_ch:
            cand_kps = [k for k in (an.get("knowledge_points") or [])
                        + [p for p in (an.get("kp_path") or []) if p] if k]
            hit_ch = vote_chapter(cand_kps, kp2ch, names)
        if not hit_ch:
            continue   # 映射不上：保持原样
        an["chapter"] = hit_ch
        an["kp_path"] = [hit_ch, "", ""]   # 知识点筛选由 knowledge_points 驱动，不写旧名进叶子
        changed.append((q.get("id"), ch, hit_ch))
    if apply and changed:
        bak = fp + ".bak"
        if os.path.exists(bak):
            os.remove(bak)
        os.rename(fp, bak)
        json.dump(db, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return changed


def main():
    apply = "--apply" in sys.argv
    total = 0
    for fp in sorted(glob.glob(os.path.join(BASE, "data", "users", "*", "wrongbook.json"))):
        uid = fp.split(os.sep)[-2]
        changed = migrate(fp, apply=apply)
        for qid, old, new in changed:
            print(f"  [{uid}] {qid}: {old!r} -> {new!r}")
        total += len(changed)
    print(("已迁移" if apply else "将迁移（dry-run，加 --apply 落盘）") +
          f"：共 {total} 题章节回归教材章")


if __name__ == "__main__":
    main()
