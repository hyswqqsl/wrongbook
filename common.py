# -*- coding: utf-8 -*-
"""common —— 工程接入层：sys.path、引擎客户端、章节词表（V1.0.1 起自包含，无父项目依赖）"""
import os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(BASE, "engine")           # 内联的数学引擎模块（平铺目录，挂 sys.path）
ROOT = BASE                                     # 兼容别名（历史代码引用）
for _p in (BASE, ENGINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_any import PROVIDERS  # noqa: E402  engine/generate_any.py

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

# 章节受控词表（教材 KB 全五章）
CHAPTERS = ["第一章 三角形", "第二章 特殊三角形", "第三章 一元一次不等式",
            "第四章 图形与坐标", "第五章 一次函数", "其他"]

REASON_KINDS = {"careless": "马虎", "unknown": "不会", "other": "其他"}
TYPE_CN = {"single": "单选", "multiple": "多选", "fill": "填空", "short": "问答"}
CN_TYPE = {v: k for k, v in TYPE_CN.items()}


def make_client(provider="deepseek", model=None):
    """国内直连客户端；清代理环境变量（经验：Clash 代理会拦截国内 API）"""
    import openai
    p = PROVIDERS[provider]
    for k in PROXY_KEYS:
        os.environ.pop(k, None)
    key = os.environ.get(p["key_env"])
    if not key:
        raise RuntimeError("请先 export {0}=你的key".format(p["key_env"]))
    return openai.OpenAI(api_key=key, base_url=p["base_url"]), model or p["model"]
