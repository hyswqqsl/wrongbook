# -*- coding: utf-8 -*-
"""
几何数值校验：从题干文字提取几何约束，数值验证 figure_spec 是否满足
支持约束：中点 / 点在线上 / 等长 / 比值 / 角度值 / 等角 / 垂直 / 平行 / 线段交点
check(q, spec, details=None) → (errors, warnings)
  若传入 details 列表，将逐条写入验证明细字符串（✓合格 ⚠临界 ✗不合格 – 跳过），
  供人工审计："期望 vs 实测"即证据链
"""
import re, math

# 阈值：坐标尺度约 0~10，模型坐标精度有限，误差带宁宽勿严（误报会引发无谓重生成）
ERR_T = dict(seg=1.2, ang=18.0, ang_eq=15.0, len_rel=0.25, ratio_rel=0.30, perp=18.0, par=18.0, inter=1.5, bis=4.0)
WARN_T = dict(seg=0.6, ang=8.0, ang_eq=7.0, len_rel=0.12, ratio_rel=0.15, perp=8.0, par=8.0, inter=0.75, bis=2.0)

def _pts(spec):
    return {str(p["name"]): (float(p["x"]), float(p["y"])) for p in spec.get("points", [])
            if isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float))}

def _L(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])

def _ang(pts, v, u, w):
    """∠u-v-w（顶点 v），点缺失/退化返回 None"""
    if not all(k in pts for k in (v, u, w)):
        return None
    v1 = (pts[u][0]-pts[v][0], pts[u][1]-pts[v][1])
    v2 = (pts[w][0]-pts[v][0], pts[w][1]-pts[v][1])
    d1, d2 = math.hypot(*v1), math.hypot(*v2)
    if d1 < 1e-9 or d2 < 1e-9:
        return None
    cos = (v1[0]*v2[0] + v1[1]*v2[1]) / (d1*d2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))

def _seg_dev(p, a, b):
    ax, ay = b[0]-a[0], b[1]-a[1]
    L2 = ax*ax + ay*ay
    if L2 < 1e-12:
        return math.hypot(p[0]-a[0], p[1]-a[1]), 0.0
    t = ((p[0]-a[0])*ax + (p[1]-a[1])*ay) / L2
    cx, cy = a[0]+t*ax, a[1]+t*ay
    return math.hypot(p[0]-cx, p[1]-cy), t

def _line_inter(p1, p2, p3, p4):
    den = (p1[0]-p2[0])*(p3[1]-p4[1]) - (p1[1]-p2[1])*(p3[0]-p4[0])
    if abs(den) < 1e-12:
        return None
    t = ((p1[0]-p3[0])*(p3[1]-p4[1]) - (p1[1]-p3[1])*(p3[0]-p4[0])) / den
    return (p1[0] + t*(p2[0]-p1[0]), p1[1] + t*(p2[1]-p1[1]))

def check(q, spec, details=None):
    """返回 (errors, warnings)；details 传入列表时逐条记录验证明细"""
    pts = _pts(spec)
    # 关键：去除全部空白——模型写的题干是"交 AD 于点 F"带空格式，
    # 不归一化会导致所有约束正则静默失效（本系统最大的隐性漏洞）
    # 只从题干提取约束；选项含故意设错的干扰项，不可作为图形依据。
    # 判断型题目（"对于以下结论：①…②…"）列出的是待判断命题，不是已知条件，需剔除。
    text = re.sub(r"\s+", "", q["question"])
    text = re.split(r"对于以下(?:三个)?结论|下列结论(?:中|的是)", text)[0]
    errs, warns = [], []
    def note(ok, msg):
        if details is not None:
            details.append(("✓" if ok else "✗", msg))
    def warn_note(msg):
        if details is not None:
            details.append(("⚠", msg))
    need = lambda *ns: all(n in pts for n in ns)
    def add(blocking, msg):
        (errs if blocking else warns).append(msg)

    def on_seg(x, y, z, soft=False):
        if not need(x, y, z):
            return note(None, f"– {x}在{y}{z}上：跳过（图中缺点名）")
        dev, t = _seg_dev(pts[x], pts[y], pts[z])
        tol = max(0.35, 0.06 * _L(pts[y], pts[z]))
        note(dev <= tol, f"{x}在线段{y}{z}上：偏离{dev:.2f}（≤{tol:.2f}）")
        if dev > tol:
            add(True, f"{x}应在线段{y}{z}上，偏离{dev:.2f}（容忍{tol:.2f}）")
        elif dev > tol / 2:
            add(False, f"{x}略偏离{y}{z}（{dev:.2f}）")
        if not soft and not (-0.05 <= t <= 1.05):
            add(True, f"{x}落在线段{y}{z}的延长线上（不在边内部），位置参数t={t:.2f}")

    # ---- 中点：X是YZ的中点 ----
    for m in re.finditer(r"([A-Z])[是为]([A-Z])([A-Z])的中点", text):
        x, y, z = m.groups()
        if not need(x, y, z):
            note(None, f"– 中点 {x}是{y}{z}中点：跳过（图中缺点名）")
            continue
        mx, my = (pts[y][0]+pts[z][0]) / 2, (pts[y][1]+pts[z][1]) / 2
        d = math.hypot(pts[x][0]-mx, pts[x][1]-my)
        tol = max(0.8, 0.08 * _L(pts[y], pts[z]))
        note(d <= tol, f"中点 {x}=({y}+{z})/2：偏差{d:.2f}（≤{tol:.2f}）")
        if d > tol:
            add(True, f"中点约束: {x}偏离{y}{z}中点{d:.2f}")
        elif d > tol / 2:
            add(False, f"中点约束: {x}略偏（{d:.2f}）")

    # ---- 垂直平分线（三重约束：垂足=中点 + 直线⊥线段 + 线上点到两端等距）----
    # 句式1: "AB的垂直平分线交AB于点E，交BC于点D"；句式2: "直线l/线段AD是BC的垂直平分线"
    # 判定型语境（"不能判定…是…垂直平分线"）是待判断命题，不是已知条件，跳过
    def _is_conditional(pos):
        return "判定" in text[max(0, pos - 6):pos] or "是否" in text[max(0, pos - 6):pos]

    def _pb_eq(p, a, b, tag):
        if not need(p, a, b):
            return note(None, f"– 垂直平分线 {tag}：跳过（图中缺点名）")
        l1, l2 = _L(pts[p], pts[a]), _L(pts[p], pts[b])
        rel = abs(l1 - l2) / max(l1, l2, 1e-9)
        note(rel <= ERR_T["len_rel"], f"垂直平分线 {tag}：{p}{a}={l1:.2f} vs {p}{b}={l2:.2f}（差{rel:.0%}）")
        if rel > ERR_T["len_rel"]:
            add(True, f"垂直平分线约束: {tag} {p}{a}={l1:.2f}≠{p}{b}={l2:.2f}（{p}应到{a}{b}等距）")
        elif rel > WARN_T["len_rel"]:
            add(False, f"垂直平分线约束: {tag} 差{rel:.0%}")

    def _pb_mid(x, y, z, tag):
        if not need(x, y, z):
            return note(None, f"– 垂直平分线 {tag}：跳过（图中缺点名）")
        mx, my = (pts[y][0] + pts[z][0]) / 2, (pts[y][1] + pts[z][1]) / 2
        d = math.hypot(pts[x][0] - mx, pts[x][1] - my)
        tol = max(0.8, 0.08 * _L(pts[y], pts[z]))
        note(d <= tol, f"垂直平分线 {tag}：{x}为{y}{z}垂足（中点），偏差{d:.2f}")
        if d > tol:
            add(True, f"垂直平分线约束: {tag} 垂足{x}偏离{y}{z}中点{d:.2f}")
        elif d > tol / 2:
            add(False, f"垂直平分线约束: {tag} 垂足略偏（{d:.2f}）")

    def _pb_perp(p1, p2, y, z, tag):
        if not need(p1, p2, y, z):
            return note(None, f"– 垂直平分线 {tag}：跳过（图中缺点名）")
        v1 = (pts[p2][0] - pts[p1][0], pts[p2][1] - pts[p1][1])
        v2 = (pts[z][0] - pts[y][0], pts[z][1] - pts[y][1])
        if math.hypot(*v1) < 1e-9 or math.hypot(*v2) < 1e-9:
            return
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
        dev = abs(90 - math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
        note(dev <= ERR_T["perp"], f"垂直平分线 {tag}：{p1}{p2}⊥{y}{z} 偏差{dev:.1f}°")
        if dev > ERR_T["perp"]:
            add(True, f"垂直平分线约束: {tag} {p1}{p2}⊥{y}{z} 偏差{dev:.0f}°")
        elif dev > WARN_T["perp"]:
            add(False, f"垂直平分线约束: {tag} 偏差{dev:.0f}°")

    for m in re.finditer(r"([A-Z])([A-Z])的垂直平分线", text):
        if _is_conditional(m.start()):
            continue
        y, z = m.group(1), m.group(2)
        tag = f"{y}{z}垂直平分线"
        win = text[m.end():m.end() + 60]
        feet, onpts = [], []
        for cm in re.finditer(r"交(?:线段|边|直线)?([A-Z])([A-Z])(?:于|在)点?([A-Z])", win):
            if {cm.group(1), cm.group(2)} == {y, z}:
                feet.append(cm.group(3))
            else:
                onpts.append(cm.group(3))
                _pb_eq(cm.group(3), y, z, tag + "(交点等距)")
        line_pts = feet + onpts
        if len(line_pts) >= 2:
            _pb_perp(line_pts[0], line_pts[1], y, z, tag)
        for f in feet:
            _pb_mid(f, y, z, tag)

    for m in re.finditer(r"(?:直线|线段)?([A-Za-z]{1,2})是(?:线段|边)?([A-Z])([A-Z])的垂直平分线", text):
        if _is_conditional(m.start()):
            continue
        ln, y, z = m.group(1), m.group(2), m.group(3)
        tag = f"{ln}是{y}{z}垂直平分线"
        if len(ln) == 2 and ln[0].isupper() and ln[1].isupper():
            # 双大写字母线名（如"直线AD是BC的垂直平分线"）：靠 近线段者为垂足
            def _dev_to(x):
                return _seg_dev(pts[x], pts[y], pts[z])[0] if need(x, y, z) else 9e9
            dp, dq = _dev_to(ln[0]), _dev_to(ln[1])
            foot, other = (ln[0], ln[1]) if dp <= dq else (ln[1], ln[0])
            _pb_mid(foot, y, z, tag)
            _pb_perp(ln[0], ln[1], y, z, tag)
            _pb_eq(other, y, z, tag + "(线上等距)")
        else:
            # 小写线名（如"直线l是线段AB的垂直平分线"）
            win = text[m.end():m.end() + 60]
            foot = None
            first_on = None
            for cm in re.finditer(r"交(?:线段|边|直线)?([A-Z])([A-Z])(?:于|在)点?([A-Z])", win):
                if {cm.group(1), cm.group(2)} == {y, z}:
                    foot = cm.group(3)
                    _pb_mid(foot, y, z, tag)
            for om in re.finditer(r"点([A-Z])在(?:直线|线段)?[A-Za-z]上", text):
                x = om.group(1)
                if x in (y, z):
                    continue
                _pb_eq(x, y, z, tag + "(线上等距)")
                if first_on is None:
                    first_on = x
            if foot and first_on:
                _pb_perp(foot, first_on, y, z, tag)

    # ---- 点在线上 ----
    for m in re.finditer(r"([A-Z])、([A-Z])分别在([A-Z])([A-Z])、([A-Z])([A-Z])上", text):
        on_seg(m.group(1), m.group(3), m.group(4))
        on_seg(m.group(2), m.group(5), m.group(6))
    for m in re.finditer(r"([A-Z])是([A-Z])([A-Z])上一点", text):
        on_seg(m.group(1), m.group(2), m.group(3))
    for m in re.finditer(r"([A-Z])在线段([A-Z])([A-Z])上", text):
        on_seg(m.group(1), m.group(2), m.group(3))
    for m in re.finditer(r"([A-Z])在([A-Z])([A-Z])边上", text):
        on_seg(m.group(1), m.group(2), m.group(3))
    for m in re.finditer(r"([A-Z])在直线([A-Z])([A-Z])上(?!方)", text):
        x, y, z = m.groups()
        if need(x, y, z):
            dev, _t = _seg_dev(pts[x], pts[y], pts[z])
            note(dev <= 1.2, f"{x}在直线{y}{z}上：偏离{dev:.2f}（≤1.20）")
            if dev > 1.2:
                add(True, f"{x}不在直线{y}{z}上（偏{dev:.2f}）")

    # ---- 交XX于X（延长线与边相交）：交AC于F ----
    for m in re.finditer(r"交([A-Z])([A-Z])于点?([A-Z])", text):
        on_seg(m.group(3), m.group(1), m.group(2))

    # ---- 共线：连接BE并延长交AC于F / 延长AD至E ----
    def collinear(p, q, r, label):
        if not need(p, q, r):
            return note(None, f"– 共线 {label}：跳过（图中缺点名）")
        dev = abs(_L(pts[p], pts[q]) + _L(pts[q], pts[r]) - _L(pts[p], pts[r]))
        tol = max(0.9, 0.10 * _L(pts[p], pts[r]))
        note(dev <= tol, f"共线 {label}：偏差{dev:.2f}（≤{tol:.2f}）")
        if dev > tol:
            add(True, f"共线约束: {label} 偏差{dev:.2f}")
        elif dev > tol / 2:
            add(False, f"共线约束: {label} 偏差{dev:.2f}")
    for m in re.finditer(r"连接([A-Z])([A-Z])并延长交([A-Z])([A-Z])于([A-Z])", text):
        g = m.groups()
        collinear(g[0], g[1], g[4], f"{g[0]}{g[1]}延长交{g[2]}{g[3]}于{g[4]}")
    for m in re.finditer(r"延长([A-Z])([A-Z])(?:至|到)([A-Z])", text):
        collinear(m.group(1), m.group(2), m.group(3), f"延长{m.group(1)}{m.group(2)}至{m.group(3)}")

    # ---- 角度：∠ABC=80° / ∠BAC=∠DAE=80° / ∠BAC=∠DAE ----
    used = []
    for m in re.finditer(r"∠([A-Z])([A-Z])([A-Z])=∠([A-Z])([A-Z])([A-Z])=(\d+)°", text):
        used.append(m.span())
        n = float(m.group(7))
        for tri in (m.group(1, 2, 3), m.group(4, 5, 6)):
            a = _ang(pts, tri[1], tri[0], tri[2])
            if a is None:
                note(None, f"– ∠{''.join(tri)}={n}°：跳过（图中缺点名）")
                continue
            dev = abs(a - n)
            note(dev <= ERR_T["ang"], f"∠{''.join(tri)}={n}°：实测{a:.1f}°（偏差{dev:.1f}°，≤{ERR_T['ang']:.0f}°）")
            if dev > ERR_T["ang"]:
                add(True, f"∠{''.join(tri)}应为{n}°，实测{a:.0f}°")
            elif dev > WARN_T["ang"]:
                add(False, f"∠{''.join(tri)}实测{a:.0f}°（应{n}°）")
    for m in re.finditer(r"∠([A-Z])([A-Z])([A-Z])=∠([A-Z])([A-Z])([A-Z])(?!=\d)", text):
        if any(s <= m.start() < e for s, e in used):
            continue
        used.append(m.span())
        a1 = _ang(pts, m.group(2), m.group(1), m.group(3))
        a2 = _ang(pts, m.group(5), m.group(4), m.group(6))
        if a1 is None or a2 is None:
            note(None, f"– 等角 ∠{''.join(m.group(1,2,3))}=∠{''.join(m.group(4,5,6))}：跳过（图中缺点名）")
            continue
        gap = abs(a1 - a2)
        note(gap <= ERR_T["ang_eq"], f"等角 ∠{''.join(m.group(1,2,3))}=∠{''.join(m.group(4,5,6))}：{a1:.1f}° vs {a2:.1f}°（差{gap:.1f}°）")
        if gap > ERR_T["ang_eq"]:
            add(True, f"等角约束: ∠{''.join(m.group(1,2,3))}={a1:.0f}° vs ∠{''.join(m.group(4,5,6))}={a2:.0f}°")
        elif gap > WARN_T["ang_eq"]:
            add(False, f"等角约束: {a1:.0f}° vs {a2:.0f}°")
    for m in re.finditer(r"∠([A-Z])([A-Z])([A-Z])=(\d+)°", text):
        if any(s <= m.start() < e for s, e in used):
            continue
        if m.start() > 0 and text[m.start()-1] in "+－+":  # ∠EOF+∠ECF=180° 的和式成员，单独跳过
            continue
        n = float(m.group(4))
        a = _ang(pts, m.group(2), m.group(1), m.group(3))
        if a is None:
            note(None, f"– ∠{''.join(m.group(1,2,3))}={n}°：跳过（图中缺点名）")
            continue
        dev = abs(a - n)
        note(dev <= ERR_T["ang"], f"∠{''.join(m.group(1,2,3))}={n}°：实测{a:.1f}°（偏差{dev:.1f}°）")
        if dev > ERR_T["ang"]:
            add(True, f"∠{''.join(m.group(1,2,3))}应为{n}°，实测{a:.0f}°")
        elif dev > WARN_T["ang"]:
            add(False, f"∠{''.join(m.group(1,2,3))}实测{a:.0f}°（应{n}°）")

    # ---- 和式：∠EOF+∠ECF=180° ----
    for m in re.finditer(r"∠([A-Z])([A-Z])([A-Z])\+∠([A-Z])([A-Z])([A-Z])=(\d+)°", text):
        a1 = _ang(pts, m.group(2), m.group(1), m.group(3))
        a2 = _ang(pts, m.group(5), m.group(4), m.group(6))
        n = float(m.group(7))
        if a1 is None or a2 is None:
            note(None, f"– 和式 ∠{''.join(m.group(1,2,3))}+∠{''.join(m.group(4,5,6))}={n}°：跳过（图中缺点名）")
            continue
        gap = abs((a1 + a2) - n)
        note(gap <= ERR_T["ang"], f"和式 ∠{''.join(m.group(1,2,3))}+∠{''.join(m.group(4,5,6))}={n}°：实测{a1:.1f}°+{a2:.1f}°")
        if gap > ERR_T["ang"]:
            add(True, f"和式约束: 实测{a1:.0f}°+{a2:.0f}°={a1+a2:.0f}° ≠ {n}°")
        elif gap > WARN_T["ang"]:
            add(False, f"和式约束: 和为{a1+a2:.0f}°（应{n}°）")

    # ---- 等长：AB=AC ----
    for m in re.finditer(r"([A-Z])([A-Z])=([A-Z])([A-Z])", text):
        p1, p2, p3, p4 = m.groups()
        if not need(p1, p2, p3, p4):
            note(None, f"– 等长 {p1}{p2}={p3}{p4}：跳过（图中缺点名）")
            continue
        l1, l2 = _L(pts[p1], pts[p2]), _L(pts[p3], pts[p4])
        rel = abs(l1 - l2) / max(l1, l2, 1e-9)
        note(rel <= ERR_T["len_rel"], f"等长 {p1}{p2}={p3}{p4}：{l1:.2f} vs {l2:.2f}（差{rel:.0%}）")
        if rel > ERR_T["len_rel"]:
            add(True, f"等长约束: {p1+p2}={l1:.1f} vs {p3+p4}={l2:.1f}")
        elif rel > WARN_T["len_rel"]:
            add(False, f"等长约束: {p1+p2}≈{p3+p4} 差{rel:.0%}")

    # ---- 比值：AE:EB=1:2 ----
    for m in re.finditer(r"([A-Z])([A-Z]):([A-Z])([A-Z])=(\d+):(\d+)", text):
        p1, p2, p3, p4, n1, n2 = m.groups()
        if not need(p1, p2, p3, p4):
            note(None, f"– 比值 {p1}{p2}:{p3}{p4}={n1}:{n2}：跳过（图中缺点名）")
            continue
        target = float(n1) / float(n2)
        actual = _L(pts[p1], pts[p2]) / max(_L(pts[p3], pts[p4]), 1e-9)
        rel = abs(actual - target) / target
        note(rel <= ERR_T["ratio_rel"], f"比值 {p1}{p2}:{p3}{p4}={n1}:{n2}：实测{actual:.2f}")
        if rel > ERR_T["ratio_rel"]:
            add(True, f"比值约束: {p1+p2}:{p3+p4} 应为{n1}:{n2}，实测{actual:.2f}")
        elif rel > WARN_T["ratio_rel"]:
            add(False, f"比值约束: 实测{actual:.2f}（应{n1}:{n2}）")

    # ---- 垂直：PM⊥OA ----
    for m in re.finditer(r"([A-Z])([A-Z])⊥([A-Z])([A-Z])", text):
        p = m.groups()
        if not need(*p):
            note(None, f"– 垂直 {m.group(0)}：跳过（图中缺点名）")
            continue
        v1 = (pts[p[1]][0]-pts[p[0]][0], pts[p[1]][1]-pts[p[0]][1])
        v2 = (pts[p[3]][0]-pts[p[2]][0], pts[p[3]][1]-pts[p[2]][1])
        if math.hypot(*v1) < 1e-9 or math.hypot(*v2) < 1e-9:
            continue
        cos = (v1[0]*v2[0] + v1[1]*v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
        dev = abs(90 - math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
        note(dev <= ERR_T["perp"], f"垂直 {p[0]+p[1]}⊥{p[2]+p[3]}：偏差{dev:.1f}°（≤{ERR_T['perp']:.0f}°）")
        if dev > ERR_T["perp"]:
            add(True, f"垂直约束: {p[0]+p[1]}⊥{p[2]+p[3]} 偏差{dev:.0f}°")
        elif dev > WARN_T["perp"]:
            add(False, f"垂直约束: 偏差{dev:.0f}°")

    # ---- 平行：PQ∥OB ----
    for m in re.finditer(r"([A-Z])([A-Z])∥([A-Z])([A-Z])", text):
        p = m.groups()
        if not need(*p):
            note(None, f"– 平行 {m.group(0)}：跳过（图中缺点名）")
            continue
        v1 = (pts[p[1]][0]-pts[p[0]][0], pts[p[1]][1]-pts[p[0]][1])
        v2 = (pts[p[3]][0]-pts[p[2]][0], pts[p[3]][1]-pts[p[2]][1])
        if math.hypot(*v1) < 1e-9 or math.hypot(*v2) < 1e-9:
            continue
        cos = abs(v1[0]*v2[0] + v1[1]*v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
        dev = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        note(dev <= ERR_T["par"], f"平行 {p[0]+p[1]}∥{p[2]+p[3]}：偏差{dev:.1f}°（≤{ERR_T['par']:.0f}°）")
        if dev > ERR_T["par"]:
            add(True, f"平行约束: {p[0]+p[1]}∥{p[2]+p[3]} 偏差{dev:.0f}°")
        elif dev > WARN_T["par"]:
            add(False, f"平行约束: 偏差{dev:.0f}°")

    # ---- 交点：BD、CE交于点O / BD、CE相交于点O / BD与CE交于点O / 直线AD与BC相交于点O ----
    inter_cons = []
    for m in re.finditer(r"([A-Z])([A-Z])[、,，与和]([A-Z])([A-Z])[，,]?(?:直线)?相?交(?:于|在)?点?([A-Z])", text):
        inter_cons.append((m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)))
    for m in re.finditer(r"直线([A-Z])([A-Z])与([A-Z])([A-Z])(?:直线)?相交于点([A-Z])", text):
        inter_cons.append((m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)))
    seen_inter = set()
    for s1, e1, s2, e2, o in inter_cons:
        key = tuple(sorted([(s1, e1), (s2, e2)])) + (o,)
        if key in seen_inter:
            continue
        seen_inter.add(key)
        if not need(s1, e1, s2, e2, o):
            note(None, f"– 交点 {s1}{e1}∩{s2}{e2}={o}：跳过（图中缺点名）")
            continue
        X = _line_inter(pts[s1], pts[e1], pts[s2], pts[e2])
        if X is None:
            note(False, f"交点 {s1}{e1}∩{s2}{e2}：两线平行无交点")
            add(True, f"交点约束: {s1}{e1}∥{s2}{e2} 无交点，但题干说交于{o}")
            continue
        d = math.hypot(pts[o][0]-X[0], pts[o][1]-X[1])
        # 段内检查：真交点必须落在两条线段内部（延长线相交不算"相交于点O"）
        t1 = ((X[0]-pts[s1][0])*(pts[e1][0]-pts[s1][0]) + (X[1]-pts[s1][1])*(pts[e1][1]-pts[s1][1])) / max((pts[e1][0]-pts[s1][0])**2 + (pts[e1][1]-pts[s1][1])**2, 1e-12)
        t2 = ((X[0]-pts[s2][0])*(pts[e2][0]-pts[s2][0]) + (X[1]-pts[s2][1])*(pts[e2][1]-pts[s2][1])) / max((pts[e2][0]-pts[s2][0])**2 + (pts[e2][1]-pts[s2][1])**2, 1e-12)
        off = max(-min(t1, t2), max(t1, t2) - 1, 0.0)
        if off > 0.04:
            add(True, f"交点约束: {s1}{e1}∩{s2}{e2} 的交点在线段外(t={t1:.2f}/{t2:.2f})，题干说相交于{o}不成立")
            continue
        note(d <= ERR_T["inter"], f"交点 {o}={s1}{e1}∩{s2}{e2}：偏离{d:.2f}（≤{ERR_T['inter']:.2f}），段内 t={t1:.2f}/{t2:.2f}")
        if d > ERR_T["inter"]:
            add(True, f"交点约束: {o}偏离{s1}{e1}与{s2}{e2}交点{d:.2f}")
        elif d > WARN_T["inter"]:
            add(False, f"交点约束: {o}略偏离交点（{d:.2f}）")

    # ---- 角平分：OA平分∠BOE / 射线AF平分∠DAE ----
    for m in re.finditer(r"([A-Z])([A-Z])平分∠([A-Z])([A-Z])([A-Z])", text):
        p1, p2, a, v, b = m.groups()
        if v == p1:
            vx, rx = p1, p2      # 顶点=OA 的 O，射线过 A
        elif v == p2:
            vx, rx = p2, p1
        else:
            note(None, f"– 平分 {m.group(0)}：顶点{v}不在{p1}{p2}中，跳过")
            continue
        if not all(k in pts for k in (vx, rx, a, b)):
            note(None, f"– 平分 {m.group(0)}：跳过（图中缺点名）")
            continue
        ang1, ang2 = _ang(pts, vx, rx, a), _ang(pts, vx, rx, b)
        if ang1 is None or ang2 is None:
            continue
        dev = abs(ang1 - ang2)
        note(dev <= ERR_T["bis"], f"平分 {rx}{vx} 平分∠{a}{v}{b}：两半角 {ang1:.1f}°/{ang2:.1f}°")
        if dev > ERR_T["bis"]:
            add(True, f"角平分约束: {rx}{vx} 未平分∠{a}{v}{b}（两半角差 {dev:.1f}°）")
        elif dev > WARN_T["bis"]:
            add(False, f"角平分约束: 两半角差 {dev:.1f}°")

    return errs, warns
