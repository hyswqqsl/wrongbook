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
DIFF_TO_LEVEL = {"easy": 1, "medium": 2, "final": 3}   # 映射错题本难度 1|2|3


# ---------- 路径（全局文件随 store._data_dir() 走，测试可重定向；练习记录按用户） ----------

def _topics_path():
    return os.path.join(store._data_dir(), "topics.json")


def _crops_dir():
    return os.path.join(store._data_dir(), "topic_crops")


def _practice_path():
    return os.path.join(store.user_dir(), "topics.json")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix):
    return prefix + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    os.replace(tmp, path)


# ---------- 全局专题库 ----------

def load_topics():
    """读全局专题库；文件缺失时种入「初二中考必考专题」（幂等：文件存在即不重种）"""
    p = _topics_path()
    if not os.path.exists(p):
        with LOCK:
            if not os.path.exists(p):   # 双检：并发首调只种一次
                seed_set = dict(SEED_SET)
                seed_set["id"] = _new_id("ts")
                seed_set["created_at"] = _now()
                seed_set["categories"] = [dict(c, id=_new_id("tc"), examples=[])
                                          for c in SEED_SET["categories"]]
                _atomic_write(p, {"version": 1, "sets": [seed_set]})
    with LOCK:
        return json.load(open(p, encoding="utf-8"))


def save_topics(db):
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
    if e.get("figure_crop") and os.path.exists(e["figure_crop"]):
        try:
            os.remove(e["figure_crop"])
        except OSError:
            pass
    v = e.get("video")
    if v and os.path.exists(v) and not video_referenced(v, exclude_eid=e["id"]):
        # 视频可能同时被错题本/未处置草稿单引用（导入时同题双目的地），有引用不删
        try:
            used_by_book = any(q.get("video") == v for q in store.load()["questions"])
            if not used_by_book and not store.video_draft_ref(v):
                os.remove(v)
        except OSError:
            pass
        except Exception:
            pass   # 引用检查失败宁可不删


def video_referenced(video, exclude_eid=None):
    """video 路径是否仍被（其他）专题例题引用"""
    db = load_topics()
    for s in db["sets"]:
        for c in s["categories"]:
            for e in c.get("examples", []):
                if e.get("video") == video and e.get("id") != exclude_eid:
                    return True
    return False


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
                analysis="", crop_src=None, ggb=None, spec=None, source="", video=None):
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
          "figure_ggb": ggb, "figure_spec": spec, "source": source or "",
          "video": video}
    cat["examples"].append(ex)
    save_topics(db)
    return ex


class DuplicateExample(Exception):
    def __init__(self, eid):
        self.eid = eid
        super().__init__("与已有例题 " + eid + " 重复")


def patch_example(eid, fields):
    """手编例题（video/analysis/answer/text 等）；返回更新后的例题"""
    db = load_topics()
    s, c, e = _find_example_in(db, eid)
    if not e:
        raise ValueError("例题不存在")
    for k in ("text", "answer", "analysis", "video", "choices"):
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
    p = _practice_path()
    if not os.path.exists(p):
        return {"version": 1, "practice": {}}
    with LOCK:
        return json.load(open(p, encoding="utf-8"))


def _save_practice(db):
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


# ---------- 存入错题本（做错例题 → 标准错题记录，走复习/归档闭环） ----------

def to_book(eid, reason=None):
    """把例题转为错题记录。verify_status=pass（例题答案是家长选定的权威参考）；
    analysis.chapter 留空（工作台显示未分类，可 PATCH 手改）；key_idea 带例题解析。"""
    s, c, e = find_example(eid)
    if not e:
        raise ValueError("例题不存在")
    dup = store.find_duplicate(e["text"])
    if dup:
        raise DuplicateExample(dup["id"])
    rec = store.add({
        "source": "专题 · {0} 第{1}类{2}".format(s["name"], c["no"], c["name"]),
        "type": e.get("type") or "fill",
        "question": {"text": e["text"], "choices": e.get("choices"),
                     "figure_spec": e.get("figure_spec"),
                     "figure_crop": e.get("figure_crop"),
                     "figure_ggb": e.get("figure_ggb")},
        "printed_answer": e.get("answer"),
        "verified_answer": e.get("answer"),
        "verify_status": "pass",
        "mistake_reason": reason or {"kind": "unknown", "note": "专题例题做错"},
        "analysis": {"grade": "八年级上册", "chapter": "", "kp_path": [],
                     "knowledge_points": [], "pitfall": "",
                     "difficulty": DIFF_TO_LEVEL.get(c.get("difficulty"), 2),
                     "key_idea": e.get("analysis") or ""},
        "photo": None,
        "flags": {"needs_review": False},
    })
    return rec
