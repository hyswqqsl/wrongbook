# -*- coding: utf-8 -*-
"""AI 引擎注入式配置回归测试（ai_config / make_client / 配置 API）

覆盖契约：
  1. 无配置文件 → 默认值；GET /api/ai-config ready=false
  2. save_patch 落盘（provider/keys/models）+ 原子写 .bak 不残留 .tmp
  3. key 解析优先级：ai_config.json > 环境变量
  4. active_provider 回落：显式 provider 无 key → 用第一个有 key 的引擎
  5. make_client：GLM 文本/视觉双模型路由（vision=True → glm-4.6v-flash）
  6. 未配置时 make_client 抛错且信息带设置指引
  7. GET/POST API：key 只回传掩码，响应永不出现明文；非法 provider 保存被拒
  8. test_provider：未知引擎 / 空 key 返回 ok=false（不发起网络调用）
"""
import os
import sys
import json
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import ai_config  # noqa: E402
from common import make_client  # noqa: E402

ENV_KEYS = ("DEEPSEEK_API_KEY", "GLM_API_KEY")


class TestAiConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_path = ai_config.config_path()
        self._orig_env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        ai_config.set_config_path(os.path.join(self.tmp, "ai_config.json"))

    def tearDown(self):
        ai_config.set_config_path(self._orig_path)
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ---------- 默认值与落盘 ----------

    def test_defaults_when_no_file(self):
        cfg = ai_config.load()
        self.assertIsNone(cfg["provider"])
        self.assertEqual(set(cfg["keys"]), {"deepseek", "glm"})
        self.assertEqual(ai_config.active_provider(), None)
        self.assertFalse(ai_config.describe()["ready"])

    def test_save_patch_roundtrip_and_atomic(self):
        ai_config.save_patch({"provider": "glm", "keys": {"glm": " glm-key-123456 "},
                              "models": {"glm": {"text": "glm-5.3-flash", "vision": "glm-4.6v-flash"}}})
        self.assertEqual(ai_config.load()["provider"], "glm")
        self.assertEqual(ai_config.load()["keys"]["glm"], "glm-key-123456")   # 去空白
        self.assertEqual(ai_config.resolve_model("glm", vision=True), "glm-4.6v-flash")
        self.assertEqual(ai_config.resolve_model("glm", vision=False), "glm-5.3-flash")
        self.assertFalse(os.path.exists(ai_config.config_path() + ".tmp"))    # 无残留临时文件
        self.assertEqual(ai_config.active_provider(), "glm")

    def test_invalid_provider_rejected(self):
        with self.assertRaises(ValueError):
            ai_config.save_patch({"provider": "openai"})
        ai_config.save_patch({"keys": {"unknown": "x"}})   # 未知引擎的 key 静默忽略（不报错）
        self.assertEqual(ai_config.load()["keys"], {"deepseek": "", "glm": ""})

    # ---------- 优先级与回落 ----------

    def test_config_key_beats_env(self):
        os.environ["GLM_API_KEY"] = "env-glm-key-987654"
        ai_config.save_patch({"keys": {"glm": "config-glm-key-123456"}})
        self.assertEqual(ai_config.resolve_key("glm"), "config-glm-key-123456")
        self.assertEqual(ai_config.key_source("glm"), "config")
        ai_config.save_patch({"keys": {"glm": ""}})                 # 清空 → 回落环境变量
        self.assertEqual(ai_config.resolve_key("glm"), "env-glm-key-987654")
        self.assertEqual(ai_config.key_source("glm"), "env")

    def test_active_provider_fallback(self):
        """provider=None（自动）→ 用第一个配了 key 的引擎；显式引擎缺 key → 回落另一家"""
        os.environ["GLM_API_KEY"] = "env-glm-key-987654"
        self.assertEqual(ai_config.active_provider(), "glm")
        ai_config.save_patch({"provider": "deepseek"})              # deepseek 无 key → 回落 glm
        self.assertEqual(ai_config.active_provider(), "glm")
        self.assertEqual(ai_config.describe()["provider"], "glm")
        ai_config.save_patch({"keys": {"deepseek": "sk-ds-key-1234567890"}})
        self.assertEqual(ai_config.active_provider(), "deepseek")   # 配了 key 的显式引擎优先

    # ---------- make_client ----------

    def test_make_client_glm_text_vision_routing(self):
        """glm-5.3-flash 原生多模态：文本/视觉默认同模型；用户仍可按字段覆盖"""
        os.environ["GLM_API_KEY"] = "env-glm-key-987654"
        client, model = make_client()                 # 文本
        self.assertEqual(model, "glm-5.3-flash")
        self.assertIn("bigmodel", str(client.base_url))
        client2, model2 = make_client(vision=True)    # 视觉（转录/向量化）——默认同模型
        self.assertEqual(model2, "glm-5.3-flash")
        ai_config.save_patch({"models": {"glm": {"text": "", "vision": "glm-4.6v-flash"}}})
        _, model3 = make_client(vision=True)          # 用户显式配了视觉模型 → 覆盖
        self.assertEqual(model3, "glm-4.6v-flash")
        client4, model4 = make_client("glm", model="glm-custom")
        self.assertEqual(model4, "glm-custom")        # 显式 model 透传（兼容旧调用）

    def test_make_client_no_key_friendly_error(self):
        ai_config.save_patch({"provider": "glm"})
        with self.assertRaises(RuntimeError) as ctx:
            make_client()
        self.assertIn("设置", str(ctx.exception))      # 指引去网页设置
        self.assertIn("GLM_API_KEY", str(ctx.exception))

    # ---------- HTTP API ----------

    def test_api_masked_and_save(self):
        from app import app
        app.config["TESTING"] = True
        c = app.test_client()
        r = c.get("/api/ai-config")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn("providers", j)
        self.assertFalse(j["ready"])

        secret = "sk-secret-key-abcdef123456"
        r = c.post("/api/ai-config", json={"provider": "deepseek", "keys": {"deepseek": secret}})
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertNotIn(secret, body)                       # 明文永不出现在响应
        j = r.get_json()
        self.assertTrue(j["ready"])
        self.assertEqual(j["provider"], "deepseek")
        self.assertEqual(j["providers"]["deepseek"]["key_masked"], ai_config.mask(secret))
        self.assertEqual(j["providers"]["deepseek"]["key_source"], "config")
        # 落盘校验（文件在 tmp，可直接读）
        with open(ai_config.config_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["keys"]["deepseek"], secret)

        r = c.post("/api/ai-config", json={"provider": "openai"})
        self.assertEqual(r.status_code, 500)                 # 非法引擎被拒
        r = c.get("/api/ai-config")
        self.assertEqual(r.get_json()["provider"], "deepseek")

    def test_test_provider_no_network_guards(self):
        self.assertEqual(ai_config.test_provider("openai")["ok"], False)
        self.assertEqual(ai_config.test_provider("glm")["ok"], False)   # 无 key，直接拒绝

    def test_mask(self):
        self.assertEqual(ai_config.mask(""), "")
        # 假 key：单测 fixture 严禁使用真实 key（真实 key 一旦入库即视为泄露）
        self.assertEqual(ai_config.mask("sk-abcdef123456fake"), "sk-a****fake")
        self.assertEqual(ai_config.mask("short"), "****")


if __name__ == "__main__":
    unittest.main()
