# -*- coding: utf-8 -*-
"""gen_spec 健壮性回归测试（stdlib unittest）

背景：DeepSeek 默认开思考，思考链会烧光 max_tokens 导致 content=''，
spec 生成必然失败（父项目 make_figures.ask_json 已踩过此坑）。
本测试用假客户端复现该行为，锁定以下契约：
  1. 首次调用返回空 content 时，必须降级（关思考）重试，而不是直接放弃
  2. 降级后应成功拿到 spec
  3. 端点拒绝 extra_body（400）时，应摘除 extra_body 继续重试
  4. 全部策略失败时，错误信息要带 finish_reason，便于人审判断原因
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import figureflow  # noqa: E402


VALID_SPEC = {
    "points": [{"name": "A", "x": 1.0, "y": 1.0},
               {"name": "B", "x": 8.0, "y": 1.0},
               {"name": "C", "x": 1.0, "y": 6.0}],
    "segments": [{"a": "A", "b": "B"}, {"a": "A", "b": "C"}, {"a": "B", "b": "C"}],
}


class _Msg:
    def __init__(self, content, finish_reason="stop"):
        self.content = content
        self.finish_reason = finish_reason


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Msg(content, finish_reason)


class _Resp:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]


class FakeCompletions:
    """按调用序列返回预设响应；记录每次调用的 kwargs"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0) if self.responses else _Resp("", "length")
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, responses):
        self.chat = type("chat", (), {})()
        self.chat.completions = FakeCompletions(responses)


class TestGenSpecRobustness(unittest.TestCase):

    def test_retries_with_thinking_disabled_on_empty_content(self):
        """第 1 次空 content（思考烧光 token）→ 应带 thinking disabled 重试并成功"""
        import json
        client = FakeClient([
            _Resp("", "length"),                       # 思考烧光 max_tokens
            _Resp(json.dumps(VALID_SPEC), "stop"),     # 关思考后正常
        ])
        spec = figureflow.gen_spec(client, "deepseek-flash", "如图，在三角形 ABC 中…")
        self.assertEqual(len(spec["points"]), 3)
        self.assertEqual(len(client.chat.completions.calls), 2)
        # 第 2 次调用应带 thinking disabled
        self.assertIn("extra_body", client.chat.completions.calls[1])

    def test_strips_extra_body_when_endpoint_rejects_it(self):
        """端点对 extra_body 报 400 → 摘除 extra_body 继续重试"""
        import json
        client = FakeClient([
            _Resp("", "length"),
            ValueError("400 unknown parameter extra_body"),
            _Resp(json.dumps(VALID_SPEC), "stop"),
        ])
        spec = figureflow.gen_spec(client, "deepseek-flash", "如图，在三角形 ABC 中…")
        self.assertEqual(len(spec["points"]), 3)

    def test_error_message_mentions_finish_reason(self):
        """全部策略失败 → 异常信息带 finish_reason，区分"模型抽风"与"几何画不出" """
        client = FakeClient([_Resp("", "length"), _Resp("", "length"), _Resp("", "length")])
        with self.assertRaises(RuntimeError) as ctx:
            figureflow.gen_spec(client, "deepseek-flash", "如图，在三角形 ABC 中…")
        self.assertIn("finish", str(ctx.exception).lower())

    def test_valid_json_in_plain_mode_is_accepted(self):
        """plain 模式（无 response_format）返回纯文本 JSON 也应被接受"""
        import json
        client = FakeClient([_Resp(json.dumps(VALID_SPEC), "stop")])
        spec = figureflow.gen_spec(client, "deepseek-flash", "如图，在三角形 ABC 中…")
        self.assertEqual(len(spec["points"]), 3)


class TestVectorizeFallback(unittest.TestCase):
    """向量化失败 → 返回 None + 可读原因（不抛异常，让调用方回落裁剪图）"""

    def test_returns_none_on_total_failure(self):
        client = FakeClient([_Resp("", "length")] * 6)
        spec, msgs = figureflow.vectorize_figure(client, "deepseek-flash", "如图，在三角形 ABC 中…", rounds=1)
        self.assertIsNone(spec)
        self.assertTrue(msgs)
        self.assertIn("失败", msgs[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
