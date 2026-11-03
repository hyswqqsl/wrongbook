# -*- coding: utf-8 -*-
"""stats —— 工作台统计：数量/类型/难度/章节/错因分布/练习情况"""
from collections import Counter
from store import load, streak

TYPE_CN = {"single": "单选", "multiple": "多选", "fill": "填空", "short": "问答"}
REASON_CN = {"careless": "马虎", "unknown": "不会", "other": "其他"}


def dashboard(chapter=None, difficulty=None, kp=None, reason=None,
              date_from=None, date_to=None, include_mastered=False):
    """工作台统计。筛选参数（全部可选）：章节精确、难度(1/2/3)、知识点子串、
    错因(kind或"none"=未标注)、录入时间段(created_at 日期 from~to)、
    include_mastered（列表含已归档题；默认仅显示在库）。
    筛选作用于全部指标；facets（筛选项候选）始终按全量题计算。"""
    qs = load()["questions"]

    def _match(q):
        an = q.get("analysis", {}) or {}
        if chapter and (an.get("chapter") or "").strip() != chapter:
            return False
        if difficulty and int(an.get("difficulty", 2)) != int(difficulty):
            return False
        if reason:
            kind = (q.get("mistake_reason") or {}).get("kind", "")
            if reason == "none":
                if kind:
                    return False
            elif kind != reason:
                return False
        if kp:
            kp_path = an.get("kp_path") or []
            hay = " ".join([k for k in kp_path if k] + (an.get("knowledge_points") or []))
            if kp not in hay:
                return False
        if date_from or date_to:
            d = (q.get("created_at") or "")[:10]
            if date_from and d < date_from:
                return False
            if date_to and d > date_to:
                return False
        return True

    qs = [q for q in qs if _match(q)]
    active = [q for q in qs if not q.get("mastered_at")]
    mastered = [q for q in qs if q.get("mastered_at")]
    dist_type = Counter(TYPE_CN.get(q.get("type"), q.get("type", "?")) for q in active)
    diff_map = {1: "① 简单", 2: "② 中等", 3: "③ 困难"}
    dist_diff = Counter(diff_map.get(int(q.get("analysis", {}).get("difficulty", 2)), "② 中等")
                        for q in active)
    dist_chapter = Counter((q.get("analysis", {}).get("chapter") or "未分类").strip()
                           for q in active)
    dist_kp = Counter()
    for q in active:
        for kp in (q.get("analysis", {}).get("knowledge_points") or [])[:3]:
            dist_kp[kp.strip()] += 1
    dist_reason = Counter(REASON_CN.get((q.get("mistake_reason") or {}).get("kind", ""), "未标注")
                          for q in active)
    list_pool = qs if include_mastered else active
    practice_rows = []
    for q in list_pool:
        pr = q.get("practice", [])
        an = q.get("analysis", {}) or {}
        kp_path = an.get("kp_path") or []
        kp = next((k for k in [kp_path[2] if len(kp_path) > 2 else "",
                               kp_path[1] if len(kp_path) > 1 else "",
                               *(an.get("knowledge_points") or [])] if k), "")
        practice_rows.append({
            "id": q["id"],
            "text": (q.get("question", {}) or {}).get("text", ""),   # 全文：前端CSS单行截断（截字会切断公式配对）
            "chapter": (an.get("chapter") or "").strip() or "未分类",
            "difficulty": int(an.get("difficulty", 2)),
            "reason": REASON_CN.get((q.get("mistake_reason") or {}).get("kind", ""), "未标注"),
            "kp": kp,
            "mastered": bool(q.get("mastered_at")),
            "times": len(pr),
            "correct": sum(1 for p in pr if p.get("correct")),
            "streak": streak(q),
            "need": max(0, 3 - streak(q)),  # 距离"连对3次可归档"还差几次
        })
    practice_rows.sort(key=lambda r: (r["times"], -r["need"]))
    # 筛选项候选：全量题（含已归档，与筛选池同口径；不受当前筛选影响）。
    # 知识点候选必须与列表展示同源（题库标注的 kp_path 叶子/knowledge_points），
    # 否则下拉选了教材名却搜不到题（列表显示的是题库标注名）。
    _all = load()["questions"]
    kp_by_chapter = {}
    for q in _all:
        an = q.get("analysis", {}) or {}
        ch = (an.get("chapter") or "").strip() or "未分类"
        kp_path = an.get("kp_path") or []
        kps = {k for k in ([kp_path[2]] if len(kp_path) > 2 else [])
               + (an.get("knowledge_points") or []) if k}
        for k in kps:
            kp_by_chapter.setdefault(ch, {})
            kp_by_chapter[ch][k] = kp_by_chapter[ch].get(k, 0) + 1
    facets = {
        "chapters": sorted({(q.get("analysis", {}) or {}).get("chapter", "").strip()
                            for q in _all} - {""}),
        "reasons": sorted({(q.get("mistake_reason") or {}).get("kind", "") or "none"
                           for q in _all}),
        # {章节: {知识点: 出现次数}}——按出现次数降序排列
        "kp_by_chapter": {ch: [k for k, _ in sorted(m.items(), key=lambda x: -x[1])]
                          for ch, m in kp_by_chapter.items()},
        "kps": sorted({k for m in kp_by_chapter.values() for k in m}),
    }
    return {
        "facets": facets,
        "total": len(qs), "active": len(active), "mastered": len(mastered),
        "by_type": dict(dist_type), "by_difficulty": dict(dist_diff),
        "by_chapter": dict(dist_chapter.most_common()),
        "by_reason": dict(dist_reason),
        "by_knowledge_point": dict(dist_kp.most_common(12)),
        "zero_practice": sum(1 for q in active if not q.get("practice")),
        "ready_to_master": [q["id"] for q in active if streak(q) >= 3],
        "practice_overview": practice_rows,
    }
