# -*- coding: utf-8 -*-
"""pipeline —— 入库质量链 / 相似题生成 / 针对性出卷"""
import os, re, json, time, subprocess, shutil

from common import make_client, CHAPTERS, TYPE_CN
import store
import figureflow
import reviewflow
from reviewflow import ask_json_vision  # 复用降级问答（文本模式同构）

ANALYSIS_PROMPT = """你是初中数学教研专家。分析下面这道错题并输出 JSON。

错题：{question}
标准答案：{answer}
学生错因：{reason}

输出字段：
- chapter: 从 {chapters} 中选一个
- knowledge_points: 2~4 个知识点（具体到定理/模型名）
- pitfall: 易错点描述。错因为"马虎"时聚焦"容易算错/看错的环节"；错因为"不会"时聚焦"思路卡点与突破口"；其他则写实际陷阱
- difficulty: 1|2|3（1=简单基础 2=中等综合 3=困难压轴）
- key_idea: 一句话解题关键（供复习提示）
只输出 JSON。"""

VERIFY_PROMPT = """你是严格的数学审题专家。请独立解答下面的题目（不要轻信题目给出的任何答案）。

题目：{question}
{choices_block}
任务：独立完整解答（写关键步骤），并检查题目是否条件自洽、答案唯一。
只输出 JSON：{{"answer":"最终答案（选择题填字母，填空填文本）","ok":true,"issues":[]}}
ok=false 表示题目矛盾/欠定/无解，此时 answer 填"无法确定"。"""

SIMILAR_PROMPT = """你是资深命题教师。基于下面的错题，原创 {n} 道相似题供学生再练。

原题：{question}
标准答案：{answer}
知识点：{kps}
易错点：{pitfall}
学生错因：{reason_note}

命题要求：
1. 紧扣同一知识点与易错点：可换数据、换情境、改设问、正逆互换；不得照搬原题
2. 难度在原题基础上允许 ±1 级，形成梯度
3. 题型与原题相同（{type}）；答案唯一、条件自洽
4. 几何题需给绘图指令 figure_spec：points 坐标 0~10 内且满足题干全部约束，segments 连线（辅助线 dashed:true）；纯文字题 figure_spec 填 null
5. explanation 写清推理链与易错点提醒
只输出 JSON：{{"items":[{{"question":"…","choices":[…]|null,"answer":"…","explanation":"…","difficulty":1,
"figure_spec":{{"points":[…],"segments":[…]}}|null}},…]}}"""


def _ask_text_json(client, model, prompt, image_uri=None, max_tokens=8000):
    import base64
    content = [{"type": "text", "text": prompt}]
    if image_uri:
        content.append({"type": "image_url", "image_url": {"url": image_uri}})
    last = ""
    for mode in ("json", "plain", "plain"):
        try:
            kwargs = dict(model=model, timeout=300, temperature=0.3, max_tokens=max_tokens,
                          messages=[{"role": "user", "content": content}])
            if mode == "json":
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            else:
                resp = client.chat.completions.create(**kwargs)
            txt = (resp.choices[0].message.content or "").strip()
            last = txt
            if not txt:
                continue
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                return json.loads(m.group(0))
        except Exception:
            continue
    raise RuntimeError("LLM 调用失败（最后输出: {0!r}）".format(last[:60]))


# ---------- F1b 入库质量链 ----------

def build_question_payload(text, choices, spec, crops):
    """入库的 question 字段：figure_spec 为权威图形，figure_crop 为兜底原图。

    - spec 有效 → figure_spec=spec, figure_crop 仍留档（对照/兜底）
    - spec 缺失但有裁剪图 → figure_spec=None, figure_crop=第一张
    - 都没有 → 两个都 None
    """
    crop = (crops or [None])[0]
    return {"text": text, "choices": choices,
            "figure_spec": spec if spec else None,
            "figure_crop": crop}


def resolve_figure_spec(q, text, vectorize=None, client=None, model=None):
    """入库时确定图形 spec —— 尊重人审期的用户选择。

    - 排版机已生成且过 geocheck 的 spec → 直接采纳（不重复调模型）
    - 用户显式选了原图（figure_mode="crop"）→ 不再向量化（否则覆盖用户选择）
    - 表格/图象类题 → 跳过向量化（SVG 表达不了）
    - 其余（SVG 模式但 spec 缺失 = 生成失败回落）→ 补向量化一次
    """
    if vectorize is None:
        def vectorize(*a, **kw):
            if client is not None:
                return figureflow.vectorize_figure(client, model, text, **kw)
            return figureflow.vectorize_figure(question=text, **kw)
    spec = q.get("figure_spec")
    if spec:
        return spec
    mode = q.get("figure_mode")
    if mode == "crop":
        return None   # 用户已选定原图为权威图形
    if reviewflow.classify_figure(text, q.get("has_figure")) == "crop":
        return None   # 表格/图象类题
    spec, _msgs = vectorize()
    return spec


def ingest_selected(sheet, provider="deepseek"):
    """人审提交：sheet 中 selected=True 的题逐个走质量链入库。
    返回 {results: [{no, id?|skipped?, report[]}], sheet_id}

    v0.6.2 起第⓪步先查题库去重：与已入库题重复的直接跳过（不调 LLM、不入库），
    结果在 results 中标 skipped=True + duplicate_of，由前端完成提示汇总展示。

    v0.6.3 起按题记录处置（ingested_id / skipped_dup 写回草稿单题上）：
    一张照片可分多次提交，每次只处理当次勾选的题；所有题都有处置结果后
    草稿单才标记 processed，否则保持 pending（剩余题仍可再勾选再提交）。
    """
    client, model = make_client(provider)
    results = []
    skipped_ids = []
    for q in sheet["questions"]:
        if not q.get("selected"):
            continue
        report = []
        text = q["question"]
        if isinstance(q.get("edited"), dict) and q["edited"].get("question"):
            text = q["edited"]["question"]
        choices = q.get("edited", {}).get("choices") if isinstance(q.get("edited"), dict) else None
        choices = choices or q.get("choices")

        # ⓪ 题库查重：重复题不入库（同批次先入库的题也能被后面题目查到）
        dup = store.find_duplicate(text)
        if dup:
            results.append({"no": q.get("no"), "skipped": True, "duplicate_of": dup["id"],
                            "report": ["[去重] 与已入库题 {0} 重复，跳过".format(dup["id"])]})
            skipped_ids.append(dup["id"])
            store.patch_sheet_question(sheet["id"], q.get("no"),
                                       {"selected": False, "skipped_dup": dup["id"]})
            continue

        # ① 图形：尊重人审期的用户选择（详见 resolve_figure_spec）
        crops = q.get("figure_crops") or []
        spec = resolve_figure_spec(q, text, client=client, model=model)
        if q.get("figure_spec"):
            report.append("[图形] 排版机已生成矢量图形（geocheck 合法）")
        elif q.get("figure_mode") == "crop":
            report.append("[图形] 人审已选定原题照片裁剪图为权威图形")
        elif reviewflow.classify_figure(text, q.get("has_figure")) == "crop":
            report.append("[图形] 表格/图象类题，改用原题照片裁剪图")
        elif spec:
            report.append("[图形] 入库时补生成矢量图形成功")
        elif crops:
            report.append("[图形] 向量化失败，已回落原题照片裁剪图")
        else:
            report.append("[图形] 未获得图形，保留题干待手动补图")

        # ② 答案验伪（独立解题，不看照片答案）
        ch_block = ""
        if choices:
            ch_block = "选项：\n" + "\n".join("{0}. {1}".format(c.get("key"), c.get("text")) for c in choices)
        v = _ask_text_json(client, model, VERIFY_PROMPT.format(question=text, choices_block=ch_block))
        printed = q.get("printed_answer")
        v_ans = str(v.get("answer") or "").strip()
        if not v.get("ok"):
            status, verified = "bad_question", None
            report.append("[验伪] 判题不合格: " + "；".join(str(x) for x in (v.get("issues") or []))[:120])
        elif printed and v_ans and not _answer_match(printed, v_ans):
            status, verified = "conflict", v_ans
            report.append("[验伪] 照片答案{0!r}与独立解答{1!r}不一致，以独立解答为准".format(printed, v_ans))
        else:
            status, verified = "pass", v_ans
            report.append("[验伪] 独立解题对账一致({0})".format(v_ans))

        # ③ 分析（错因驱动侧重）
        reason = q.get("reason") or {}
        reason_note = "{0}（{1}）".format(
            {"careless": "马虎", "unknown": "不会", "other": "其他"}.get(reason.get("kind"), "未标注"),
            reason.get("note") or "无备注")
        an = _ask_text_json(client, model, ANALYSIS_PROMPT.format(
            question=text, answer=verified or v_ans, reason=reason_note,
            chapters="、".join(CHAPTERS)))
        report.append("[分析] {0} · 难度{1} · {2}".format(
            an.get("chapter"), an.get("difficulty"), "/".join(an.get("knowledge_points") or [])))

        # ④ 年级/知识点（人审树形选择的值为权威；缺省回落 LLM 分析结果）
        kp_sel = q.get("kp_sel") or {}
        grade = q.get("grade") or "八年级上册"
        chapter = (kp_sel.get("chapter") or an.get("chapter") or "其他")
        report.append("[归类] {0} · {1}{2}{3}".format(
            grade, chapter,
            " " + kp_sel["section"] if kp_sel.get("section") else "",
            " · " + kp_sel["kp"] if kp_sel.get("kp") else ""))

        rec = store.add({
            "source": sheet.get("source") or "",
            "type": q.get("type") or "fill",
            "question": build_question_payload(text, choices, spec, crops),
            "printed_answer": printed,
            "verified_answer": verified,
            "verify_status": status,
            "mistake_reason": reason,
            "analysis": {"grade": grade,
                         "chapter": chapter,
                         "kp_path": ([kp_sel.get("chapter", ""), kp_sel.get("section", ""),
                                      kp_sel.get("kp", "")] if kp_sel else []),
                         "knowledge_points": an.get("knowledge_points") or [],
                         "pitfall": an.get("pitfall") or "",
                         "difficulty": max(1, min(3, int(an.get("difficulty") or 2))),
                         "key_idea": an.get("key_idea") or ""},
            "photo": sheet.get("photo"),
            "flags": {"needs_review": status == "bad_question"},
        })
        report.append("[入库] " + rec["id"])
        results.append({"no": q.get("no"), "id": rec["id"], "report": report})
        store.patch_sheet_question(sheet["id"], q.get("no"),
                                   {"selected": False, "ingested_id": rec["id"]})

    ingested_ids = [r["id"] for r in results if r.get("id")]
    sheet_now = store.get_sheet(sheet["id"]) or sheet
    remaining = [q for q in sheet_now.get("questions", [])
                 if not (q.get("ingested_id") or q.get("skipped_dup"))]
    # 还有未处置的题 → 保持 pending（用户可继续勾选提交）；全部处置完 → processed
    store.set_sheet_status(sheet["id"], "processed" if not remaining else "pending", {
        "ingested": ingested_ids, "skipped_duplicates": skipped_ids})
    return {"sheet_id": sheet["id"], "results": results}


def _answer_match(a, b):
    """宽松对账：字母集合 或 数值序列"""
    def norm_letters(s):
        return "".join(sorted(set(re.findall(r"[A-D]", str(s).upper()))))
    la, lb = norm_letters(a), norm_letters(b)
    if la and lb:
        return la == lb
    na, nb = [], []
    for s in (str(a), str(b)):
        s2 = re.sub(r"√(\d+(?:\.\d+)?)", lambda m: str(round(pow(float(m.group(1)), 0.5), 3)), s)
        na.append([float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s2)])
    return na[0] == na[1]


# ---------- F2 相似题 ----------

def gen_similar(qid, count=2, provider="deepseek"):
    record = store.get(qid)
    if not record:
        raise RuntimeError("题目不存在: " + qid)
    client, model = make_client(provider)
    an = record.get("analysis", {})
    reason = (record.get("mistake_reason") or {})
    prompt = SIMILAR_PROMPT.format(
        n=count, question=record["question"]["text"], answer=record.get("verified_answer"),
        kps="、".join(an.get("knowledge_points") or []), pitfall=an.get("pitfall") or "",
        reason_note=reason.get("note") or "", type=TYPE_CN.get(record.get("type"), record.get("type")))
    data = _ask_text_json(client, model, prompt, max_tokens=12000)

    items, kept = data.get("items") or [], []
    for it in items:
        if not it.get("question") or not it.get("answer"):
            continue
        spec = None
        if isinstance(it.get("figure_spec"), dict) and it["figure_spec"].get("points"):
            from make_figures import coerce_spec, full_validate
            spec = coerce_spec(it["figure_spec"])
            errs, _w = full_validate({"question": it["question"], "type": "fill"}, spec)
            if errs:
                spec = None  # 图形不合法 → 弃图保题（纯文字可练）
        kept.append({
            "id": "s" + str(len(record.get("similar", [])) + len(kept) + 1),
            "text": it["question"], "choices": it.get("choices") if isinstance(it.get("choices"), list) else None,
            "answer": str(it["answer"]), "explanation": it.get("explanation") or "",
            "difficulty": max(1, min(3, int(it.get("difficulty") or 2))),
            "figure_spec": spec,
            "verified": False,  # v1 生成时标注；批量验伪可后续触发
        })
    record.setdefault("similar", []).extend(kept)
    store.update(qid, similar=record["similar"])
    return kept


# ---------- F4 针对性出卷 ----------

PAPER_PROMPT = """你是资深命题教师。根据学生的错题画像命制一份针对卷，共 {count} 题。

学生错题知识点与易错点（命题重点，必须覆盖高频项）：
{focus}

难度分布要求：{mix}
章节范围：{chapters}
模式：{mode_desc}

要求：
1. 每题输出 knowledge_point（对应上面某个知识点）
2. 选择题恰一个正确项；填空答案唯一；条件必须自洽（不得出现矛盾/欠定）
3. {figure_req}
4. explanation 写推理链，并针对学生易错点给出提醒
只输出 JSON：{{"questions":[{{"type":"single|multiple|fill|short","question":"…",
"choices":[{{"key":"A","text":"…"}},…]|null,"answer":"…","explanation":"…",
"knowledge_point":"…","difficulty":1,"figure_spec":{{…}}|null}},…]}}"""


def gen_paper(chapters=None, kps=None, difficulty_mix=None, count=8,
              mode="variant", provider="deepseek", out_dir=None):
    """错题画像 → 针对卷 json + export_paper PDF。返回 {pdf, md, n}"""
    from stats import dashboard
    db = store.load()
    pool = [q for q in db["questions"] if not q.get("mastered_at")]
    if chapters:
        pool = [q for q in pool if (q.get("analysis", {}).get("chapter") in chapters)]
    if kps:
        pool = [q for q in pool if set(kps) & set(q.get("analysis", {}).get("knowledge_points") or [])]
    if not pool:
        raise RuntimeError("筛选条件下没有在库错题")

    # 错题画像：知识点+易错点（"不会"优先加权）
    from collections import Counter
    kp_w = Counter()
    focus_lines = []
    for q in pool:
        an = q.get("analysis", {})
        w = 2 if (q.get("mistake_reason") or {}).get("kind") == "unknown" else 1
        for kp in (an.get("knowledge_points") or [])[:3]:
            kp_w[kp] += w
    for kp, w in kp_w.most_common(10):
        pits = {q["analysis"].get("pitfall") for q in pool
                if kp in (q["analysis"].get("knowledge_points") or []) and q["analysis"].get("pitfall")}
        focus_lines.append("- {0}（×{1}）：{2}".format(kp, w, "；".join(list(pits)[:2])[:80]))
    focus = "\n".join(focus_lines) or "-（无）"

    mix = difficulty_mix or {"1": 30, "2": 50, "3": 20}
    mix_desc = "、".join("{0}%难度{1}".format(v, k) for k, v in mix.items())
    mode_desc = ("变式卷：基于学生错题改数据/换情境/正逆互换，不照搬原题" if mode == "variant"
                 else "原创卷：围绕易错点全新命题，情境数据全新")
    figure_req = ("几何题必须给 figure_spec（坐标0~10满足题干全部约束，辅助线 dashed:true）；"
                  "纯文字题 figure_spec 填 null。注意：无\"如图\"字样的题不要 figure_spec")

    client, model = make_client(provider)
    prompt = PAPER_PROMPT.format(count=count, focus=focus, mix=mix_desc,
                                 chapters="、".join(chapters) if chapters else "不限（以错题章节为准）",
                                 mode_desc=mode_desc, figure_req=figure_req)
    data = _ask_text_json(client, model, prompt, max_tokens=30000)
    questions = data.get("questions") or []

    # 图形校验循环 + 结构归一
_LATEX_REPS = [  # 顺序敏感：长命令在前，避免子串误替换
    ("\\triangle", "△"), ("\\angle", "∠"), ("^\\circ", "°"), ("^{\\circ}", "°"),
    ("\\perp", "⊥"), ("\\cong", "≌"), ("\\sim", "∽"), ("\\parallel", "∥"),
    ("\\times", "×"), ("\\div", "÷"), ("\\geq", "≥"), ("\\ge", "≥"),
    ("\\leq", "≤"), ("\\le", "≤"), ("\\neq", "≠"), ("\\ne", "≠"),
    ("\\pm", "±"), ("\\cdot", "·"), ("\\degree", "°"), ("\\pi", "π"),
]
_SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
        "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻", "+": "⁺"}


def _latex2uni(s):
    """题库 LaTeX 公式 → Unicode 数学符号（export_paper 的 md 链不渲染 LaTeX）。

    LaTeX 命令映射为 Unicode 数学符号（△∠°⊥≌√≥ 等）；
    分数转 (a/b)、根号转 √(x)、残余命令删名留内容、去 $ 定界符。"""
    if not s:
        return s
    t = str(s)
    for a, b in _LATEX_REPS:
        t = t.replace(a, b)
    t = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1/\2)", t)
    t = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", t)
    t = re.sub(r"\\sqrt(\d+)", r"√\1", t)
    t = re.sub(r"\\(?:text|mathrm|mbox)\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\^(\d)", lambda m: _SUP.get(m.group(1), m.group(1)), t)
    t = re.sub(r"\^\{([-+0-9]+)\}", lambda m: "".join(_SUP.get(c, c) for c in m.group(1)), t)
    t = re.sub(r"\\[a-zA-Z]+", "", t)          # 残余命令：删命令名
    t = t.replace("{", "").replace("}", "").replace("$", "")
    return t


def export_selected_pdf(ids, with_answer=True):
    """工作台勾选题 → 练习卷 PDF（复用内联引擎 export_paper，无 AI 调用）。

    题目均为已入库的验证过的题：直接映射 QuizBank 字段（图形用已过 geocheck
    的 figure_spec 矢量绘制）；答案默认附卷末（可裁剪）。返回 {pdf, n}。
    """
    from make_figures import normalize_bank
    import sys
    from common import BASE
    recs = [store.get(i) for i in ids]
    recs = [r for r in recs if r]
    if not recs:
        raise RuntimeError("未找到要导出的题目")
    diff_map = {1: "easy", 2: "medium", 3: "hard"}
    bank_qs = []
    for r in recs:
        an = r.get("analysis", {}) or {}
        qq = r.get("question", {}) or {}
        kp_path = an.get("kp_path") or []
        kp = next((k for k in [kp_path[2] if len(kp_path) > 2 else "",
                               kp_path[1] if len(kp_path) > 1 else "",
                               *(an.get("knowledge_points") or [])] if k), "综合")
        bank_qs.append({
            "type": r.get("type") or "fill",
            "question": _latex2uni(qq.get("text", "")),
            "choices": [{**c, "text": _latex2uni(c.get("text", ""))}
                        for c in (qq.get("choices") or [])],
            "answer": _latex2uni(r.get("verified_answer") or ""),
            "explanation": _latex2uni(an.get("key_idea") or an.get("pitfall") or ""),
            "knowledge_point": _latex2uni(kp),
            "difficulty": diff_map.get(an.get("difficulty", 2), "medium"),
            "source_page": 0, "figure_box": None,
            "figure_spec": qq.get("figure_spec"),
            # 抓图裁剪（无矢量图时的权威图形）→ export_paper 按 figure_image 嵌入
            "figure_image": (qq.get("figure_crop") if not qq.get("figure_spec") else None),
        })
    bank = normalize_bank({"chapter": "错题练习卷", "questions": bank_qs})
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(out_dir, "错题练习卷_{0}.json".format(stamp))
    json.dump(bank, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    prefix = os.path.join(out_dir, "错题练习卷_{0}".format(stamp))
    cmd = [sys.executable,
           os.path.join(BASE, "engine", "export_paper.py"), jpath,
           "--title", "数学错题练习卷", "--out-prefix", prefix]
    if not with_answer:
        cmd.append("--no-answer")
    subprocess.run(cmd, check=True, capture_output=True)
    pdf = prefix + ".pdf"
    if not os.path.exists(pdf):
        raise RuntimeError("PDF 生成失败（Chrome 缺失时 export_paper 只出 md/html）")
    return {"pdf": pdf, "n": len(bank["questions"])}


    from make_figures import coerce_spec, full_validate, wants_figure, normalize_bank
    bank_qs = []
    for it in questions:
        it.setdefault("type", "fill")
        it["source_page"] = 0
        it["figure_box"] = None
        spec = it.get("figure_spec")
        if isinstance(spec, dict) and spec.get("points"):
            spec = coerce_spec(spec)
            errs, _w = full_validate({"question": it["question"], "type": it["type"]}, spec)
            if errs:
                spec2, _m = figureflow.vectorize_figure(client, model, it["question"])
                spec = spec2 if spec2 else None
        elif wants_figure({"question": it.get("question") or ""}):
            spec, _m = figureflow.vectorize_figure(client, model, it.get("question") or "")
        else:
            spec = None
        it["figure_spec"] = spec
        bank_qs.append(it)
    bank = normalize_bank({"chapter": "错题针对卷（{0}）".format(
        "、".join(chapters) if chapters else "全部章节"), "questions": bank_qs})

    # 答案验伪（复用父项目 verify_answers）
    from verify_answers import verify_llm, layer1_rules
    bad = []
    for i, q in enumerate(bank["questions"]):
        if layer1_rules(q):
            bad.append(i)
            continue
        try:
            v, msg = verify_llm(client, model, q)
            if v == "FAIL":
                bad.append(i)
        except Exception:
            bad.append(i)
    for i in reversed(bad):
        bank["questions"].pop(i)

    # 落盘 + export_paper（父项目矢量导出）
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(out_dir, "错题针对卷_{0}.json".format(stamp))
    json.dump(bank, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    import sys
    from common import BASE
    subprocess.run([sys.executable,
                    os.path.join(BASE, "engine", "export_paper.py"),
                    jpath, "--title", "错题针对卷", "--out-prefix",
                    os.path.join(out_dir, "错题针对卷_{0}".format(stamp))],
                   check=True, capture_output=True)
    pdf = os.path.join(out_dir, "错题针对卷_{0}.pdf".format(stamp))
    return {"pdf": pdf if os.path.exists(pdf) else None, "json": jpath,
            "n": len(bank["questions"]), "dropped": len(bad)}
