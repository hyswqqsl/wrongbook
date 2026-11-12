# -*- coding: utf-8 -*-
"""云端鉴权与门禁回归（Plan B B1-T3/T4）

覆盖契约：
  1. 激活：有效码 200+Cookie；不存在 401；停用 403；过期 403
  2. 门禁：无 token/Cookie 全 401；Bearer 头与 Cookie 双轨等价
  3. 租户越权：B 读不到 A 的题/图片/导出产物；../ 穿透 404
  4. 成员头：X-Wrongbook-Uid 优先于会话默认；不属于本码回落
  5. 云端禁用端点：/api/shutdown 404；/api/license 返回 cloud 态
"""
import io
import os
import tempfile
import unittest

import tenant_ctx
import store
import cloud_store
import auth as _auth


class AuthTestBase(unittest.TestCase):
    def setUp(self):
        self._old_cloud = tenant_ctx.CLOUD
        tenant_ctx.CLOUD = True
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)
        now = __import__("time").time()
        cloud_store.ensure_account("WB-OK", "正常户")
        cloud_store.ensure_account("WB-STOP", "停用户", is_active=0)
        cloud_store.ensure_account("WB-OLD", "过期户", expires_at=int(now - 86400))
        from app import app
        self.app = app
        self.c = app.test_client()

    def tearDown(self):
        tenant_ctx.CLOUD = self._old_cloud
        store.set_test_data_dir(None)

    def login(self, code="WB-OK"):
        r = self.c.post("/api/auth/activate", json={"code": code})
        self.assertEqual(r.status_code, 200)
        return r.get_json()


class TestActivate(AuthTestBase):

    def test_activate_ok_sets_cookies(self):
        r = self.c.post("/api/auth/activate", json={"code": "WB-OK"})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j["code"], "WB-OK")
        self.assertTrue(j["token"].startswith("tk_"))
        self.assertTrue(j["users"])
        self.assertEqual(j["users"][0]["name"], "默认用户")
        # Cookie 双轨：激活响应种 wb_token(HttpOnly)/wb_uid
        set_cookies = "; ".join(r.headers.get_all("Set-Cookie")) if hasattr(r.headers, "get_all") else \
            "; ".join(str(v) for k, v in r.headers.items() if k == "Set-Cookie")
        self.assertIn(_auth.TOKEN_COOKIE + "=", set_cookies)
        self.assertIn("HttpOnly", set_cookies)
        # Cookie 已入 client jar → 无 Bearer 头也能过门禁
        raw = self.c.get("/api/users")
        self.assertEqual(raw.status_code, 200)

    def test_activate_wrong_code_401(self):
        r = self.c.post("/api/auth/activate", json={"code": "WB-NOPE"})
        self.assertEqual(r.status_code, 401)

    def test_activate_empty_401(self):
        r = self.c.post("/api/auth/activate", json={"code": ""})
        self.assertEqual(r.status_code, 401)

    def test_activate_revoked_403(self):
        r = self.c.post("/api/auth/activate", json={"code": "WB-STOP"})
        self.assertEqual(r.status_code, 403)

    def test_activate_expired_403(self):
        r = self.c.post("/api/auth/activate", json={"code": "WB-OLD"})
        self.assertEqual(r.status_code, 403)


class TestGate(AuthTestBase):

    def test_no_token_401(self):
        self.c = self.app.test_client()   # 干净 client（无 Cookie）
        for path in ("/api/stats", "/api/questions", "/api/topics", "/api/sheets",
                     "/api/users", "/data/users/x/photos/x.jpg", "/out/x.pdf"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 401, path)

    def test_bearer_header(self):
        j = self.login()
        self.c = self.app.test_client()
        r = self.c.get("/api/stats", headers={"Authorization": "Bearer " + j["token"]})
        self.assertEqual(r.status_code, 200)

    def test_invalid_token_401(self):
        self.c = self.app.test_client()
        r = self.c.get("/api/stats", headers={"Authorization": "Bearer tk_bogus"})
        self.assertEqual(r.status_code, 401)

    def test_revoked_account_immediately_403(self):
        j = self.login()
        cloud_store.ensure_account("WB-OK", is_active=0)   # 登录后吊销
        r = self.c.get("/api/stats")   # Cookie 在 jar → 走门禁 → 403
        self.assertEqual(r.status_code, 403)
        r2 = self.c.get("/api/stats", headers={"Authorization": "Bearer " + j["token"]})
        self.assertEqual(r2.status_code, 403)

    def test_heartbeat(self):
        self.login()
        r = self.c.post("/api/auth/heartbeat")
        self.assertEqual(r.status_code, 200)
        c2 = self.app.test_client()
        self.assertEqual(c2.post("/api/auth/heartbeat").status_code, 401)

    def test_logout(self):
        self.login()
        self.c.post("/api/auth/logout")
        r = self.c.get("/api/stats")
        self.assertEqual(r.status_code, 401)


class TestTenantIsolation(AuthTestBase):

    def _seed_a_question(self):
        import tenant_ctx as _t
        with _t.bind_tenant("WB-OK", None):
            from cloud_store import ensure_user
            uid = ensure_user("WB-OK")
        with _t.bind_tenant("WB-OK", uid):
            import store
            return uid, store.add({"type": "fill",
                                   "question": {"text": "测试题干内容足够长用于查重", "choices": None},
                                   "analysis": {"chapter": "第一章 三角形", "kp_path": [],
                                                "knowledge_points": [], "difficulty": 2}})

    def test_cross_tenant_question_404(self):
        cloud_store.ensure_account("WB-OTHER", "别家")
        uid, rec = self._seed_a_question()
        self.login("WB-OTHER")
        r = self.c.get("/api/questions/" + rec["id"])
        self.assertEqual(r.status_code, 404)
        # 本户可见
        j = self.login("WB-OK")
        r = self.c.get("/api/questions/" + rec["id"],
                       headers={"Authorization": "Bearer " + j["token"],
                                "X-Wrongbook-Uid": uid})
        self.assertEqual(r.status_code, 200)

    def test_media_cross_tenant_404(self):
        uid_a, _rec = self._seed_a_question()
        p = os.path.join(self.tmp, "users", uid_a, "photos")
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, "s1.jpg"), "wb").write(b"jpg")
        cloud_store.ensure_account("WB-OTHER", "别家")
        j_other = self.login("WB-OTHER")
        h = {"Authorization": "Bearer " + j_other["token"]}
        self.assertEqual(self.c.get("/data/users/{0}/photos/s1.jpg".format(uid_a),
                                    headers=h).status_code, 404)
        # 本户可读（Cookie client 切回 WB-OK）
        j_a = self.login("WB-OK")
        h_a = {"Authorization": "Bearer " + j_a["token"], "X-Wrongbook-Uid": uid_a}
        self.assertEqual(self.c.get("/data/users/{0}/photos/s1.jpg".format(uid_a),
                                    headers=h_a).status_code, 200)
        # ../ 穿透 404（send_from_directory 兜底）
        self.assertEqual(self.c.get("/data/users/{0}/photos/../../accounts.db".format(uid_a),
                                    headers=h_a).status_code in (404, 400), True)
        # topic_crops 他户 404
        self.assertEqual(self.c.get("/data/topic_crops/WB-OTHER/te_x.png",
                                    headers=h_a).status_code, 404)

    def test_out_scoped_by_code(self):
        od = os.path.join(self.tmp, "out", "WB-OK")
        os.makedirs(od, exist_ok=True)
        open(os.path.join(od, "卷.pdf"), "wb").write(b"%PDF-")
        od2 = os.path.join(self.tmp, "out", "WB-OTHER")
        os.makedirs(od2, exist_ok=True)
        open(os.path.join(od2, "卷.pdf"), "wb").write(b"%PDF-")
        j = self.login("WB-OK")
        h = {"Authorization": "Bearer " + j["token"]}
        self.assertEqual(self.c.get("/out/WB-OK/卷.pdf", headers=h).status_code, 200)
        self.assertEqual(self.c.get("/out/WB-OTHER/卷.pdf", headers=h).status_code, 404)

    def test_member_header_routing(self):
        j = self.login()
        uid_default = j["current_uid"]
        r = self.c.post("/api/users", json={"name": "二宝"})
        users = r.get_json()["users"]
        uid2 = next(u["id"] for u in users if u["id"] != uid_default)
        # 头指定二宝 → 列表当前为二宝；且数据互相隔离（空库看不出，检查 current 字段）
        r2 = self.c.get("/api/users", headers={"X-Wrongbook-Uid": uid2})
        self.assertEqual(r2.get_json()["current"], uid2)
        # 伪造成员头（不属于本码）→ 回落会话默认，不报错不越权
        r3 = self.c.get("/api/users", headers={"X-Wrongbook-Uid": "u_hacker"})
        self.assertEqual(r3.get_json()["current"], uid_default)

    def test_users_switch_updates_session(self):
        j = self.login()
        uid_default = j["current_uid"]
        r = self.c.post("/api/users", json={"name": "二宝"})
        uid2 = next(u["id"] for u in r.get_json()["users"] if u["id"] != uid_default)
        r2 = self.c.post("/api/users/switch", json={"id": uid2})
        self.assertEqual(r2.status_code, 200)
        # 切换后：不带头的请求回落会话新默认
        r3 = self.c.get("/api/users")
        self.assertEqual(r3.get_json()["current"], uid2)
        # 切换到不属于本码的成员 → 400
        r4 = self.c.post("/api/users/switch", json={"id": "u_ghost"})
        self.assertEqual(r4.status_code, 400)


class TestCloudOnlyEndpoints(AuthTestBase):

    def test_shutdown_disabled(self):
        self.login()
        r = self.c.post("/api/shutdown", headers={"X-WrongBook-Exit": "1"})
        self.assertEqual(r.status_code, 404)

    def test_license_state_cloud(self):
        r = self.c.get("/api/license")
        self.assertEqual(r.get_json().get("state"), "cloud")


if __name__ == "__main__":
    unittest.main()
