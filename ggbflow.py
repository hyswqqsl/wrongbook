# -*- coding: utf-8 -*-
"""ggbflow —— GeoGebra 图形工作台（v1.1a，DESIGN.md 5.5）：题干 → 构造命令脚本

分工：LLM 只"翻译"几何关系为 GeoGebra 命令（草稿，纯文本 1 次调用）；
GeoGebra 引擎在前端 applet 本地执行成图（构造即正确：Midpoint/PerpendicularLine 是真约束）；
人审拖拽修正画法后导出静态 PNG。服务端不跑 GeoGebra —— 执行/导出全部发生在前端。
"""
import re

# 命令白名单：八年级平面几何够用（写进 prompt 引导输出；执行层的真检查是前端 evalCommand 回执）
GGB_COMMAND_WHITELIST = (
    "点坐标 A=(x,y)", "Segment", "Line", "Ray", "Polygon", "Circle", "Arc", "Midpoint",
    "Intersect", "PerpendicularLine", "PerpendicularBisector", "AngleBisector",
    "ParallelLine", "Tangent", "Vector", "Translate", "Rotate", "Dilate", "Distance",
    "Angle", "SetLineStyle", "SetColor", "SetCaption", "SetLabelMode", "SetPointSize",
    "SetLineThickness", "ShowLabel", "ZoomIn", "SetViewDirection",
)

GGB_PROMPT = """你是几何作图专家。根据下面的初中数学题干，输出 GeoGebra 构造命令序列（英文命令名）。
规则：
1. 点名与题干大写字母严格一致；自由点用 A=(x,y) 摆放，布局须满足题干几何关系
   （等腰用对称摆放、垂直用水平/竖直线、比例协调、图形居中不过界）
2. 几何关系一律用构造命令表达，禁止手算依赖点坐标：
   中点 Midpoint(B,C)、垂线 PerpendicularLine(D,A,B)、垂直平分线 PerpendicularBisector(A,B)、
   角平分线 AngleBisector(A,B,C)、平行线 ParallelLine(P,A,B)、圆 Circle(A,B)、
   交点 Intersect(l,Segment(A,B))、多边形 Polygon(A,B,C)
3. 需要虚线的辅助线：先给线段/直线命名（如 sDE=Segment(D,E)），再 SetLineStyle(sDE,2)；
   SetLineStyle 第一个参数必须是已定义的对象名，禁止直接传 Segment(…) 等命令表达式；
   需要隐藏的辅助点用 SetVisible(P,false)（不要用 SetPointSize(P,0)）
4. 命令按依赖顺序排列（先定义后引用）；只用这些命令：{whitelist}
5. 只输出 JSON（不要其他文字）：{{"commands":["A=(0,0)","B=(4,0)",…]}}

题干：
{question}"""


def gen_commands(question, prev_errors=None, client=None, model=None):
    """题干 → 命令列表；prev_errors = 前端 evalCommand 失败行（文本回路重试，不发图片）。

    抛异常 = 生成失败（调用方返回 500，前端提示）。
    client/model 可注入（测试 stub 用）。
    """
    from common import make_client
    from reviewflow import ask_json_vision
    if not (question or "").strip():
        raise ValueError("题干为空，无法生成图形命令")
    fb = ""
    if prev_errors:
        fb = ("\n上一版命令中有以下行执行失败，请修正语法或引用顺序：\n- "
              + "\n- ".join(str(e)[:120] for e in prev_errors[:4]))
    prompt = GGB_PROMPT.format(
        whitelist="、".join(GGB_COMMAND_WHITELIST), question=question.strip()) + fb
    if client is None:
        client, model = make_client(vision=False)
    data = ask_json_vision(client, model, prompt, None, max_tokens=8000)
    cmds = [str(c).strip() for c in (data.get("commands") or []) if str(c).strip()]
    if not cmds:
        raise RuntimeError("模型未返回有效命令")
    # 轻防御：剥离 markdown 代码栅栏残留 / 行尾分号
    cmds = [re.sub(r"^```[a-z]*|```$", "", c).rstrip(";").strip() for c in cmds]
    cmds = [c for c in cmds if c]
    return cmds
