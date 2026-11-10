# -*- coding: utf-8 -*-
"""kbtree —— 教材知识库(KB) → 章/节/知识点树

数据源：父项目 build_kb.py 产出的教材 KB JSON（index: chapter/section/kind/title）。
  【浙教版】八年级上册(2025秋版)数学电子课本_1-100_kb.json   （第一~三章）
  【浙教版】八年级上册(2025秋版)数学电子课本_101-201_kb.json （第三~五章）
用途：
  - GET /api/kb        → 前端树形知识点选择器
  - compact_summary()  → 注入转录期"年级/知识点自动归类"的 prompt
"""
import os, json, shutil
import paths

KB_FILES = [
    "【浙教版】八年级上册(2025秋版)数学电子课本_1-100_kb.json",
    "【浙教版】八年级上册(2025秋版)数学电子课本_101-201_kb.json",
]

# 年级候选（旧版初中候选，仅作 classify 兜底词表；新版录入页年级来自 grade_options()）
GRADE_OPTIONS = ["八年级上册", "八年级下册", "九年级上册", "九年级下册", "其他"]

# 年级全集数据源（前置年级选择 v1.4.1）：小学 primary_math_all（1~6 年级上下册）+
# 初中 junior_math_7_9_all（7~9 年级上下册，含七年级）→ 合计 1~9 年级全支持；
# 结构 {年级名: {grade, chapters:[{title, kps:[...]}]}}，key 序即年级序；
# knowledge_points.json 为旧版八上清单（未选年级时的回落词表）
GRADE_SOURCES = ["primary_math_all.json", "junior_math_7_9_all.json"]

_CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9"}

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


def _kb_dir():
    """KB 目录解析（R1）：数据目录 data/kb，首次从随包种子（app_root/data/kb）播种。
    开发态两者同目录（自拷自零操作）；打包态种子在安装包内、可写副本在用户数据目录。
    播种按文件逐个补齐（已存在不覆盖）：升级覆盖安装能把新增的年级全集 json
    （primary_math_all / junior_math_7_9_all）带给老用户，手编清单仍不丢。"""
    if _KB_DIR:
        return _KB_DIR
    d = os.path.join(paths.DATA_HOME, "kb")
    src = os.path.join(paths.app_root(), "data", "kb")
    if os.path.isdir(src):
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(src):
            dst = os.path.join(d, f)
            if not os.path.exists(dst) and os.path.isfile(os.path.join(src, f)):
                shutil.copy2(os.path.join(src, f), dst)
    return d


def set_kb_dir(d=None):
    """测试钩子：从指定目录读 KB 文件（None=父项目根目录）"""
    global _cache, _KB_DIR, _grades_cache
    _cache = None
    _grades_cache = None
    _KB_DIR = d


def _load_kb(name):
    p = os.path.join(_kb_dir(), name)
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
    return os.path.join(_kb_dir(), "knowledge_points.json")


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
    paths.atomic_write_json(p, out)   # R1 收口：手编清单也走 fsync+.bak
    return out


# ---------- 前置年级：按年级分文件的细分知识点清单（v1.4.1） ----------

_grades_cache = None


def grade_slug(grade):
    """「四年级上册」→ "4_1"（kp_<slug>.json 文件名）；无法解析返回 None"""
    m = _re.match(r"^([一二三四五六七八九])年级(上|下)册$", (grade or "").strip())
    if not m:
        return None
    return "{0}_{1}".format(_CN_NUM.get(m.group(1), ""), "1" if m.group(2) == "上" else "2")


def grade_options(force=False):
    """全部年级候选（两个全集 json 的 key 序：小学一~六年级 + 初中八/九年级）"""
    global _grades_cache
    if _grades_cache and not force:
        return _grades_cache
    out = []
    for name in GRADE_SOURCES:
        data = _load_kb(name)
        if not data:
            continue
        for k in data.keys():
            if k not in out:
                out.append(k)
    _grades_cache = out
    return out


def knowledge_points_for_grade(grade, force=False):
    """按年级取细分知识点清单 → {"grade","chapters":[{"title","kps":[...]}]}；未知/其他 → None

    首次从年级全集 json（GRADE_SOURCES）提取落盘 data/kb/kp_<slug>.json
    （四年级上册→kp_4_1.json、九年级下册→kp_9_2.json，与 knowledge_points.json 同构），
    之后直读精简文件：转录 prompt 只塞该年级的清单 → 大模型在正确年级的章节与
    知识点中受控匹配，不再盲猜年级。清单可直接手编对应 kp_*.json（立即生效），
    force=True 从全集 json 重提取。"""
    g = (grade or "").strip()
    if not g or g == "其他":
        return None
    slug = grade_slug(g)
    if not slug:
        return None
    p = os.path.join(_kb_dir(), "kp_{0}.json".format(slug))
    if not force and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass   # 手编坏档 → 重提取自愈
    for name in GRADE_SOURCES:
        data = _load_kb(name) or {}
        node = data.get(g)
        if node and node.get("chapters"):
            out = {"grade": g,
                   "chapters": [{"title": c.get("title", ""),
                                 "kps": [k for k in (c.get("kps") or []) if k]}
                                for c in node["chapters"]]}
            paths.atomic_write_json(p, out)   # 手编清单同款 fsync+.bak
            return out
    return None
