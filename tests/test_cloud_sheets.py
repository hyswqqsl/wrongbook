# -*- coding: utf-8 -*-
"""云端异步转录回归（Plan B B1-T5，D-B3 统一协议）

覆盖契约：
  1. POST /api/sheets 立即返回 {id, status:transcribing}（不等 LLM）
  2. 占位草稿单后台回填 → 轮询 GET /api/sheets/<id> 直到 status=pending
  3. 转录失败 → status=error + error 字段（前端可展示）
  4. 云端后台线程带租户上下文（占位单/回填落本户，不串户）
  5. job 表 TTL 清理（2h）防 2G 内存服务器驻留膨胀
"""
import io
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import tenant_ctx  # noqa: E402
import store  # noqa: E402
import cloud_store  # noqa: E402


class CloudSheetTestBase(unittest.TestCase):
    def setUp(self):
        self._old_cloud = tenant_ctx.CLOUD
        tenant_ctx.CLOUD = True
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)
        cloud_store.ensure_account("WB-AA", "A家")
        from app import app
        self.app = app
        self.c = app.test_client()
        r = self.c.post("/api/auth/activate", json={"code": "WB-AA"})
        self.token = r.get_json()["token"]
        self.h = {"Authorization": "Bearer " + self.token}

    def tearDown(self):
        tenant_ctx.CLOUD = self._old_cloud
        store.set_test_data_dir(None)

    def _post_sheet(self):
        import reviewflow as _rf
        with mock.patch.object(_rf, "transcribe_photo", side_effect=self._fake):
            r = self.c.post("/api/sheets", data={"photo": (io.BytesIO(b"fakejpg"), "p.jpg"),
                                                 "grade": "四年级下册", "source": "期中卷"},
                            content_type="multipart/form-data", headers=self.h)
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def _fake(self, photo_bytes, source="", provider=None, grade=None, sheet_id=None):
        time.sleep(0.15)   # 模拟 LLM 耗时：验证请求先于转录完成返回
        store.set_sheet_status(sheet_id, "pending", {
            "questions": [{"no": 1, "type": "fill", "question": "1+1=?",
                           "grade": grade or "", "selected": False}],
            "photo": os.path.join(store.photo_dir(), sheet_id + ".jpg")})


class TestAsyncTranscribe(CloudSheetTestBase):

    def test_immediate_return_then_poll_pending(self):
        t0 = time.time()
        j = self._post_sheet()
        self.assertLess(time.time() - t0, 1.0)   # 立即返回（不等转录）
        self.assertTrue(j["id"])
        self.assertEqual(j["status"], "transcribing")
        # 轮询直至 pending
        deadline = time.time() + 10
        sheet = None
        while time.time() < deadline:
            sheet = self.c.get("/api/sheets/" + j["id"], headers=self.h).get_json()
            if sheet.get("status") != "transcribing":
                break
            time.sleep(0.1)
        self.assertIsNotNone(sheet)
        self.assertEqual(sheet["status"], "pending")
        self.assertEqual(sheet["questions"][0]["question"], "1+1=?")
        self.assertEqual(sheet["questions"][0]["grade"], "四年级下册")   # 年级透传

    def test_error_status_propagates(self):
        import reviewflow as _rf
        def boom(photo_bytes, source="", provider=None, grade=None, sheet_id=None):
            time.sleep(0.1)
            raise RuntimeError("照片中未识别到题目")
        with mock.patch.object(_rf, "transcribe_photo", side_effect=boom):
            j = self.c.post("/api/sheets", data={"photo": (io.BytesIO(b"x"), "p.jpg")},
                            content_type="multipart/form-data", headers=self.h).get_json()
        self.assertEqual(j["status"], "transcribing")
        deadline = time.time() + 10
        sheet = None
        while time.time() < deadline:
            sheet = self.c.get("/api/sheets/" + j["id"], headers=self.h).get_json()
            if sheet.get("status") != "transcribing":
                break
            time.sleep(0.1)
        self.assertEqual(sheet["status"], "error")
        self.assertIn("未识别到题目", sheet.get("error", ""))

    def test_placeholder_isolated_per_tenant(self):
        """云端后台线程绑定租户：占位单落本户，他户不可见"""
        j = self._post_sheet()
        cloud_store.ensure_account("WB-BB", "B家")
        h_b = {"Authorization": "Bearer " + self.c.post(
            "/api/auth/activate", json={"code": "WB-BB"}).get_json()["token"]}
        self.assertEqual(self.c.get("/api/sheets/" + j["id"], headers=h_b).status_code, 404)
        deadline = time.time() + 10
        while time.time() < deadline:
            sheet = self.c.get("/api/sheets/" + j["id"], headers=self.h).get_json()
            if sheet.get("status") != "transcribing":
                break
            time.sleep(0.1)
        self.assertEqual(sheet["status"], "pending")


class TestJobTtl(unittest.TestCase):

    def test_jobs_gc_drops_expired_only(self):
        from app import _jobs_gc, _JOB_TTL
        now = time.time()
        jobs = {"old": {"state": "done", "finished": now - _JOB_TTL - 10},
                "fresh": {"state": "done", "finished": now - 10},
                "running": {"state": "running", "finished": 0}}
        _jobs_gc(jobs, __import__("threading").Lock())
        self.assertNotIn("old", jobs)
        self.assertIn("fresh", jobs)
        self.assertIn("running", jobs)

    def test_commit_job_gc_ttl(self):
        import app as _app
        now = time.time()
        sid_old, sid_fresh = "d_old", "d_fresh"
        with _app._commit_lock:
            _app._commit_jobs[sid_old] = {"state": "done", "started": now - 9999,
                                          "finished": now - _app._JOB_TTL - 10}
            _app._commit_jobs[sid_fresh] = {"state": "done", "started": now,
                                            "finished": now}
            _app._commit_job_gc()
        self.assertNotIn(sid_old, _app._commit_jobs)
        self.assertIn(sid_fresh, _app._commit_jobs)
        with _app._commit_lock:
            _app._commit_jobs.pop(sid_fresh, None)


if __name__ == "__main__":
    unittest.main()
