# -*- coding: utf-8 -*-
"""cloud_store —— 云端多租户 SQLite 存储层（WAL；Plan B 决策 D-B1/D-B2）

设计（对齐 Doc/development-B.md §2.1 七表 DDL）：
  - 「主检索字段关系化 + 嵌套详情字段 JSON 化」：questions.data_json / sheets.data_json
    内部结构与桌面版 wrongbook.json / drafts.json 单条完全一致 → store.py 只需把
    读写原语委托到本模块（同名 dict 形状），pipeline/stats/topics/reviewflow 零改动。
  - 租户隔离：所有数据函数按 tenant_ctx.get_tenant() 的 (code, uid) 过滤，
    未绑定租户直接抛错（宁可不服务，不可串户）。
  - 索引列（chapter/difficulty/reason_kind/mastered/norm_text）随保存维护，供 SQL 筛选
    与未来按章聚合提速；当前查询仍以整表小数据（单户数百题）为主，difflib 复核查重。

规模与迁移预留（Plan B 拍板）：SQLite WAL + 本地磁盘；StorageBackend / MediaStorage
抽象仅以本模块接口为边界预留——未来扩内存换 MySQL、图片上 OSS 时实现同接口即可。
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing

import paths
import store as _store   # 复用 _data_dir()（测试重定向钩子）与 norm_text 归一化

_lock = threading.Lock()   # 仅保护 schema 初始化；数据并发靠 WAL + busy_timeout


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    code        TEXT PRIMARY KEY,      -- 激活码（签发账本在 license.db，此处为业务投影）
    owner       TEXT NOT NULL DEFAULT '',
    expires_at  INTEGER,               -- Unix 秒；NULL=永久
    is_active   INTEGER DEFAULT 1,
    ai_config   TEXT DEFAULT '{}',     -- 该家庭自配 GLM/DeepSeek Key（模式 B，平台零垫资）
    created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS sessions(
    token       TEXT PRIMARY KEY,      -- Web/H5 会话（tk_…），与桌面设备激活是两回事
    code        TEXT NOT NULL,
    current_uid TEXT NOT NULL DEFAULT '',
    device_name TEXT,
    last_seen   INTEGER,
    created_at  INTEGER,
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS users(
    user_id     TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  INTEGER,
    PRIMARY KEY(code, user_id),
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sheets(
    sheet_id    TEXT NOT NULL,
    code        TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    source      TEXT,
    photo_path  TEXT,
    data_json   TEXT NOT NULL,
    created_at  INTEGER,
    PRIMARY KEY(code, sheet_id)
);
CREATE INDEX IF NOT EXISTS idx_sheets_code_uid ON sheets(code, user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS questions(
    qid         TEXT NOT NULL,
    code        TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    chapter     TEXT,
    difficulty  INTEGER,
    reason_kind TEXT,
    mastered    INTEGER DEFAULT 0,
    norm_text   TEXT,
    data_json   TEXT NOT NULL,
    created_at  INTEGER,
    PRIMARY KEY(code, qid)
);
CREATE INDEX IF NOT EXISTS idx_q_filter ON questions(code, user_id, mastered, chapter);
CREATE TABLE IF NOT EXISTS topic_sets(
    code        TEXT PRIMARY KEY,
    sets_json   TEXT NOT NULL,
    updated_at  INTEGER,
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS topic_practice(
    code         TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    practice_json TEXT DEFAULT '{}',
    updated_at   INTEGER,
    PRIMARY KEY(code, user_id)
);
"""


def db_path():
    """云端数据库文件：<DATA_HOME>/wrongbook.db（测试随 store.set_test_data_dir 重定向）"""
    return os.path.join(_store._data_dir(), "wrongbook.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema():
    os.makedirs(_store._data_dir(), exist_ok=True)
    with _lock, closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)


def _tenant():
    """取当前租户；未绑定即抛错（防串户：宁可不服务）"""
    import tenant_ctx
    t = tenant_ctx.get_tenant()
    if not t:
        raise RuntimeError("cloud_store 需要租户上下文（HTTP 经 auth 网关 / 线程 bind_tenant）")
    return t[0], t[1]


def _ts(s):
    """'2026-11-01 15:30:00' → Unix 秒；解析失败回落 0（仅影响排序稳定性）"""
    try:
        return int(time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S")))
    except Exception:
        return 0


# ---------- 错题本（questions；dict 形状与 wrongbook.json 一致） ----------

def load_questions():
    code, uid = _tenant()
    init_schema()
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM questions WHERE code=? AND user_id=? "
            "ORDER BY created_at ASC, qid ASC", (code, uid)).fetchall()
    return {"version": 1, "questions": [json.loads(r["data_json"]) for r in rows]}


def _q_row(code, uid, q):
    an = q.get("analysis") or {}
    return (q["id"], code, uid,
            (an.get("chapter") or "").strip() or None,
            an.get("difficulty"),
            (q.get("mistake_reason") or {}).get("kind"),
            1 if q.get("mastered_at") else 0,
            _store.norm_text((q.get("question") or {}).get("text", ""))[:80],
            json.dumps(q, ensure_ascii=False),
            _ts(q.get("created_at")))


def save_questions(db):
    code, uid = _tenant()
    init_schema()
    qs = db.get("questions", [])
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM questions WHERE code=? AND user_id=?", (code, uid))
        conn.executemany(
            "INSERT INTO questions(qid,code,user_id,chapter,difficulty,reason_kind,"
            "mastered,norm_text,data_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [_q_row(code, uid, q) for q in qs])


# ---------- 草稿单（sheets；dict 形状与 drafts.json 一致） ----------

def load_sheets():
    code, uid = _tenant()
    init_schema()
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM sheets WHERE code=? AND user_id=? "
            "ORDER BY created_at ASC, sheet_id ASC", (code, uid)).fetchall()
    return {"version": 1, "sheets": [json.loads(r["data_json"]) for r in rows]}


def save_sheets(db):
    code, uid = _tenant()
    init_schema()
    rows = []
    for sh in db.get("sheets", []):
        rows.append((sh["id"], code, uid, sh.get("status") or "pending",
                     sh.get("source"), sh.get("photo_path") or sh.get("photo"),
                     json.dumps(sh, ensure_ascii=False), _ts(sh.get("created_at"))))
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM sheets WHERE code=? AND user_id=?", (code, uid))
        conn.executemany(
            "INSERT INTO sheets(sheet_id,code,user_id,status,source,photo_path,data_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)", rows)


# ---------- 专题（topic_sets / topic_practice；每户一份树 + 每成员练习记录） ----------

def load_topics():
    code, _uid = _tenant()
    init_schema()
    with closing(connect()) as conn:
        row = conn.execute("SELECT sets_json FROM topic_sets WHERE code=?", (code,)).fetchone()
    if row:
        return json.loads(row["sets_json"])
    import topics as _topics
    db = _topics.build_seed()   # 首跑：从内置种子克隆一份本户专题树（幂等）
    save_topics(db)
    return db


def save_topics(db):
    code, _uid = _tenant()
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO topic_sets(code,sets_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET sets_json=excluded.sets_json, updated_at=excluded.updated_at",
            (code, json.dumps(db, ensure_ascii=False), int(time.time())))


def load_practice():
    code, uid = _tenant()
    init_schema()
    with closing(connect()) as conn:
        row = conn.execute("SELECT practice_json FROM topic_practice WHERE code=? AND user_id=?",
                           (code, uid)).fetchone()
    return json.loads(row["practice_json"]) if row else {"version": 1, "practice": {}}


def save_practice(db):
    code, uid = _tenant()
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO topic_practice(code,user_id,practice_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(code,user_id) DO UPDATE SET practice_json=excluded.practice_json, "
            "updated_at=excluded.updated_at",
            (code, uid, json.dumps(db, ensure_ascii=False), int(time.time())))


# ---------- 账户 / 会话 / 成员（鉴权层显式传 code，不走租户上下文） ----------

def ensure_account(code, owner="", expires_at=None, is_active=1):
    """测试/管理 CLI 用：直接建或更新账户投影"""
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO accounts(code,owner,expires_at,is_active,ai_config,created_at) "
            "VALUES(?,?,?,?, '{}', ?) ON CONFLICT(code) DO UPDATE SET owner=excluded.owner, "
            "expires_at=excluded.expires_at, is_active=excluded.is_active",
            (code, owner, expires_at, 1 if is_active else 0, int(time.time())))


def get_account(code):
    init_schema()
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM accounts WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def all_accounts():
    init_schema()
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY code")]


def deactivate_accounts(codes):
    """sync-accounts：账本中不存在/已吊销的码 → 停用（留行留痕，不删）"""
    init_schema()
    with closing(connect()) as conn, conn:
        for c in codes:
            conn.execute("UPDATE accounts SET is_active=0 WHERE code=?", (c,))


def _uid_new():
    return "u" + time.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4]


def _users_of(code):
    with closing(connect()) as conn:
        rows = conn.execute("SELECT user_id,name,created_at FROM users WHERE code=? "
                            "ORDER BY created_at ASC, user_id ASC", (code,)).fetchall()
    return [{"id": r["user_id"], "name": r["name"], "created_at": r["created_at"]} for r in rows]


def ensure_user(code, name="默认用户"):
    """确保该户至少有一名成员（激活时调用）；返回默认成员 uid"""
    users = _users_of(code)
    if users:
        return users[0]["id"]
    uid = _uid_new()
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO users(user_id,code,name,created_at) VALUES(?,?,?,?)",
                     (uid, code, name, int(time.time())))
    return uid


def add_user(code, name):
    name = (name or "").strip() or "未命名"
    uid = _uid_new()
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO users(user_id,code,name,created_at) VALUES(?,?,?,?)",
                     (uid, code, name, int(time.time())))
    return {"users": _users_of(code), "current": uid}


def list_users(code, current_uid=None):
    users = _users_of(code)
    cur = current_uid or (users[0]["id"] if users else None)
    return {"users": users, "current": cur}


def uid_belongs(code, uid):
    with closing(connect()) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE code=? AND user_id=?", (code, uid)).fetchone()
    return bool(row)


# ---------- 会话 ----------

def create_session(code, uid, device_name=""):
    token = "tk_" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO sessions(token,code,current_uid,device_name,last_seen,created_at) "
                     "VALUES(?,?,?,?,?,?)",
                     (token, code, uid, (device_name or "")[:80], int(time.time()), int(time.time())))
    return token


def get_session(token):
    if not token:
        return None
    init_schema()
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def touch_session(token):
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE sessions SET last_seen=? WHERE token=?", (int(time.time()), token))


def set_session_uid(token, uid):
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE sessions SET current_uid=? WHERE token=?", (uid, token))


def drop_session(token):
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def drop_expired_sessions(max_age=90 * 24 * 3600):
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE last_seen < ?", (int(time.time() - max_age),))


def list_sessions(code=None):
    init_schema()
    with closing(connect()) as conn:
        if code:
            rows = conn.execute("SELECT * FROM sessions WHERE code=? ORDER BY last_seen DESC",
                                (code,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sessions ORDER BY last_seen DESC").fetchall()
    return [dict(r) for r in rows]


# ---------- 租户级 AI Key（模式 B：家长自配；平台零垫资，无环境变量兜底） ----------

def load_ai_config():
    import ai_config
    code, _uid = _tenant()
    acct = get_account(code)
    raw = {}
    if acct and acct.get("ai_config"):
        try:
            raw = json.loads(acct["ai_config"])
        except Exception:
            raw = {}
    cfg = ai_config._defaults()
    if isinstance(raw, dict):
        ai_config._apply_patch(cfg, raw)
    return cfg


def save_ai_config_patch(patch):
    import ai_config
    code, _uid = _tenant()
    cfg = load_ai_config()
    ai_config._apply_patch(cfg, patch)
    init_schema()
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE accounts SET ai_config=? WHERE code=?",
                     (json.dumps(cfg, ensure_ascii=False), code))
    return cfg
