# -*- coding: utf-8 -*-
"""pipeline —— 入库质量链 / 相似题生成 / 针对性出卷"""
import os, re, json, sys, time, subprocess, shutil

import paths

from common import make_client, CHAPTERS
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

# 分析 prompt 的章节受控词表：前置年级 → 该年级教材章（kbtree 按年级清单）；
# 未知/其他/读不到 → 内置 CHAPTERS（八上五章，旧行为兜底）。进程内按年级缓存。
_chapter_cache = {}


def _grade_chapters(grade):
    g = (grade or "").strip()
    if g and g != "其他":
        if g not in _chapter_cache:
            chs = []
            try:
                import kbtree
                kt = kbtree.knowledge_points_for_grade(g) or {}
                chs = [c["title"] for c in kt.get("chapters", []) if c.get("title")]
            except Exception:
                chs = []
            _chapter_cache[g] = (chs + ["其他"]) if chs else list(CHAPTERS)
        return _chapter_cache[g]
    return CHAPTERS


def _ask_text_json(client, model, prompt, image_uri=None, max_tokens=16000):
    """文本问答：json_object → 纯文本 ×2 → 关思考（D12 老方子，v1.2 对齐 reviewflow）。

    v1.2 修复：原 8000 max_tokens + 三级降级——DeepSeek 思考链烧光预算致 content='',
    每次都等满响应（实测验伪+分析拖到 2~3 分钟/题，提交遮罩久转）。
    现预算翻倍 + 第 4 级 nothink（关思考重试，实测 645s→16s），timeout 300→120s 快速失败。"""
    import base64
    content = [{"type": "text", "text": prompt}]
    if image_uri:
        content.append({"type": "image_url", "image_url": {"url": image_uri}})
    last = ""
    for mode in ("json", "plain", "plain", "nothink"):
        try:
            kwargs = dict(model=model, timeout=120, temperature=0.3, max_tokens=max_tokens,
                          messages=[{"role": "user", "content": content}])
            if mode == "json":
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            else:
                if mode == "nothink":
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                resp = client.chat.completions.create(**kwargs)
            txt = (resp.choices[0].message.content or "").strip()
            last = txt
            if not txt:
                continue
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
        except Exception:
            continue
    raise RuntimeError("LLM 调用失败（最后输出: {0!r}）".format(last[:60]))


# ---------- F1b 入库质量链 ----------

def build_question_payload(text, choices, spec, crops):
    """入库的 question 字段：figure_spec 为权威图形，figure_crop 为兜底原图。

    - spec 有效 → figure_spec=spec, figure_crop 仍留档（对照/兜底）
    - spec 缺失但有裁剪图 → figure_spec=None, figure_crop=第一张
    - 都没有 → 两个都 None
    - crops = 裁剪图路径列表（权威原图；与 spec 独立并存）

    Returns:
        dict: question 子对象
    """
    crop = (crops or [None])[0]
    return {"text": text, "choices": choices,
            "figure_spec": spec if spec else None,
            "figure_crop": crop,
            "figure_crop": (crops or [None])[0]}


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


def ingest_selected(sheet, provider=None, progress=None):
    """人审提交：sheet 中 selected=True（存错题本）/ dest_topic（存专题）的题逐个处置。
    返回 {results: [{no, id?|skipped?|topic_id?, report[]}], sheet_id}

    v1.2r：progress 回调（每处置完一题触发一次）——异步提交时前端轮询展示实时进度
    （错题本/专题/跳过重复计数 + 当前题号）；同步调用不传即可。

    v1.2 起每题可双目的地（可多选）：
    - dest_topic（存入专题）：零 LLM —— 题干/图形/答案已由排版机+人审定稿，
      专题归类由人点选，直接落库（图形复制进全局 topic_crops）
    - selected（存错题本）：走完整质量链（查重/图形/验伪/分析）
    - 两边都选：先存专题再走错题本链；仅选题但未配 AI 引擎 → 报错提示

    v0.6.2 起第⓪步先查题库去重：与已入库题重复的直接跳过（不调 LLM、不入库），
    结果在 results 中标 skipped=True + duplicate_of，由前端完成提示汇总展示。
    （查重只守错题本：重复题仍可存专题）

    v0.6.3 起按题记录处置（ingested_id / skipped_dup / topic_example_id 写回草稿单题上）：
    一张照片可分多次提交，每次只处理当次勾选的题；所有题都有处置结果后
    草稿单才标记 processed，否则保持 pending（剩余题仍可再勾选再提交）。
    """
    import topics
    client = None
    model = None
    results = []
    skipped_ids = []

    def _tick(no=None):
        """进度回调（v1.2r 异步提交）：汇总当前处置计数"""
        if progress:
            try:
                progress({"no": no,
                          "ingested": sum(1 for r in results if r.get("id")),
                          "topics": sum(1 for r in results if r.get("topic_id")),
                          "skipped": len(skipped_ids)})
            except Exception:
                pass

    for q in sheet["questions"]:
        dest_topic = q.get("dest_topic") or None
        selected = q.get("selected")
        # 显式跳过（v1.2：每题必须明确处置去向；跳过也算已处置，不阻塞 processed）
        if q.get("skip"):
            results.append({"no": q.get("no"), "skipped_manual": True,
                            "report": ["[跳过] 不录入"]})
            store.patch_sheet_question(sheet["id"], q.get("no"),
                                       {"selected": False, "skip": True})
            _tick(q.get("no"))
            continue
        if not selected and not dest_topic:
            continue
        report = []
        text = q["question"]
        if isinstance(q.get("edited"), dict) and q["edited"].get("question"):
            text = q["edited"]["question"]
        choices = q.get("edited", {}).get("choices") if isinstance(q.get("edited"), dict) else None
        choices = choices or q.get("choices")
        _tick(q.get("no"))   # R4.0e：开始处置即上报题号——质量链耗时 0.5~2 分钟/题，
                             # 否则前端在首题完成前一直显示“第?题（0·0·0）”

        # ⓪-A 存入专题（零 LLM，v1.2）：排版机+人审已定稿，直接落库
        topic_id = None
        if dest_topic:
            crops = q.get("figure_crops") or []
            try:
                ex = topics.add_example(
                    dest_topic.get("set"), dest_topic.get("cat"), text,
                    choices=choices, answer=q.get("printed_answer") or "",
                    ex_type=q.get("type") or "fill",
                    crop_src=(crops or [None])[0],
                    spec=q.get("figure_spec"),
                    source=sheet.get("source") or "", video=q.get("video"),
                    kps=q.get("kps"))   # v1.2q：转录期受控知识点随例题存档
                topic_id = ex["id"]
                report.append("[专题] 已存入（图形随例题存档）")
                store.patch_sheet_question(sheet["id"], q.get("no"),
                                           {"topic_example_id": topic_id})
            except topics.DuplicateExample as e:
                report.append("[专题] 与该类已有例题重复，跳过（" + e.eid + "）")
                # 同样算已处置：不阻塞草稿单完成（与错题本 skipped_dup 同语义）
                store.patch_sheet_question(sheet["id"], q.get("no"),
                                           {"topic_dup_of": e.eid})
            except Exception as e:
                report.append("[专题] 存入失败：" + str(e)[:120])

        if not selected:
            results.append({"no": q.get("no"), "topic_id": topic_id, "report": report})
            _tick(q.get("no"))
            continue

        # 错题本链需要 LLM（验伪/分析/补向量化）→ 惰性构造（仅存专题不建客户端）
        if client is None:
            client, model = make_client(provider, vision=True)

        # ⓪ 题库查重：重复题不入库（同批次先入库的题也能被后面题目查到）
        dup = store.find_duplicate(text)
        if dup:
            report.insert(0, "[去重] 与已入库题 {0} 重复，跳过".format(dup["id"]))
            results.append({"no": q.get("no"), "skipped": True,
                            "duplicate_of": dup["id"], "topic_id": topic_id,
                            "report": report})
            skipped_ids.append(dup["id"])
            store.patch_sheet_question(sheet["id"], q.get("no"),
                                       {"selected": False, "skipped_dup": dup["id"]})
            _tick(q.get("no"))
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
        grade = (q.get("grade") or "").strip()   # v1.4.1：录入页前置年级（小学/初中全集），缺省空
        reason = q.get("reason") or {}
        reason_note = "{0}（{1}）".format(
            {"careless": "马虎", "unknown": "不会", "other": "其他"}.get(reason.get("kind"), "未标注"),
            reason.get("note") or "无备注")
        an = _ask_text_json(client, model, ANALYSIS_PROMPT.format(
            question=text, answer=verified or v_ans, reason=reason_note,
            chapters="、".join(_grade_chapters(grade))))
        report.append("[分析] {0} · 难度{1} · {2}".format(
            an.get("chapter"), an.get("difficulty"), "/".join(an.get("knowledge_points") or [])))

        # ④ 归类（v1.2o：chapter 回归教材章 —— 与 data/kb/knowledge_points.json 同构，
        #    转录期由受控 kp 反查所属章；专题类别只服务专题入库（dest_topic），不再写工作台章节；
        #    kp_sel 为旧草稿兼容，缺省回落 LLM 分析）
        #    v1.2k：细分知识点优先用转录期受控 kps（内置 KB 清单词表），LLM 自由分析仅作兜底
        kp_sel = q.get("kp_sel") or {}
        kps_ctl = [k for k in (q.get("kps") or []) if k][:3]   # v1.2k：受控细分知识点（转录期归一化）
        # chapter 优先级：转录期受控 kp 反查的教材章 > 旧草稿 kp_sel > LLM 分析（CHAPTERS 词表）
        chapter = (q.get("chapter") or "").strip()   # v1.2o：教材章（转录期受控 kp 反查）
        if chapter:
            kp_path = [chapter, "", kps_ctl[0] if kps_ctl else ""]
        elif kp_sel.get("chapter"):
            chapter = kp_sel["chapter"]
            kp_path = [kp_sel.get("chapter", ""), kp_sel.get("section", ""),
                       kp_sel.get("kp", "")]
        else:
            chapter = (an.get("chapter") or "").strip()   # 兜底：LLM 分析（受控词表）
            kp_path = []
        report.append("[归类] {0} · {1}{2}".format(
            grade, chapter or "未分类",
            " · " + "/".join(kps_ctl) if kps_ctl else ""))

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
                         "kp_path": kp_path,
                         "knowledge_points": kps_ctl or (an.get("knowledge_points") or []),
                         "pitfall": an.get("pitfall") or "",
                         "difficulty": max(1, min(3, int(an.get("difficulty") or 2))),
                         "key_idea": an.get("key_idea") or ""},
            "photo": sheet.get("photo"),
            "video": q.get("video"),   # v1.2：可选视频讲解（人审卡上传），详情页可播放
            "flags": {"needs_review": status == "bad_question"},
        })
        # v1.2q：双目的地时把错题本链的分析结果回写专题例题（思路/易错/受控kps），0 额外 token
        if topic_id:
            try:
                import topics as _t
                _t.patch_example(topic_id, {
                    "key_idea": an.get("key_idea") or "",
                    "pitfall": an.get("pitfall") or "",
                    "kps": kps_ctl})
                report.append("[专题] 例题已带思路/易错点/知识点分析")
            except Exception:
                pass
        report.append("[入库] " + rec["id"])
        results.append({"no": q.get("no"), "id": rec["id"], "topic_id": topic_id,
                        "report": report})
        store.patch_sheet_question(sheet["id"], q.get("no"),
                                   {"selected": False, "ingested_id": rec["id"]})
        _tick(q.get("no"))

    ingested_ids = [r["id"] for r in results if r.get("id")]
    sheet_now = store.get_sheet(sheet["id"]) or sheet
    remaining = [q for q in sheet_now.get("questions", [])
                 if not (q.get("ingested_id") or q.get("skipped_dup")
                         or q.get("topic_example_id") or q.get("topic_dup_of")
                         or q.get("skip"))]
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


def _run_export_paper(args_tail):
    """统一导出入口：开发态子进程调 engine/export_paper.py；冻结态（打包）进程内调 main()
    ——打包后 sys.executable 是应用自身，不能再当 Python 解释器用（R4）"""
    if getattr(sys, "frozen", False):
        import export_paper   # engine/ 已在 sys.path（common）；spec hiddenimports 已入包
        old = sys.argv
        sys.argv = ["export_paper"] + args_tail
        try:
            export_paper.main()
        finally:
            sys.argv = old
        return
    from common import BASE
    subprocess.run([sys.executable,
                    os.path.join(BASE, "engine", "export_paper.py")] + args_tail,
                   check=True, capture_output=True)


def export_selected_pdf(ids, with_answer=True):
    """工作台勾选题 → 练习卷 PDF（复用内联引擎 export_paper，无 AI 调用）。

    题目均为已入库的验证过的题：直接映射 QuizBank 字段（图形用已过 geocheck
    的 figure_spec 矢量绘制）；答案默认附卷末（可裁剪）。返回 {pdf, n}。
    """
    from make_figures import normalize_bank
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
    out_dir = os.path.join(paths.DATA_HOME, "out")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(out_dir, "错题练习卷_{0}.json".format(stamp))
    json.dump(bank, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    prefix = os.path.join(out_dir, "错题练习卷_{0}".format(stamp))
    cmd = ["--title", "数学错题练习卷", "--out-prefix", prefix]
    if not with_answer:
        cmd.append("--no-answer")
    _run_export_paper([jpath] + cmd)
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
    out_dir = out_dir or os.path.join(paths.DATA_HOME, "out")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(out_dir, "错题针对卷_{0}.json".format(stamp))
    json.dump(bank, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    import sys
    _run_export_paper([jpath, "--title", "错题针对卷", "--out-prefix",
                       os.path.join(out_dir, "错题针对卷_{0}".format(stamp))])
    pdf = os.path.join(out_dir, "错题针对卷_{0}.pdf".format(stamp))
    return {"pdf": pdf if os.path.exists(pdf) else None, "json": jpath,
            "n": len(bank["questions"]), "dropped": len(bad)}
