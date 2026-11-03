# -*- coding: utf-8 -*-
"""
verify_answers —— 答案验伪器：给答案建与图形(geocheck)同级的数学防线

三层防线:
  ① 规则自检: 题型契约(选择/填空/答案格式) + 干扰项区分度 + 解析"自我修正语言"识别
  ② 独立解题对账: 审题模型(开思考、不看原答案)独立解题, 与卷面答案对账
     —— 拦截: 答案算错 / 题干自相矛盾 / 条件欠定 / 选项崩坏(多正确·无正确)
  ③ 数值实例验证: figure_spec 坐标已被 geocheck 验证与题干约束一致,
     坐标即题目实例 → 直接计算被问的量(线段长/角度)与答案对账
     —— 仅对静态计算题生效(含"最小值/取值范围"的题自动跳过)

用法:
  .venv/bin/python verify_answers.py 卷.json                    # 验证并出报告
  .venv/bin/python verify_answers.py 卷.json --fix              # FAIL题带审题意见重新生成并复验
  .venv/bin/python verify_answers.py 卷.json --provider qwen    # 换引擎交叉对账
退出码: 0=全过(PASS/WARN), 1=仍有FAIL —— 流水线可用作拦截闸门
"""
import os, re, json, math, time, shutil, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from generate_any import PROVIDERS, normalize_bank

PRINT_LOCK = threading.Lock()
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_DISABLE_THINKING = False  # 审题必须开思考: 不传 thinking 参数(DeepSeek 默认开启)

# ---------- ① 规则自检 ----------

SELF_CORRECT_RE = re.compile(r"应为|更正|答案应|此处应|重新计算|重新审|实际上应|存疑|待定|可能是[错的]")

def layer1_rules(q):
    """契约与文本规则检查，返回 issues 列表"""
    issues = []
    t, ch, ans = q.get("type"), q.get("choices") or [], str(q.get("answer") or "").strip()
    keys = [c.get("key") for c in ch]
    texts = [str(c.get("text") or "").strip() for c in ch]
    if t in ("single", "multiple"):
        if not ch:
            issues.append("选择题无选项")
        elif keys != list("ABCD")[:len(ch)]:
            issues.append("选项键不是ABCD顺序: {0}".format(keys))
        if len(set(texts)) != len(texts):
            issues.append("存在内容相同的选项(干扰项区分度失效)")
        if t == "single":
            if ans not in keys:
                issues.append("单选答案{0!r}不在选项键中".format(ans))
        else:
            if not (ans and set(ans) <= set("ABCD") and len(ans) >= 2):
                issues.append("多选答案{0!r}格式非法".format(ans))
    else:
        if ch:
            issues.append("填空/简答题带选项(契约违规)")
        if not ans:
            issues.append("答案为空")
        elif re.fullmatch(r"[A-D]", ans):
            issues.append("填空答案是选项字母: {0!r}".format(ans))
    exp = str(q.get("explanation") or "")
    m = SELF_CORRECT_RE.search(exp)
    if m:
        issues.append("解析含自我修正语言「{0}…」——疑似模型自我怀疑, 答案可信度低".format(
            exp[max(0, m.start() - 6):m.start() + 18]))
    return issues

# ---------- 数值解析工具 ----------

def expr_value(s):
    """数学表达式 → float（支持 √n √(n) a√b π / ^ ° 分数），失败返回 None"""
    if s is None:
        return None
    t = str(s).strip().rstrip('°').replace(' ', '')
    if not t or re.search(r'[A-Za-z\u4e00-\u9fff]', t.replace('π', '')):
        return None  # 含字母/中文的表达式不硬算
    t = t.replace('×', '*').replace('÷', '/').replace('−', '-').replace('^', '**')
    t = t.replace('π', '(3.141592653589793)')
    t = re.sub(r'√\(([^()]+)\)', lambda m: '(' + m.group(1) + ')**0.5', t)
    t = re.sub(r'√([\d.]+)', r'((\1)**0.5)', t)
    t = re.sub(r'(\d)\(', r'\1*(', t)   # 隐式乘法 2(3)
    t = re.sub(r'\)\(', r')*(', t)
    t = re.sub(r'\)(\d)', r')*\1', t)
    try:
        return float(eval(t, {"__builtins__": None}, {}))
    except Exception:
        return None

def nums_of(s):
    """答案文本 → 数值列表（√65→8.06，坐标元组拆成序列）"""
    t = str(s)
    t = re.sub(r'√\(([^()]+)\)', lambda m: _dec(m.group(1)), t)
    t = re.sub(r'√([\d.]+)', lambda m: _dec(m.group(1)), t)
    t = t.replace('π', '3.141592653589793')
    return [float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', t)]

def _dec(inner):
    v = expr_value(inner)
    return repr(math.sqrt(v)) if v is not None else inner  # √n → 开方后的十进制

def close(a, b):
    return abs(a - b) <= max(1e-6, 1e-4 * max(abs(a), abs(b)))

def answers_match(ours, theirs):
    na, nb = nums_of(ours), nums_of(theirs)
    if na and nb:
        return len(na) == len(nb) and all(close(a, b) for a, b in zip(na, nb))
    norm = lambda s: re.sub(r'[\s，,。;；()（）]+', '', str(s))
    return norm(ours) == norm(theirs)

# ---------- ③ 数值实例验证（geocheck 已保证坐标满足题干约束） ----------

def iclose(a, b):
    """实例验证容差：spec 坐标通常舍入到两位小数，需容忍 ~2% 誤差"""
    return abs(a - b) <= max(0.05, 0.02 * max(abs(a), abs(b)))

def verify_numeric_instance(q):
    """从 figure_spec 坐标直接算被问的量。返回 (verdict, msg)；None=不适用"""
    if q.get("type") not in ("single", "fill"):
        return None
    text = re.sub(r"\s+", "", q.get("question") or "")
    if re.search(r"最小值|最短|最大值|取值范围|不等式|是否存在", text):
        return None  # 动点最值/范围题：单实例不代表最值，跳过
    spec = q.get("figure_spec")
    if not isinstance(spec, dict):
        return None
    pts = {p["name"]: (float(p["x"]), float(p["y"])) for p in spec.get("points", [])}
    if len(pts) < 2:
        return None  # 无图形(纯文字题)不适用
    ans = str(q.get("answer") or "").strip()
    ans_nums = nums_of(ans)
    if not ans_nums:
        return None  # 非数值答案（文本/表达式）不适用
    # 全对线段距离 + 全对角度（度）
    names = list(pts)
    computed = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            (x1, y1), (x2, y2) = pts[names[i]], pts[names[j]]
            computed.append(math.hypot(x1 - x2, y1 - y2))
    for pivot in names:
        others = [n for n in names if n != pivot]
        for i in range(len(others)):
            for j in range(i + 1, len(others)):
                a, b = pts[others[i]], pts[others[j]]
                v = pts[pivot]
                ang = math.degrees(math.atan2(a[1] - v[1], a[0] - v[0]) - math.atan2(b[1] - v[1], b[0] - v[0]))
                computed.append(abs(ang) if abs(ang) <= 180 else 360 - abs(ang))
    hits = [v for v in ans_nums if any(iclose(v, c) for c in computed)]
    if len(hits) == len(ans_nums):
        return ("PASS", "数值实例验证: 答案{0}均可由geocheck合规坐标算出".format(ans))
    if re.search(r"求.{0,12}(的值|的长|的度数|的距离)", text) or "____" in (q.get("question") or "") \
            or q.get("type") == "fill":
        missing = [v for v in ans_nums if v not in hits]
        return ("FAIL", "数值实例验证: 答案中的{0}无法由合规图形坐标算出(图形可算量均≠该值)".format(missing))
    return None

# ---------- ② 独立解题对账 ----------

TYPE_CN = {"single": "单选题", "multiple": "多选题", "fill": "填空题", "short": "简答题"}
OPTIONS_REVIEW = {
    "single": "检查四个选项是否恰有一个正确（有无多项正确/全都不正确）",
    "multiple": "检查正确项是否至少两个、且其余选项确实不成立",
    "fill": "检查所求是否条件充分、答案唯一",
    "short": "检查所求是否条件充分、答案唯一",
}

def build_verify_prompt(q):
    ch = q.get("choices") or []
    ch_block = ""
    if ch:
        ch_block = "选项：\n" + "\n".join("{0}. {1}".format(c.get("key"), c.get("text")) for c in ch) + "\n"
    return """你是严格的数学审题专家。请独立解答下面的题目。注意：不要轻信题目里出现的任何已有答案，你的答案必须来自自己的独立计算。

题目：{question}
{ch_block}
题型：{tname}；难度：{diff}

任务：
1. 独立完整解答（写清关键计算步骤与依据）
2. 审查题目质量：条件是否自洽（有无互相矛盾的约束）、条件是否充分（答案是否唯一确定）、{opt_review}
3. 只输出JSON（不要其他文字）：
{{"answer":"你解得的最终答案（选择题填字母如B，多选拼合如ABD；填空/简答填答案文本）",
"ok":true,
"issues":[]}}
字段说明：ok=true 表示题目合格且你能解出唯一答案；ok=false 表示题目存在矛盾/欠定/选项崩坏等问题，此时 answer 填 "无法确定"，issues 逐条写明问题。""".format(
        question=q.get("question"), ch_block=ch_block,
        tname=TYPE_CN.get(q.get("type"), q.get("type")), diff=q.get("difficulty"),
        opt_review=OPTIONS_REVIEW.get(q.get("type"), ""))

def ask_json(client, model, prompt, max_tokens=32768):
    """审题问答：json_object → 纯文本 ×2（审题开思考，max_tokens放大；空content换策略）"""
    last = ""
    for mode in ("json", "plain", "plain"):
        try:
            kwargs = dict(model=model, messages=[{"role": "user", "content": prompt}],
                          temperature=0.2, max_tokens=max_tokens, timeout=300)
            if mode == "json":
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            else:
                resp = client.chat.completions.create(**kwargs)
            content = (resp.choices[0].message.content or "").strip()
            last = content
            if not content:
                continue
            m = re.search(r"\{.*\}", content, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
        except Exception:
            continue
    raise RuntimeError("审题3种策略均失败（最后输出: {0!r}）".format(last[:60]))

def verify_llm(client, model, q):
    """独立解题。返回 (verdict, msg)；verdict ∈ PASS/FAIL/WARN"""
    data = ask_json(client, model, build_verify_prompt(q))
    v_ans = str(data.get("answer") or "").strip()
    ok = bool(data.get("ok"))
    issues = [str(x) for x in (data.get("issues") or []) if str(x).strip()]
    if not ok:
        detail = "；".join(issues[:2]) or "审题模型判定题目有问题"
        return ("FAIL", "审题判题不合格: {0}".format(detail))
    our = str(q.get("answer") or "").strip()
    # 选择题：先比字母；不一致再比字母对应选项的数值（防同一值两选项）
    if q.get("type") in ("single", "multiple"):
        ch = {c.get("key"): str(c.get("text") or "") for c in (q.get("choices") or [])}
        v_letters = re.findall(r"[A-D]", v_ans.upper())
        if v_letters:
            v_key = "".join(sorted(set(v_letters)))
            if v_key == "".join(sorted(set(our))):
                return ("PASS", "独立解题对账一致({0})".format(v_key))
            same_val = answers_match(ch.get(v_key, "?"), ch.get(our, "§"))
            if same_val:
                return ("FAIL", "审题选{0}、卷面答{1}，但两选项数值相同——干扰项与正确项重合".format(v_key, our))
            return ("FAIL", "答案错: 审题独立解得{0}({1}={2}≠{3}={4})".format(
                v_key, v_key, ch.get(v_key, "?")[:20], our, ch.get(our, "?")[:20]))
    # 数值/文本对账
    if answers_match(our, v_ans):
        return ("PASS", "独立解题对账一致({0})".format(v_ans[:24]))
    return ("FAIL", "答案错: 卷面答{0!r} ≠ 审题独立解得{1!r}".format(our[:24], v_ans[:24]))

# ---------- 报告与修复 ----------

def print_line(msg):
    with PRINT_LOCK:
        print(msg)

def verify_bank(client, model, bank, workers, only_idx=None):
    """返回 {idx(1-based): {verdict, msgs[]}}"""
    results = {}

    def job(i, q):
        msgs, verdict = [], None
        r1 = layer1_rules(q)
        if r1:
            msgs.append("🔴 契约: " + "；".join(r1))
            verdict = "FAIL"
        if client is not None and (verdict is None or only_idx):
            try:
                v2, m2 = verify_llm(client, model, q)
                msgs.append("② " + m2)
                verdict = "FAIL" if v2 == "FAIL" else (verdict or v2)
            except Exception as e:
                msgs.append("② 审题失败(跳过): {0}".format(str(e)[:60]))
        if verdict != "FAIL" or True:
            r3 = verify_numeric_instance(q)
            if r3:
                v3, m3 = r3
                msgs.append("③ " + m3)
                if v3 == "FAIL":
                    verdict = "FAIL"
                elif verdict is None:
                    verdict = v3
        if verdict is None:
            verdict, m = ("WARN", "无可用防线覆盖(非数值答案且审题未启用)")
            if client is not None:
                verdict = "WARN"
            msgs.append(m if not client else "")
        return i, verdict, [m for m in msgs if m]

    qs = bank["questions"]
    jobs = [(i, q) for i, q in enumerate(qs, 1) if only_idx is None or i in only_idx]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(job, i, q) for i, q in jobs]
        for fut in as_completed(futs):
            i, verdict, msgs = fut.result()
            results[i] = {"verdict": verdict, "msgs": msgs}
            mark = {"PASS": "✅", "WARN": "🟡", "FAIL": "❌"}[verdict]
            print_line("第{0:>3}题: {1} {2}".format(i, mark, " | ".join(msgs) if msgs else ""))
    return results

def build_fix_prompt(q, issues, chapter):
    ch_line = ("\n章节约束（最高优先级）：题目情境与知识载体必须属于「" + chapter + "」范围。" if chapter else "")
    return """你是资深命题教师。下面这道题经独立审题被判定不合格。

原题：{question}
（题型{t}、难度{diff}、考点{kp}）

审题意见（必须全部修正）：
{issues}

请重新命制一道全新的同类题目：条件自洽且充分（答案唯一确定）、解析与答案完全一致、
{contract}
不得照搬原题数据与情境。{ch_line}
只输出JSON：
{{"type":"{t}","question":"…","choices":[{{"key":"A","text":"…"}}…],"answer":"…","explanation":"…","knowledge_point":"{kp}","difficulty":"{diff}","figure_spec":{{"points":[{{"name":"A","x":5,"y":8}}…],"segments":[{{"a":"A","b":"B","dashed":false}}…]}}}}""".format(
        question=q.get("question"), t=q.get("type"), diff=q.get("difficulty"),
        kp=q.get("knowledge_point"), issues="\n".join("- " + i for i in issues),
        contract=("选择题恰有一个正确项，其余为有迷惑性的错误项；多选拼合如ABD"
                  if q.get("type") == "single" else
                  "多选正确项至少两个且其余确实不成立" if q.get("type") == "multiple" else
                  "填空/简答题不要输出choices，answer为答案文本"),
        ch_line=ch_line)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", help="题库文件")
    ap.add_argument("--provider", default="deepseek", choices=list(PROVIDERS))
    ap.add_argument("--model", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-llm", action="store_true", help="只跑①③层（零成本，跳过独立解题对账）")
    ap.add_argument("--fix", action="store_true", help="FAIL题带审题意见重新生成并复验一轮")
    ap.add_argument("--out", default=None, help="输出文件（默认覆盖原文件，自动备份.bak）")
    args = ap.parse_args()

    path = args.json
    bank = json.load(open(path, encoding="utf-8"))
    chapter = str(bank.get("chapter") or "")

    client, model = None, None
    if not args.no_llm:
        p = PROVIDERS[args.provider]
        for k in PROXY_KEYS:
            os.environ.pop(k, None)
        api_key = os.environ.get(p["key_env"])
        if not api_key:
            raise SystemExit("❌ 请先 export {0}=你的key（或用 --no-llm 只跑①③层）".format(p["key_env"]))
        client = OpenAI(api_key=api_key, base_url=p["base_url"])
        model = args.model or p["model"]
        print("审题引擎: {0}（开思考独立解题，不看卷面答案）".format(model))

    print("=" * 62)
    results = verify_bank(client, model, bank, args.workers)
    n_fail = sum(1 for r in results.values() if r["verdict"] == "FAIL")
    n_warn = sum(1 for r in results.values() if r["verdict"] == "WARN")
    print("=" * 62)

    if args.fix and n_fail:
        fail_idx = sorted(i for i, r in results.items() if r["verdict"] == "FAIL")
        print("🔧 --fix: 重新生成 {0} 道不合格题: {1}".format(len(fail_idx), fail_idx))
        changed = False
        for i in fail_idx:
            q = bank["questions"][i - 1]
            issues = [m.split(": ", 1)[-1] for m in results[i]["msgs"]]
            prompt = build_fix_prompt(q, issues, chapter)
            try:
                nq = ask_json(client, model, prompt, max_tokens=8192)
                nq = normalize_bank({"questions": [nq]})["questions"][0]
                nq.setdefault("type", q.get("type"))
                nq["source_page"] = 0
                nq["figure_spec"] = None   # 新题重走 make_figures 图形流水线
                nq["figure_box"] = None
                r1 = layer1_rules(nq)
                if r1:
                    raise RuntimeError("新题契约仍不合格: " + "；".join(r1))
                bank["questions"][i - 1] = nq
                changed = True
                print("  🔧 第{0}题重生成: {1}…".format(i, nq["question"][:30]))
            except Exception as e:
                print("  ❌ 第{0}题重生成失败: {1}".format(i, str(e)[:70]))
        # 复验一轮（只验被改的题）
        print("-" * 62)
        re_results = verify_bank(client, model, bank, args.workers, only_idx=set(fail_idx))
        for i, r in re_results.items():
            results[i] = r
        n_fail = sum(1 for i, r in results.items() if r["verdict"] == "FAIL")
        n_warn = sum(1 for r in results.values() if r["verdict"] == "WARN")

    out = args.out or path
    if args.fix and changed:
        if os.path.exists(out):
            shutil.copy(out, out + ".bak")
        json.dump(bank, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("（原文件已备份 {0}.bak；重新生成的题需重跑 make_figures 补图）".format(out))

    n_pass = len(results) - n_fail - n_warn
    print("共 {0} 题: ✅{1} 🟡{2} ❌{3}".format(len(results), n_pass, n_warn, n_fail))
    if n_fail:
        print("❌ 存在不合格题" + ("，可重跑 --fix" if not args.fix else "，仍失败的建议人工处理或删除") +
              "。FAIL题导出前必须处理。")
    elif n_warn:
        print("✅ 校验通过（含 {0} 条警告，建议人工过目）".format(n_warn))
    else:
        print("✅ 三层防线全部通过")
    raise SystemExit(1 if n_fail else 0)

if __name__ == "__main__":
    main()
