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


GGB_FIX_PROMPT = """你是 GeoGebra 排错专家。下面是一段 GeoGebra 构造命令脚本，部分行执行失败。
请修复：保留正确的行，只修错误行（常见错误：引用了未定义的对象名、Set 系列 SetPointSize/SetLineStyle 参数不合法、
依赖顺序颠倒、多余的中文字符或全角符号）。按依赖顺序输出修复后的完整命令序列；
只用这些命令：{whitelist}
只输出 JSON（不要其他文字）：{{"commands":[…]}}

题干（供参考）：
{question}

当前脚本：
{script}

执行失败的行：
{errors}"""


def fix_commands(question, script, errors, uri=None, client=None, model=None):
    """脚本+报错 → 修复后的完整命令列表（「🩺分析解决」按钮，人手动触发）。client/model 可注入。"""
    from common import make_client
    from reviewflow import ask_json_vision
    if not (script or "").strip():
        raise ValueError("脚本为空，无需修复")
    img_hint = "\n附图是原题图形的照片，修复后的布局尽量与图一致。" if uri else ""
    prompt = GGB_FIX_PROMPT.format(
        whitelist="、".join(GGB_COMMAND_WHITELIST),
        question=(question or "").strip()[:500],
        script=script.strip(),
        errors="\n".join(str(e)[:200] for e in (errors or [])) or "（未提供，请检查脚本中的明显错误）") + img_hint
    if client is None:
        client, model = make_client(vision=bool(uri))
    data = ask_json_vision(client, model, prompt, uri, max_tokens=8000)
    cmds = [str(c).strip() for c in (data.get("commands") or []) if str(c).strip()]
    cmds = [re.sub(r"^```[a-z]*|```$", "", c).rstrip(";").strip() for c in cmds]
    cmds = [c for c in cmds if c]
    if not cmds:
        raise RuntimeError("模型未返回有效命令")
    return cmds


def gen_commands(question, prev_errors=None, client=None, model=None, uri=None):
    """题干(+可选原题图形照片) → 命令列表；prev_errors = 前端失败行（文本回路重试）。

    uri = 原题图形 data URI（v1.1.0e：有对照图就走视觉调用，布局/摆向与课本原图对齐）；
    无 uri 纯文本。抛异常 = 生成失败（调用方返回 500，前端提示）。client/model 可注入（测试 stub 用）。
    """
    from common import make_client
    from reviewflow import ask_json_vision
    if not (question or "").strip():
        raise ValueError("题干为空，无法生成图形命令")
    fb = ""
    if prev_errors:
        fb = ("\n上一版命令中有以下行执行失败，请修正语法或引用顺序：\n- "
              + "\n- ".join(str(e)[:120] for e in prev_errors[:4]))
    img_hint = ("\n附图是原题图形的照片：布局、摆向、点的相对位置尽量与图一致；\n"
                "几何关系与数值仍以题干文字为准，照片模糊处不要臆造。") if uri else ""
    prompt = GGB_PROMPT.format(
        whitelist="、".join(GGB_COMMAND_WHITELIST), question=question.strip()) + img_hint + fb
    if client is None:
        client, model = make_client(vision=bool(uri))
    data = ask_json_vision(client, model, prompt, uri, max_tokens=8000)
    cmds = [str(c).strip() for c in (data.get("commands") or []) if str(c).strip()]
    if not cmds:
        raise RuntimeError("模型未返回有效命令")
    # 轻防御：剥离 markdown 代码栅栏残留 / 行尾分号
    cmds = [re.sub(r"^```[a-z]*|```$", "", c).rstrip(";").strip() for c in cmds]
    cmds = [c for c in cmds if c]
    return cmds
