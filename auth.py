# -*- coding: utf-8 -*-
"""auth —— 云端形态（WRONGBOOK_CLOUD=on）鉴权：激活码 → 会话 Token → 租户上下文

契约（Plan B 决策 D-B2/D-B4/D-B7）：
  - 登录：POST /api/auth/activate {code, device_name} → 校验 accounts（存在/启用/未过期）
    → 建 sessions 行 + 默认成员 → 返回 {token, owner, users, current_uid} 并种 Cookie；
  - 每请求：app._cloud_gate 调 gate() 解析 Bearer 头（优先）或 wb_token Cookie（<img>/
    下载不带 Bearer 头），同时查 accounts 吊销/过期 → 即时断会话；解析成员
    （X-Wrongbook-Uid 头 > wb_uid Cookie > 会话默认，均校验属于本码，防跨户）；
  - 成员隔离语义：大宝/二宝可在多设备并行操作，请求头指定成员优先于会话默认。
"""
import time

import cloud_store
import tenant_ctx

TOKEN_COOKIE = "wb_token"
UID_COOKIE = "wb_uid"
TOKEN_TTL = 30 * 24 * 3600   # Cookie Max-Age 与会话有效窗一致；心跳刷新 last_seen


class AuthError(Exception):
    def __init__(self, msg, status=401):
        super().__init__(msg)
        self.msg = msg
        self.status = status


def enabled():
    return tenant_ctx.CLOUD


def activate(code, device_name=""):
    """激活码换会话。错误语义：401=码不存在/空；403=停用/过期（前端可区分文案）"""
    code = (code or "").strip().upper()
    if not code:
        raise AuthError("请输入激活码")
    acct = cloud_store.get_account(code)
    if not acct:
        raise AuthError("激活码不存在", 401)
    if not acct.get("is_active"):
        raise AuthError("激活码已被停用，请联系管理员", 403)
    if acct.get("expires_at") and acct["expires_at"] < time.time():
        raise AuthError("激活码已过期，请联系管理员续期", 403)
    uid = cloud_store.ensure_user(code)
    token = cloud_store.create_session(code, uid, device_name)
    return {"token": token, "code": code, "owner": acct.get("owner") or "",
            "users": cloud_store.list_users(code, uid)["users"], "current_uid": uid}


def _bearer_token(req):
    h = req.headers.get("Authorization", "")
    return h[7:].strip() if h.startswith("Bearer ") else None


def resolve_session(req):
    """Token 双轨解析：Authorization: Bearer 优先，wb_token Cookie 兜底"""
    token = _bearer_token(req) or req.cookies.get(TOKEN_COOKIE)
    return cloud_store.get_session(token) if token else None


def gate(req):
    """请求门禁：校验会话与账户状态 → (code, uid)；失败抛 AuthError（转 401/403）"""
    sess = resolve_session(req)
    if sess is None:
        raise AuthError("未登录或会话已失效")
    acct = cloud_store.get_account(sess["code"])
    if not acct or not acct.get("is_active"):
        raise AuthError("激活码已被停用，请联系管理员", 403)
    if acct.get("expires_at") and acct["expires_at"] < time.time():
        raise AuthError("激活码已过期，请联系管理员续期", 403)
    cloud_store.touch_session(sess["token"])
    # 成员解析：X-Wrongbook-Uid 头 > wb_uid Cookie > 会话默认；不属于本码一律回落（防跨户）
    want = req.headers.get("X-Wrongbook-Uid") or req.cookies.get(UID_COOKIE) or ""
    users = cloud_store.list_users(sess["code"], sess["current_uid"])
    known = {u["id"] for u in users["users"]}
    uid = want if want in known else sess["current_uid"]
    if uid not in known:
        uid = cloud_store.ensure_user(sess["code"])
    return sess["code"], uid


def set_cookies(resp, token, uid):
    """wb_token HttpOnly（<img>/下载自动携带）；wb_uid 可读（前端发 X-Wrongbook-Uid 头）"""
    resp.set_cookie(TOKEN_COOKIE, token, max_age=TOKEN_TTL, httponly=True,
                    samesite="Lax", path="/")
    resp.set_cookie(UID_COOKIE, uid, max_age=TOKEN_TTL, httponly=False,
                    samesite="Lax", path="/")
    return resp


def clear_cookies(resp):
    resp.delete_cookie(TOKEN_COOKIE, path="/")
    resp.delete_cookie(UID_COOKIE, path="/")
    return resp


def tenant_media_allowed(rel, code, uid):
    """/data/<rel> 租户归属校验：本人 photos/crops + 本户 topic_crops；其余一律 404。

    路径为前端 relDataUrl 归一化后的相对形式（反斜杠已转正、data/ 前缀已剥）；
    send_from_directory 自带 .. 穿透防护，这里管「跨租户越权」。"""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if rel.startswith("users/" + uid + "/"):
        return True
    if rel.startswith("topic_crops/" + code + "/"):
        return True
    return False


def tenant_out_allowed(rel, code):
    """/out/<rel> 同规则：导出产物按激活码隔离（out/<code>/…）"""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    return bool(code) and rel.startswith(code + "/")
