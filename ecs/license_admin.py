# -*- coding: utf-8 -*-
"""license_admin —— 激活码签发/管理 CLI（在 ECS 上执行）

用法示例：
    venv/bin/python license_admin.py issue --owner 张同学家                  # 永久码，2 设备
    venv/bin/python license_admin.py issue --owner 体验家长 --days 7        # 试用期码
    venv/bin/python license_admin.py issue --owner 王同学家 --days 365      # 按年
    venv/bin/python license_admin.py list [--code WB-…]
    venv/bin/python license_admin.py revoke --code WB-…                     # 吊销（客户端心跳 401 即锁）
    venv/bin/python license_admin.py reset-seats --code WB-…                # 重装系统后清席位
    venv/bin/python license_admin.py extend --code WB-… --days 180          # 延期（下次心跳生效）
    venv/bin/python license_admin.py audit [--limit 50]

线下收款后 issue → 微信发码给家长。码形如 WB-ABCD-EFGH-JKLM（去易混字符 0/O/1/I）。
"""
import argparse
import json
import os
import random
import sqlite3
import string
import time

DB_PATH = os.environ.get("WB_LICENSE_DB", "/var/lib/wrongbook-license/license.db")
PERMANENT_EXPIRES = 4102444800
CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去易混字符


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def new_code():
    g = ["".join(random.choices(CHARS, k=4)) for _ in range(3)]
    return "WB-" + "-".join(g)


def fmt_ts(t):
    if not t:
        return "—"
    if t == PERMANENT_EXPIRES:
        return "永久"
    return time.strftime("%Y-%m-%d", time.localtime(t))


def main():
    ap = argparse.ArgumentParser(description="AI错题本激活码管理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("issue", help="签发新码")
    p.add_argument("--owner", required=True, help="备注（如：张同学家）")
    p.add_argument("--days", type=int, default=None, help="有效天数；缺省=永久")
    p.add_argument("--devices", type=int, default=2, help="设备数上限（默认 2）")
    p.add_argument("--features", default="", help="逗号分隔功能位（可选，当前恒空）")

    p = sub.add_parser("list", help="查码（含绑定设备）")
    p.add_argument("--code", default=None)

    p = sub.add_parser("revoke", help="吊销")
    p.add_argument("--code", required=True)

    p = sub.add_parser("enable", help="解除吊销（重新启用码）")
    p.add_argument("--code", required=True)

    p = sub.add_parser("reset-seats", help="清空该码的设备绑定（重装系统后用）")
    p.add_argument("--code", required=True)

    p = sub.add_parser("extend", help="延期 N 天（永久码将变为限期码，从今天起算）")
    p.add_argument("--code", required=True)
    p.add_argument("--days", type=int, required=True)

    p = sub.add_parser("audit", help="最近事件")
    p.add_argument("--limit", type=int, default=30)

    args = ap.parse_args()
    now = int(time.time())

    if args.cmd == "issue":
        with db() as conn:
            while True:   # 极小概率撞码，重试
                code = new_code()
                if not conn.execute("SELECT 1 FROM codes WHERE code=?", (code,)).fetchone():
                    break
            expires = (now + args.days * 86400) if args.days else PERMANENT_EXPIRES
            conn.execute(
                "INSERT INTO codes(code,owner,max_devices,issued_at,expires_at,revoked,features)"
                " VALUES(?,?,?,?,?,0,?)",
                (code, args.owner, args.devices, now, expires,
                 json.dumps([f for f in args.features.split(",") if f])))
        print("✅ 已签发：{}（{}，{} 台设备，{}）".format(
            code, "永久" if not args.days else "至 " + fmt_ts(expires),
            args.devices, args.owner))
        print("   复制发给家长：", code)
        return

    if args.cmd == "list":
        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM codes" + (" WHERE code=?" if args.code else "") +
                " ORDER BY issued_at DESC", (args.code,) if args.code else ()).fetchall()
            for r in rows:
                acts = conn.execute(
                    "SELECT device_name,os,last_seen FROM activations WHERE code=?",
                    (r["code"],)).fetchall()
                state = "已吊销" if r["revoked"] else (
                    "已过期" if r["expires_at"] and r["expires_at"] < now else "有效")
                print("{} [{}] {}｜上限 {} 台｜已绑 {} 台：{}".format(
                    r["code"], state, r["owner"] or "—", r["max_devices"], len(acts),
                    "; ".join("{}({}) 最近 {}".format(
                        a["device_name"] or "?", a["os"] or "?",
                        time.strftime("%m-%d %H:%M", time.localtime(a["last_seen"])))
                        for a in acts) or "无"))
                print("    签发 {} ｜ 到期 {}".format(fmt_ts(r["issued_at"]), fmt_ts(r["expires_at"])))
        return

    if args.cmd == "revoke":
        with db() as conn:
            cur = conn.execute("UPDATE codes SET revoked=1 WHERE code=?", (args.code,))
        print("🚫 已吊销" if cur.rowcount else "码不存在：", args.code)
        return

    if args.cmd == "enable":
        with db() as conn:
            cur = conn.execute("UPDATE codes SET revoked=0 WHERE code=?", (args.code,))
        print("✅ 已重新启用" if cur.rowcount else "码不存在：", args.code)
        return

    if args.cmd == "reset-seats":
        with db() as conn:
            cur = conn.execute("DELETE FROM activations WHERE code=?", (args.code,))
        print("♻️ 已清除 {} 个设备绑定：".format(cur.rowcount), args.code)
        return

    if args.cmd == "extend":
        with db() as conn:
            row = conn.execute("SELECT expires_at FROM codes WHERE code=?", (args.code,)).fetchone()
            if not row:
                print("码不存在：", args.code)
                return
            if row["expires_at"] == PERMANENT_EXPIRES:
                print("已是永久码，无需延期（", args.code, "）")
                return
            base = row["expires_at"] if row["expires_at"] and row["expires_at"] > now else now
            conn.execute("UPDATE codes SET expires_at=? WHERE code=?",
                         (base + args.days * 86400, args.code))
        print("📅 已延期至：", fmt_ts(base + args.days * 86400), "（", args.code, "）")
        return

    if args.cmd == "audit":
        with db() as conn:
            rows = conn.execute("SELECT * FROM audit ORDER BY at DESC LIMIT ?",
                                (args.limit,)).fetchall()
            for r in rows:
                print(time.strftime("%m-%d %H:%M:%S", time.localtime(r["at"])),
                      r["ip"], r["event"], r["code"] or "—", r["device_id"][:16], r["detail"])


if __name__ == "__main__":
    main()
