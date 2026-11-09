# -*- coding: utf-8 -*-
"""license_client —— 激活码客户端（R2，RELEASE_DESIGN.md §4）

职责与边界：
- 设备指纹（Windows MachineGuid / macOS IOPlatformUUID 哈希 / 兜底随机存档）
- license.json 本地凭证：Ed25519 验签（客户端只嵌公钥）+ 绑定校验
- activate / heartbeat（urllib，HTTPError 返回 (status, body) 不抛异常；网络错误上抛=离线）
- 状态机：ok ｜ grace(离线宽限，黄条) ｜ locked(回激活页，方案A) ｜ unactivated
- 本机回拨时钟无效：以服务端时间做单调基准（server_time 只增不减）

开关策略：打包态（sys.frozen）默认启用；开发态默认关闭（无 ECS 时不能锁死开发者），
联调设 WRONGBOOK_LICENSE=on。发布前替换 LICENSE_PUBKEY_PEM 为 ECS 正式公钥。
"""
import base64, hashlib, json, os, platform, re, subprocess, sys, threading, time, uuid
import urllib.request, urllib.error, ssl

import paths

# ---------- 配置 ----------

SERVER_BASE = os.environ.get("WRONGBOOK_LICENSE_SERVER",
                             "http://147.139.167.18:8901").rstrip("/")   # ECS 验证服务（R3）
CA_PEM_PATH = os.environ.get("WRONGBOOK_LICENSE_CA", "") or None   # 自签证书场景：随包公钥文件
HTTP_TIMEOUT = 10
GRACE_DAYS = 14
GRACE = GRACE_DAYS * 86400
HEARTBEAT_INTERVAL = 2 * 3600   # 运行中后台周期心跳
HEARTBEAT_CHECK = 600           # 打开应用/状态查询/后台循环的检查节流：吊销/延期最长 10 分钟生效（R4.0b）
REVOKED_HINT_FILE = "license_revoked.json"   # 吊销提示标记（激活页红字用，重新激活后清除）
APP_VERSION = paths.APP_VERSION   # R4：与打包版本唯一源联动（paths.py）

# 启用策略（定稿）：打包态默认启用；开发态默认关闭（无 ECS 会锁死开发者），
# 本机联调：export WRONGBOOK_LICENSE=on 后启动
ENABLED = bool(getattr(sys, "frozen", False)) or os.environ.get("WRONGBOOK_LICENSE") == "on"

# 正式公钥（R3 生成；私钥随部署包上传 ECS，开发期密钥已废弃）
LICENSE_PUBKEY_PEM = base64.b64decode(
    "LS0tLS1CRUdJTiBQVUJMSUMgS0VZLS0tLS0KTUNvd0JRWURLMlZ3QXlFQW1lWVNFLzc3VkRxOUNQYnVIOUF3UFhxbWdtZTc3dGFkMG1ESk5mTFVYVUk9Ci0tLS0tRU5EIFBVQkxJQyBLRVktLS0tLQo=")

# ---------- 凭证结构（v2：签发凭证 + 签名心跳回执） ----------
# license.json = {"license": {服务端签发字段+sig，客户端不可变},
#                 "receipt": {"type":"receipt","code","device_id","server_time","sig"}}
# 心跳续期不能改已签名字段（客户端无私钥）→ 服务端每次激活/心跳签发新回执，
# 宽限期以【最新回执的 server_time】为基准；本机回拨被回执时间封底，无法续命。

LICENSE_PATH = os.path.join(paths.DATA_HOME, "license.json")
_DEVICE_ID_PATH = os.path.join(paths.DATA_HOME, "device_id")
_DEVICE_ID = None


# ---------- 设备指纹 ----------

def device_id():
    """稳定机器标识：优先已存档（升级保留）→ 平台硬件 ID 哈希 → 随机数落盘"""
    global _DEVICE_ID
    if _DEVICE_ID:
        return _DEVICE_ID
    if os.path.exists(_DEVICE_ID_PATH):
        try:
            with open(_DEVICE_ID_PATH, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                _DEVICE_ID = v
                return _DEVICE_ID
        except OSError:
            pass
    hw = None
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                hw, _ = winreg.QueryValueEx(k, "MachineGuid")
        elif sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            hw = m and m.group(1)
    except Exception:
        hw = None
    if hw:
        did = "hw-" + hashlib.sha256(hw.encode()).hexdigest()[:24]
    else:
        did = "rnd-" + uuid.uuid4().hex   # 平台标识都拿不到：随机生成并落盘
    try:
        os.makedirs(paths.DATA_HOME, exist_ok=True)
        with open(_DEVICE_ID_PATH, "w", encoding="utf-8") as f:
            f.write(did)
    except OSError:
        pass
    _DEVICE_ID = did
    return _DEVICE_ID


# ---------- 签名验证 ----------

def _canonical(lic):
    """规范化序列化（签名字段除外）：key 排序 + 无空白，与服务端签发口径互为契约"""
    payload = {k: v for k, v in lic.items() if k != "sig"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def verify_license(lic):
    """验签签发凭证（issued 字段）+ 设备绑定校验；通过返回 True"""
    if not isinstance(lic, dict) or not lic.get("sig"):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives import serialization
        pub = serialization.load_pem_public_key(LICENSE_PUBKEY_PEM)
        pub.verify(base64.b64decode(lic["sig"]), _canonical(lic))
    except Exception:
        return False
    return lic.get("device_id") == device_id()


def verify_receipt(r, code=None):
    """验签心跳回执；code 给定时要求匹配"""
    if not isinstance(r, dict) or r.get("type") != "receipt" or not r.get("sig"):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives import serialization
        pub = serialization.load_pem_public_key(LICENSE_PUBKEY_PEM)
        pub.verify(base64.b64decode(r["sig"]), _canonical(r))
    except Exception:
        return False
    if r.get("device_id") != device_id():
        return False
    return code is None or r.get("code") == code


# ---------- 凭证读写 ----------

def load():
    """读本地凭证 {license, receipt}；不存在/损坏/验签不过/非本机 → None"""
    if not os.path.exists(LICENSE_PATH):
        return None
    try:
        with open(LICENSE_PATH, encoding="utf-8") as f:
            env = json.load(f)
    except Exception:
        return None
    if not isinstance(env, dict):
        return None
    lic, r = env.get("license"), env.get("receipt")
    if not verify_license(lic) or not verify_receipt(r, lic.get("code")):
        return None
    return env


def save(env):
    paths.atomic_write_json(LICENSE_PATH, env)


def _forget():
    """吊销/席位变动：删本地凭证（数据保留，回激活页联网激活即恢复）"""
    try:
        os.remove(LICENSE_PATH)
    except OSError:
        pass


# ---------- 状态机 ----------

def _now():
    return int(time.time())


def state():
    """返回 (state, info)：ok ｜ grace ｜ locked(reason) ｜ unactivated（未启用恒 ok）

    时间基准：max(本机时钟, 最新回执 server_time) —— 本机回拨被签名回执封底，无法续命；
    宽限期从【最近一次服务端确认】起算：ok = 距回执 < 2×心跳间隔且回拨≤1h 容差；
    grace = 仍在宽限窗口（默认 14 天）；超过即锁定（方案 A）。"""
    if not ENABLED:
        return "ok", {}
    env = load()
    if not env:
        return "unactivated", {}
    lic, r = env["license"], env["receipt"]
    now = max(_now(), int(r.get("server_time") or 0))
    exp = int(lic.get("expires_at") or 0)
    lv = int(r.get("server_time") or 0)
    if exp and now > exp:
        return "locked", {"reason": "expired"}
    if not lv:
        return "locked", {"reason": "no_validation"}
    if now > lv + GRACE:
        return "locked", {"reason": "grace_exceeded"}
    # ok 需本机时钟不早于最后回执（回拨/未同步时不视为"新鲜"，按宽限满额处理，
    # 联网后服务端时间即刻纠正）
    if now <= lv + 2 * HEARTBEAT_INTERVAL and _now() >= lv - 3600:
        return "ok", {}
    return "grace", {"grace_days_left": max(1, (lv + GRACE - now + 86399) // 86400)}   # 向上取整


def status():
    """前端状态视图（/api/license）"""
    st, info = state()
    env = load()
    out = {"enabled": ENABLED, "state": st, "app_version": APP_VERSION}
    out.update(info)
    if env:
        lic = env["license"]
        out["owner"] = lic.get("owner") or ""
        code = str(lic.get("code") or "")
        out["code_masked"] = (code[:5] + "…" + code[-4:]) if len(code) > 12 else code
        out["expires_at"] = time.strftime("%Y-%m-%d", time.localtime(lic["expires_at"])) \
            if lic.get("expires_at") and lic["expires_at"] < 4102444800 else "永久"
    out["revoked_hint"] = revoked_hint()
    return out


# ---------- 网络交互（测试可整体 monkeypatch _http_post） ----------

def _http_post(path, payload, timeout=HTTP_TIMEOUT):
    """返回 (status, body_dict)；HTTP 4xx/5xx 也返回状态码与错误体；网络异常上抛
    许可流量强制直连（不走系统代理）：家庭机器上代理软件半死不活是常见故障源"""
    url = SERVER_BASE + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    ctx = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 绕过系统代理
    if url.startswith("https://") and CA_PEM_PATH and os.path.exists(CA_PEM_PATH):
        ctx = ssl.create_default_context(cafile=CA_PEM_PATH)   # 自签证书固定验证
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return e.code, body


def activate(code, timeout=HTTP_TIMEOUT):
    """激活：成功保存凭证返回 (True, None)；失败返回 (False, 错误信息)"""
    code = str(code or "").strip()
    if not code:
        return False, "请输入激活码"
    payload = {"code": code, "device_id": device_id(),
               "device_name": (platform.node() or "未知设备")[:40],
               "os": sys.platform, "app_version": APP_VERSION}
    try:
        status_, body = _http_post("/v1/activate", payload)
    except Exception:
        return False, "无法连接激活服务器，请检查网络后重试"
    if status_ != 200:
        reasons = {"code_invalid": "激活码无效或已过期",
                   "code_revoked": "激活码已被停用",
                   "seat_limit": "设备数已达上限，请联系管理员重置"}
        return False, reasons.get(body.get("error"),
                                 "激活失败（%s）" % (body.get("error") or status_))
    lic, r = body.get("license"), body.get("receipt")
    if not verify_license(lic) or not verify_receipt(r, lic and lic.get("code")):
        return False, "凭证校验失败，请重试"
    save({"license": lic, "receipt": r})
    try:
        os.remove(os.path.join(paths.DATA_HOME, REVOKED_HINT_FILE))   # 重新激活成功：清除吊销提示
    except OSError:
        pass
    return True, None


def heartbeat(timeout=HTTP_TIMEOUT):
    """心跳续期：200 → 存新回执（可附更新后的 license，如续费延期）；401/403 → 立即删凭证回激活页；
    code_revoked → 额外落吊销提示标记（激活页红字）"""
    env = load()
    if not env:
        return
    try:
        status_, body = _http_post("/v1/heartbeat",
                                   {"code": env["license"].get("code"),
                                    "device_id": env["license"].get("device_id")},
                                   timeout=timeout)
    except Exception:
        return   # 离线：保持宽限状态
    if status_ == 200:
        r = body.get("receipt")
        lic = body.get("license") or env["license"]
        if not verify_receipt(r, env["license"].get("code")) or not verify_license(lic):
            return   # 异常回执：丢弃，保持现状（下次心跳再试）
        save({"license": lic, "receipt": r})
    elif status_ in (401, 403):
        if body.get("error") == "code_revoked":
            try:
                paths.atomic_write_json(os.path.join(paths.DATA_HOME, REVOKED_HINT_FILE),
                                        {"code": env["license"].get("code"),
                                         "device_id": env["license"].get("device_id"), "at": _now()})
            except Exception:
                pass
        _forget()


def validate_on_startup(timeout=4):
    """启动时**无条件**同步验证一次（不看检查节流）：吊销/延期在下次启动时立即感知；
    网络异常按宽限处理（短超时，最多阻塞启动 4 秒）——R4.0b：修复“刚验证过就吊销，
    重启应用仍可进入”的设计缺口。

    吊销恢复（R4.0c）：若存在停用提示标记（说明管理员可能已 enable），
    用标记里的原码静默尝试重新激活——成功则自动恢复全功能，家长零操作。"""
    if not ENABLED:
        return
    hint = os.path.join(paths.DATA_HOME, REVOKED_HINT_FILE)
    if not os.path.exists(LICENSE_PATH) and os.path.exists(hint):
        try:
            code = json.load(open(hint, encoding="utf-8")).get("code")
            if code:
                ok, _ = activate(code, timeout=timeout)   # 管理员已 enable → 静默恢复；仍停用 → 保留红字
                if ok:
                    return
        except Exception:
            pass
    heartbeat(timeout=timeout)


def revoked_hint():
    """激活页红字提示：上次心跳收到过 code_revoked 且尚未重新激活（重新激活成功时清除）"""
    return os.path.exists(os.path.join(paths.DATA_HOME, REVOKED_HINT_FILE))


def due_for_heartbeat():
    env = load()
    if not env:
        return False
    return _now() - int(env["receipt"].get("server_time") or 0) > HEARTBEAT_CHECK


def maybe_heartbeat_async():
    """距上次成功验证超过间隔则后台心跳（启动时/状态查询时触发）"""
    if not ENABLED or not due_for_heartbeat():
        return False
    threading.Thread(target=heartbeat, daemon=True).start()
    return True


def start_heartbeat_loop():
    """常驻后台循环：运行中每到一个心跳间隔就检查补发（实测期 1 分钟一跳，观察黄条/恢复用）"""
    if not ENABLED:
        return

    def _loop():
        while True:
            time.sleep(HEARTBEAT_CHECK)   # R4.0b：10 分钟检查一次（原 24h——吊销感知最长滞后一天）
            try:
                if due_for_heartbeat():
                    heartbeat()
            except Exception:
                pass   # 后台循环永不中断主程序

    threading.Thread(target=_loop, daemon=True).start()
