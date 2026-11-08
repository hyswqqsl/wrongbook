# -*- coding: utf-8 -*-
"""store —— 错题本 JSON 存储层（原子写 + 自动备份，与父项目同风格）"""
import os, json, re, shutil, threading, time, uuid

import paths

LOCK = threading.Lock()
DATA_DIR = paths.DATA_HOME   # R1 数据外置：开发态=项目内 data/（不变）；打包态=平台数据目录
DB_PATH = os.path.join(DATA_DIR, "wrongbook.json")
DRAFT_PATH = os.path.join(DATA_DIR, "drafts.json")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
CROP_DIR = os.path.join(DATA_DIR, "crops")

# ---------- 多用户（v0.7）：data/users/<uid>/{wrongbook.json,drafts.json,photos,crops} ----------
# 服务端保存「当前用户」；切换用户 = 重绑上述路径全局量，所有读写自动路由到该用户目录。
# 测试兼容：既有测试直接 patch 这些全局量即可（set_test_mode 跳过初始化/重绑）。
_TEST_DATA_DIR = None
_INIT = False


def set_test_mode():
    """测试钩子：跳过用户表初始化/迁移/路径重绑（测试自行 patch 路径全局量）"""
    global _INIT
    _INIT = True


def set_test_data_dir(d=None):
    """测试钩子：重定向数据根目录（None 恢复真实目录）；之后首次调用会重新初始化"""
    global _TEST_DATA_DIR, _INIT
    _TEST_DATA_DIR = d
    _INIT = False


def _data_dir():
    return _TEST_DATA_DIR or DATA_DIR


def users_path():
    return os.path.join(_data_dir(), "users.json")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load_users_raw():
    p = users_path()
    if not os.path.exists(p):
        return None
    with LOCK:
        return json.load(open(p, encoding="utf-8"))


def _save_users_raw(u):
    with LOCK:
        paths.atomic_write_json(users_path(), u)   # R1 加固：原先无 .bak/fsync，统一收口


def _migrate_legacy(udir):
    """旧全局数据（data/wrongbook.json 等）迁移进默认用户目录；幂等。

    迁移后改写 JSON 里的绝对路径（…/data/photos/ → …/data/users/<uid>/photos/），
    保证历史记录的照片/裁剪图引用仍然有效。
    """
    uid = os.path.basename(udir)
    os.makedirs(udir, exist_ok=True)
    moved = []
    for name in ("wrongbook.json", "drafts.json"):
        src = os.path.join(_data_dir(), name)
        dst = os.path.join(udir, name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.move(src, dst)
            moved.append(dst)
    for d in ("photos", "crops"):
        src = os.path.join(_data_dir(), d)
        dst = os.path.join(udir, d)
        if os.path.isdir(src) and not os.path.isdir(dst):
            shutil.move(src, dst)
    for dst in moved:
        txt = open(dst, encoding="utf-8").read()
        for d in ("photos", "crops"):
            txt = txt.replace(os.path.join(_data_dir(), d) + os.sep,
                              os.path.join(udir, d) + os.sep)
        open(dst, "w", encoding="utf-8").write(txt)


def _ensure_users():
    """初始化用户表：首次调用时建默认用户「测试账号」并迁移旧全局数据；幂等"""
    u = _load_users_raw()
    if u and u.get("users"):
        ids = {x["id"] for x in u["users"]}
        if u.get("current") not in ids:
            u["current"] = u["users"][0]["id"]
            _save_users_raw(u)
        return u
    udir = os.path.join(_data_dir(), "users", "test")
    if u is None:
        _migrate_legacy(udir)   # 仅 users.json 缺失（首次）时迁移
    os.makedirs(udir, exist_ok=True)
    for f, key in (("wrongbook.json", "questions"), ("drafts.json", "sheets")):
        fp = os.path.join(udir, f)
        if not os.path.exists(fp):
            json.dump({"version": 1, key: []}, open(fp, "w", encoding="utf-8"),
                      ensure_ascii=False)
    u = {"version": 1, "current": "test",
         "users": [{"id": "test", "name": "测试账号", "created_at": _now()}]}
    _save_users_raw(u)
    return u


def _rebind_paths():
    """把路径全局量重绑到当前用户目录（store 内部与测试 patch 都以这些全局量为准）"""
    global DB_PATH, DRAFT_PATH, PHOTO_DIR, CROP_DIR
    udir = os.path.join(_data_dir(), "users", current_uid())
    DB_PATH = os.path.join(udir, "wrongbook.json")
    DRAFT_PATH = os.path.join(udir, "drafts.json")
    PHOTO_DIR = os.path.join(udir, "photos")
    CROP_DIR = os.path.join(udir, "crops")


def _init_once():
    global _INIT
    if _INIT:
        return
    _INIT = True
    paths.ensure_meta(_data_dir())   # R1：data/meta.json 版本钩子（首个写入口触发，幂等）
    _ensure_users()
    _rebind_paths()


def list_users():
    _init_once()
    return _ensure_users()


def current_uid():
    u = _ensure_users()
    return u["current"]


def user_dir(uid=None):
    return os.path.join(_data_dir(), "users", uid or current_uid())


def add_user(name):
    """新建用户（题库为空）并切换为当前用户；返回用户表"""
    name = (name or "").strip() or "未命名"
    _init_once()
    u = _ensure_users()
    uid = "u" + time.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4]
    udir = os.path.join(_data_dir(), "users", uid)
    os.makedirs(udir, exist_ok=True)
    for f, key in (("wrongbook.json", "questions"), ("drafts.json", "sheets")):
        json.dump({"version": 1, key: []},
                  open(os.path.join(udir, f), "w", encoding="utf-8"), ensure_ascii=False)
    u["users"].append({"id": uid, "name": name, "created_at": _now()})
    u["current"] = uid
    _save_users_raw(u)
    _rebind_paths()
    return u


def switch_user(uid):
    """切换当前用户；uid 不存在抛 ValueError"""
    _init_once()
    u = _ensure_users()
    if uid not in {x["id"] for x in u["users"]}:
        raise ValueError("用户不存在: " + str(uid))
    u["current"] = uid
    _save_users_raw(u)
    _rebind_paths()
    return u


def _new_id():
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def load():
    _init_once()
    if not os.path.exists(DB_PATH):
        return {"version": 1, "questions": []}
    with LOCK:
        return json.load(open(DB_PATH, encoding="utf-8"))


def save(db):
    _init_once()
    with LOCK:
        paths.atomic_write_json(DB_PATH, db)


def add(record):
    _init_once()
    db = load()
    rec = dict(record)
    rec.setdefault("id", _new_id())
    rec.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    rec.setdefault("practice", [])
    rec.setdefault("mastered_at", None)
    db["questions"].append(rec)
    save(db)
    return rec


def get(qid):
    _init_once()
    for q in load()["questions"]:
        if q["id"] == qid:
            return q
    return None


def update(qid, **fields):
    _init_once()
    db = load()
    for q in db["questions"]:
        if q["id"] == qid:
            q.update(fields)
            save(db)
            return q
    return None


def remove(qid):
    """删除题目：JSON 记录 + 连带图片/视频清理。
    - figure_crop（裁剪图）：直接删文件，并从草稿单引用中同步清理（避免已处理草稿渲染坏图）
    - photo（原图）：同一张照片可产多题，仅当剩余题目与草稿单都无引用时才删
    - video（视频讲解 v1.2）：可能被错题/专题例题/草稿共用，无任何引用才删文件
    返回是否删除"""
    _init_once()
    db = load()
    target = next((q for q in db["questions"] if q["id"] == qid), None)
    if not target:
        return False
    db["questions"] = [q for q in db["questions"] if q["id"] != qid]
    save(db)

    # ---- 连带图片清理 ----
    crop = (target.get("question") or {}).get("figure_crop")
    # ---- 连带图片清理（v1.2q 修复：crop 可能被其他错题/专题例题/未处置草稿引用，全无引用才删文件） ----
    crop = (target.get("question") or {}).get("figure_crop")
    if crop:
        used_by_q = any((q.get("question") or {}).get("figure_crop") == crop for q in db["questions"])
        used_by_topic = _topics_crop_ref(crop)
        if not used_by_q and not used_by_topic and not crop_draft_ref(crop):
            try:
                os.remove(crop)
            except OSError:
                pass
            _scrub_crop_refs(crop)   # 文件已删：同步清理草稿单里的坏引用（文件保留时引用不清）
    photo = target.get("photo")
    if photo:
        used_by_q = any(q.get("photo") == photo for q in db["questions"])
        used_by_draft = any(sh.get("photo") == photo for sh in load_sheets().get("sheets", []))
        if not used_by_q and not used_by_draft:
            try:
                os.remove(photo)
            except OSError:
                pass
    # ---- 视频讲解清理（v1.2，交叉引用：其余题目/未处置草稿单/专题例题） ----
    video = target.get("video")
    if video and not str(video).startswith("http") and os.path.exists(video):
        # v1.2p：视频讲解改存链接（B站等），仅本地文件路径参与清理/交叉引用
        used_by_q = any(q.get("video") == video for q in db["questions"])
        if not used_by_q and not video_draft_ref(video) and not _topics_video_ref(video):
            try:
                os.remove(video)
            except OSError:
                pass
    return True


def crop_draft_ref(crop):
    """图形文件是否仍被【未处置】草稿单题引用（figure_crops[]/figure_crop；已处置引用不阻塞）"""
    return any(crop in (q.get("figure_crops") or []) or q.get("figure_crop") == crop
               for sh in load_sheets().get("sheets", [])
               for q in sh.get("questions", [])
               if not (q.get("ingested_id") or q.get("skipped_dup")
                       or q.get("topic_example_id") or q.get("topic_dup_of")))


def _topics_crop_ref(crop):
    """图形文件是否仍被专题例题引用（惰性导入避免循环依赖；异常时宁可不删）"""
    try:
        import topics
        return topics.crop_referenced(crop)
    except Exception:
        return True


def video_draft_ref(video):
    """视频是否仍被【未处置】草稿单题引用（已处置的题不会再提交，引用不阻塞清理）"""
    return any(q.get("video") == video
               for sh in load_sheets().get("sheets", [])
               for q in sh.get("questions", [])
               if not (q.get("ingested_id") or q.get("skipped_dup")
                       or q.get("topic_example_id") or q.get("topic_dup_of")))


def _topics_video_ref(video):
    """视频是否仍被专题例题引用（惰性导入避免循环依赖；异常时宁可不删）"""
    try:
        import topics
        return topics.video_referenced(video)
    except Exception:
        return True


def _scrub_crop_refs(crop):
    """从所有草稿单中清理对已删除裁剪图的引用（figure_crops[] / figure_crop）"""
    if not crop:
        return
    db = load_sheets()
    changed = False
    for sh in db.get("sheets", []):
        for q in sh.get("questions", []):
            if isinstance(q.get("figure_crops"), list) and crop in q["figure_crops"]:
                q["figure_crops"] = [c for c in q["figure_crops"] if c != crop]
                changed = True
            if q.get("figure_crop") == crop:
                q["figure_crop"] = None
                changed = True
    if changed:
        save_sheets(db)


def add_practice(qid, correct, variant="original"):
    """记录一次作答；返回更新后的记录"""
    _init_once()
    db = load()
    for q in db["questions"]:
        if q["id"] == qid:
            q.setdefault("practice", []).append({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "correct": bool(correct), "variant": variant})
            save(db)
            return q
    return None


def streak(q):
    """当前连续答对次数（从最近往前数）"""
    s = 0
    for p in reversed(q.get("practice", [])):
        if p.get("correct"):
            s += 1
        else:
            break
    return s


# ---------- 草稿单（排版机） ----------

def _new_sheet_id():
    return "d" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def load_sheets():
    _init_once()
    if not os.path.exists(DRAFT_PATH):
        return {"version": 1, "sheets": []}
    with LOCK:
        return json.load(open(DRAFT_PATH, encoding="utf-8"))


def save_sheets(db):
    _init_once()
    with LOCK:
        paths.atomic_write_json(DRAFT_PATH, db)


def add_sheet(sheet):
    _init_once()
    db = load_sheets()
    rec = dict(sheet)
    rec.setdefault("id", _new_sheet_id())
    rec.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    rec.setdefault("status", "pending")
    db["sheets"].append(rec)
    save_sheets(db)
    return rec


def get_sheet(sid):
    _init_once()
    for s in load_sheets()["sheets"]:
        if s["id"] == sid:
            return s
    return None


def patch_sheet_question(sid, no, fields):
    """人审编辑：{text?, choices?, selected?, reason?...} 按题号 no 合并"""
    db = load_sheets()
    for s in db["sheets"]:
        if s["id"] == sid:
            for q in s.get("questions", []):
                if q.get("no") == no:
                    q.update(fields)
                    save_sheets(db)
                    return s
    return None


def set_sheet_status(sid, status, extra=None):
    db = load_sheets()
    for s in db["sheets"]:
        if s["id"] == sid:
            s["status"] = status
            if extra:
                s.update(extra)
            save_sheets(db)
            return s
    return None


# ---------- 题库查重（v0.6.2：录入时不重复入库） ----------

def norm_text(s):
    """查重用归一化：去空白/标点/LaTeX 命令名，小写，只留中英文与数字。

    例：'如图，在 $\\triangle ABC$ 中，AB=AC' 与 '如图在triangle abc中ab=ac' 归一化后相同。
    """
    s = str(s or "").lower()
    s = re.sub(r"\\[a-zA-Z]+", "", s)          # \frac \sqrt 等命令名去掉，保留内容
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s)  # 去空白与标点（\w 含字母数字下划线）
    return s


def find_duplicate(text, threshold=0.92):
    _init_once()
    """题库查重：与任一已入库题（含已归档）归一化相似度 ≥ threshold 即视为重复。

    返回重复的记录 dict；无重复返回 None。题干过短（归一化后<8字符）不查重（误报高）。
    用 difflib 纯标准库；数百题规模 O(n) 比对足够快。
    """
    import difflib
    t = norm_text(text)
    if len(t) < 8:
        return None
    best, best_ratio = None, 0.0
    for rec in load()["questions"]:
        qt = norm_text((rec.get("question") or {}).get("text", ""))
        if not qt:
            continue
        if abs(len(qt) - len(t)) > max(len(qt), len(t)) * (1 - threshold):
            continue   # 长度差过大不可能达标，跳过昂贵的序列比对
        r = difflib.SequenceMatcher(None, t, qt).ratio()
        if r > best_ratio:
            best, best_ratio = rec, r
    return best if best_ratio >= threshold else None
