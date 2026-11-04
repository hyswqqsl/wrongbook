# -*- coding: utf-8 -*-
"""reviewflow —— Word 排版机后端：照片 → 多题转录 → 图形来源分层 → 草稿单

图形策略（v0.6 定稿：原图为主 + SVG 按需）：
  - 转录时索取 figure_boxes → 按框裁剪原图存 data/crops/（权威原件，永远保留）
  - 【默认】所有配图题用原图裁剪（figure_mode="crop"）—— 向量化是全链路最费
    token 的一步（每题 1~3 次 LLM 调用 + geocheck 重试），转录期不再自动做
  - 重新生成SVG 按钮已从人审界面移除（v0.6 图形默认原图后无必要）；
    regen_figure 仅保留为 API 备用
  - 表格/函数图象等 SVG 画不出的题：只保留原图裁剪
  - 纯文字题：无图（figure_mode="none"）
"""
import os, re, json, io, base64
from PIL import Image, ImageOps

from common import make_client, CN_TYPE
from store import add_sheet
import store   # 路径经 store.PHOTO_DIR/store.CROP_DIR 动态取（随当前用户重绑）

TRANSCRIBE_PROMPT = """你是数学试卷数字化专家。图片是一张数学试卷/练习册的照片，其中可能有多道题（编号连续），有的做对了、有的做错了，可能还有手写痕迹。请把所有印刷体题目完整转录为 JSON。

转录规则：
1. 只转录印刷体：题干、选项、印刷的答案/解析摘要；手写内容（学生解答、涂改）不转录，只在 has_handwriting 标记该题有手写痕迹
2. 每道题一个对象，question 为题干全文，数学公式用 LaTeX（行内 $...$），如 $\\frac{3}{4}$、$\\sqrt{65}$、$\\angle BAC=90^\\circ$、$\\triangle ABC$
3. has_figure: 题干是否附带几何图形/函数图象/表格（题干含"如图"或旁有图形即 true）
4. figure_boxes: 该题附带图形在照片中的位置，格式 [left,top,right,bottom]，各为 0~1 的比例坐标（相对整张照片），可多张图；无图填 []
    ⚠ 只要 has_figure 为 true，就必须给出 figure_boxes（硬性要求，宁多勿漏）；框要略大于图形本身
5. type: "single"(单选) | "multiple"(多选) | "fill"(填空) | "short"(问答/解答)
6. choices: 选择题给 [{"key":"A","text":"…"}]，非选择题填 null
7. printed_answer: 照片中可见的印刷答案（选项字母或答案文本）；看不到填 null
8. 照片顶部/页边的卷名、日期等线索写到 source_guess（没有填 ""）
9. 宁全勿漏：照片里每一道题都要转录，包括做对的题（由用户决定取舍）

只输出 JSON（不要其他文字）：
{"source_guess":"…",
 "questions":[{"no":1,"type":"single","question":"…","choices":[…]|null,
   "has_figure":true,"figure_boxes":[[0.55,0.2,0.95,0.5]],
   "printed_answer":"B","has_handwriting":true},…]}"""


def preprocess(photo_bytes):
    """照片 → 压缩后的 JPEG data URI（最长边1600，q85）

    ⚠ 必须先应用 EXIF orientation（转正）：否则浏览器显示的是转正后的图，
    而 LLM/裁剪用的是原始像素 → 两个坐标系错位（抓图会转 90°，见 test_orientation.py）。
    """
    img = upright_image(Image.open(io.BytesIO(photo_bytes)))
    img = img.convert("RGB")
    w, h = img.size
    m = max(w, h)
    if m > 1600:
        r = 1600 / m
        img = img.resize((int(w * r), int(h * r)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), img.size


# ---------- EXIF 方向（orientation）统一 ----------

def exif_orientation(img):
    """读 EXIF orientation（1~8）；无则返回 1。"""
    try:
        return int((img.getexif() or {}).get(274) or 1)
    except Exception:
        return 1


def upright_image(img):
    """按 EXIF orientation 把图转正，返回 PIL Image（无 EXIF 方向的图原样返回）。"""
    from PIL import ImageOps
    try:
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def box_to_upright(box, orientation):
    """把【原始像素空间】的归一化 bbox 变换到【转正空间】。

    orientation=1/None → 不变
    orientation=3（180°）→ (1-r, 1-b, 1-l, 1-t)
    orientation=6（顺时针90°）→ (1-b, l, 1-t, r)
    orientation=8（逆时针90°）→ (t, 1-r, b, 1-l)
    """
    l, t, r, b = box
    o = orientation or 1
    if o == 1:
        return (l, t, r, b)
    if o == 3:
        return (1 - r, 1 - b, 1 - l, 1 - t)
    if o == 6:
        return (1 - b, l, 1 - t, r)
    if o == 8:
        return (t, 1 - r, b, 1 - l)
    # 其余（镜像类 2/4/5/7）罕见，按不变处理以免错裁
    return (l, t, r, b)


# ---------- 图形来源分层（A 方案） ----------

# SVG 渲染器只支持"点 + 线段"，画不了圆/弧/函数图象/表格 → 这类题只保留原图裁剪
_NON_SVG_HINTS = ("表格", "下表", "如下表", "统计表", "数轴", "坐标系", "函数图象",
                   "函数图像", "图象", "折线图", "条形图", "扇形图", "示意图如下")


def classify_figure(question, has_figure):
    """图形来源分层：'svg' | 'crop' | 'none'

    - none：模型判定无图 → 不进图形链
    - svg ：几何题（题干含"如图"等）→ 走 LLM spec → geocheck → SVG
    - crop：表格/函数图象等 SVG 画不出的题 → 只保留原图裁剪
    """
    if not has_figure:
        return "none"
    q = question or ""
    if any(h in q for h in _NON_SVG_HINTS):
        return "crop"
    if re.search(r"如图|上图|下图|图\s*\d+", q):
        return "svg"
    # 判为有图但题干没给几何线索：仍尝试 SVG（可能是转录漏了"如图"字样）
    return "svg"


def crop_figures(photo_bytes, sheet_id, boxes):
    """按归一化 bbox 裁剪原图 → data/crops/<sheet_id>_q<no>_<k>.png；返回 [{no,k,path,w,h}]

    k 由调用方决定：自动抓图用序号 0/1/2…，手动框选用 "m"（不覆盖自动图，保留原始证据）。

    ⚠ 坐标空间：box 一律按【转正空间】（=浏览器显示方向）解释。
    图先 exif_transpose 转正再裁 → 服务端像素与用户所见一致（否则转 90°）。
    """
    # 先过滤非法框（退化/越界），再开图：避免为无效输入浪费一次解码
    valid = []
    for item in boxes:
        box = item.get("box")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        l, t, r, b = (max(0, min(1, float(v))) for v in box)
        if r - l < 0.02 or b - t < 0.02:
            continue
        valid.append((item["no"], item["k"], (l, t, r, b)))
    if not valid:
        return []
    img = upright_image(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    W, H = img.size
    os.makedirs(store.CROP_DIR, exist_ok=True)
    out = []
    for no, k, (l, t, r, b) in valid:
        crop = img.crop((int(l * W), int(t * H), int(r * W), int(b * H)))
        crop = ImageOps.autocontrast(crop, cutoff=1)   # 偏暗纸张照片自动拉对比（v1.1.0g；展示/视觉输入都受益）
        path = os.path.join(store.CROP_DIR, "{0}_q{1}_{2}.png".format(sheet_id, no, k))
        crop.save(path)
        out.append({"no": no, "k": k, "path": path, "w": crop.size[0], "h": crop.size[1]})
    return out


def migrate_sheet_orientation(sheet):
    """历史草稿单迁移：照片转正 + figure_boxes 同步变换到转正空间。

    幂等：照片已转正（orientation=1）时直接返回，不动 boxes。
    """
    photo = sheet.get("photo")
    if not photo or not os.path.exists(photo):
        return False
    im = Image.open(photo)
    o = exif_orientation(im)
    if o == 1:
        return False
    # 1) 照片转正后覆盖存盘（EXIF 清掉，浏览器显示 = 服务端像素）
    up = upright_image(im).convert("RGB")
    up.save(photo, "JPEG", quality=92)
    # 2) boxes 从原始空间变换到转正空间
    for q in sheet.get("questions", []):
        boxes = q.get("figure_boxes")
        if boxes:
            q["figure_boxes"] = [list(box_to_upright(b, o)) for b in boxes]
    sheet["photo_size"] = [up.width, up.height]
    return True


def default_figure_fields(question, has_figure):
    """转录期图形字段的默认值（v0.6 起：不自动向量化，省 token + 提速）。

    返回 (figure_mode, figure_note)：
    - none：无图
    - crop：默认用自动抓取的原图（原图即权威图形）
    """
    mode = classify_figure(question, has_figure)
    if mode == "none":
        return "none", ""
    if mode == "crop":
        return "crop", "表格/图象类题，SVG 无法表达，使用原题照片裁剪图"
    return "crop", "默认使用原题照片裁剪图（省 token，原图即权威图形）"


def build_figure_fields(question, has_figure, crops, spec, svg, msgs):
    """确定一道题的图形三件套：(figure_spec, figure_svg, figure_note)

    SVG 成功 → 权威图形是 SVG（裁剪图仍留档作对照，但不进 display 字段）
    SVG 失败/不适用 → 回落裁剪图（figure_note 说明原因）
    """
    mode = classify_figure(question, has_figure)
    if mode == "none":
        return None, None, ""
    if mode == "crop":
        note = "表格/图象类题，SVG 无法表达，改用原题照片裁剪图"
        return None, None, note
    if spec and svg:
        return spec, svg, ""
    # SVG 失败 → 回落裁剪图，不阻塞入库（D10）
    reason = (msgs or [""])[-1] if msgs else ""
    if crops:
        return None, None, "矢量图形生成失败（{0}），已改用原题照片裁剪图".format(reason)
    return None, None, "矢量图形生成失败（{0}），入库后保留题干，可手动补图".format(reason)


# ---------- 人审期图形纠正（重新生成 SVG / 切换为原图） ----------

def regen_figure(question, has_figure, crops, vectorize=None, render=None):
    """重新生成矢量图形：题干(+裁剪图) → LLM spec → geocheck → SVG。

    返回 {ok, figure_spec, figure_svg, figure_mode, figure_crops, note}
    失败时 ok=False 且 figure_spec/figure_svg 为 None —— 调用方应保留旧图形不覆盖。
    """
    import figureflow
    vectorize = vectorize or figureflow.vectorize_figure
    render = render or figureflow.render
    if classify_figure(question, has_figure) == "crop":
        return {"ok": False, "figure_spec": None, "figure_svg": None,
                "figure_mode": "crop", "figure_crops": list(crops or []),
                "note": "该题型（表格/函数图象）无法用 SVG 表达，请直接抓取原图"}
    crop = (crops or [None])[0]
    uri_img = None
    try:
        if crop and os.path.exists(crop):
            with open(crop, "rb") as f:
                uri_img = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        uri_img = None
    try:
        spec, msgs = vectorize(question=question, crop_path=crop, rounds=2, uri=uri_img)
        svg = render(spec) if spec else None
    except Exception as e:
        return {"ok": False, "figure_spec": None, "figure_svg": None,
                "figure_mode": "svg", "figure_crops": list(crops or []),
                "note": "重新生成异常: " + str(e)[:80]}
    if not spec or not svg:
        return {"ok": False, "figure_spec": None, "figure_svg": None,
                "figure_mode": "svg", "figure_crops": list(crops or []),
                "note": "重新生成失败（{0}），已保留原图形".format((msgs or [""])[-1])}
    return {"ok": True, "figure_spec": spec, "figure_svg": svg, "figure_mode": "svg",
            "figure_crops": list(crops or []), "note": (msgs or [""])[-1]}


def use_crop(q):
    """把该题权威图形切回原图裁剪：清空 spec/svg，figure_mode=crop（原图取代 SVG）"""
    out = dict(q)
    out["figure_spec"] = None
    out["figure_svg"] = None
    out["figure_mode"] = "crop"
    out.setdefault("figure_crops", [])
    out["figure_note"] = "已切换为原题照片裁剪图（原图取代矢量图形）"
    return out


def manual_crop(photo_bytes, sheet_id, no, box):
    """人审手动框选：用户拖拽的 bbox → 裁剪出该题的新图 → [path]

    box 属于【显示空间】（=转正空间）= 浏览器上用户所见 → 直接可用。
    用独立后缀 _m（manual），不覆盖自动抓取的 _0 文件（保留原始证据备查）。
    """
    out = crop_figures(photo_bytes, sheet_id, [{"no": no, "k": "m", "box": box}])
    return [c["path"] for c in out]


def auto_relocate(photo_bytes, sheet_id, no, boxes):
    """自动重定位：复用该题 figure_boxes 重新裁剪 → [path]"""
    items = [{"no": no, "k": k, "box": b} for k, b in enumerate(boxes or [])]
    out = crop_figures(photo_bytes, sheet_id, items)
    return [c["path"] for c in out]


def recrop_all(sheet):
    """按【当前】照片重新裁剪该单所有图形（图源唯一权威 → 必然一致）。

    - 自动图：figure_boxes（已是转正空间）
    - 手动图：figure_manual_box（用户框选时存档的转正空间 box）
    返回 {auto_paths, manual_paths}
    """
    photo = sheet.get("photo")
    if not photo or not os.path.exists(photo):
        return {"auto_paths": [], "manual_paths": []}
    pb = open(photo, "rb").read()
    sid = sheet["id"]
    auto_items, manual_items = [], []
    for q in sheet.get("questions", []):
        no = q["no"]
        for k, b in enumerate(q.get("figure_boxes") or []):
            auto_items.append({"no": no, "k": k, "box": b})
        mb = q.get("figure_manual_box")
        if mb:
            manual_items.append({"no": no, "k": "m", "box": mb})
    auto_paths = [c["path"] for c in crop_figures(pb, sid, auto_items)]
    manual_paths = [c["path"] for c in crop_figures(pb, sid, manual_items)]
    # 写回草稿单
    by_no = {}
    for p in auto_paths + manual_paths:
        by_no.setdefault(os.path.basename(p).split("_q")[1].split("_")[0], []).append(p)
    for q in sheet.get("questions", []):
        paths = by_no.get(str(q["no"]), [])
        if paths:
            q["figure_crops"] = paths
    return {"auto_paths": auto_paths, "manual_paths": manual_paths}


def ask_json_vision(client, model, prompt, uri, max_tokens=16000):
    """视觉问答：json_object → 纯文本 ×2 → 关思考纯文本（父项目降级经验；转录开思考保准确率）

    第 4 级 nothink：D12 场景——思考链烧光 max_tokens 致 content=''，关思考重试（实测 645s→16s）。"""
    last = ""
    for mode in ("json", "plain", "plain", "nothink"):
        try:
            content = [{"type": "text", "text": prompt}]
            if uri:
                content.append({"type": "image_url", "image_url": {"url": uri}})
            kwargs = dict(model=model, timeout=300, temperature=0.2, max_tokens=max_tokens,
                          messages=[{"role": "user", "content": content}])
            if mode == "json":
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            else:
                if mode == "nothink":
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
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
    raise RuntimeError("转录失败（最后输出: {0!r}）".format(last[:60]))


CLASSIFY_PROMPT = """你是初中数学教研专家。下面是【浙教版八年级上册】教材知识树，以及若干道数学题。
给每道题判定年级和知识点归属：
- grade：题目内容落在知识树内 → "八年级上册"；明显超出八上范围 → 按浙教版进度给
  "九年级上册"（如二次函数/相似三角形/锐角三角函数）或"九年级下册"（如圆/解直角三角形/概率）；
  无法判断 → "其他"
- chapter/section/kp：grade 为"八年级上册"时，从知识树中选出最匹配的一组
  （chapter 如"第一章 三角形"，section 如"1.5 三角形全等的判定"，kp 为该节下最匹配的知识点）；
  其他年级给出你认为的教材归属，kp 可为空字符串

知识树：
{tree}

题目列表：
{questions}

只输出 JSON（不要其他文字）：
{{"questions":[{{"no":1,"grade":"八年级上册","chapter":"…","section":"…","kp":"…"}},…]}}"""


def classify_questions(client, model, questions, tree=None):
    """转录后批量归类：每题 → (年级, 章节知识点)。纯文本 LLM 一次调用，失败返回 []"""
    from kbtree import compact_summary, GRADE_OPTIONS
    lines = "\n".join("{0}. {1}".format(q.get("no"), (q.get("question") or "")[:150])
                      for q in questions)
    prompt = CLASSIFY_PROMPT.format(tree=compact_summary(tree), questions=lines)
    try:
        data = ask_json_vision(client, model, prompt, None, max_tokens=4000)
    except Exception:
        return []
    out = []
    for c in (data.get("questions") or []):
        if not isinstance(c, dict):
            continue
        grade = c.get("grade") if c.get("grade") in GRADE_OPTIONS else "八年级上册"
        out.append({"no": c.get("no"), "grade": grade,
                    "chapter": (c.get("chapter") or "").strip(),
                    "section": (c.get("section") or "").strip(),
                    "kp": (c.get("kp") or "").strip()})
    return out


def transcribe_photo(photo_bytes, source_note="", provider=None):
    """照片 → 草稿单（多题转录 + 几何题矢量图形 + 落盘）。返回草稿单 dict。
    视觉调用：provider 缺省用配置引擎；GLM 自动切视觉模型 glm-4.6v-flash。"""
    import figureflow
    uri, (W, H) = preprocess(photo_bytes)
    client, model = make_client(provider, vision=True)
    data = ask_json_vision(client, model, TRANSCRIBE_PROMPT, uri)

    questions_raw = data.get("questions") or []
    if not questions_raw:
        raise RuntimeError("照片中未识别到题目（可能太模糊或不是试卷照片）")

    sheet = add_sheet({
        "source": source_note or (data.get("source_guess") or "").strip(),
        "photo_size": [W, H],
        "provider_note": model,
        "questions": [],
    })

    os.makedirs(store.PHOTO_DIR, exist_ok=True)
    photo_path = os.path.join(store.PHOTO_DIR, sheet["id"] + ".jpg")
    # 存【转正后】的照片（EXIF 已应用）：浏览器显示方向 = 服务端像素坐标，
    # 用户手动框选的坐标才能和 crop_figures 对得上。
    up = upright_image(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    up.save(photo_path, "JPEG", quality=92)
    sheet["photo"] = photo_path

    for q in questions_raw:
        t = str(q.get("type") or "").strip().lower()
        rec = {
            "no": q.get("no"),
            "type": t if t in ("single", "multiple", "fill", "short") else "fill",
            "question": str(q.get("question") or "").strip(),
            "choices": q.get("choices") if isinstance(q.get("choices"), list) else None,
            "has_figure": bool(q.get("has_figure")) or bool(re.search(r"如图|下图", q.get("question") or "")),
            "printed_answer": q.get("printed_answer"),
            "has_handwriting": bool(q.get("has_handwriting")),
            "figure_boxes": q.get("figure_boxes") if isinstance(q.get("figure_boxes"), list) else [],
            "figure_crops": [],
            # 人审字段
            "selected": False, "reason": None, "edited": None,
        }
        sheet["questions"].append(rec)

    # ① 自动抓图（按 figure_boxes 裁原图）——权威原件，永远保留（A 方案兜底）
    box_items = []
    for rec in sheet["questions"]:
        for k, box in enumerate(rec.get("figure_boxes") or []):
            if isinstance(box, (list, tuple)) and len(box) == 4:
                box_items.append({"no": rec["no"], "k": k, "box": box})
    if box_items:
        try:
            crops = crop_figures(photo_bytes, sheet["id"], box_items)
            by_q = {}
            for c in crops:
                by_q.setdefault(c["no"], []).append(c["path"])
            for rec in sheet["questions"]:
                rec["figure_crops"] = by_q.get(rec["no"], [])
        except Exception as e:
            for rec in sheet["questions"]:
                rec["figure_crops"] = []
                rec["figure_note"] = "抓图失败: " + str(e)[:60]

    # ② 图形默认用自动抓取的原图（v0.6：向量化是最费 token 的一步，改为人审期按需生成）
    #    「重新生成SVG」按钮已从 UI 移除；regen_figure 仅保留为 API 备用。
    #    入库链 resolve_figure_spec 尊重 crop 选择，不会重复向量化。
    for rec in sheet["questions"]:
        rec["figure_spec"] = None
        rec["figure_svg"] = None
        rec["figure_mode"], rec["figure_note"] = default_figure_fields(
            rec["question"], rec["has_figure"])

    # ③ 年级/知识点自动归类（纯文本 LLM 批量一次；失败留空由人审手动选择）
    try:
        import kbtree
        tree = kbtree.build_tree()
        cls = {c.get("no"): c for c in classify_questions(client, model, sheet["questions"], tree)}
        for rec in sheet["questions"]:
            c = cls.get(rec.get("no"))
            rec["grade"] = c.get("grade") if c else "八年级上册"
            rec["kp_sel"] = ({k: c.get(k, "") for k in ("chapter", "section", "kp")}
                             if c and c.get("chapter") else None)
    except Exception:
        for rec in sheet["questions"]:
            rec["grade"], rec["kp_sel"] = "八年级上册", None

    from store import load_sheets, save_sheets
    db = load_sheets()
    for s in db["sheets"]:
        if s["id"] == sheet["id"]:
            s.update(sheet)
    save_sheets(db)
    return sheet
