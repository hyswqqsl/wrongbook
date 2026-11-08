# -*- coding: utf-8 -*-
"""license_server —— AI错题本激活验证服务（ECS 侧，R3）

API 契约（与客户端 license_client.py 互为约定，见 RELEASE_DESIGN.md §5.2）：
- POST /v1/activate  {code, device_id, device_name, os, app_version}
    → 200 {license, receipt, server_time}；400 格式错；401 invalid/revoked/expired；403 seat_limit；429 限流
- POST /v1/heartbeat {code, device_id}
    → 200 {receipt, license, server_time}（license 每次重签：管理端续费/延期下次心跳即生效）
    → 401 invalid/revoked/expired/not_activated
- GET  /v1/version   → version.json 内容（客户端升级检查，改文件即发版）
- GET  /healthz      → 部署探活

部署：见 DEPLOY.md（gunicorn 1 worker 8 threads，0.0.0.0:8901，systemd 常驻）。
私钥：/etc/wrongbook-license/private.pem（0600，绝对不进 git/不随客户端分发）。
"""
import base64
import json
import os
import re
import sqlite3
import threading
import time

from flask import Flask, request, jsonify
from cryptography.hazmat.primitives import serialization

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WB_LICENSE_DB", "/var/lib/wrongbook-license/license.db")
KEY_PATH = os.environ.get("WB_LICENSE_KEY", "/etc/wrongbook-license/private.pem")
VERSION_FILE = os.path.join(BASE, "version.json")
PERMANENT_EXPIRES = 4102444800                      # 2100-01-01：永久码
CODE_RE = re.compile(r"^WB-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")

KEY = serialization.load_pem_private_key(open(KEY_PATH, "rb").read(), password=None)

app = Flask(__name__)


# ---------- 签名（与客户端 _canonical 口径一致：key 排序/无空白/排除 sig） ----------

def _canonical(d):
    payload = {k: v for k, v in d.items() if k != "sig"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(d):
    d = dict(d)
    d["sig"] = base64.b64encode(KEY.sign(_canonical(d))).decode()
    return d


# ---------- SQLite（WAL，单 worker 多线程） ----------

_LOCK = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _LOCK, db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS codes(
          code TEXT PRIMARY KEY,
          owner TEXT,
          max_devices INTEGER DEFAULT 2,
          issued_at INTEGER, expires_at INTEGER,
          revoked INTEGER DEFAULT 0,
          features TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS activations(
          code TEXT, device_id TEXT, device_name TEXT, os TEXT, app_version TEXT,
          activated_at INTEGER, last_seen INTEGER,
          PRIMARY KEY(code, device_id)
        );
        CREATE TABLE IF NOT EXISTS audit(
          at INTEGER, ip TEXT, code TEXT, device_id TEXT, event TEXT, detail TEXT
        );
        """)


init_schema()


def audit(ip, code, device_id, event, detail=""):
    with _LOCK, db() as conn:
        conn.execute("INSERT INTO audit(at,ip,code,device_id,event,detail) VALUES(?,?,?,?,?,?)",
                     (int(time.time()), ip, code, device_id, event, str(detail)[:300]))


# ---------- 简易限流（单 worker 内存滑动窗口；activate 10/min、heartbeat 60/min） ----------

_RL = {}
_RL_LOCK = threading.Lock()


def rate_ok(ip, bucket, limit, window=60):
    now = time.time()
    with _RL_LOCK:
        lst = [t for t in _RL.get((ip, bucket), []) if t > now - window]
        if len(lst) >= limit:
            _RL[(ip, bucket)] = lst
            return False
        lst.append(now)
        _RL[(ip, bucket)] = lst
        if len(_RL) > 10000:                      # 防字典膨胀
            for k in [k for k, v in _RL.items() if not v or v[-1] < now - 3600]:
                _RL.pop(k, None)
        return True


def _ip():
    return request.headers.get("X-Real-IP", request.remote_addr or "?")


def _load_code(code):
    with db() as conn:
        row = conn.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    return row


def _issue_payload(code, device_id, row):
    """签发 license + receipt（expires_at 每次按 codes 当前值签：续费/延期自动生效）"""
    now = int(time.time())
    license_ = _sign({"code": code, "device_id": device_id,
                      "owner": row["owner"] or "",
                      "issued_at": row["issued_at"],
                      "expires_at": row["expires_at"] or PERMANENT_EXPIRES,
                      "features": json.loads(row["features"] or "[]")})
    receipt = _sign({"type": "receipt", "code": code,
                     "device_id": device_id, "server_time": now})
    return license_, receipt, now


@app.post("/v1/activate")
def activate():
    ip = _ip()
    if not rate_ok(ip, "act", 10):
        return jsonify({"error": "rate_limited"}), 429
    b = request.get_json(force=True, silent=True) or {}
    code = str(b.get("code") or "").strip().upper()
    device_id = str(b.get("device_id") or "").strip()
    if not CODE_RE.match(code) or not device_id:
        return jsonify({"error": "bad_code_format"}), 400
    row = _load_code(code)
    if not row:
        audit(ip, code, device_id, "activate_fail", "not_found")
        return jsonify({"error": "code_invalid"}), 401
    if row["revoked"]:
        audit(ip, code, device_id, "activate_fail", "revoked")
        return jsonify({"error": "code_revoked"}), 401
    now = int(time.time())
    if row["expires_at"] and row["expires_at"] < now:
        audit(ip, code, device_id, "activate_fail", "expired")
        return jsonify({"error": "code_invalid"}), 401
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM activations WHERE code=? AND device_id=?",
            (code, device_id)).fetchone()
        others = conn.execute(
            "SELECT device_name, os, last_seen FROM activations WHERE code=? AND device_id<>?",
            (code, device_id)).fetchall()
    if not existing and len(others) >= (row["max_devices"] or 2):
        audit(ip, code, device_id, "activate_fail", "seat_limit")
        return jsonify({"error": "seat_limit",
                        "devices": [dict(o) for o in others]}), 403
    with _LOCK, db() as conn:
        conn.execute(
            "INSERT INTO activations(code,device_id,device_name,os,app_version,activated_at,last_seen)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(code,device_id) DO UPDATE SET device_name=excluded.device_name,"
            " os=excluded.os, app_version=excluded.app_version, last_seen=excluded.last_seen",
            (code, device_id, str(b.get("device_name") or "")[:40],
             str(b.get("os") or "")[:20], str(b.get("app_version") or "")[:20],
             existing["activated_at"] if existing else now, now))
    audit(ip, code, device_id, "activate_ok",
          "reactivate" if existing else "new_seat")
    license_, receipt, server_time = _issue_payload(code, device_id, row)
    return jsonify({"license": license_, "receipt": receipt, "server_time": server_time})


@app.post("/v1/heartbeat")
def heartbeat():
    ip = _ip()
    if not rate_ok(ip, "hb", 60):
        return jsonify({"error": "rate_limited"}), 429
    b = request.get_json(force=True, silent=True) or {}
    code = str(b.get("code") or "").strip().upper()
    device_id = str(b.get("device_id") or "").strip()
    row = _load_code(code) if code and device_id else None
    if not row:
        return jsonify({"error": "code_invalid"}), 401
    if row["revoked"]:
        audit(ip, code, device_id, "heartbeat_fail", "revoked")
        return jsonify({"error": "code_revoked"}), 401
    now = int(time.time())
    if row["expires_at"] and row["expires_at"] < now:
        audit(ip, code, device_id, "heartbeat_fail", "expired")
        return jsonify({"error": "code_expired"}), 401
    with db() as conn:
        act = conn.execute("SELECT 1 FROM activations WHERE code=? AND device_id=?",
                           (code, device_id)).fetchone()
    if not act:
        return jsonify({"error": "not_activated"}), 401
    with _LOCK, db() as conn:
        conn.execute("UPDATE activations SET last_seen=? WHERE code=? AND device_id=?",
                     (now, code, device_id))
    license_, receipt, server_time = _issue_payload(code, device_id, row)
    return jsonify({"receipt": receipt, "license": license_, "server_time": server_time})


@app.get("/v1/version")
def version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return app.response_class(f.read(), mimetype="application/json")
    except FileNotFoundError:
        return jsonify({"latest": None})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "time": int(time.time())})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8901, threaded=True)   # 本机调试用；生产走 gunicorn
