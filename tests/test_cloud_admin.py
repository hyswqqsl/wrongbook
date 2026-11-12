# -*- coding: utf-8 -*-
"""wrongbook_admin sync-accounts 回归（Plan B B1-T8，D-B7）

覆盖契约：
  1. license 账本 → accounts 单向同步（owner/expires_at 跟随；幂等可反复跑）
  2. 吊销/账本删除的码 → is_active=0（留行留痕，不删）
  3. 同步绝不覆盖业务侧 ai_config（家长自配 Key）
"""
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import tenant_ctx  # noqa: E402
import store  # noqa: E402
import cloud_store  # noqa: E402
import wrongbook_admin  # noqa: E402


def _make_license_db(path, rows):
    if os.path.exists(path):
        os.remove(path)   # 重建账本（模拟管理员重新发/吊销）
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE codes(code TEXT PRIMARY KEY, owner TEXT, "
                 "max_devices INTEGER DEFAULT 2, issued_at INTEGER, expires_at INTEGER, "
                 "revoked INTEGER DEFAULT 0, features TEXT DEFAULT '[]')")
    for code, owner, expires_at, revoked in rows:
        conn.execute("INSERT INTO codes(code,owner,issued_at,expires_at,revoked) "
                     "VALUES(?,?,?,?,?)", (code, owner, 1700000000, expires_at, revoked))
    conn.commit()
    conn.close()


class TestSyncAccounts(unittest.TestCase):

    def setUp(self):
        self._old_cloud = tenant_ctx.CLOUD
        tenant_ctx.CLOUD = True
        self.tmp = tempfile.mkdtemp()
        store.set_test_data_dir(self.tmp)
        self.licdb = os.path.join(self.tmp, "license.db")
        _make_license_db(self.licdb, [
            ("WB-ALIVE", "李同学家", 1800000000, 0),
            ("WB-DEAD", "旧用户", 1800000000, 1),
        ])

    def tearDown(self):
        tenant_ctx.CLOUD = self._old_cloud
        store.set_test_data_dir(None)

    def test_sync_creates_and_deactivates(self):
        n = wrongbook_admin.sync_accounts(self.licdb)
        self.assertEqual(n, 1)
        acct = cloud_store.get_account("WB-ALIVE")
        self.assertEqual(acct["owner"], "李同学家")
        self.assertEqual(acct["expires_at"], 1800000000)
        self.assertEqual(acct["is_active"], 1)
        self.assertEqual(cloud_store.get_account("WB-DEAD"), None)   # 未入账本前不存在
        # 幂等：再跑一次结果不变
        self.assertEqual(wrongbook_admin.sync_accounts(self.licdb), 1)

    def test_revoke_then_resync_deactivates_without_delete(self):
        wrongbook_admin.sync_accounts(self.licdb)
        self.login_ready("WB-ALIVE")
        # 家长配了 Key（业务侧数据）
        import tenant_ctx as _t
        with _t.bind_tenant("WB-ALIVE", cloud_store.ensure_user("WB-ALIVE")):
            import ai_config
            ai_config.save_patch({"keys": {"glm": "family-glm-key"}})
        # 管理员吊销 WB-ALIVE
        _make_license_db(self.licdb, [("WB-ALIVE", "李同学家", 1800000000, 1)])
        wrongbook_admin.sync_accounts(self.licdb)
        acct = cloud_store.get_account("WB-ALIVE")
        self.assertEqual(acct["is_active"], 0)          # 停用（留行）
        # 吊销不覆盖 ai_config：重新启用后家长 Key 仍在
        _make_license_db(self.licdb, [("WB-ALIVE", "李同学家", 1800000000, 0)])
        wrongbook_admin.sync_accounts(self.licdb)
        with _t.bind_tenant("WB-ALIVE", cloud_store.ensure_user("WB-ALIVE")):
            self.assertEqual(ai_config.load()["keys"]["glm"], "family-glm-key")

    def test_resync_updates_owner_and_expiry(self):
        wrongbook_admin.sync_accounts(self.licdb)
        _make_license_db(self.licdb, [("WB-ALIVE", "王同学家", 1900000000, 0)])
        wrongbook_admin.sync_accounts(self.licdb)
        acct = cloud_store.get_account("WB-ALIVE")
        self.assertEqual(acct["owner"], "王同学家")
        self.assertEqual(acct["expires_at"], 1900000000)

    def login_ready(self, code):
        """给已同步码建默认成员（真实激活路径的等价物）"""
        cloud_store.ensure_user(code)


if __name__ == "__main__":
    unittest.main()
