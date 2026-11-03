# -*- coding: utf-8 -*-
"""figureflow —— 图形子链：裁剪图/题干 → figure_spec（LLM）→ geocheck 校验循环 → SVG"""
import os, sys
from common import ROOT

sys.path.insert(0, ROOT)
import geocheck  # noqa: E402
import geoconstruct  # noqa: E402
from make_figures import coerce_spec, wants_figure  # noqa: E402  父项目结构校验+准入


SPEC_PROMPT = """根据题干和几何图形，输出绘图指令 JSON。
要求：points 为各点坐标（0~10 坐标系，布局必须满足题干全部几何约束：中点取平均、
垂直用水平竖直线或精确斜率、等长用对称；角度按题干数值精确摆放），
segments 为连线列表（辅助线 dashed:true）。
题干提到的每个大写字母点都必须出现在 points 中；多余点禁止。
只输出 JSON：{"points":[{"name":"A","x":1,"y":2},…],"segments":[{"a":"A","b":"B","dashed":false},…]}"""


def gen_spec(client, model, question_text, crop_path=None, prev_errs=None, uri=None):
    """题干(+裁剪图) → figure_spec 声明

    健壮性（父项目 make_figures.ask_json 的经验）：
      - DeepSeek 默认开思考，思考链会烧光 max_tokens → content='' 且 finish_reason='length'
      - 因此策略顺序：json_object → (空 content) 关思考重试 → plain → plain
      - 端点若拒绝 extra_body（400）→ 摘除 extra_body 继续
      - 全失败时异常带 finish_reason，便于人审区分"模型抽风"与"几何画不出"
    """
    import re, json
    fb = ""
    if prev_errs:
        fb = "\n上一次生成的图形未通过校验，错误：\n- " + "\n- ".join(prev_errs[:4]) + "\n请修正坐标。"
    content = [{"type": "text", "text": SPEC_PROMPT + fb + "\n\n题干：" + question_text}]
    if uri or (crop_path and os.path.exists(crop_path)):
        u = uri
        if not u:
            with open(crop_path, "rb") as f:
                u = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        content.append({"type": "image_url", "image_url": {"url": u}})
    last = ""
    last_finish = ""
    use_extra = False   # 关思考（DeepSeek 思考会烧光 max_tokens）
    for mode in ("json", "plain", "plain"):
        try:
            kwargs = dict(model=model, timeout=240, temperature=0.3, max_tokens=8000,
                          messages=[{"role": "user", "content": content}])
            if use_extra:
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            if mode == "json":
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            else:
                resp = client.chat.completions.create(**kwargs)
            txt = (resp.choices[0].message.content or "").strip()
            last = txt
            last_finish = getattr(resp.choices[0], "finish_reason", "") or ""
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
            # 空 content / 非 JSON：下一轮关思考重来（思考链烧光的典型症状）
            if not use_extra:
                use_extra = True
        except Exception as e:
            msg = str(e)
            # 端点拒绝 extra_body → 摘除后继续；其他异常（如网络）也换模式重试
            if "extra_body" in msg or "thinking" in msg:
                use_extra = False
            continue
    raise RuntimeError("图形声明生成失败（最后输出: {0!r}，finish_reason: {1}）".format(
        last[:60], last_finish or "未知"))


def vectorize_figure(client=None, model=None, question=None, crop_path=None, rounds=2, uri=None):
    """图形向量化：spec 生成 → 结构+geocheck 校验循环。
    返回 (spec 或 None, msgs)；None = 向量化失败（调用方保留照片裁剪图）

    client/model 省略时按需自建（人审期"重新生成"走这条路）。
    """
    if question is None:
        raise ValueError("question 不能为空")
    if client is None:
        from common import make_client
        client, model = make_client("deepseek")
    if not wants_figure({"question": question}):
        return None, ["非配图题，跳过向量化"]
    errs = []
    for i in range(rounds + 1):
        try:
            decl = gen_spec(client, model, question, crop_path, errs or None, uri)
            spec = coerce_spec(decl)
        except Exception as e:
            errs = ["生成失败: " + str(e)[:80]]
            continue
        if not (spec and spec.get("points")):
            errs = ["模型未返回有效图形声明"]
            continue
        from make_figures import full_validate
        errs, warns = full_validate({"question": question, "type": "fill"}, spec)
        if not errs:
            return spec, ["图形向量化通过（第{0}轮）".format(i + 1)] + warns
    return None, ["图形向量化失败：{0}".format("; ".join(errs[:2]))]


def render(spec):
    """spec → SVG 文本（geoconstruct 纯矢量渲染）"""
    pts = {p["name"]: (float(p["x"]), float(p["y"])) for p in spec["points"]}
    segs = [(s["a"], s["b"], bool(s.get("dashed"))) for s in spec.get("segments", [])]
    return geoconstruct.render_svg(pts, segs)
