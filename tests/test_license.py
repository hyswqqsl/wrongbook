# -*- coding: utf-8 -*-
"""R2 激活客户端回归：验签/设备绑定/回执结构/状态机/时钟回拨防护/激活与心跳流程/API 门禁

凭证结构（v2）：license.json = {license: 服务端签发+签名（不可变）, receipt: 最近一次服务端
签名回执（server_time 为宽限期基准）}。客户端无私钥，不能伪造/刷新任何签名字段。
"""
import os, json, time, base64, unittest, tempfile, shutil
from unittest import mock

import paths
import license_client as lc
import store
import app as appmod

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

_QA_KEY = Ed25519PrivateKey.generate()
_QA_PUB = _QA_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _sign(d):
    payload = {k: v for k, v in d.items() if k != "sig"}
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    d = dict(d)
    d["sig"] = base64.b64encode(_QA_KEY.sign(canon)).decode()
    return d


class LicenseTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = (lc.LICENSE_PATH, lc.LICENSE_PUBKEY_PEM, lc._DEVICE_ID,
                     lc.ENABLED, lc._http_post)
        lc.LICENSE_PATH = os.path.join(self.tmp, "license.json")
        lc.LICENSE_PUBKEY_PEM = _QA_PUB
        lc._DEVICE_ID = "hw-test"          # 固定设备指纹（隔离真实机器）
        lc.ENABLED = True

    def tearDown(self):
        (lc.LICENSE_PATH, lc.LICENSE_PUBKEY_PEM, lc._DEVICE_ID,
         lc.ENABLED, lc._http_post) = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkenv(self, **kw):
        """构造合法凭证信封；kw 支持 license_* / receipt_time 覆写"""
        now = int(time.time())
        lic = {"code": "WB-AAAA-BBBB-CCCC", "device_id": lc._DEVICE_ID,
               "owner": "测试家庭", "issued_at": now - 86400,
               "expires_at": kw.pop("license_expires_at", now + 365 * 86400),
               "features": []}
        lic.update({k[8:]: v for k, v in kw.items() if k.startswith("license_")})
        rtime = kw.pop("receipt_time", now)   # 默认"刚回执"：不依赖 HEARTBEAT_INTERVAL 取值
        r = {"type": "receipt", "code": lic["code"], "device_id": lc._DEVICE_ID,
             "server_time": rtime}
        return {"license": _sign(lic), "receipt": _sign(r)}

    def _write(self, **kw):
        env = self._mkenv(**kw)
        lc.save(env)
        return env


class TestVerifyAndBind(LicenseTestBase):
    def test_roundtrip_and_tamper(self):
        env = self._mkenv()
        self.assertTrue(lc.verify_license(env["license"]))
        self.assertTrue(lc.verify_receipt(env["receipt"], env["license"]["code"]))
        self.assertFalse(lc.verify_license(dict(env["license"], owner="被篡改")))
        self.assertFalse(lc.verify_license(dict(env["license"], sig="AAAA")))
        self.assertFalse(lc.verify_license({"code": "x"}))
        bad_receipt = dict(env["receipt"], server_time=int(time.time()) + 99999999)
        self.assertFalse(lc.verify_receipt(_sign(bad_receipt) if False else bad_receipt))  # 直接改字段=坏签
        self.assertFalse(lc.verify_receipt(env["receipt"], "WB-OTHER"))   # code 不匹配

    def test_receipt_tamper_resign_fails_without_privkey(self):
        r = dict(self._mkenv()["receipt"], server_time=int(time.time()) + 10 ** 9)
        self.assertFalse(lc.verify_receipt(r))            # 改字段=坏签：重签需要私钥（客户端没有）

    def test_device_binding(self):
        env = self._mkenv(license_device_id="hw-other")
        self.assertFalse(lc.verify_license(env["license"]))   # 拷贝来的凭证无效
        self.assertIsNone(lc.load())

    def test_load_rejects_corrupted(self):
        self._write()
        with open(lc.LICENSE_PATH, "w", encoding="utf-8") as f:
            f.write("{broken json")
        self.assertIsNone(lc.load())

    def test_receipt_device_binding(self):
        env = self._mkenv()
        env["receipt"] = _sign(dict(env["receipt"], device_id="hw-other"))
        lc.save(env)
        self.assertIsNone(lc.load())                      # 回执与设备不绑 → 整体无效


class TestStateMachine(LicenseTestBase):
    def test_unactivated_when_no_license(self):
        self.assertEqual(lc.state(), ("unactivated", {}))

    def test_ok_then_grace_then_locked(self):
        now = int(time.time())
        self._write(receipt_time=now)
        self.assertEqual(lc.state()[0], "ok")
        self._write(receipt_time=now - 3 * 86400)
        st, info = lc.state()
        self.assertEqual(st, "grace")
        self.assertEqual(info["grace_days_left"], 11)     # 14 - 3
        self._write(receipt_time=now - 15 * 86400)
        self.assertEqual(lc.state(), ("locked", {"reason": "grace_exceeded"}))

    def test_expired_locks(self):
        self._write(license_expires_at=int(time.time()) - 100)
        self.assertEqual(lc.state(), ("locked", {"reason": "expired"}))

    def test_permanent_license_never_expires(self):
        self._write(license_expires_at=4102444800)        # 2100-01-01
        self.assertEqual(lc.state()[0], "ok")

    def test_clock_rollback_receipt_floor(self):
        now = int(time.time())
        self._write(receipt_time=now - 13 * 86400)        # 已离线 13 天
        with mock.patch.object(lc.time, "time", return_value=now - 40 * 86400):
            st, info = lc.state()                          # 本机回拨 40 天：回执时间封底 +
        self.assertEqual(st, "grace")                      # 不视为"新鲜"→ 宽限满额显示，
        self.assertEqual(info["grace_days_left"], lc.GRACE_DAYS)   # 联网后服务端即刻纠正

    def test_disabled_always_ok(self):
        lc.ENABLED = False
        self.assertEqual(lc.state(), ("ok", {}))           # 开发态未启用：恒放行

    def test_status_masked_code_and_expiry(self):
        self._write(license_expires_at=int(time.time()) + 30 * 86400)
        s = lc.status()
        self.assertEqual(s["state"], "ok")
        self.assertIn("WB-AA", s["code_masked"])
        self.assertNotIn("BBBB", s["code_masked"])         # 掩码不泄露中段
        self.assertTrue(s["expires_at"].startswith(time.strftime("%Y")))


class TestActivateAndHeartbeat(LicenseTestBase):
    def test_activate_saves_verified_envelope(self):
        now = int(time.time())
        env = self._mkenv(receipt_time=now)
        lc._http_post = lambda path, payload, timeout=10: (
            (200, {"license": env["license"], "receipt": env["receipt"]})
            if path == "/v1/activate" else (0, {}))
        ok, err = lc.activate("WB-AAAA-BBBB-CCCC")
        self.assertTrue(ok, err)
        self.assertEqual(lc.state()[0], "ok")
        self.assertTrue(os.path.exists(lc.LICENSE_PATH))

    def test_activate_server_errors(self):
        for status, err_key, want in ((401, "code_invalid", "无效"),
                                      (403, "seat_limit", "上限"),
                                      (401, "code_revoked", "停用")):
            lc._http_post = lambda path, payload, timeout=10, s=status, e=err_key: (s, {"error": e})
            ok, msg = lc.activate("WB-X")
            self.assertFalse(ok)
            self.assertIn(want, msg)

    def test_activate_offline(self):
        def boom(path, payload, timeout=10):
            raise OSError("no net")
        lc._http_post = boom
        ok, msg = lc.activate("WB-X")
        self.assertFalse(ok)
        self.assertIn("无法连接", msg)

    def test_activate_rejects_bad_envelope(self):
        env = self._mkenv()
        forged = {"license": dict(env["license"], owner="假货"), "receipt": env["receipt"]}
        lc._http_post = lambda path, payload, timeout=10: (200, forged)
        ok, msg = lc.activate("WB-X")
        self.assertFalse(ok)
        self.assertIn("校验失败", msg)
        self.assertFalse(os.path.exists(lc.LICENSE_PATH))   # 坏凭证不落盘

    def test_heartbeat_refreshes_receipt_and_extends(self):
        env = self._write()
        now = int(time.time())
        new_r = _sign({"type": "receipt", "code": env["license"]["code"],
                       "device_id": lc._DEVICE_ID, "server_time": now})
        new_lic = _sign(dict(env["license"], expires_at=now + 400 * 86400))   # 服务端重签新凭证
        lc._http_post = lambda path, payload, timeout=10: (
            200, {"receipt": new_r, "license": new_lic})
        lc.heartbeat()
        fresh = lc.load()
        self.assertEqual(fresh["receipt"]["server_time"], now)             # 回执刷新
        self.assertEqual(fresh["license"]["expires_at"], now + 400 * 86400)  # 续费即时生效
        self.assertEqual(lc.state()[0], "ok")

    def test_heartbeat_revoked_forgets(self):
        self._write()
        lc._http_post = lambda path, payload, timeout=10: (401, {"error": "code_revoked"})
        lc.heartbeat()
        self.assertIsNone(lc.load())                       # 吊销 → 删凭证回激活页
        self.assertEqual(lc.state(), ("unactivated", {}))

    def test_revoked_hint_lifecycle(self):
        """吊销 → 激活页红字提示标记；重新激活成功 → 标记清除"""
        now = int(time.time())
        self._write()
        lc._http_post = lambda path, payload, timeout=10: (401, {"error": "code_revoked"})
        lc.heartbeat()
        self.assertTrue(lc.status()["revoked_hint"])
        self.assertTrue(os.path.exists(os.path.join(paths.DATA_HOME, lc.REVOKED_HINT_FILE)))
        env = self._mkenv(receipt_time=now)
        lc._http_post = lambda path, payload, timeout=10: (
            200, {"license": env["license"], "receipt": env["receipt"]})
        ok, err = lc.activate("WB-AAAA-BBBB-CCCC")
        self.assertTrue(ok, err)
        self.assertFalse(os.path.exists(os.path.join(paths.DATA_HOME, lc.REVOKED_HINT_FILE)))
        self.assertFalse(lc.status()["revoked_hint"])

    def test_heartbeat_bad_receipt_ignored(self):
        env = self._write()
        before = env["receipt"]["server_time"]
        bad = dict(env["receipt"], server_time=int(time.time()) + 10 ** 8)   # 改字段=坏签
        lc._http_post = lambda path, payload, timeout=10: (200, {"receipt": bad})
        lc.heartbeat()
        self.assertEqual(lc.load()["receipt"]["server_time"], before)      # 异常回执丢弃

    def test_heartbeat_offline_keeps_license(self):
        self._write()
        def boom(path, payload, timeout=10):
            raise OSError("no net")
        lc._http_post = boom
        lc.heartbeat()
        self.assertIsNotNone(lc.load())                    # 离线：宽限状态保留

    def test_due_for_heartbeat_window(self):
        self.assertFalse(lc.due_for_heartbeat())           # 无凭证
        now = int(time.time())
        self._write(receipt_time=now)
        self.assertFalse(lc.due_for_heartbeat())           # 1h 前验证过：<24h
        self._write(receipt_time=now - 25 * 3600)
        self.assertTrue(lc.due_for_heartbeat())


class TestAppGate(unittest.TestCase):
    """app 门禁：锁定时业务 API 全 403，首页/激活接口放行；未启用时全放行"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        store.set_test_mode()
        store.set_test_data_dir(self.tmp)
        self._old = (lc.LICENSE_PATH, lc.LICENSE_PUBKEY_PEM, lc._DEVICE_ID,
                     lc.ENABLED, lc._http_post)
        lc.LICENSE_PATH = os.path.join(self.tmp, "license.json")
        lc.LICENSE_PUBKEY_PEM = _QA_PUB
        lc._DEVICE_ID = "hw-test"
        lc.ENABLED = True
        self.c = appmod.app.test_client()

    def tearDown(self):
        lc.ENABLED = False   # 恢复开发态默认（防污染其他测试文件）
        (lc.LICENSE_PATH, lc.LICENSE_PUBKEY_PEM, lc._DEVICE_ID,
         lc.ENABLED, lc._http_post) = self._old
        store.set_test_data_dir(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_valid(self, receipt_time=None):
        now = int(time.time())
        lic = {"code": "WB-AAAA-BBBB-CCCC", "device_id": "hw-test", "owner": "测试",
               "issued_at": now, "expires_at": now + 86400, "features": []}
        r = {"type": "receipt", "code": lic["code"], "device_id": "hw-test",
             "server_time": receipt_time or now}
        lc.save({"license": _sign(lic), "receipt": _sign(r)})

    def test_disabled_all_open(self):
        lc.ENABLED = False
        self.assertEqual(self.c.get("/api/stats").status_code, 200)

    def test_locked_blocks_business_api_but_not_activation(self):
        lc.ENABLED = True   # 无凭证 = unactivated
        r = self.c.get("/api/stats")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], "license_unactivated")
        self.assertEqual(self.c.get("/api/questions").status_code, 403)
        self.assertEqual(self.c.get("/").status_code, 200)              # 首页放行（渲染激活页）
        self.assertEqual(self.c.get("/api/license").status_code, 200)   # 状态可查
        self.assertEqual(self.c.post("/api/license/activate",
                                     json={"code": ""}).status_code, 400)  # 激活可调

    def test_licensed_api_open(self):
        self._write_valid()
        self.assertEqual(self.c.get("/api/stats").status_code, 200)
        st = self.c.get("/api/license").get_json()
        self.assertEqual(st["state"], "ok")
        self.assertIn("WB-AA", st["code_masked"])

    def test_grace_api_open_with_banner_info(self):
        self._write_valid(receipt_time=int(time.time()) - 3 * 86400)
        self.assertEqual(self.c.get("/api/stats").status_code, 200)     # 宽限期全功能
        self.assertEqual(self.c.get("/api/license").get_json()["state"], "grace")


if __name__ == "__main__":
    unittest.main()


class TestValidateOnStartup(LicenseTestBase):
    """R4.0b：启动无条件同步验证——刚验证过（节流窗口内）也必须发心跳，吊销下次启动即感知"""

    def test_startup_validates_even_when_fresh(self):
        now = int(time.time())
        env = self._mkenv(receipt_time=now)          # 刚验证过：due=False（节流窗口内）
        lc.save(env)
        self.assertFalse(lc.due_for_heartbeat())
        lc._http_post = mock.Mock(return_value=(
            200, {"license": env["license"], "receipt": env["receipt"]}))
        lc.validate_on_startup()
        lc._http_post.assert_called_once()           # 无条件发心跳（不看节流）

    def test_startup_detects_revocation(self):
        self._write()
        lc._http_post = lambda path, payload, timeout=10: (401, {"error": "code_revoked"})
        lc.validate_on_startup()
        self.assertIsNone(lc.load())                 # 吊销：启动即感知 → 锁回激活页
        self.assertTrue(lc.status()["revoked_hint"])

    def test_startup_offline_tolerated(self):
        self._write()
        def boom(path, payload, timeout=4):
            raise OSError("no net")
        lc._http_post = boom
        lc.validate_on_startup(timeout=4)            # 离线：不抛异常，宽限继续
        self.assertIsNotNone(lc.load())

    def test_disabled_skips(self):
        lc.ENABLED = False
        calls = []
        lc._http_post = lambda *a, **k: calls.append(1) or (0, {})
        lc.validate_on_startup()
        self.assertEqual(calls, [])                  # 未启用：不发
