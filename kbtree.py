# -*- coding: utf-8 -*-
"""kbtree —— 教材知识库(KB) → 章/节/知识点树

数据源：父项目 build_kb.py 产出的教材 KB JSON（index: chapter/section/kind/title）。
  【浙教版】八年级上册(2025秋版)数学电子课本_1-100_kb.json   （第一~三章）
  【浙教版】八年级上册(2025秋版)数学电子课本_101-201_kb.json （第三~五章）
用途：
  - GET /api/kb        → 前端树形知识点选择器
  - compact_summary()  → 注入转录期"年级/知识点自动归类"的 prompt
"""
import os
import json
from common import BASE

KB_FILES = [
    "【浙教版】八年级上册(2025秋版)数学电子课本_1-100_kb.json",
    "【浙教版】八年级上册(2025秋版)数学电子课本_101-201_kb.json",
]

# 年级候选（录入页"年级"列表；八上知识树内的题默认八年级上册）
GRADE_OPTIONS = ["八年级上册", "八年级下册", "九年级上册", "九年级下册", "其他"]

# KB 里混入的非知识点标题（视觉转录噪声）
_NOISE = {"章导语", "分析", "解", "答", "合作学习", "做一做", "探究", "思考",
          "例", "小结", "目标与评定", "复习题", "课内练习", "作业题", "章节导图",
          "设计题", "例题解答"}
_NOISE_PREFIX = ("例题", "图2-", "图5-", "目标 ", "目标", "阅读材料", "综合实践",
                 "课题学习")   # 前缀命中即弃

import re as _re
_SEC_RE = _re.compile(r"^\d+\.\d+")   # 正式小节编号（如 "1.5 三角形全等的判定"）

_cache = None
_KB_DIR = None   # 测试钩子：覆盖 KB 所在目录


def set_kb_dir(d=None):
    """测试钩子：从指定目录读 KB 文件（None=父项目根目录）"""
    global _cache, _KB_DIR
    _cache = None
    _KB_DIR = d


def _load_kb(name):
    p = os.path.join(_KB_DIR or os.path.join(BASE, "data", "kb"), name)
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def _is_kp_title(t):
    t = (t or "").strip()
    if not t or t in _NOISE:
        return False
    if any(t.startswith(px) for px in _NOISE_PREFIX):
        return False
    return 3 <= len(t) <= 40


def build_tree(force=False):
    """合并各卷 KB → {grade, chapters:[{title, sections:[{title, kps:[...]}]}]}

    - 章按 KB 出现顺序（1-100 卷在前），卷内按 index 首次出现顺序
    - 节标题取 index.section（如 "1.5 三角形全等的判定"），保序去重
    - 知识点只取 kind=knowledge 且通过噪声过滤的 title，保序去重
    """
    global _cache
    if _cache and not force:
        return _cache
    chapters = {}   # title -> {"title", "sections": {sec_title: {"title","kps"}}}
    for name in KB_FILES:
        kb = _load_kb(name)
        if not kb:
            continue
        for it in kb.get("index", []):
            ch = (it.get("chapter") or "").strip()
            if not ch:
                continue
            c = chapters.setdefault(ch, {"title": ch, "sections": {}})
            sec = (it.get("section") or "").strip()
            if sec and not _SEC_RE.match(sec):
                sec = ""   # "课内练习/作业题/目标与评定" 等非正式小节 → 不进树
            if sec and sec not in c["sections"]:
                c["sections"][sec] = {"title": sec, "kps": []}
            if sec and it.get("kind") == "knowledge":
                t = (it.get("title") or "").strip()
                if _is_kp_title(t) and t not in c["sections"][sec]["kps"]:
                    c["sections"][sec]["kps"].append(t)
    # 兜底：KB 里知识点条目可能全是空标题（视觉转录缺失）→ 用节名自身作可选项
    for c in chapters.values():
        for sec in c["sections"].values():
            if not sec["kps"]:
                sec["kps"] = [sec["title"]]
    tree = {"grade": "八年级上册",
            "chapters": [{"title": c["title"],
                          "sections": list(c["sections"].values())}
                         for c in chapters.values()]}
    _cache = tree
    return tree


def compact_summary(tree=None):
    """给 LLM 的紧凑知识树文本（章/节/知识点）"""
    tree = tree or build_tree()
    lines = []
    for c in tree["chapters"]:
        lines.append("《%s》" % c["title"])
        for s in c["sections"]:
            kps = "；".join(s["kps"][:6]) if s["kps"] else "（见教材）"
            lines.append("  %s：%s" % (s["title"], kps))
    return "\n".join(lines)


# ---------- 精简细分知识点清单（v1.2i） ----------

def _kp_path():
    return os.path.join(_KB_DIR or os.path.join(BASE, "data", "kb"), "knowledge_points.json")


def knowledge_points(force=False):
    """精简细分知识点清单 → {"grade","chapters":[{"title","kps":[...]}]}

    首次调用从教材 KB 大 json 提炼（build_tree → 去重），落盘 knowledge_points.json
    （~2KB）；之后直接读精简文件，不再解析两个 ~300KB 的教材大 json（省解析时间，
    prompt 也只塞这份精简清单）。KB 更新后可 force=True 重新提炼。
    """
    p = _kp_path()
    if not force and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    tree = build_tree()
    out = {"grade": tree.get("grade", ""), "chapters": []}
    seen = set()
    for ch in tree["chapters"]:
        kps = []
        for s in ch["sections"]:
            for k in s["kps"]:
                if k not in seen:   # 跨节/跨卷去重
                    seen.add(k)
                    kps.append(k)
        if kps:
            out["chapters"].append({"title": ch["title"], "kps": kps})
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out
