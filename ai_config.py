# -*- coding: utf-8 -*-
"""ai_config —— LLM 引擎注入式配置中心（发布不携带 key，用户自行配置）

发布卫生（三层来源，永不入库/打包）：
  1. data/ai_config.json —— 用户在网页「⚙ 设置」里配置（引擎二选一 + key + 模型名），
     已加入 .gitignore，随部署生成
  2. 环境变量            —— DEEPSEEK_API_KEY / GLM_API_KEY（脚本/容器部署注入）
  3. key.env             —— 开发者本机便利文件（已 gitignore；run.sh/serve.sh
     以 `set -a; . key.env` 转成环境变量，等价于第 2 层）

优先级：ai_config.json > 环境变量。

引擎说明（DeepSeek / 智谱 GLM 两种选择）：
  - deepseek：deepseek-flash 原生多模态，文字/识图同一个模型
  - glm：glm-5.3-flash 原生多模态（GLM-5 系列首个，320B/18B 激活，
    输入 ¥0.8 / 输出 ¥2.8 每百万 tokens，约为旗舰 GLM-5.3 的 1/10），
    看图/解题同一个模型；文本/视觉字段默认同值，想省钱可把视觉字段
    改回免费老模型 glm-4.6v-flash，想提速可改 glm-5.3-flashx
"""
import os, json, time, threading, base64

BASE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(BASE, "data", "ai_config.json")   # 测试可重定向

# 引擎元数据（显示名 / key 环境变量 / 申请入口 / 默认模型）
PROVIDER_META = {
    "deepseek": {
        "label": "DeepSeek",
        "key_env": "DEEPSEEK_API_KEY",
        "apply_url": "https://platform.deepseek.com/api_keys",
        "default_text_model": "deepseek-flash",      # 原生多模态
        "default_vision_model": "deepseek-flash",
        "note": "文字/识图同一个模型（deepseek-flash 原生多模态）",
    },
    "glm": {
        "label": "智谱 GLM",
        "key_env": "GLM_API_KEY",
        "apply_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "default_text_model": "glm-5.3-flash",     # 原生多模态，看图/解题同一个模型
        "default_vision_model": "glm-5.3-flash",
        "note": "原生多模态：看图/解题同一个模型（超低价；可改 glm-4.6v-flash 免费或 glm-5.3-flashx 提速）",
    },
}

_LOCK = threading.Lock()
_CACHE = None   # (mtime, cfg_dict)


# ---------- 配置读写（原子写 + .bak，与 store 同风格） ----------

def config_path():
    return _CONFIG_PATH


def set_config_path(p):
    """测试钩子：重定向配置文件路径并清缓存"""
    global _CONFIG_PATH, _CACHE
    _CONFIG_PATH = p
    _CACHE = None


def _defaults():
    return {
        "version": 1,
        "provider": None,                                          # None = 自动（取第一个配了 key 的引擎）
        "keys": {p: "" for p in PROVIDER_META},                    # 明文 key，仅落盘不外传
        "models": {p: {"text": "", "vision": ""} for p in PROVIDER_META},  # 空 = 用引擎默认
    }


def load(force=False):
    """读取配置（mtime 缓存；文件不存在/损坏返回默认值）"""
    global _CACHE
    with _LOCK:
        try:
            mt = os.path.getmtime(_CONFIG_PATH)
        except OSError:
            return _defaults()
        if not force and _CACHE and _CACHE[0] == mt:
            return _CACHE[1]
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return _defaults()
        d = _defaults()
        d.update({k: cfg[k] for k in ("provider",) if k in cfg})
        for p in PROVIDER_META:
            if isinstance(cfg.get("keys", {}).get(p), str):
                d["keys"][p] = cfg["keys"][p].strip()
            m = cfg.get("models", {}).get(p)
            if isinstance(m, dict):
                d["models"][p] = {"text": str(m.get("text") or "").strip(),
                                  "vision": str(m.get("vision") or "").strip()}
        _CACHE = (mt, d)
        return d


def save_patch(patch):
    """增量保存（provider / keys / models）；key 传空串 = 清除。原子写 .bak"""
    cfg = load(force=True)
    if isinstance(patch.get("provider"), str):
        if patch["provider"] and patch["provider"] not in PROVIDER_META:
            raise ValueError("未知引擎：{0}".format(patch["provider"]))
        cfg["provider"] = patch["provider"] or None
    keys = patch.get("keys")
    if isinstance(keys, dict):
        for p, v in keys.items():
            if p in PROVIDER_META and isinstance(v, str):
                cfg["keys"][p] = v.strip()
    models = patch.get("models")
    if isinstance(models, dict):
        for p, m in models.items():
            if p in PROVIDER_META and isinstance(m, dict):
                cfg["models"][p] = {"text": str(m.get("text") or "").strip(),
                                    "vision": str(m.get("vision") or "").strip()}
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CONFIG_PATH)
    global _CACHE
    with _LOCK:
        _CACHE = None     # 下次 load 重读新 mtime
    return cfg


# ---------- 解析（供 common.make_client 使用） ----------

def resolve_key(provider):
    """key 解析：ai_config.json > 环境变量"""
    if provider not in PROVIDER_META:
        return ""
    return load()["keys"][provider] or os.environ.get(PROVIDER_META[provider]["key_env"], "")


def resolve_model(provider, vision=False):
    """模型解析：用户配置 > 引擎默认；vision=True 取视觉模型（转录/图形向量化）"""
    m = load()["models"].get(provider) or {}
    meta = PROVIDER_META[provider]
    return (m.get("vision") if vision else m.get("text")) or \
           (meta["default_vision_model"] if vision else meta["default_text_model"])


def active_provider():
    """当前引擎：显式配置且该引擎有 key → 用之；否则回落到第一个配了 key 的引擎"""
    cfg = load()
    p = cfg["provider"]
    if p and resolve_key(p):
        return p
    for cand in PROVIDER_META:
        if resolve_key(cand):
            return cand
    return None


def require(provider=None, vision=False):
    """解析出 (provider, key, model)；未配置时抛出带指引的 RuntimeError"""
    p = provider or active_provider()
    if not p:
        raise RuntimeError("未配置 AI 引擎：请点页面右上角「⚙ 设置」填入 API Key"
                           "（支持 DeepSeek / 智谱GLM），或设环境变量 DEEPSEEK_API_KEY / GLM_API_KEY")
    key = resolve_key(p)
    if not key:
        meta = PROVIDER_META[p]
        raise RuntimeError("{0} 未配置 API Key：请在「⚙ 设置」里填入（申请入口 {1}），"
                           "或 export {2}=你的key".format(meta["label"], meta["apply_url"], meta["key_env"]))
    return p, key, resolve_model(p, vision=vision)


# ---------- 视图（GET API 用；key 永不明文回传） ----------

def mask(key):
    if not key:
        return ""
    return (key[:4] + "****" + key[-4:]) if len(key) > 10 else "****"


def key_source(provider):
    if load()["keys"][provider]:
        return "config"
    return "env" if os.environ.get(PROVIDER_META[provider]["key_env"]) else "none"


def describe():
    cfg = load()
    cur = active_provider()
    return {
        "provider": cur,               # 实际生效引擎（含回落）
        "configured": cfg["provider"],  # 用户显式选择的引擎（可能因缺 key 未生效）
        "ready": cur is not None,       # 任一引擎可用
        "providers": {p: {
            "label": m["label"],
            "key_env": m["key_env"],
            "apply_url": m["apply_url"],
            "note": m["note"],
            "has_key": bool(resolve_key(p)),
            "key_masked": mask(resolve_key(p)),
            "key_source": key_source(p),
            "text_model": resolve_model(p, vision=False),
            "vision_model": resolve_model(p, vision=True),
            "default_text_model": m["default_text_model"],
            "default_vision_model": m["default_vision_model"],
        } for p, m in PROVIDER_META.items()},
    }


# ---------- 连通性测试（设置弹窗「测试连接」，不落盘） ----------

def _test_png_uri():
    """标准 1×1 PNG（PIL 生成），避免手拼 hex 被模型端判为非法图片"""
    import io
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (200, 60, 60)).save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def test_provider(provider, key=None, model=None, vision=False):
    """最小真实调用验证 key/模型；成功 {"ok":true,"model":...}，失败 {"ok":false,"error":...}"""
    if provider not in PROVIDER_META:
        return {"ok": False, "error": "未知引擎：{0}".format(provider)}
    try:
        import openai
        from common import PROXY_KEYS
        from generate_any import PROVIDERS
        for k in PROXY_KEYS:                     # 与 make_client 同规则：清代理（Clash 拦国内 API）
            os.environ.pop(k, None)
        k = key or resolve_key(provider)
        if not k:
            return {"ok": False, "error": "该引擎尚未填入 API Key"}
        m = model or resolve_model(provider, vision=vision)
        client = openai.OpenAI(api_key=k, base_url=PROVIDERS[provider]["base_url"])
        if vision:
            uri = _test_png_uri()
            if not uri:
                return {"ok": False, "error": "视觉测试需要 Pillow（PIL）"}
            content = [{"type": "text", "text": "图中是什么？一个词回答"},
                       {"type": "image_url", "image_url": {"url": uri}}]
        else:
            content = "ping，请回复：ok"
        kw = {"model": m, "messages": [{"role": "user", "content": content}], "max_tokens": 2048}
        if provider == "glm":
            kw["extra_body"] = {"thinking": {"type": "disabled"}}   # 关思考提速省 token
        try:
            client.chat.completions.create(**kw)
        except Exception:
            if "extra_body" not in kw:
                raise
            kw.pop("extra_body")            # glm-5.3-flash 常开思考：不收 thinking 参数则裸调重试
            client.chat.completions.create(**kw)
        return {"ok": True, "model": m, "provider": provider}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
