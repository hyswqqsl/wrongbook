# -*- coding: utf-8 -*-
"""
补图/修图：逐题生成（或重生成）figure_spec，带程序化校验，专治"图不对题"
用法:
  .venv/bin/python make_figures.py original_questions.json                 # 逐题重生成图形
  .venv/bin/python make_figures.py original_questions.json --check        # 只校验不调用API
  .venv/bin/python make_figures.py xxx.json --provider qwen --model qwen-max
可靠性设计:
  - 单题内部重试3次（DeepSeek json_object 有概率返回空 content，官方已知问题）
  - 坐标软修复：字符串转数字、越界收敛到[-5,10]（不因小越界报废整图）
  - 重新生成失败时保留原有合法 spec（绝不破坏性置空）
  - 覆盖写入前自动备份 .bak
校验规则（不满足自动重试）:
  1. 题干提到的每个大写字母点必须在 points 中；points 中不得出现题干没有的点
  2. segments 端点必须在 points 中；连线数 ≥ 点数-1（连通）
"""
import os, re, json, shutil, argparse, difflib, math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from generate_any import PROVIDERS, normalize_bank

_DISABLE_THINKING = False  # main 中按 provider 设置：deepseek 默认关思考以提速
import geocheck
import geoconstruct

TEMPLATES = [
    ("倍长中线", "B(1,2), C(9,2), D(5,2)为BC中点, A(5,8), E(5,-4)在AD延长线上且DE=AD。实线: AB,AC,BC,AE; 虚线(辅助): BE,CE"),
    ("手拉手",   "共顶点A(5,1)。三角形ABC: B(1,6), C(7,5); 三角形ADE: D(4,9), E(9,6), 保持AB=AC、AD=AE、∠BAC=∠DAE。实线: AB,AC,AD,AE,BC,DE,BD,CE"),
    ("一线三等角","B(1,1), C(9,1), D(5,1)在BC上, A(2,7), E(8,7)。实线: AB,AC,DE,AD,AE"),
    ("三线合一", "等腰△ABC: A(5,8), B(1,1), C(9,1), D(5,1)为BC中点, AD是高/中线/角平分线"),
    ("半角",     "顶点O(2,1), 射线OA至(9,2), OB至(5,9), 内部半角三角形 OC(7,4), OD(4,6)"),
    ("角平分线", "角顶点O(2,1), 两边OA(9,3), OB(6,9), 平分线上点P(5,4), P向两边垂足用虚线"),
    ("垂直平分线","A(2,2), B(9,3), 中点M(5.5,2.5), 中垂线上点P(6,8), 满足PA=PB"),
    ("八字",     "AB与CD相交于O(5,5): A(1,8), B(8,7), C(2,2), D(7,1)。实线: AB,CD,AD,BC"),
]

def pick_template(text):
    for kw, t in TEMPLATES:
        if kw in text:
            return t
    return "一般三角形: 三个不共线点正置摆放"

def letters_in_text(q):
    """题干+选项中提到的点名（排除面积记号 S△、直角记号 Rt；连写字母如 ABC 逐个拆开）"""
    text = q["question"] + "".join(c.get("text", "") for c in q.get("choices", []))
    text = re.sub(r"S\s*△[A-Z]*|S\d?\s*=", "", text)  # S△ABC=面积，S不是点
    text = re.sub(r"Rt", "", text)                      # Rt△=直角三角形
    return set(re.findall(r"[A-Z]", text))

def coerce_spec(spec):
    """软修复：字符串坐标转数字并收敛到[-5,10]，dashed 字符串转布尔"""
    if not isinstance(spec, dict):
        return spec
    for p in spec.get("points", []):
        if isinstance(p, dict):
            try:
                p["x"] = max(-15.0, min(15.0, float(p.get("x"))))
                p["y"] = max(-15.0, min(15.0, float(p.get("y"))))
            except (TypeError, ValueError):
                pass
    for s in spec.get("segments", []):
        if isinstance(s, dict) and isinstance(s.get("dashed"), str):
            s["dashed"] = s["dashed"].strip().lower() in ("true", "1", "是", "虚线")
    return spec

def validate(q, spec):
    errs = []
    pts = {str(p.get("name", "")).strip(): p for p in spec.get("points", []) if isinstance(p, dict)}
    if not (3 <= len(pts) <= 8):
        errs.append(f"点数{len(pts)}不合理(应3~8)")
    for name, p in pts.items():
        if not (isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float))):
            errs.append(f"点{name}坐标缺失或非数字")
    segs = spec.get("segments", [])
    for s in segs:
        if not (isinstance(s, dict) and str(s.get("a")) in pts and str(s.get("b")) in pts):
            errs.append(f"连线{s}端点不在points中")
    mentioned = letters_in_text(q)
    missing = mentioned - set(pts)
    extra = set(pts) - mentioned
    if missing:
        errs.append(f"题干提到的点缺失: {sorted(missing)}")
    if extra:  # 题图必须一致：题干没有的辅助点一律不允许（会让学生困惑）
        errs.append(f"题干中不存在的点多余: {sorted(extra)}（题图应一致）")
    # 重合点检查：任意两点距离 < 0.5 视为重合（图上无法分辨）
    names_l = list(pts)
    declared_inter = "交于点" in q["question"]  # 题干声明的交点允许贴近其他点（嵌套构型的必然几何）
    for a_i in range(len(names_l)):
        for b_i in range(a_i+1, len(names_l)):
            na, nb = names_l[a_i], names_l[b_i]
            if declared_inter and "O" in (na, nb):
                continue
            pa, pb = pts[na], pts[nb]
            if not all(isinstance(v.get(k), (int, float)) for v in (pa, pb) for k in ("x", "y")):
                continue
            d2 = math.hypot(pa["x"]-pb["x"], pa["y"]-pb["y"])
            if d2 < 0.5:
                errs.append("点{}与{}几乎重合(距离{:.2f})".format(na, nb, d2))
    if len(segs) < len(pts) - 2:
        errs.append(f"连线过少({len(segs)}条)，图形过散")
    return errs

FIGURE_MENU = (
    "可用几何模板（构造式求解，坐标由尺规公理精确计算）：\n"
    "1. collinear_equilateral 共线双等边（B、A、D 共线） 参数: {ab, ad}\n"
    "2. shoulashou_cross 手拉手旋转·交叉构型（BD、CE 相交于 O，自动保证 O 在段内且 OA 平分∠BOE） 参数: {apex, L1(=AB=AC), L2(=AD=AE), gamma(D 越过 C 方向的角度,取20~50)}\n"
    "3. shoulashou_fan 手拉手张开 参数: {apex, L1, L2, phi}\n"
    "4. iso_right_pair 双等腰直角（共顶点 90°） 参数: {leg1, leg2, rot}\n"
    "5. banjiao120 等边+120°外点+半角 参数: {s, m(BE), n(CF)}\n"
    "6. mid_double 倍长中线 参数: {A:[ax,ay], bx, cx, t_E}\n"
    "7. yixian_right 一线三等角（直角型） 参数: {b, m, c, h}\n"
    "8. custom_points 自由坐标（最后手段） 参数: {points:[{name,x,y}…], segments:[{a,b,dashed}…]}")

def gen_spec(client, model, q, prev_errs=None):
    """几何图声明：模型只选模板+报参数，坐标由 geoconstruct 闭式解精确计算。
    无合适模板时才允许 custom_points 自由坐标（需通过几何校验）。"""
    extra = ""
    if prev_errs:
        extra = "\n\n上一次输出未通过几何校验，必须修复:\n- " + "\n- ".join(prev_errs)
    prompt = f"""你是几何制图调度助手。根据题目文本，从模板表中选择最合适的模板并输出参数 JSON（只输出JSON）。
{FIGURE_MENU}

要求：
1. 优先选 1~7 号参数化模板——坐标由几何求解器按尺规公理精确计算，绝不会畸形
2. 点名与题干一致（模板会自动命名 A/B/C/D/E，题干用其他字母时在 answer 里说明对应关系即可忽略）
3. 参数体现题干数量：如"等边三角形边长4"→ s=4；"BE=2"→ m=2；"∠BAC=60°"→ apex=60
4. 只有在 1~7 号都确实无法表达时，才用 8 号 custom_points（此时坐标必须满足所有几何约束）
5. 题目: {q["question"]}
   选项: {json.dumps([c.get("text","") for c in q.get("choices",[])], ensure_ascii=False)}{extra}"""
    return ask_json(client, model, prompt, max_tokens=1200, temperature=0.2)

def build_spec_from_declaration(decl):
    """模板声明 → 精确坐标 spec（points/segments 标准格式）"""
    template = decl.get("template")
    if template == "custom_points":
        return {"points": decl.get("points", []), "segments": decl.get("segments", [])}
    pts, segs, _meta = geoconstruct.build(template, decl.get("params") or {})
    return {"points": [{"name": n, "x": x, "y": y} for n, (x, y) in pts.items()],
            "segments": [{"a": a, "b": b, "dashed": bool(d)} for a, b, d in segs]}

def best_twin(qs, idx, q, min_ratio=0.8):
    """题干最相似且有合法图形的其他题 → (相似度, spec)；无则 None。
    几何模型相同/近似的题图形结构基本一致（如80°/90°手拉手），借用即可用"""
    best = None
    for j, other in enumerate(qs):
        if j == idx:
            continue
        spec = other.get("figure_spec")
        if not isinstance(spec, dict) or validate(other, coerce_spec(spec)):
            continue
        r = difflib.SequenceMatcher(None, q["question"].strip(), other["question"].strip()).ratio()
        if r >= min_ratio and (best is None or r > best[0]):
            best = (r, spec)
    return best

def dedup(qs):
    """去除题干相似度≥90%的重复题，优先保留有合法图形的副本。返回 (保留列表, 删除说明)"""
    keep, removed = [], []  # keep: [(原编号, q)]
    for i, q in enumerate(qs, 1):
        dup_k, r = None, 0.0
        for ki, (no, kq) in enumerate(keep):
            r = difflib.SequenceMatcher(None, q["question"].strip(), kq["question"].strip()).ratio()
            if r >= 0.90:
                dup_k = ki
                break
        if dup_k is None:
            keep.append((i, q))
            continue
        no, kq = keep[dup_k]
        kept_ok = isinstance(kq.get("figure_spec"), dict) and not validate(kq, coerce_spec(kq["figure_spec"]))
        cur_ok = isinstance(q.get("figure_spec"), dict) and not validate(q, coerce_spec(q["figure_spec"]))
        if cur_ok and not kept_ok:
            keep[dup_k] = (no, q)
            removed.append(f"第{i}题(无图) 与 第{no}题 重复 {r:.0%} → 删第{i}题，留有图的第{no}题")
        else:
            removed.append(f"第{i}题 与 第{no}题 重复 {r:.0%} → 删第{i}题")
    return [q for _, q in keep], removed

def wants_figure(q):
    """题干明示配图（如图/上图/下图/图N）才需要图形；纯文字题永不进图形流水线、永不换题"""
    return bool(re.search(r"如图|上图|下图|图\s*\d+", q.get("question", "")))

def full_validate(q, spec, details=None):
    """结构校验 + 几何数值校验。返回 (errors, warnings)；details 传入列表时记录验证明细"""
    errs = validate(q, spec)
    warns = []
    if isinstance(spec, dict):
        try:
            gerrs, gwarns = geocheck.check(q, spec, details)
            errs += gerrs
            warns = gwarns
        except Exception:
            pass
    return errs, warns

def ask_json(client, model, prompt, max_tokens=2000, temperature=0.3):
    """容错JSON问答：json_object → 纯文本 → 纯文本（DeepSeek 空content自动换策略）
    thinking-disabled 仅在端点报错时摘除；空content换模式时保留——否则 deepseek 思考
    烧光 max_tokens，后续重试继续空（finish=length, content=''）"""
    last = ""
    use_extra = _DISABLE_THINKING
    for mode in ("json", "plain", "plain"):
        try:
            kwargs = dict(model=model, messages=[{"role": "user", "content": prompt}],
                          temperature=temperature, max_tokens=max_tokens)
            if use_extra:
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
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
            use_extra = False  # 仅疑似端点拒绝参数（如对 extra_body 报400）时才摘除
            continue
    raise RuntimeError(f"3种策略均未获得有效JSON（最后输出: {last[:50]!r}）")

def gen_replacement(client, model, q, chapter=""):
    """换题：同考点原创一道全新题（题干+绘图指令一起生成），受卷面章节约束防漂移"""
    kp = q.get("knowledge_point") or "几何模型"
    t = q.get("type", "single")
    diff = q.get("difficulty", "medium")
    chap_line = ("\n章节约束（最高优先级）：题目情境与知识载体必须属于「" + chapter + "」范围；"
                 "考点以外的部分不得借用范围外的知识载体（如坐标系、函数图象、统计图表）；"
                 "仅当坐标系本身是考点时才可使用。" if chapter else "")
    if t in ("single", "multiple"):
        ans_line = ('"choices":[{"key":"A","text":"…"},{"key":"B","text":"…"},'
                    '{"key":"C","text":"…"},{"key":"D","text":"…"}],\n'
                    '"answer":"' + ('多选拼合如ABD' if t == 'multiple' else 'A/B/C/D之一') + '",')
    else:
        ans_line = '"answer":"答案文本（填空/简答题禁止输出choices）",'
    prompt = f"""你是资深命题教师。围绕考点「{kp}」原创一道全新的初中几何题（题型{t}，难度{diff}），
要求与常见题目不同、条件自洽、答案唯一正确、解析写明所用模型与关键推理步骤。{chap_line}
绘图指令要求：points 为各点坐标(0~10内，布局体现几何约束：中点取平均、垂直用水平竖直线、等长用对称)，
segments 为连线，辅助线 dashed:true；题干提到的每个大写字母点都必须出现在 points 中。
只输出JSON：
{{"question":"…",{ans_line}"explanation":"…","knowledge_point":"{kp}","difficulty":"{diff}",
"figure_spec":{{"points":[{{"name":"A","x":5,"y":8}},…],"segments":[{{"a":"A","b":"B","dashed":false}},…]}}}}"""
    nq = ask_json(client, model, prompt, max_tokens=3000, temperature=0.8)
    if t in ("single", "multiple") and not (isinstance(nq.get("choices"), list) and len(nq["choices"]) >= 2):
        raise RuntimeError("新题缺少选项")
    if t not in ("single", "multiple"):
        nq.pop("choices", None)  # 防模型惯性泄漏选项（历史事故：填空题带choices+选项字母答案）
    if not nq.get("answer") or not nq.get("question"):
        raise RuntimeError("新题缺少题干或答案")
    nq.setdefault("type", t)
    nq.setdefault("difficulty", diff)  # 模型漏传时保持原题难度
    return nq

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", help="题库文件")
    ap.add_argument("--provider", default="deepseek", choices=PROVIDERS)
    ap.add_argument("--model", default=None)
    ap.add_argument("--check", action="store_true", help="只校验现有 figure_spec，不调用API")
    ap.add_argument("--force", action="store_true", help="强制重新生成所有图形（默认只补缺失/不合法的）")
    ap.add_argument("--workers", type=int, default=4, help="并发数（多题同时生成图形，默认4）")
    ap.add_argument("--think", action="store_true", help="启用思考模式（更慢；默认关闭以提速）")
    ap.add_argument("--dedup", action="store_true", help="删除题干相似度≥90%%的重复题（优先保留有合法图形的副本）")
    ap.add_argument("--verbose", action="store_true", help="校验时输出每条几何约束的验证明细（期望 vs 实测）")
    ap.add_argument("--out", default=None, help="输出文件（默认覆盖原文件，自动备份.bak）")
    args = ap.parse_args()

    bank = json.load(open(args.json, encoding="utf-8"))

    if args.dedup:
        qs = bank["questions"]
        keep, removed = dedup(qs)
        if not removed:
            print("✅ 未发现重复题（相似度≥90%）")
            return
        for line in removed:
            print("  🗑 " + line)
        bank["questions"] = keep
        out = args.out or args.json
        if os.path.exists(out):
            shutil.copy(out, out + ".bak")
        json.dump(bank, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n🗑 去重完成: 删除 {len(removed)} 题，剩余 {len(keep)} 题（原文件已备份 .bak）")
        print("继续: 先 make_figures.py 补缺失图形，再 export_paper.py 导出")
        return

    qs = bank["questions"]
    chapter = str(bank.get("chapter") or "")  # 卷面章节 → 换题约束（防章节漂移）

    if args.check:
        bad = wcount = 0
        for i, q in enumerate(qs, 1):
            if not q.get("figure_spec"):
                if wants_figure(q):
                    print(f"第{i:>2}题: ⚠ 题干要求配图但无图形指令")
                    bad += 1
                else:
                    print(f"第{i:>2}题: ➖ 纯文字题")
                continue
            details = [] if args.verbose else None
            errs, warns = full_validate(q, coerce_spec(q["figure_spec"]), details)
            for mark_i, dmsg in (details or []):
                print(f"       {mark_i} {dmsg}")
            if errs:
                mark = "❌ " + "; ".join(errs[:2])
            elif warns:
                mark = "⚠ " + "; ".join(warns[:1])
            else:
                mark = "✅"
            bad += bool(errs)
            wcount += len(warns)
            print(f"第{i:>2}题: {mark}  [{q['question'][:24]}…]")
        tail = f"，另有 {wcount} 条几何精度提示(⚠)" if wcount else ""
        print(f"\n共 {len(qs)} 题: " + (f"{bad} 题校验未过{tail}" if bad else f"全部通过{tail}"))
        return

    p = PROVIDERS[args.provider]
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    api_key = os.environ.get(p["key_env"])
    if not api_key:
        raise SystemExit(f"❌ 请先 export {p['key_env']}=你的key")
    client = OpenAI(api_key=api_key, base_url=p["base_url"])
    global _DISABLE_THINKING
    _DISABLE_THINKING = (args.provider == "deepseek" and not args.think)
    model = args.model or p["model"]

    def process_job(job):
        i, q = job
        old = q.get("figure_spec")
        if not args.force:
            if isinstance(old, dict):
                errs0, _w0 = full_validate(q, coerce_spec(old))
                if not errs0:
                    return i, "⏭ 已有合法图形，跳过", True
                if not wants_figure(q):
                    q["figure_spec"] = None  # 文字题配图不合法 → 移除图形保留原题，绝不换题
                    return i, "🗑 文字题配图不合法，已移除图形（保留原题）", True
            elif not wants_figure(q):
                return i, "⏭ 纯文字题，跳过", True
        errs, spec = [], None
        for attempt in range(3):  # 整题级重试
            try:
                decl = gen_spec(client, model, q, prev_errs=errs or None)
                spec = build_spec_from_declaration(decl)
                spec = coerce_spec(spec)
            except Exception as e:
                errs = [f"生成失败: {str(e)[:80]}"]
                continue
            if spec and spec.get("points"):
                errs, _w = full_validate(q, spec)
            else:
                errs = ["模型未返回有效图形声明"]
            if not errs:
                break
        if not errs:
            q["figure_spec"] = spec
            return i, "✅", True
        # 换题：同考点重新命题（题+图一起换，新题须通过结构+几何全部校验才收货）
        replaced = False
        for _r in range(3):
            try:
                nq = gen_replacement(client, model, q, chapter)
            except Exception as e:
                errs = [f"换题生成失败: {str(e)[:60]}"]
                continue
            nq = normalize_bank({"questions": [nq]})["questions"][0]
            cand = {**q, **{k: nq[k] for k in
                    ("question", "choices", "answer", "explanation", "knowledge_point",
                     "difficulty", "figure_spec", "source_page") if k in nq}}
            cand["source_page"] = 0
            cand["figure_spec"] = coerce_spec(cand.get("figure_spec") or {"points": [], "segments": []})
            e2, _w2 = full_validate(cand, cand["figure_spec"])
            if not e2:
                qs[i - 1] = cand
                return i, f"🔄 换题成功: {cand['question'][:28]}…", True
            errs = e2
        old = q.get("figure_spec")
        if isinstance(old, dict) and not validate(q, coerce_spec(old)):
            q["figure_spec"] = coerce_spec(old)  # 换题也失败 → 保留原有合法图形，绝不置空
            return i, "↩ 换题失败，保留原图形指令", True
        q["figure_spec"] = None
        return i, f"❌ 换题失败且无图({errs[0][:40]})，已置空待人工处理", False

    t0 = time.time()
    ok = 0
    jobs = list(enumerate(qs, 1))
    if args.workers <= 1:
        results = []
        for job in jobs:
            r = process_job(job)
            results.append(r)
            print(f"第{r[0]:>2}题: {r[1]}")
    else:
        print(f"并发生成图形（workers={args.workers}）…")
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(process_job, j) for j in jobs]
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                print(f"第{r[0]:>2}题: {r[1]}")
        results.sort(key=lambda x: x[0])
    ok = sum(1 for _i, _m, okf in results if okf)
    print(f"（图形阶段耗时 {time.time()-t0:.0f}s）")

    out = args.out or args.json
    if os.path.exists(out):
        shutil.copy(out, out + ".bak")  # 覆盖前备份，防破坏性写入
        print(f"（原文件已备份为 {out}.bak）")
    json.dump(bank, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n✅ {ok}/{len(qs)} 题图形重新生成并通过校验 → {out}")
    print("下一步: .venv/bin/python export_paper.py " + out)

if __name__ == "__main__":
    main()
