# -*- coding: utf-8 -*-
"""wordexport —— 排版稿导出：题干/选项(LaTeX公式渲染PNG) + 抓图 + 答案 → .docx"""
import os, re, io
import matplotlib
matplotlib.use("Agg")
import matplotlib.mathtext as mathtext
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

_MATH_CACHE = {}
_PARSER = mathtext.MathTextParser("path")


def _render_math_png(tex, fontsize=14, dpi=300):
    """LaTeX 片段 → (png_bytes, w_px, h_px)；失败返回 None"""
    key = (tex, fontsize, dpi)
    if key in _MATH_CACHE:
        return _MATH_CACHE[key]
    try:
        from matplotlib.figure import Figure
        fig = Figure(figsize=(0.1, 0.1), dpi=dpi)
        fig.text(0, 0, "$" + tex + "$", fontsize=fontsize)
        buf = io.BytesIO()
        fig.savefig(buf, dpi=dpi, transparent=True, bbox_inches="tight", pad_inches=0.02)
        data = buf.getvalue()
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        out = (data, im.size[0], im.size[1])
    except Exception:
        out = None
    _MATH_CACHE[key] = out
    return out


def _add_inline(par, text, fontsize=12):
    """把含 $..$ 的文本写进段落：普通文字逐段、公式渲染 PNG 行内插入"""
    parts = re.split(r"(\$[^$]+\$)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            tex = part[1:-1]
            r = _render_math_png(tex, fontsize=fontsize)
            if r:
                data, w, h = r
                # 行内公式高度 ≈ 1.5 倍字号；用 EMU 控制显示尺寸
                from docx.shared import Emu
                disp_h = Emu(int(fontsize * 1.6 * 9525))
                disp_w = Emu(int(w * disp_h / h))
                run = par.add_run()
                run.add_picture(io.BytesIO(data), height=disp_h, width=disp_w)
            else:
                par.add_run(part)  # 渲染失败退化为源码展示
        else:
            par.add_run(part)


def export_sheet_docx(sheet, out_path):
    """草稿单 → Word 排版稿（人审对照用）"""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Songti SC"
    style.font.size = Pt(12)

    title = doc.add_heading("错题排版稿", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = meta.add_run("来源：{0} ｜ 导入：{1} ｜ 共 {2} 题".format(
        sheet.get("source") or "（未标注）", sheet.get("created_at", ""), len(sheet["questions"])))
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    for q in sheet["questions"]:
        p = doc.add_paragraph()
        head = p.add_run("第{0}题（{1}）".format(q.get("no"), {"single": "单选", "multiple": "多选",
                                                              "fill": "填空", "short": "问答"}.get(q.get("type"), q.get("type"))))
        head.bold = True
        text = (q.get("edited") or {}).get("question") if isinstance(q.get("edited"), dict) else None
        _add_inline(p, text or q.get("question") or "", fontsize=12)

        for c in (q.get("choices") or []):
            cp = doc.add_paragraph()
            cp.paragraph_format.left_indent = Cm(0.6)
            _add_inline(cp, "{0}. {1}".format(c.get("key"), c.get("text", "")), fontsize=11)

        # 图形：SVG 优先（spec → SVG → PNG 嵌入）；无 SVG 则嵌入原题照片裁剪图（兜底）
        spec = q.get("figure_spec")
        fig_img = None
        if isinstance(spec, dict) and spec.get("points"):
            try:
                import figureflow, pymupdf
                svg = figureflow.render(spec)
                pdf_doc = pymupdf.open("svg", svg.encode())
                pix = pdf_doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                fpath = out_path + f"_fig{q.get('no')}.png"
                pix.save(fpath)
                fig_img = fpath
            except Exception:
                fig_img = None
        if fig_img is None:
            crops = q.get("figure_crops") or []
            if crops and os.path.exists(crops[0]):
                fig_img = crops[0]
        if fig_img:
            try:
                ip = doc.add_paragraph()
                ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
                ip.add_run().add_picture(fig_img, width=Cm(7.0))
            except Exception:
                pass
            finally:
                if fig_img.endswith(".png") and fig_img.startswith(out_path):
                    try:
                        os.remove(fig_img)
                    except OSError:
                        pass

        if q.get("printed_answer"):
            ap = doc.add_paragraph()
            ar = ap.add_run("（照片答案：{0}）".format(q["printed_answer"]))
            ar.font.size = Pt(9)
            ar.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        if q.get("has_handwriting"):
            hp = doc.add_paragraph()
            hr = hp.add_run("（有手写痕迹）")
            hr.font.size = Pt(9)
            hr.font.color.rgb = RGBColor(0xAA, 0x66, 0x00)
        doc.add_paragraph()

    doc.save(out_path)
    return out_path
