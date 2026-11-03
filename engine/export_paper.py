# -*- coding: utf-8 -*-
"""
试卷导出：questions.json → paper.md / paper.html / paper.pdf（含教材原图形）
图形来源三选一：
  1. 原创题（figure_spec 绘图指令）→ matplotlib 自动绘制，无需 --pdf
  2. 教材原题（source_page+figure_box）→ 从原 PDF 高清裁剪，需 --pdf
  3. 都没有 → 纯文字题
用法:
  .venv/bin/python export_paper.py questions.json --pdf 几何模型.pdf --pages 1-8
  .venv/bin/python export_paper.py original_questions.json --title "几何模型变式卷"
  .venv/bin/python export_paper.py questions.json --pdf 教材.pdf --no-answer
依赖: pip install pymupdf markdown matplotlib（Chrome 用于渲染 PDF，缺失时只出 md/html，可在浏览器手动打印）
"""
import os, sys, re, json, argparse, subprocess, glob, shutil
import html as html_mod
try:
    import pymupdf as fitz  # 新版 PyMuPDF
except ImportError:
    import fitz  # 旧版兼容
import markdown
import geoconstruct

TYPE_CN = {"single": "single", "multiple": "multiple", "fill": "fill", "short": "short"}
SECTION = [("single", "一、单项选择题"), ("multiple", "二、多项选择题"),
           ("fill", "三、填空题"), ("short", "四、解答题")]
POINTS = {"single": 3, "multiple": 4, "fill": 4, "short": 10}
DIFF_CN = {"easy": "易", "medium": "中", "hard": "难"}

def find_chrome():
    for pat in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/usr/bin/chromium", "/usr/bin/google-chrome"):
        if os.path.exists(pat):
            return pat
    return None

def md_escape(s):
    return re.sub(r"([*_\[\]`#])", r"\\\1", str(s))

def render_figure(pdf_path, pages_spec, q, idx, fig_dir):
    """按 source_page + figure_box 从原 PDF 裁剪图形，返回 md 内引用的相对路径或 None"""
    sp = q.get("source_page") or 0
    fb = q.get("figure_box")
    if not sp:
        return None
    doc = fitz.open(os.path.expanduser(pdf_path))
    pages = fitz_page_indices(pdf_path, pages_spec)  # 与出题时喂图的页序保持一致
    if sp > len(pages):
        return None
    page = doc[pages[sp - 1]]
    w, h = page.rect.width, page.rect.height
    if fb and all(0 <= v <= 1 for v in fb) and fb[2] - fb[0] > 0.05 and fb[3] - fb[1] > 0.03:
        # 比例框 → 实际坐标，外扩 1.5% 防裁边
        pad = 0.015
        clip = fitz.Rect(max(0, (fb[0]-pad)*w), max(0, (fb[1]-pad)*h),
                         min(1, (fb[2]+pad))*w, min(1, (fb[3]+pad))*h)
    else:  # 无有效框 → 整页兜底
        clip = page.rect
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip)  # 3x ≈216dpi 高清
    os.makedirs(fig_dir, exist_ok=True)
    path = os.path.join(fig_dir, f"q{idx}.png")
    pix.save(path)
    return path

def render_spec(spec, idx, fig_dir):
    """原创题：按模型给的绘图指令（点+线段）用 matplotlib 画图"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pts = {}
    for p in spec.get("points", []):
        try:
            pts[str(p["name"])] = (float(p["x"]), float(p["y"]))
        except (KeyError, TypeError, ValueError):
            continue
    fig, ax = plt.subplots(figsize=(3.2, 3.2), dpi=150)
    for s in spec.get("segments", []):
        a, b = pts.get(str(s.get("a"))), pts.get(str(s.get("b")))
        if a and b:
            ax.plot([a[0], b[0]], [a[1], b[1]], "k--" if s.get("dashed") else "k-",
                    lw=1.4, zorder=1)
    for name, (x, y) in pts.items():
        ax.plot(x, y, "k.", ms=4, zorder=2)
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(6, 4), fontsize=11)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.margins(0.18)
    os.makedirs(fig_dir, exist_ok=True)
    path = os.path.join(fig_dir, f"q{idx}.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path

def render_svg_spec(spec, idx, fig_dir, width=430, height=340):
    """纯矢量 SVG 渲染（全链路不失真核心）：返回 (svg全文, svg相对路径)"""
    os.makedirs(fig_dir, exist_ok=True)
    svg = geoconstruct.render_svg(
        {p["name"]: (float(p["x"]), float(p["y"])) for p in spec.get("points", []) if p.get("name")},
        [(s_.get("a"), s_.get("b"), bool(s_.get("dashed"))) for s_ in spec.get("segments", [])],
        out_path=None, width=width, height=height)
    path = os.path.join(fig_dir, f"q{idx}.svg")
    open(path, "w", encoding="utf-8").write(svg)
    return path, svg

_FITZ_CACHE = {}
def fitz_page_indices(pdf_path, pages_spec):
    key = (pdf_path, pages_spec)
    if key not in _FITZ_CACHE:
        with fitz.open(os.path.expanduser(pdf_path)) as d:
            _FITZ_CACHE[key] = parse_pages(pages_spec, len(d))
    return _FITZ_CACHE[key]

def parse_pages(spec, n_pages):
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

def ordered_questions(qs):
    """按试卷排版顺序（题型分组）重排，图形编号必须与它一致，否则图不对题"""
    out = []
    for key, _ in SECTION:
        out.extend([q for q in qs if q["type"] == key])
    return out

def build_md(bank, fig_of, title, with_answer):
    qs = bank["questions"]
    total = sum(POINTS[q["type"]] for q in qs)
    md = [f"# {title}\n", f"> 共 {len(qs)} 题 · 满分 {total} 分 · 来源题库: {bank.get('chapter','')}\n\n---\n"]
    answers = ["\n---\n\n## 参考答案与解析\n"]
    n = 0
    for key, label in SECTION:
        group = [q for q in qs if q["type"] == key]
        if not group:
            continue
        md.append(f"\n## {label}（每题 {POINTS[key]} 分）\n")
        for q in group:
            n += 1
            md.append(f"\n**{n}.**（{POINTS[key]}分）{md_escape(q['question'])}\n")
            fig = fig_of.get(n)
            if fig:
                md.append(f"\n![第{n}题图]({fig})\n")
            if q.get("choices"):
                md.append("\n" + "  \n".join(f"**{c['key']}.** {md_escape(c['text'])}" for c in q["choices"]) + "\n")
            answers.append(
                f"\n**{n}.** 【{q['answer']}】（{DIFF_CN.get(q['difficulty'],'')} · {md_escape(q['knowledge_point'])}）"
                f"{md_escape(q.get('analysis') or q.get('explanation') or '')}\n")
    if not with_answer:  # 默认答案区放卷末，家长/学生可裁掉
        pass
    if answers:  # 答案强制另起一页（不与题目混排）
        answers.insert(0, '\n<div style="page-break-before:always"></div>\n')
    return "\n".join(md) + "\n" + "".join(answers)

HTML_TMPL = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
@page { size: A4; margin: 16mm 15mm; }
body { font-family: "PingFang SC","Hiragino Sans GB",sans-serif; font-size: 10.5pt; line-height: 1.65; color:#111; }
h1 { text-align: center; font-size: 17pt; margin: 4px 0 8px; }
blockquote { color:#666; font-size: 9.5pt; border-left: 3px solid #ccc; margin: 6px 0; padding: 2px 10px; }
h2 { font-size: 12.5pt; border-bottom: 1.5px solid #333; padding-bottom: 3px; margin: 18px 0 10px; }
img { max-width: 42%; display: block; margin: 6px 0; border: .5px solid #ddd; border-radius: 3px; }
p { margin: 5px 0; }
</style></head><body>__BODY__</body></html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", help="题库 questions.json")
    ap.add_argument("--pdf", default=None, help="来源 PDF（教材原题裁剪图形用；纯原创题题库可不传）")
    ap.add_argument("--pages", default=None, help="与出题时一致的页码范围，如 1-8")
    ap.add_argument("--title", default=None, help="试卷标题（默认取题库 chapter）")
    ap.add_argument("--no-answer", action="store_true", help="不附参考答案（默认附在卷末）")
    ap.add_argument("--out-prefix", default="paper", help="输出文件前缀: paper.md/.html/.pdf")
    args = ap.parse_args()

    bank = json.load(open(args.json, encoding="utf-8"))
    bank["questions"] = ordered_questions(bank["questions"])  # 统一编号顺序：先重排再渲染，杜绝图不对题
    title = args.title or bank.get("chapter", "专项练习卷")

    fig_dir = f"{args.out_prefix}_figures"
    md_path = f"{args.out_prefix}.md"
    md_dir = os.path.dirname(os.path.abspath(md_path))
    fig_dir = f"{args.out_prefix}_figures"
    md_path = f"{args.out_prefix}.md"
    md_dir = os.path.dirname(os.path.abspath(md_path))
    fig_of, inline_svg, n = {}, {}, 0
    for q in bank["questions"]:
        n += 1
        try:
            if q.get("figure_spec"):            # 矢量优先：SVG 全链路不失真
                path, svg = render_svg_spec(q["figure_spec"], n, fig_dir)
                fig_of[n] = os.path.relpath(path, md_dir)
                inline_svg[n] = svg
            elif q.get("figure_image"):          # 题目自带成图（如错题抓图裁剪）
                if os.path.exists(q["figure_image"]):
                    os.makedirs(fig_dir, exist_ok=True)
                    ext = os.path.splitext(q["figure_image"])[1] or ".png"
                    path = os.path.join(fig_dir, f"q{n}{ext}")
                    shutil.copyfile(q["figure_image"], path)
                    fig_of[n] = os.path.relpath(path, md_dir)
            elif args.pdf:                       # 教材原题：从原 PDF 裁剪
                fig = render_figure(args.pdf, args.pages, q, n, fig_dir)
                if fig:
                    fig_of[n] = os.path.relpath(fig, md_dir)
        except Exception as e:
            print(f"⚠ 第{n}题图形处理失败（跳过）: {e}")

    md = build_md(bank, fig_of, title, with_answer=not args.no_answer)
    open(md_path, "w", encoding="utf-8").write(md)
    print(f"✅ Markdown: {md_path}（含 {len(fig_of)} 张教材图形，图片在 {fig_dir}/）")

    body = markdown.markdown(md, extensions=["extra"])
    # 矢量内联：把 md 生成的 <img ...qN.svg...> 整体替换为内联 <svg>（Chrome 打印 → 原生矢量 PDF）
    import re as _re
    for i in range(1, n + 1):
        svg_p = os.path.join(fig_dir, f"q{i}.svg")
        if not os.path.exists(svg_p):
            continue
        svg_text = open(svg_p, encoding="utf-8").read()
        svg_text = svg_text.replace('width="430"', 'width="42%"').replace('height="340"', '')
        pat = _re.compile(r'<img[^>]*q' + str(i) + r'\.svg[^>]*/?>')
        body, cnt = pat.subn(lambda _m: svg_text, body)
        if cnt == 0:
            print(f"⚠ 第{i}题 SVG 未内联（md 中无对应引用）")
    html_path = f"{args.out_prefix}.html"
    with open(html_path, "w", encoding="utf-8") as fp:
        fp.write(HTML_TMPL.replace("__BODY__", body))
    print(f"✅ HTML:     {html_path}")

    chrome = find_chrome()
    if chrome:
        pdf_path = f"{args.out_prefix}.pdf"
        r = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={os.path.abspath(pdf_path)}",
                            "file://" + os.path.abspath(html_path)],
                           capture_output=True, text=True, timeout=60)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            print(f"✅ PDF:      {pdf_path}")
        else:
            print(f"⚠ Chrome 渲染失败: {r.stderr[:150]}\n  可直接用浏览器打开 {html_path} → Cmd+P 存为 PDF")
    else:
        print(f"ℹ 未找到 Chrome/Edge，用浏览器打开 {html_path} 手动打印为 PDF 也可")

if __name__ == "__main__":
    main()
