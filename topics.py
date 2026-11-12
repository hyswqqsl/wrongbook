# -*- coding: utf-8 -*-
"""topics —— 专题模块（V1.2）：专题集 → 类别 → 典型例题 + 每用户练习记录

数据分两层：
- 全局 data/topics.json：专题集/类别/例题（教学内容，所有用户共享，家长录一次）
- 每用户 data/users/<uid>/topics.json：练习记录（对错流水；连对 3 次 = 已掌握）

例题录入主入口 = 排版机转录 → 人审卡选「存入专题」→ 提交时零 LLM 落库
（题干/图形/答案已由排版机+人审定稿，专题归类由人点选，无需再调模型）。
做错的例题：人手动点「存入错题本」→ 转为标准错题记录走复习/归档闭环。
"""
import os
import json
import shutil
import threading
import time
import uuid
import difflib

import paths
import store

LOCK = threading.Lock()

# 首次运行种入「初二中考必考专题」10 类（难度：1-3 简单 / 4,5,7,8,9 中等 / 6,10 压轴）
SEED_SET = {
    "name": "初二中考必考专题",
    "categories": [
        {"no": 1, "name": "三边关系", "points": 3, "difficulty": "easy"},
        {"no": 2, "name": "中线、高、角平分线", "points": 8, "difficulty": "easy"},
        {"no": 3, "name": "内角和与外角性质", "points": 3, "difficulty": "easy"},
        {"no": 4, "name": "角平分线模型", "points": 8, "difficulty": "medium"},
        {"no": 5, "name": "全等的性质与判定", "points": 8, "difficulty": "medium"},
        {"no": 6, "name": "全等模型", "points": 12, "difficulty": "final"},
        {"no": 7, "name": "角平分线的性质与判定", "points": 8, "difficulty": "medium"},
        {"no": 8, "name": "垂直平分线", "points": 8, "difficulty": "medium"},
        {"no": 9, "name": "三线合一", "points": 8, "difficulty": "medium"},
        {"no": 10, "name": "将军饮马", "points": 3, "difficulty": "final"},
    ],
}

DIFF_CN = {"easy": "简单", "medium": "中等", "final": "压轴"}


# ---------- 路径（全局文件随 store._data_dir() 走，测试可重定向；练习记录按用户） ----------

def _topics_path():
    return os.path.join(store._data_dir(), "topics.json")


def _crops_dir():
    """例题图形目录：云端按激活码隔离（topic_crops/<code>/，媒体路由租户校验用）；桌面全局共享"""
    import tenant_ctx
    t = tenant_ctx.get_tenant()
    if t:
        return os.path.join(store._data_dir(), "topic_crops", t[0])
    return os.path.join(store._data_dir(), "topic_crops")


def _practice_path():
    return os.path.join(store.user_dir(), "topics.json")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix):
    return prefix + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def _atomic_write(path, data):
    paths.atomic_write_json(path, data)   # R1 收口：fsync + .bak 三代轮换


# ---------- 全局专题库 ----------

def build_seed():
    """内置种子专题树（每户克隆一份：新 ts/tc id，例题空）——桌面首种与云端首跑共用"""
    seed_set = dict(SEED_SET)
    seed_set["id"] = _new_id("ts")
    seed_set["created_at"] = _now()
    seed_set["categories"] = [dict(c, id=_new_id("tc"), examples=[])
                              for c in SEED_SET["categories"]]
    return {"version": 1, "sets": [seed_set]}


def _cloud_topics():
    """云端租户 → cloud_store 专题存储；桌面返回 None（走全局 JSON，原行为）"""
    import tenant_ctx
    if tenant_ctx.get_tenant() is None:
        return None
    import cloud_store
    return cloud_store


def load_topics():
    """读专题库：云端=每户一份（首跑种子克隆）；桌面=全局文件（缺失时种入，幂等）"""
    b = _cloud_topics()
    if b:
        return b.load_topics()
    p = _topics_path()
    if not os.path.exists(p):
        with LOCK:
            if not os.path.exists(p):   # 双检：并发首调只种一次
                _atomic_write(p, build_seed())
    with LOCK:
        return json.load(open(p, encoding="utf-8"))


def save_topics(db):
    b = _cloud_topics()
    if b:
        return b.save_topics(db)
    with LOCK:
        _atomic_write(_topics_path(), db)


def find_set(sid):
    return _find_set_in(load_topics(), sid)


def _find_set_in(db, sid):
    for s in db["sets"]:
        if s["id"] == sid:
            return s
    return None


def _find_cat_in(db, sid, cid):
    s = _find_set_in(db, sid)
    if not s:
        return None, None
    for c in s["categories"]:
        if c["id"] == cid:
            return s, c
    return s, None


def find_category(sid, cid):
    return _find_cat_in(load_topics(), sid, cid)


def _find_example_in(db, eid):
    for s in db["sets"]:
        for c in s["categories"]:
            for e in c.get("examples", []):
                if e["id"] == eid:
                    return s, c, e
    return None, None, None


def find_example(eid):
    """按例题 id 全库查找 → (set, cat, example) | (None, None, None)"""
    return _find_example_in(load_topics(), eid)


def add_set(name):
    db = load_topics()
    rec = {"id": _new_id("ts"), "name": str(name).strip() or "未命名专题",
           "created_at": _now(), "categories": []}
    db["sets"].append(rec)
    save_topics(db)
    return rec


def delete_set(sid):
    db = load_topics()
    before = len(db["sets"])
    db["sets"] = [s for s in db["sets"] if s["id"] != sid]
    if len(db["sets"]) < before:
        save_topics(db)
        # 连带清理该专题全部例题的图形文件（孤儿检测：不在剩余库引用即删）
        _deleted_set_files(sid, db)
        return True
    return False


def _deleted_set_files(sid, db_after):
    """删除专题后清理无主图形文件：扫剩余库收集存活路径，删 crops 目录下的孤儿"""
    alive = set()
    for s in db_after["sets"]:
        for c in s["categories"]:
            for e in c.get("examples", []):
                if e.get("figure_crop"):
                    alive.add(os.path.abspath(e["figure_crop"]))
    d = _crops_dir()
    if os.path.isdir(d):
        for f in os.listdir(d):
            fp = os.path.abspath(os.path.join(d, f))
            if fp not in alive:
                try:
                    os.remove(fp)
                except OSError:
                    pass


def add_category(sid, name, points=3, difficulty="medium", no=None):
    db = load_topics()
    s = _find_set_in(db, sid)
    if not s:
        raise ValueError("专题集不存在")
    if difficulty not in DIFF_CN:
        raise ValueError("难度必须是 easy|medium|final")
    cat = {"id": _new_id("tc"), "no": int(no) if no else len(s["categories"]) + 1,
           "name": str(name).strip(), "points": int(points or 3),
           "difficulty": difficulty, "examples": []}
    s["categories"].append(cat)
    save_topics(db)
    return cat


def patch_category(sid, cid, fields):
    db = load_topics()
    s, c = _find_cat_in(db, sid, cid)
    if not c:
        raise ValueError("类别不存在")
    for k in ("name", "points", "difficulty", "no"):
        if k in fields and fields[k] is not None:
            c[k] = fields[k]
    save_topics(db)
    return c


def delete_category(sid, cid):
    db = load_topics()
    s = _find_set_in(db, sid)
    if not s:
        return False
    removed = [c for c in s["categories"] if c["id"] == cid]
    if not removed:
        return False
    s["categories"] = [c for c in s["categories"] if c["id"] != cid]
    save_topics(db)
    for c in removed:
        for e in c.get("examples", []):
            _remove_example_file(e)
    return True


# ---------- 例题 ----------

def _remove_example_file(e):
    # 图形（v1.2q 修复）：topic_crops 文件可能同时被错题记录引用（历史 to_book/双目的地共享同一文件），
    # 有引用必须保留（否则错题详情图形丢失——q20261106-111019 事故根因）
    crop = e.get("figure_crop")
    if crop and os.path.exists(crop) and not crop_referenced(crop, exclude_eid=e["id"]) \
            and not _book_crop_ref(crop) and not store.crop_draft_ref(crop):
        try:
            os.remove(crop)
        except OSError:
            pass
    # video（v1.4.2 视频功能已删除）：历史残留字段/文件忽略不清洗


def crop_referenced(crop, exclude_eid=None):
    """图形文件是否仍被（其他）专题例题引用"""
    db = load_topics()
    for s in db["sets"]:
        for c in s["categories"]:
            for e in c.get("examples", []):
                if e.get("figure_crop") == crop and e.get("id") != exclude_eid:
                    return True
    return False


def _book_crop_ref(crop):
    """图形文件是否仍被错题记录引用（历史 to_book/双目的地与例题共享 topic_crops 文件；异常宁可不删）"""
    try:
        return any((q.get("question") or {}).get("figure_crop") == crop
                   for q in store.load()["questions"])
    except Exception:
        return True


def _copy_crop(src, eid):
    """例题图形落盘到全局 topic_crops/<eid>.<ext>（全局共享，与用户 crops 目录解耦）"""
    if not src or not os.path.exists(src):
        return None
    os.makedirs(_crops_dir(), exist_ok=True)
    ext = os.path.splitext(src)[1] or ".png"
    dst = os.path.join(_crops_dir(), eid + ext)
    shutil.copy(src, dst)
    return dst


def _category_dup(cat, text, threshold=0.92):
    """同类内查重：归一化相似度 ≥ threshold 视为重复（防同一照片重复存入）"""
    t = store.norm_text(text)
    if len(t) < 8:
        return None
    for e in cat.get("examples", []):
        et = store.norm_text(e.get("text", ""))
        if not et:
            continue
        if abs(len(et) - len(t)) > max(len(et), len(t)) * (1 - threshold):
            continue
        if difflib.SequenceMatcher(None, t, et).ratio() >= threshold:
            return e
    return None


def add_example(sid, cid, text, choices=None, answer=None, ex_type="fill",
                analysis="", crop_src=None, spec=None, source="",
                kps=None):
    """新增例题（pipeline 从草稿题组装；crop 文件复制进全局 topic_crops）"""
    db = load_topics()
    s, cat = _find_cat_in(db, sid, cid)
    if not cat:
        raise ValueError("专题类别不存在")
    text = (text or "").strip()
    if not text:
        raise ValueError("题干为空")
    dup = _category_dup(cat, text)
    if dup:
        raise DuplicateExample(dup["id"])
    eid = _new_id("te")
    ex = {"id": eid, "created_at": _now(), "type": ex_type or "fill",
          "text": text, "choices": choices or None, "answer": (answer or "").strip(),
          "analysis": analysis or "", "figure_crop": _copy_crop(crop_src, eid),
          "figure_spec": spec, "source": source or "",
          # v1.2q：与错题本详情同口径（转录期受控 kps；思路/易错由分析补全）
          "kps": [k for k in (kps or []) if k][:3],
          "key_idea": "", "pitfall": ""}
    cat["examples"].append(ex)
    save_topics(db)
    return ex


class DuplicateExample(Exception):
    def __init__(self, eid):
        self.eid = eid
        super().__init__("与已有例题 " + eid + " 重复")


# 专题例题按需分析（v1.2q）：仅存专题走 0 LLM 快速入库（D25），思路/易错点/知识点
# 由人点「🤖 分析」按需生成（一次纯文本调用）；kps 受控收敛到教材 KB 清单。
ANALYZE_PROMPT = """你是初中数学教研专家。下面是一道典型例题（可能含多个小问）及其答案。
请给出面向复习的分析：

- key_idea：核心思路/破题点（1~2 句，讲"怎么想"、关键辅助线或模型，不写完整过程）
- pitfall：易错点（学生在这道题上最容易错的一处，1 句，具体到动作）
- kps：该题考查的知识点，从【知识点清单】中**逐字**选 1~3 个（禁止编造清单外名称）

例题：
{question}

答案：{answer}

知识点清单：{kplist}

只输出 JSON（不要其他文字）：
{{"key_idea":"…","pitfall":"…","kps":["…"]}}"""


def analyze_example(eid):
    """例题按需分析：LLM 生成思路/易错点 + 受控知识点，写回例题（覆盖旧值）。"""
    s, c, e = find_example(eid)
    if not e:
        raise ValueError("例题不存在")
    from common import make_client
    import reviewflow as rf
    import kbtree
    client, model = make_client(None)
    kb_kps = [{"kp": k, "chapter": ch["title"]}
              for ch in kbtree.knowledge_points().get("chapters", []) for k in ch["kps"]]
    prompt = ANALYZE_PROMPT.format(
        question=(e["text"] or "")[:900],
        answer=(e.get("answer") or "（无）")[:200],
        kplist="；".join(it["kp"] for it in kb_kps) or "（无）")
    data = rf.ask_json_vision(client, model, prompt, None, max_tokens=4000)
    fields = {"key_idea": str(data.get("key_idea") or "").strip()[:200],
              "pitfall": str(data.get("pitfall") or "").strip()[:200],
              "kps": rf.converge_kps(data.get("kps"), kb_kps)}
    if not fields["key_idea"] and not fields["pitfall"] and not fields["kps"]:
        raise RuntimeError("分析结果为空（模型未按格式输出）")
    return patch_example(eid, fields)


def patch_example(eid, fields):
    """手编例题（analysis/answer/text 等）；返回更新后的例题"""
    db = load_topics()
    s, c, e = _find_example_in(db, eid)
    if not e:
        raise ValueError("例题不存在")
    for k in ("text", "answer", "analysis", "choices",
              "key_idea", "pitfall", "kps"):   # v1.2q：与错题本详情同口径
        if k in fields:
            e[k] = fields[k]
    save_topics(db)
    return e


def delete_example(eid):
    db = load_topics()
    s, c, e = _find_example_in(db, eid)
    if not e:
        return False
    c["examples"] = [x for x in c["examples"] if x["id"] != eid]
    save_topics(db)
    _remove_example_file(e)
    return True


# ---------- 每用户练习记录 ----------

def _load_practice():
    b = _cloud_topics()
    if b:
        return b.load_practice()
    p = _practice_path()
    if not os.path.exists(p):
        return {"version": 1, "practice": {}}
    with LOCK:
        return json.load(open(p, encoding="utf-8"))


def _save_practice(db):
    b = _cloud_topics()
    if b:
        return b.save_practice(db)
    with LOCK:
        _atomic_write(_practice_path(), db)


def _streak(records):
    s = 0
    for p in reversed(records):
        if p.get("correct"):
            s += 1
        else:
            break
    return s


def record_practice(eid, correct):
    """记一次练习 → {streak, ready(连对3次=已掌握), count, correct}"""
    s, c, e = find_example(eid)
    if not e:
        raise ValueError("例题不存在")
    db = _load_practice()
    recs = db["practice"].setdefault(eid, [])
    recs.append({"at": _now(), "correct": bool(correct)})
    _save_practice(db)
    stk = _streak(recs)
    return {"streak": stk, "ready": stk >= 3, "count": len(recs),
            "correct": sum(1 for x in recs if x.get("correct"))}


# ---------- 前端视图（含每用户练习统计 + spec 渲染 SVG） ----------

def _render_svg(e):
    if not isinstance(e.get("figure_spec"), dict) or not e["figure_spec"].get("points"):
        return None
    try:
        import figureflow
        return figureflow.render(e["figure_spec"])
    except Exception:
        return None


def view():
    """GET /api/topics 数据：专题全集 + 例题练习统计（当前用户）"""
    db = load_topics()
    prac = _load_practice()["practice"]
    sets = []
    for s in db["sets"]:
        cats = []
        for c in s["categories"]:
            exs = []
            for e in c.get("examples", []):
                v = dict(e)
                recs = prac.get(e["id"]) or []
                stk = _streak(recs)
                v["practice"] = {"count": len(recs),
                                 "correct": sum(1 for x in recs if x.get("correct")),
                                 "streak": stk, "ready": stk >= 3}
                v["figure_svg"] = _render_svg(e)
                exs.append(v)
            cats.append(dict(c, examples=exs))
        sets.append(dict(s, categories=cats))
    return {"version": 1, "sets": sets}


