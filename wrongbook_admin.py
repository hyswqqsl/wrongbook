# -*- coding: utf-8 -*-
"""wrongbook_admin —— 云端业务库管理 CLI（在 ECS 上执行；Plan B 决策 D-B7）

激活码的签发账本在 license.db（license_admin.py / 8901 服务管理）；本 CLI 把它
单向同步到业务库 wrongbook.db 的 accounts 表（业务账本，另存家长 ai_config）。
业务服务对 license.db 只读，两服务不共库、不互写。

用法示例：
    venv/bin/python wrongbook_admin.py sync-accounts                 # 发/停码后跑一次
    venv/bin/python wrongbook_admin.py sync-accounts --watch         # 常驻循环（30 分钟）
    venv/bin/python wrongbook_admin.py list-accounts
    venv/bin/python wrongbook_admin.py list-sessions [--code WB-…]
    venv/bin/python wrongbook_admin.py kick --token tk_…             # 踩下线
    venv/bin/python wrongbook_admin.py set-owner --code WB-… --owner 李同学家

同步规则（幂等，可反复跑）：
  · license.codes 且 revoked=0 → accounts upsert（owner/expires_at 跟随；绝不覆盖 ai_config）
  · license.codes revoked=1 或已从账本删除 → accounts.is_active=0（留行留痕）
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LICENSE_DB = os.environ.get("WB_LICENSE_DB", "/var/lib/wrongbook-license/license.db")
PERMANENT_EXPIRES = 4102444800


def _license_codes(license_db):
    """只读打开签发账本 → {code: {owner, expires_at, revoked}}"""
    conn = sqlite3.connect("file:{0}?mode=ro".format(license_db), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT code, owner, expires_at, revoked FROM codes").fetchall()
        return {r["code"]: {"owner": r["owner"] or "", "expires_at": r["expires_at"],
                            "revoked": bool(r["revoked"])} for r in rows}
    finally:
        conn.close()


def sync_accounts(license_db):
    import cloud_store
    codes = _license_codes(license_db)
    cloud_store.init_schema()
    active = []
    for code, info in codes.items():
        if info["revoked"]:
            continue
        active.append(code)
        cloud_store.ensure_account(code, owner=info["owner"], expires_at=info["expires_at"],
                                   is_active=1)
    # 账本中消失/吊销的 → 停用（不删行，留审计痕迹）
    known = {a["code"] for a in cloud_store.all_accounts()}
    cloud_store.deactivate_accounts(known - set(active))
    gone = len(known - set(active))
    print("同步完成：有效 {0} 户 ｜ 本次停用 {1} 户（账本吊销/删除）".format(len(active), gone))
    return len(active)


def fmt_ts(t):
    if not t:
        return "—"
    if t == PERMANENT_EXPIRES:
        return "永久"
    return time.strftime("%Y-%m-%d", time.localtime(t))


def main():
    p = argparse.ArgumentParser(description="云端业务库管理（账户同步/会话管理）")
    p.add_argument("--license-db", default=LICENSE_DB, help="签发账本 license.db 路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("sync-accounts", help="license 账本 → accounts 单向同步（幂等）")
    w.add_argument("--watch", action="store_true", help="常驻：每 30 分钟同步一次（crontab 替代）")
    w.add_argument("--interval", type=int, default=1800)

    sub.add_parser("list-accounts", help="列出账户（含 ai_config 是否已配）")

    s = sub.add_parser("list-sessions", help="列出会话")
    s.add_argument("--code")

    k = sub.add_parser("kick", help="踢下线（删会话）")
    k.add_argument("--token", required=True)

    o = sub.add_parser("set-owner", help="改户备注（业务侧，不影响账本）")
    o.add_argument("--code", required=True)
    o.add_argument("--owner", required=True)

    args = p.parse_args()
    import cloud_store

    if args.cmd == "sync-accounts":
        if args.watch:
            while True:
                try:
                    sync_accounts(args.license_db)
                except Exception as e:
                    print("同步失败（下轮重试）：", e)
                time.sleep(args.interval)
        sync_accounts(args.license_db)

    elif args.cmd == "list-accounts":
        now = int(time.time())
        for a in cloud_store.all_accounts():
            st = "停用" if not a["is_active"] else (
                "已过期" if a["expires_at"] and a["expires_at"] < now else "有效")
            key = "已配Key" if _has_key(a) else "未配Key"
            print("{0} ｜ {1} ｜ 到期 {2} ｜ {3} ｜ {4}".format(
                a["code"], a["owner"] or "（未备注）", fmt_ts(a["expires_at"]), st, key))

    elif args.cmd == "list-sessions":
        for sess in cloud_store.list_sessions(args.code):
            print("token {0}… ｜ 码 {1} ｜ 成员 {2} ｜ 设备 {3} ｜ 最近活跃 {4}".format(
                (sess["token"] or "")[:14], sess["code"], sess["current_uid"] or "—",
                sess["device_name"] or "—",
                time.strftime("%m-%d %H:%M", time.localtime(sess["last_seen"]))))

    elif args.cmd == "kick":
        cloud_store.drop_session(args.token)
        print("已踢下线")

    elif args.cmd == "set-owner":
        cloud_store.ensure_account(args.code, owner=args.owner)
        print("已更新")


def _has_key(acct):
    try:
        cfg = __import__("json").loads(acct.get("ai_config") or "{}")
        return any(str(v or "").strip() for v in (cfg.get("keys") or {}).values())
    except Exception:
        return False


if __name__ == "__main__":
    main()
