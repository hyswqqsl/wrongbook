# -*- coding: utf-8 -*-
"""common —— 工程接入层：sys.path、引擎客户端、章节词表（V1.0.1 起自包含，无父项目依赖）"""
import os, sys

try:
    import paths
    BASE = paths.app_root()   # R4：冻结态取 exe 目录（engine/ 随包复制在旁）
except Exception:            # paths 不可用时（极端隔离场景）回退源码目录
    BASE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(BASE, "engine")           # 内联的数学引擎模块（平铺目录，挂 sys.path）
ROOT = BASE                                     # 兼容别名（历史代码引用）
for _p in (BASE, ENGINE):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from generate_any import PROVIDERS  # noqa: E402  engine/generate_any.py

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

# 章节受控词表（教材 KB 全五章）
CHAPTERS = ["第一章 三角形", "第二章 特殊三角形", "第三章 一元一次不等式",
            "第四章 图形与坐标", "第五章 一次函数", "其他"]

REASON_KINDS = {"careless": "马虎", "unknown": "不会", "other": "其他"}
TYPE_CN = {"single": "单选", "multiple": "多选", "fill": "填空", "short": "问答"}
CN_TYPE = {v: k for k, v in TYPE_CN.items()}


def make_client(provider=None, model=None, vision=False):
    """引擎客户端（注入式：data/ai_config.json > 环境变量，见 ai_config.py）。
    provider 缺省 = 用户配置的引擎（DeepSeek/智谱GLM 二选一）；
    vision=True 选用视觉模型（转录/图形向量化；GLM 文本与视觉是两个模型）。"""
    import ai_config
    provider = provider or ai_config.active_provider()
    if not provider:
        raise RuntimeError("未配置 AI 引擎：请点页面右上角「⚙ 设置」填入 API Key"
                           "（支持 DeepSeek / 智谱GLM），或设环境变量 DEEPSEEK_API_KEY / GLM_API_KEY")
    if provider not in PROVIDERS:
        raise RuntimeError("未知引擎 provider={0}".format(provider))
    p = PROVIDERS[provider]
    for k in PROXY_KEYS:
        os.environ.pop(k, None)   # 经验：Clash 代理会拦截国内 API
    _, key, resolved_model = ai_config.require(provider, vision=vision)
    import openai
    return openai.OpenAI(api_key=key, base_url=p["base_url"]), model or resolved_model
