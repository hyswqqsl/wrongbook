# -*- coding: utf-8 -*-
"""
geoconstruct —— 几何构造求解器（懒人学霸式 GCS 的轻量实现）

设计原则（对应懒人学霸技术分析）：
1. 构造式闭式解：模型只声明「模板 + 参数」，坐标由尺规公理（SAS/SSS/极坐标/交点公式）
   解析计算，微秒级精确浮点——杜绝"直角变钝角、平行线相交"。
2. 纯矢量输出：render_svg 直接生成 SVG 矢量图形（无栅格化），
   经无头浏览器打印 PDF 时为原生矢量路径，无限放大无锯齿。
3. 标签动态避让：8 方向 × 多距离候选位，对已放置标签与所有线段做碰撞检测。

模板注册表（模型按名字选用）：
  collinear_equilateral  共线双等边（B、A、D 共线，BE=CD 经典）
  shoulashou_cross       手拉手旋转（共顶点，BD∩CE=O，OA 平分角）
  shoulashou_fan         手拉手张开（两三角形同侧张开）
  iso_right_pair         双等腰直角（共顶点 90°）
  banjiao120             等边+120°外点+半角模型
  mid_double             倍长中线（D 中点，DF=DE 延长）
  yixian_right           一线三等角（直角型）
"""
import math

R3 = math.sqrt(3)

# ---------- 尺规公理（闭式解原语） ----------

def polar(A, deg, L):
    """极坐标：从 A 沿 deg 方向取长 L（数学系角度，逆时针，x 轴正向为 0°）"""
    r = math.radians(deg)
    return (A[0] + L*math.cos(r), A[1] + L*math.sin(r))

def rot_about(P, deg, X):
    """点 X 绕 P 旋转 deg 度（闭式）"""
    r = math.radians(deg)
    dx, dy = X[0]-P[0], X[1]-P[1]
    return (P[0] + dx*math.cos(r) - dy*math.sin(r),
            P[1] + dx*math.sin(r) + dy*math.cos(r))

def inter_lines(P1, P2, P3, P4):
    """直线 P1P2 ∩ 直线 P3P4"""
    den = (P1[0]-P2[0])*(P3[1]-P4[1]) - (P1[1]-P2[1])*(P3[0]-P4[0])
    if abs(den) < 1e-12:
        return None
    t = ((P1[0]-P3[0])*(P3[1]-P4[1]) - (P1[1]-P3[1])*(P3[0]-P4[0])) / den
    return (P1[0]+t*(P2[0]-P1[0]), P1[1]+t*(P2[1]-P1[1]))

def inter_line_circle(A, B, C, r):
    """直线 AB 与圆(C,r) 的两个交点（解析闭式）"""
    dx, dy = B[0]-A[0], B[1]-A[1]
    fx, fy = A[0]-C[0], A[1]-C[1]
    a = dx*dx + dy*dy
    b = 2*(fx*dx + fy*dy)
    c = fx*fx + fy*fy - r*r
    disc = b*b - 4*a*c
    if disc < 0 or a < 1e-12:
        return None
    sq = math.sqrt(disc)
    t1, t2 = (-b-sq)/(2*a), (-b+sq)/(2*a)
    return ((A[0]+t1*dx, A[1]+t1*dy), (A[0]+t2*dx, A[1]+t2*dy))

def on_ray(A, u, L):
    return (A[0]+L*u[0], A[1]+L*u[1])

def unit(v):
    n = math.hypot(*v)
    return (v[0]/n, v[1]/n) if n > 1e-12 else (0.0, 0.0)

# ---------- 模板库（构造式闭式解） ----------

def t_collinear_equilateral(ab=2.0, ad=4.0):
    """B、A、D 共线双等边：B(0,0), A(ab,0), D(ab+ad,0)；C/E 在上方"""
    A, B, D = (ab, 0.0), (0.0, 0.0), (ab+ad, 0.0)
    C = (ab/2, ab*R3/2)
    E = (ab+ad/2, ad*R3/2)
    O = inter_lines(B, E, C, D)          # 经典：BD=CE，交于 O
    return ({"A":A,"B":B,"C":C,"D":D,"E":E,"F":O},
            [("A","B",False),("B","C",False),("C","A",False),
             ("A","D",False),("D","E",False),("E","A",False),
             ("B","E",False),("C","D",False)],
            {"template": "collinear_equilateral", "ab": ab, "ad": ad})

def t_shoulashou_cross(apex=60.0, L1=5.0, L2=None, gamma=30.0):
    """手拉手旋转·交叉构型（BD、CE 相交于 O，可证 OA 平分∠BOE）：
    △ADE 的两翼从 C 方向起再往外张 gamma 度——D 越过 C 方向（gamma>0）
    是 BD 与 CE 在段内相交的充要条件；坐标全部闭式计算。
    内建自检：交点必须在两段内部 + OA 平分 ∠BOE，违反则抛异常（绝不输出坏图）"""
    L2 = L1 if L2 is None else L2
    half = apex / 2.0
    A = (2.5, 7.0)
    B = polar(A, 270 - half, L1)
    C = polar(A, 270 + half, L1)
    D = polar(A, 270 + half + gamma, L2)
    E = polar(A, 270 + half + gamma + apex, L2)
    O = inter_lines(B, D, C, E)
    if O is None:
        raise ValueError("BD∥CE 无交点，gamma 参数非法")
    # 自检 1：真交点落在两线段内部
    def _t(P, Q):
        dx, dy = Q[0]-P[0], Q[1]-P[1]
        return ((O[0]-P[0])*dx + (O[1]-P[1])*dy) / (dx*dx + dy*dy)
    t1, t2 = _t(B, D), _t(C, E)
    if not (0.02 <= t1 <= 0.98 and 0.02 <= t2 <= 0.98):
        raise ValueError(f"交点不在段内(t={t1:.2f}/{t2:.2f})，gamma={gamma} 需调大")
    # 自检 2：OA 平分 ∠BOE（模板的数学保证，数值验证）
    def _ang(u):
        return math.atan2(u[1]-O[1], u[0]-O[0])
    a1 = abs(_ang(A) - _ang(B))
    a2 = abs(_ang(A) - _ang(E))
    a1 = min(a1, 2*math.pi - a1)
    a2 = min(a2, 2*math.pi - a2)
    if math.degrees(abs(a1 - a2)) > 0.5:
        raise ValueError(f"OA 不平分∠BOE（差 {math.degrees(abs(a1-a2)):.1f}°），参数非法")
    pts = {"A": A, "B": B, "C": C, "D": D, "E": E, "O": O}
    segs = [("A", "B", False), ("B", "C", False), ("C", "A", False),
            ("A", "D", False), ("D", "E", False), ("E", "A", False),
            ("B", "D", False), ("C", "E", False), ("A", "O", True)]
    return (pts, segs, {"template": "shoulashou_cross", "apex": apex,
                        "L1": L1, "L2": L2, "gamma": gamma})

def t_shoulashou_fan(apex=60.0, L1=5.0, L2=6.0, phi=25.0):
    """手拉手张开：△ADE 张角与 △ABC 相同、向外张开 phi"""
    half = apex/2
    base = 270.0
    A = (2.5, 7.0)
    B = polar(A, base-half, L1)
    C = polar(A, base+half, L1)
    D = polar(A, base-half+phi, L2)
    E = polar(A, base+half+phi, L2)
    O = inter_lines(B, D, C, E)
    pts = {"A":A,"B":B,"C":C,"D":D,"E":E}
    segs = [("A","B",False),("B","C",False),("C","A",False),
            ("A","D",False),("D","E",False),("E","A",False),
            ("B","D",False),("C","E",False)]
    if O and 0.05 < _t_of(B, D, O) < 0.95 and 0.05 < _t_of(C, E, O) < 0.95:
        pts["O"] = O
        segs.append(("A","O",True))
    return (pts, segs, {"template": "shoulashou_fan", "apex": apex, "L1": L1, "L2": L2, "phi": phi})

def _t_of(P1, P2, X):
    dx, dy = P2[0]-P1[0], P2[1]-P1[1]
    L2 = dx*dx + dy*dy
    if L2 < 1e-12:
        return 0.0
    return ((X[0]-P1[0])*dx + (X[1]-P1[1])*dy) / L2

def t_banjiao120(s=12.0, m=2.0, n=3.0):
    """等边 △ABC(边 s) + D 外点(∠BDC=120°, BD=CD) + E∈AB(BE=m) + F∈AC(CF=n)"""
    A, B, C = (s/2, s*R3/2), (0.0, 0.0), (s, 0.0)
    D = (s/2, -s*R3/6)
    E = (B[0] + m/s*(A[0]-B[0]), B[1] + m/s*(A[1]-B[1]))
    F = (C[0] + n/s*(A[0]-C[0]), C[1] + n/s*(A[1]-C[1]))
    return ({"A":A,"B":B,"C":C,"D":D,"E":E,"F":F},
            [("A","B",False),("B","C",False),("C","A",False),
             ("B","D",False),("C","D",False),
             ("D","E",False),("D","F",False)],
            {"template": "banjiao120", "s": s, "m": m, "n": n})

def t_mid_double(A=(1.0, 4.0), bx=-2.0, cx=6.0, t_E=0.5):
    """倍长中线：D 为 BC 中点，E 在 AD 上(t_E)，F = 2D−E（DF=DE 延长）"""
    B, C = (bx, 0.0), (cx, 0.0)
    D = ((B[0]+C[0])/2, 0.0)
    E = (A[0] + t_E*(D[0]-A[0]), A[1] + t_E*(D[1]-A[1]))
    F = (2*D[0]-E[0], 2*D[1]-E[1])
    return ({"A":A,"B":B,"C":C,"D":D,"E":E,"F":F},
            [("A","B",False),("B","C",False),("C","A",False),
             ("A","F",False),("E","F",False),
             ("B","F",False),("C","F",False)],
            {"template": "mid_double", "t_E": t_E})

def t_iso_right_pair(leg1=5.0, leg2=4.0, rot=45.0):
    """双等腰直角（共顶点 90°，△ADE 旋转 rot）"""
    A = (2.5, 6.5)
    B = polar(A, 200.0, leg1)
    C = polar(A, 290.0, leg1)
    D = polar(A, 200.0+rot, leg2)
    E = polar(A, 290.0+rot, leg2)
    return ({"A":A,"B":B,"C":C,"D":D,"E":E},
            [("A","B",False),("B","C",False),("C","A",False),
             ("A","D",False),("D","E",False),("E","A",False)],
            {"template": "iso_right_pair", "leg1": leg1, "leg2": leg2, "rot": rot})

def t_yixian_right(b=0.0, m=2.0, c=6.0, h=3.0):
    """一线三等角（直角型）：B(0,0), M(m,0), C(c,0), A(0,h), P(m,h)"""
    A, B, M, C, P = (0.0, h), (b, 0.0), (m, 0.0), (c, 0.0), (m, h)
    return ({"A":A,"B":B,"C":C,"M":M,"P":P},
            [("A","B",False),("B","M",False),("M","C",False),
             ("A","P",False),("P","M",False),("A","P",False)],
            {"template": "yixian_right", "m": m, "c": c, "h": h})

TEMPLATES = {
    "collinear_equilateral": t_collinear_equilateral,
    "shoulashou_cross":      t_shoulashou_cross,
    "shoulashou_fan":        t_shoulashou_fan,
    "banjiao120":            t_banjiao120,
    "mid_double":            t_mid_double,
    "iso_right_pair":        t_iso_right_pair,
    "yixian_right":          t_yixian_right,
}

def build(template, params):
    """模板分发：参数 JSON → 精确坐标 (points, segments, meta)"""
    fn = TEMPLATES.get(template)
    if fn is None:
        raise KeyError(f"未知模板: {template}（可选: {', '.join(TEMPLATES)}）")
    return fn(**(params or {}))

# ---------- SVG 矢量渲染（含标签动态避让） ----------

def _dist_seg(px, py, ax, ay, bx, by):
    dx, dy = bx-ax, by-ay
    L2 = dx*dx + dy*dy
    if L2 < 1e-12:
        return math.hypot(px-ax, py-ay)
    t = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy)/L2))
    return math.hypot(px-(ax+t*dx), py-(ay+t*dy))

def _rect_overlap(r1, r2):
    return not (r1[2] < r2[0] or r2[2] < r1[0] or r1[3] < r2[1] or r2[3] < r1[1])

def render_svg(points, segments, out_path=None, width=520, height=420,
               font_size=15, margin=38, stroke="#1a1a1a", accent="#1a1a1a"):
    """纯矢量 SVG 渲染：数学系坐标 → 屏幕系（Y 翻转 + viewBox 等比缩放）+ 标签动态避让"""
    if not points:
        return ""
    xs = [p[0] for p in points.values()]
    ys = [p[1] for p in points.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span_x = max(maxx-minx, 1e-6)
    span_y = max(maxy-miny, 1e-6)
    pad_x = max(span_x*0.18, 0.8)
    pad_y = max(span_y*0.18, 0.8)
    minx -= pad_x; maxx += pad_x; miny -= pad_y; maxy += pad_y
    span_x = maxx-minx; span_y = maxy-miny
    scale = min((width-2*margin)/span_x, (height-2*margin)/span_y)
    tx = (width - span_x*scale)/2 - minx*scale
    ty = margin + maxy*scale
    def to_screen(p):
        return (p[0]*scale + tx, -p[1]*scale + ty)
    seg_screen = []
    for a, b, dashed in segments:
        if a in points and b in points:
            seg_screen.append((to_screen(points[a]), to_screen(points[b]), dashed))
    # 标签避让：8 方向 × 两档距离，避开已放置标签与所有线段
    placed = []
    labels = []
    order = sorted(points.items(), key=lambda kv: -_centroid_bias(kv[1], points))
    for name, P in order:
        sx, sy = to_screen(P)
        w = font_size * 0.66
        h = font_size * 1.15
        placed_rect = None
        for dist_i in (0.32, 0.52):
            for k in range(8):
                r = math.radians(45*k + 22)
                ox, oy = math.cos(r)*font_size*dist_i, math.sin(r)*font_size*dist_i
                rect = (sx+ox-w/2, sy+oy-h/2, sx+ox+w/2, sy+oy+h/2)
                if rect[0] < 3 or rect[1] < 3 or rect[2] > width - 3 or rect[3] > height - 3:
                    continue  # 标签不能裁切在视窗外
                if any(_rect_overlap(rect, pr) for pr in placed):
                    continue
                cx_, cy_ = rect[0]+w/2, rect[1]+h/2
                if any(_dist_seg(cx_, cy_, a2[0], a2[1], b2[0], b2[1]) < font_size*0.42
                       for a2, b2, _d in seg_screen):
                    continue
                placed_rect = rect
                labels.append((name, sx+ox, sy+oy))
                break
            if placed_rect:
                break
        if not placed_rect:
            # 兜底：钳制在视窗内（贴边点标签内收）
            lx = min(max(sx + font_size*0.4, w/2 + 3), width - w/2 - 3)
            ly = min(max(sy - font_size*0.4, h/2 + 3), height - h/2 - 3)
            labels.append((name, lx, ly))
        if placed_rect:
            placed.append(placed_rect)
    # 组装 SVG
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="{width}" height="{height}" font-family="STIX Two Text, Times New Roman, serif">']
    parts.append(f'<rect width="{width}" height="{height}" fill="white"/>')
    for (a, b), (c, d), dashed in seg_screen:
        dash = ' stroke-dasharray="6,4"' if dashed else ''
        parts.append(f'<line x1="{a:.2f}" y1="{b:.2f}" x2="{c:.2f}" y2="{d:.2f}" '
                     f'stroke="{stroke}" stroke-width="1.7" stroke-linecap="round"{dash}/>')
    for name, P in points.items():
        sx, sy = to_screen(P)
        parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="2.3" fill="{accent}"/>')
    for name, lx, ly in labels:
        parts.append(f'<text x="{lx:.2f}" y="{ly:.2f}" font-size="{font_size}" '
                     f'font-style="italic" fill="#111">{name}</text>')
    parts.append('</svg>')
    svg = "\n".join(parts)
    if out_path:
        open(out_path, "w", encoding="utf-8").write(svg)
    return svg

def _centroid_bias(P, points):
    """离质心越远的点越优先放置标签（四周点先占好位置）"""
    cx = sum(p[0] for p in points.values())/len(points)
    cy = sum(p[1] for p in points.values())/len(points)
    return math.hypot(P[0]-cx, P[1]-cy)
