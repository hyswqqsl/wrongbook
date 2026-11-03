# -*- coding: utf-8 -*-
"""
出题（国内引擎版）：DeepSeek V4.1-Flash / Qwen / GLM 等 OpenAI 兼容模型
- 文本型 PDF  → 本地抽取文本喂模型
- 扫描版/图片型 PDF → 自动切换多模态：每页渲染成图片，用 deepseek-flash 看图出题
用法:
  .venv/bin/python generate_any.py 教材.pdf --provider deepseek --count 20
  .venv/bin/python generate_any.py 扫描版.pdf --provider deepseek --pages 1-8
  .venv/bin/python generate_any.py 教材.pdf --provider glm --model glm-4.6
依赖: pip install openai pdfplumber pymupdf pydantic
Key:  export DEEPSEEK_API_KEY=xxx / DASHSCOPE_API_KEY=xxx / GLM_API_KEY=xxx
"""
import os, json, re, base64, argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pdfplumber
try:
    import pymupdf as fitz  # 新版 PyMuPDF
except ImportError:
    import fitz  # 旧版兼容
from openai import OpenAI
from schema import QuizBank  # 复用同一套题库 Schema，practice.py 无需改动

PROVIDERS = {  # 国内主流模型的 OpenAI 兼容端点；supports_schema=支持 JSON Schema 严格模式
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash",  # = V4.1-Flash-1017，原生多模态
                 "key_env": "DEEPSEEK_API_KEY", "supports_schema": False},  # 仅支持 json_object 模式
    "qwen":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
                 "key_env": "DASHSCOPE_API_KEY", "supports_schema": True},
    "glm":      {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.6",
                 "key_env": "GLM_API_KEY", "supports_schema": True},
}
SCHEMA = QuizBank.model_json_schema()

def parse_pages(spec, n_pages):
    """'1-3,5' → 0-based 页码列表；spec 为空则全部页"""
    if not spec:
        return list(range(n_pages))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a) - 1, int(b)))
        elif part:
            out.append(int(part) - 1)
    return [i for i in out if 0 <= i < n_pages]

def extract_text(paths, pages_spec=None):
    """文本型 PDF → 文本；返回（拼接文本, 页数, 正文有效字符数）"""
    parts, pages, body = [], 0, 0
    for p in paths:
        with pdfplumber.open(os.path.expanduser(p)) as pdf:
            idx = parse_pages(pages_spec, len(pdf.pages))
            pages += len(idx)
            texts = [pdf.pages[i].extract_text() or "" for i in idx]
            body += sum(len(t.strip()) for t in texts)  # 只统计正文，不含分隔头
            parts.append(f"\n===== 文件: {p} =====\n" + "\n".join(texts))
    return "\n".join(parts), pages, body

def pdf_to_images(paths, pages_spec=None, zoom=2.0):
    """每页渲染为 JPEG data URI（deepseek-flash 单图≤32MiB，总请求≤48MiB；图会被缩到约800×800，zoom 2 足够清晰）"""
    uris = []
    for p in paths:
        doc = fitz.open(p)
        for i in parse_pages(pages_spec, len(doc)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            try:
                data = pix.tobytes("jpeg", jpg_quality=85)
            except (ValueError, TypeError):  # 老版本 PyMuPDF 不支持 jpeg
                data = pix.tobytes("png")
            uris.append("data:image/jpeg;base64," + base64.b64encode(data).decode())
    return uris

def sparse(body_chars, pages):
    """每页平均不足30字 → 图片型 PDF"""
    return pages and body_chars / pages < 30

def kb_context(kb_path, chap_filter="", budget=32000):
    """知识库 → 出题上下文文本（按章节过滤，按预算截断）
    返回（文本, 选用页数, 实际字符数）"""
    kb = json.load(open(os.path.expanduser(kb_path), encoding="utf-8"))
    recs = sorted(kb.get("pages", {}).values(), key=lambda r: r["page"])
    subs = [s.strip() for s in chap_filter.split(",") if s.strip()]
    if subs:
        recs = [r for r in recs if any(s in (r["chapter"] or "") or s in (r["section"] or "") for s in subs)]
        if not recs:
            raise SystemExit("❌ 知识库中无匹配章节/小节: {}（现有: {}）".format(
                chap_filter, " / ".join(kb.get("chapters", []))))
    KIND = {"knowledge": "知识点", "example": "例题", "exercise": "习题",
            "summary": "归纳", "table": "表格", "tip": "批注"}
    parts, total = [], 0
    for r in recs:
        lines = ["\n===== {}｜{}｜PDF p{} =====".format(
            r["chapter"] or "未标注", r["section"] or "无小节", r["page"])]
        for u in r["units"]:
            head = "【{}】".format(KIND.get(u["kind"], u["kind"])) + (u["title"] + "：" if u["title"] else "")
            body = u.get("content", "")
            if u["kind"] == "example" and u.get("question"):
                body = u["question"]
                if u.get("answer"):
                    body += "\n解答：" + u["answer"]
                if u.get("method"):
                    body += "\n方法：" + u["method"]
            if body:
                lines.append(head + body)
        block = "\n".join(lines)
        if total + len(block) > budget:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts), len(parts), total

def build_prompt(chapter, count, types, mode="faithful", topic=""):
    head = ("基于所给教材内容命题" if mode == "faithful" else
            "【原创变式命题】先从材料中提炼全部几何模型清单（如倍长中线、手拉手、半角、一线三等角等），"
            "然后仅用这些模型原创新题。严禁照搬原题：题干情境、数据、图形结构、设问角度必须全新，"
            "鼓励正逆用互换（如原题给中线证全等 → 改为给全等关系求线段比）。题目难度不超过初中范围")
    geo_line = ("- source_page: 题目主要依据的页码（按提供内容的顺序从1计数，整数）\n"
                "- figure_box: 若题干或选项配图，给图形在页面中的位置 [left,top,right,bottom]（各为0~1的小数比例框）；纯文字题填 null"
                if mode == "faithful" else
                "- source_page: 原创题填 0\n"
                "- figure_spec: 原创题必须给出绘图指令：points 为各点坐标（0~10坐标系，布局需符合几何模型的真实比例），"
                "segments 为连线（辅助线 dashed:true）；纯文字题填 null")
    example_tail = ('"source_page":3,"figure_box":[0.55,0.2,0.95,0.5]}}' if mode == "faithful" else
                    '"source_page":0,"figure_spec":{"points":[{"name":"A","x":1,"y":2},'
                    '{"name":"B","x":9,"y":2},{"name":"C","x":5,"y":7},{"name":"D","x":5,"y":2.2}],'
                    '"segments":[{"a":"A","b":"C","dashed":false},{"a":"C","b":"B","dashed":false},'
                    '{"a":"A","b":"B","dashed":false},{"a":"D","b":"C","dashed":true}]}}')
    topic_line = ("\n- 专项主题（最高优先级）: 所有题目必须围绕「" + topic + "」这一几何模型命题——"
                   "图形结构、已知条件、设问方式都要体现该模型特征，可正用/逆用/复合，但不得偏离") if topic else ""

    return f"""你是资深命题教师。{head}，要求：
- 章节范围: {chapter}；数量: {count} 题；题型: {types}
{topic_line}
- 难度分布: 易40% 中40% 难20%
- explanation 必须写清推理链（{'引用教材原句/原图作为依据' if mode == 'faithful' else '标明所用模型名称及关键步骤'}）；答案唯一正确，干扰项有迷惑性无歧义
- 几何题请注意图中标注的线段、角度、辅助线

只输出 JSON（不要包含其他文字），字段契约（严格遵守，枚举值必须用英文）：
- type: "single"(单选) | "multiple"(多选) | "fill"(填空) | "short"(简答)，禁止中文
- choices: 仅选择题有，元素必须是对象 {{"key":"A","text":"选项内容"}}，key 依次 A/B/C/D，禁止纯字符串
- answer: 选择题用 "A"/"B"/"C"/"D"（多选拼合如 "ABD"）；填空/简答用答案文本
- difficulty: "easy" | "medium" | "hard"，禁止"易""中""难"等中文
{geo_line}

单题示例：
{{"type":"single","question":"…","choices":[{{"key":"A","text":"…"}},{{"key":"B","text":"…"}},{{"key":"C","text":"…"}},{{"key":"D","text":"…"}}],"answer":"A","explanation":"…","knowledge_point":"…","difficulty":"easy",{example_tail}

顶层结构：{{"chapter":"…","questions":[单题, …]}}"""

DIFF_MAP = {"易": "easy", "简单": "easy", "容易": "easy", "基础": "easy",
           "中": "medium", "中等": "medium", "一般": "medium",
           "难": "hard", "较难": "hard", "困难": "hard", "偏难": "hard"}
TYPE_MAP = {"单选": "single", "单选题": "single", "选择题": "single",
            "多选": "multiple", "多选题": "multiple",
            "填空": "fill", "填空题": "fill",
            "简答": "short", "简答题": "short", "解答": "short", "解答题": "short"}

def normalize_bank(obj):
    """兼容 json_object 模式下的中文习惯输出：choices 纯字符串、易/中/难等"""
    qs = obj.get("questions") if isinstance(obj, dict) else obj
    if not isinstance(qs, list):
        return obj
    for q in qs:
        if not isinstance(q, dict):
            continue
        t = str(q.get("type", "")).strip()
        if t in TYPE_MAP:
            q["type"] = TYPE_MAP[t]
        d = str(q.get("difficulty", "")).strip()
        if d in DIFF_MAP:
            q["difficulty"] = DIFF_MAP[d]
        chs = q.get("choices")
        if isinstance(chs, list):
            fixed = []
            for i, c in enumerate(chs):
                default_key = "ABCD"[i] if i < 4 else str(i + 1)
                if isinstance(c, str):
                    fixed.append({"key": default_key, "text": c})
                elif isinstance(c, dict):
                    c.setdefault("key", default_key)
                    if not isinstance(c.get("text"), str):
                        c["text"] = str(c.get("text", c.get("content", c.get("option", ""))))
                    fixed.append(c)
            q["choices"] = fixed
        sp = q.get("source_page")
        try:
            q["source_page"] = max(0, int(sp))
        except (TypeError, ValueError):
            q["source_page"] = 0
        fb = q.get("figure_box")
        if isinstance(fb, str):  # 容错："[0.5,0.2,0.9,0.6]" 字符串形式
            try:
                fb = json.loads(fb)
            except ValueError:
                fb = None
        if (isinstance(fb, list) and len(fb) == 4
                and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in fb)):
            q["figure_box"] = [float(v) for v in fb]
        else:
            q["figure_box"] = None
        fs = q.get("figure_spec")
        if isinstance(fs, dict):  # 容错：segments 写成 ["A","B"] 形式
            segs = fs.get("segments")
            if isinstance(segs, list):
                fixed = []
                for s in segs:
                    if isinstance(s, list) and len(s) == 2:
                        fixed.append({"a": s[0], "b": s[1], "dashed": False})
                    elif isinstance(s, dict):
                        s.setdefault("dashed", False)
                        fixed.append(s)
                fs["segments"] = fixed
        else:
            q["figure_spec"] = None
    return obj

def parse_bank(content):
    """健壮解析：剥掉 ```json 围栏、取首尾大括号之间，归一化后校验"""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("模型输出中未找到 JSON:\n" + content[:300])
    obj = json.loads(m.group(0))
    return QuizBank.model_validate(normalize_bank(obj))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="*", help="教材/笔记 PDF，可传多个；与 --kb 二选一")
    ap.add_argument("--kb", action="append", default=None,
                    help="知识库 JSON（build_kb.py 产物），可重复传多个库/逗号分隔；纯文本出题，不啉图更快更准")
    ap.add_argument("--kb-chapter", default="", help="知识库章节过滤：逗号分隔子串，如 '第一章,第二章' 或 '不等式'")
    ap.add_argument("--kb-budget", type=int, default=32000, help="知识库上下文字符预算（默认3.2万）")
    ap.add_argument("--provider", default="deepseek", choices=PROVIDERS)
    ap.add_argument("--model", default=None, help="覆盖默认模型名")
    ap.add_argument("--chapter", default="全卷")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--types", default="single,fill")
    ap.add_argument("--pages", default=None, help="页码范围，如 1-8 或 1,3,5（默认全部页）")
    ap.add_argument("--mode", default="faithful", choices=["faithful", "original"],
                    help="faithful=忠于教材出题；original=提炼模型后原创变式题（不与原题重复）")
    ap.add_argument("--out", default="questions.json")
    ap.add_argument("--workers", type=int, default=3, help="并发分块数：题目分块并发生成后合并（默认3，1=单次整卷）")
    ap.add_argument("--think", action="store_true", help="启用思考模式（更慢；默认关闭——出题是结构化任务，无需思考）")
    ap.add_argument("--topic", default="", help="专项主题：所有题目围绕该几何模型命题（如 手拉手模型/半角模型/倍长中线）")
    args = ap.parse_args()

    p = PROVIDERS[args.provider]
    # 国内直连服务：清掉代理环境变量，避免本机梯子导致 httpx SOCKS 报错
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)

    api_key = os.environ.get(p["key_env"])
    if not api_key:
        raise SystemExit(f"❌ 请先设置密钥: export {p['key_env']}=你的key")
    client = OpenAI(api_key=api_key, base_url=p["base_url"])
    model = args.model or p["model"]

    import time
    t0 = time.time()

    # ---------- 内容准备：--kb 知识库（可多库）/ 文本 PDF / 扫描版自动切多模态 ----------
    if args.kb:
        if args.pdf:
            raise SystemExit("❌ --kb 与 PDF 位置参数二选一")
        kbs = [s.strip() for a in args.kb for s in a.split(",") if s.strip()]
        per = max(4000, args.kb_budget // len(kbs))  # 预算均分到各库
        blocks = []
        for kp in kbs:
            t, n_used, n_chars = kb_context(kp, args.kb_chapter, per)
            n_total = len(json.load(open(os.path.expanduser(kp), encoding="utf-8"))["pages"])
            print("知识库 {}：选用 {}/{} 页，{} 有效字符（预算 {}）".format(
                os.path.basename(kp), n_used, n_total, n_chars, per))
            blocks.append(t)
        source_label, source_text, images, use_vision = "知识库摘录", "\n".join(blocks), [], False
    elif not args.pdf:
        raise SystemExit("❌ 请提供教材 PDF 或 --kb 知识库")
    else:
        text, pages, body = extract_text(args.pdf, args.pages)
        print(f"已抽取 {len(args.pdf)} 个文件，{pages} 页，共 {body} 有效字符")
        use_vision = sparse(body, pages)
        images = []
        if use_vision:
            print(f"检测到图片型 PDF（每页仅 {body/max(pages,1):.0f} 字符）→ 切换多模态，渲染页面图片…")
            images = pdf_to_images(args.pdf, args.pages)
            total_mb = sum(len(u) for u in images) * 3 / 4 / 1024 / 1024
            if total_mb > 45:
                raise SystemExit(f"❌ 图片总大小 {total_mb:.0f}MiB 接近 48MiB 请求上限，请用 --pages 分批出题")
            print(f"已渲染 {len(images)} 页图片（共 {total_mb:.1f}MiB）")
        source_label, source_text = "教材文本", text

    def make_messages(count_i):
        prompt = build_prompt(args.chapter, count_i, args.types, args.mode, args.topic)
        if args.kb:
            prompt += "\n- source_page: 填所依据摘录块标题中的 PDF 页码（整数；原创变式题填 0）"
        if use_vision:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in images]
            return [{"role": "user", "content": content}]
        return [{"role": "user", "content": prompt + "\n\n{}:\n{}".format(source_label, source_text[:500000])}]

    def gen_chunk(count_i):
        """生成一块题目（含 response_format 降级与自修复），返回 QuizBank"""
        kwargs = dict(model=model, messages=make_messages(count_i), temperature=0.7,
                      max_tokens=65536 if args.provider == "deepseek" else 16384)
        if args.provider == "deepseek" and not args.think:
            # 出题是结构化任务：关思考模式大幅提速（官方文档 thinking.type=disabled）
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            fmt = ({"type": "json_schema", "json_schema": {"name": "quiz_bank", "strict": True, "schema": SCHEMA}}
                   if p["supports_schema"] else {"type": "json_object"})
            resp = client.chat.completions.create(**kwargs, response_format=fmt)
            raw = resp.choices[0].message.content
            finish = resp.choices[0].finish_reason
        except Exception as e:
            print(f"⚠ response_format 被拒绝（{str(e)[:80]}…），降级为纯文本 JSON 输出重试")
            resp = client.chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content
            finish = resp.choices[0].finish_reason
        try:
            return parse_bank(raw)
        except Exception as first_err:
            if finish == "length":
                open("broken_output.txt", "w", encoding="utf-8").write(raw)
                raise RuntimeError("输出被 max_tokens 截断，请减小 --count 或 --workers 分批")
            print(f"⚠ JSON 解析失败（{str(first_err)[:100]}），让模型自修复…")
            err = first_err
            for _ in range(2):
                fix = client.chat.completions.create(
                    model=model, temperature=0, max_tokens=65536,
                    messages=[{"role": "user", "content": (
                        "下面的内容本应是一个JSON但存在语法错误，导致解析失败。"
                        "请只修复语法错误使其成为合法JSON，内容保持完全不变，只输出修复后的JSON本身。\n"
                        "解析错误信息: " + str(err)[:300] + "\n\n待修复内容:\n" + raw)}])
                raw = fix.choices[0].message.content
                try:
                    return parse_bank(raw)
                except Exception as e2:
                    err = e2
            open("broken_output.txt", "a", encoding="utf-8").write("\n\n====\n\n" + raw)
            raise RuntimeError(f"自修复仍失败: {err}")

    # ---------- 分块并发 ----------
    workers = max(1, min(args.workers, args.count))
    base, extra_n = divmod(args.count, workers)
    counts = [base + (1 if i < extra_n else 0) for i in range(workers)]
    counts = [c for c in counts if c > 0]

    if len(counts) == 1:
        bank = gen_chunk(counts[0])
        questions = list(bank.questions)
    else:
        print(f"分 {len(counts)} 块并发生成（每块 {counts} 题）…")
        banks, failed = [], []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(gen_chunk, c): c for c in counts}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    banks.append(fut.result())
                    print(f"  ✓ 一块完成（{c} 题）")
                except Exception as e:
                    failed.append(c)
                    print(f"  ✗ 一块失败（{c} 题）: {str(e)[:80]}")
        if failed:
            if banks:
                print(f"⚠ {sum(failed)} 题所在块失败；已保留成功的 {len(banks)} 块。"
                      f"如需补齐请重跑 ① 或执行: make_figures.py")
            else:
                raise SystemExit("❌ 所有块均失败，详见上方日志")
        chapter = next((b.chapter for b in banks if b.chapter), args.chapter)
        questions = [q for b in banks for q in b.questions]
        # 跨块完全同文去重
        seen, uniq = set(), []
        for q in questions:
            key = q.question.strip()
            if key not in seen:
                seen.add(key)
                uniq.append(q)
        questions = uniq
        bank = QuizBank(chapter=chapter, questions=questions)

    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(bank.model_dump(), fp, ensure_ascii=False, indent=2)
    print(f"✅ {model} 已生成 {len(bank.questions)} 题 → {args.out}（耗时 {time.time()-t0:.0f}s）")

if __name__ == "__main__":
    main()
