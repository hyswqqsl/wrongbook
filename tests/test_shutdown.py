# -*- coding: utf-8 -*-
"""退出接口契约：必须带自定义头（防跨站静默关停）；带 token 则 200 并调度 os._exit"""
import time
import unittest
from unittest import mock

import app as appmod


class TestShutdown(unittest.TestCase):
    def setUp(self):
        self.c = appmod.app.test_client()

    def test_requires_exit_token(self):
        r = self.c.post("/api/shutdown")
        self.assertEqual(r.status_code, 403)                      # 无自定义头 → 拒绝（防 CSRF）
        self.assertEqual(r.get_json()["error"], "missing exit token")

    def test_shutdown_schedules_exit(self):
        with mock.patch("os._exit") as ex, \
             mock.patch.object(appmod.threading, "Timer") as T:
            T.return_value = mock.MagicMock()                 # start() 变空操作
            r = self.c.post("/api/shutdown", headers={"X-WrongBook-Exit": "1"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), {"ok": True})
            T.assert_called_once()                            # 调度了退出定时器
            T.call_args[0][1]()                               # 同步执行回调（os._exit 已 mock）
            ex.assert_called_once_with(0)                     # 进程退出被调用（不真退）

    def test_gate_exempt_when_locked(self):
        """锁定/未激活状态下也必须能退出（否则被锁用户连退出按钮都用不了）"""
        with mock.patch.object(appmod.license_client, "ENABLED", True), \
             mock.patch.object(appmod.license_client, "state", return_value=("locked", {})):
            r = self.c.post("/api/shutdown", headers={"X-WrongBook-Exit": "1"})
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
