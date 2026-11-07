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
from PIL import Image, ImageOps, ImageDraw

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
10. 照片可能是两页上下拼接的长图（页间有空白分隔）：跨页被截断的同一道题
    必须合并为**一题完整转录**（题干接续 + 全部小问；figure_boxes 标注在长图坐标上）

只输出 JSON（不要其他文字）：
{"source_guess":"…",
 "questions":[{"no":1,"type":"single","question":"…","choices":[…]|null,
   "has_figure":true,"figure_boxes":[[0.55,0.2,0.95,0.5]],
   "printed_answer":"B","has_handwriting":true},…]}"""


def strip_kaoqian_title(text):
    """剥掉卷面考点栏头（v1.2m）：转录 LLM 有时把「【考点N】xxx（N分）」栏头并进题干，
    造成同一题带/不带前缀两份转录查重不中 → 重复入库。转录后统一剥掉。

    规则：仅匹配题干开头的完整栏头（【考点…】+ 标题 + （N分），全/半角括号均可）；
    普通「（8分）」分值标注、无分值的裸【考点】不剥（宁保勿剥）。"""
    if not text:
        return text
    return re.sub(r"^【考点[^】]{0,20}】\s*[^（()】]{0,30}[（(]\s*\d+\s*分\s*[）)]\s*",
                  "", text).lstrip()


def preprocess(photo_bytes, max_side=1600):
    """照片 → 压缩后的 JPEG data URI（最长边 max_side，q85；跨页拼接长图自动放宽）

    ⚠ 必须先应用 EXIF orientation（转正）：否则浏览器显示的是转正后的图，
    而 LLM/裁剪用的是原始像素 → 两个坐标系错位（抓图会转 90°，见 test_orientation.py）。
    """
    img = upright_image(Image.open(io.BytesIO(photo_bytes)))
    img = img.convert("RGB")
    w, h = img.size
    m = max(w, h)
    if m > max_side:
        r = max_side / m
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


# ---------- 拍摄方向自动扶正（v1.3）----------
# 场景：照片内容本身横躺 90°（拍照时手机转了 90°，EXIF 无标记/被剥离——v0.5.1 只管 EXIF）。
# 策略：CV 行/列投影剖面差分能量零 token 预筛（正立文档文字行水平 → 行剖面波动远大于列）；
# 判"躺倒/不确定"才用 640px 小图视觉调用确认角度（±90° 与 180° 行剖面分不出，交给 LLM）。
# 扶正后的图作为转录/抓图/存盘的唯一输入 → 全链路坐标系天然一致，下游零变换。

ROT_DETECT_PROMPT = """图中同一份数档文档照片按四种旋转方向摆成四宫格：1=左上、2=右上、3=左下、4=右下。
哪一个里的文字是正常方向（水平、可自上而下阅读）？
只输出 JSON：{"n": 1}（n 为该象限序号 1/2/3/4）"""


def _proj_energy(img):
    """行/列投影剖面差分能量 (row_e, col_e)：前景（暗像素）按行/列求和后取一阶差分平方和。
    正立文档：文字行水平 → 行剖面在"文字行/行间空白"间剧烈跳变 → 行能量大；躺倒则转移到列。"""
    import numpy as np
    g = np.asarray(img.convert("L"), dtype=np.float32)
    fg = (g < 140).astype(np.float32)          # 文字为前景（纸张照片偏暗，阈值放宽）
    rows, cols = fg.sum(axis=1), fg.sum(axis=0)

    def energy(v):
        d = np.diff(v)
        return float((d * d).sum())

    return energy(rows), energy(cols)


def detect_rotation_cv(img):
    """CV 预筛（零 token）：返回 'upright'（正立，跳过 LLM）| 'rotated'（躺倒）| 'unsure'"""
    try:
        small = img.copy()
        small.thumbnail((500, 500))
        row_e, col_e = _proj_energy(small)
        if row_e <= 0 or col_e <= 0:
            return "unsure"
        ratio = row_e / col_e
        if ratio >= 1.25:
            return "upright"
        if ratio <= 0.8:
            return "rotated"
        return "unsure"
    except Exception:
        return "unsure"


def detect_rotation_llm(img, provider=None):
    """四宫格指认式确认（v1.3.1）：把原图/顺转90/180/270 四个方向拼一张图，
    让视觉模型"指认"哪个正立——选择类任务远比开放心算角度可靠（真实照片实测：
    单图提问 270° 会误答 180°，四宫格全对）。失败返回 0（不旋转，保持现状）。"""
    try:
        cell = 300
        tiles = []
        for k in range(4):                      # 1:原图 2:顺90 3:顺180 4:顺270
            t = img.copy()
            t.thumbnail((cell, cell))
            t = t.rotate(-90 * k, expand=True)
            t.thumbnail((cell, cell))
            tiles.append(t)
        # 2x2 拼图：每格上方留 34px 标号条
        cw = max(t.size[0] for t in tiles) + 12
        ch = max(t.size[1] for t in tiles) + 46
        grid = Image.new("RGB", (cw * 2, ch * 2), "white")
        d = ImageDraw.Draw(grid)
        for i, t in enumerate(tiles):
            x, y = (i % 2) * cw, (i // 2) * ch
            d.text((x + 8, y + 6), str(i + 1), fill=(200, 30, 30))
            grid.paste(t, (x + 6, y + 34))
            d.rectangle([x + 2, y + 2, x + cw - 3, y + ch - 3], outline=(180, 180, 180))
        buf = io.BytesIO()
        grid.save(buf, "JPEG", quality=70)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        client, model = make_client(provider, vision=True)
        data = ask_json_vision(client, model, ROT_DETECT_PROMPT, uri, max_tokens=2000)
        n = int(data.get("n") or 1)
        if n not in (1, 2, 3, 4):
            return 0
        return (n - 1) * 90                     # 1→0 2→90 3→180 4→270
    except Exception:
        return 0


def detect_rotation(img, provider=None):
    """拍摄方向检测主入口：返回需顺时针旋转的度数 0/90/180/270。
    CV 判正立 → 直接 0（多数照片零开销）；躺倒/不确定 → LLM 确认。"""
    if detect_rotation_cv(img) == "upright":
        return 0
    return detect_rotation_llm(img, provider)


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


def rotate_sheet_photo(sid):
    """人审兜底（v1.3）：原图【顺时针】旋转 90° 并同步变换全部图形框 + 整单重裁。

    自动扶正（LLM 二选一）约 75% 准确，转错方向时人点此按钮一键修正（可连点）。
    box 变换复用 box_to_upright(orientation=6)（顺 90 的坐标映射）。返回更新后的草稿单。"""
    sheet = store.get_sheet(sid)
    if not sheet:
        raise ValueError("草稿单不存在")
    photo = sheet.get("photo")
    if not photo or not os.path.exists(photo):
        raise ValueError("无照片")
    img = Image.open(photo).convert("RGB").rotate(-90, expand=True)
    img.save(photo, "JPEG", quality=92)
    from store import load_sheets, save_sheets
    db = load_sheets()
    target = next((sh for sh in db["sheets"] if sh["id"] == sid), None)
    if not target:
        raise ValueError("草稿单不存在")
    for q in target.get("questions", []):
        if q.get("figure_boxes"):
            q["figure_boxes"] = [box_to_upright(b, 6) for b in q["figure_boxes"]]
        mb = q.get("figure_manual_box")
        if mb:
            q["figure_manual_box"] = box_to_upright(mb, 6)
    target["auto_rotated"] = ((target.get("auto_rotated") or 0) + 90) % 360
    save_sheets(db)
    sheet = store.get_sheet(sid) or target
    recrop_all(sheet)
    return sheet


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


CLASSIFY_PROMPT = """你是初中数学教研专家。给下面每道数学题做三项归类：

- cat：从「专题分类清单」中选最匹配的一个专题类别（必须逐字输出清单中的类别完整名称）；都不合适输出"其他"
- kps：从「细分知识点清单」中选出该题涉及的知识点，**最多 3 个**（不足 3 个就只写实际的）。严格遵守：
  1) 每个知识点必须逐字抄写清单中的名称，禁止自己编造、改写或拼凑新名称
  2) 先判断题目实际考查什么，优先从对应章节的知识点中选
     （如"证明两个三角形全等"→ 从第一章选"全等三角形的性质"，而不是写清单中没有的
       "全等三角形的判定"，也不是"等边三角形的判定定理"这类不相关条目）
  3) 只有清单确实完全没有覆盖时，才允许输出最多一个简短的知识点名称
- grade：题目所属年级（八年级上册/八年级下册/九年级上册/九年级下册/其他）

专题分类清单：
{cats}

细分知识点清单（按章分组）：
{kb_kps}

题目列表：
{questions}

只输出 JSON（不要其他文字）：
{{"questions":[{{"no":1,"grade":"八年级上册","cat":"第1类 三边关系","kps":["三角形的三边关系","不等关系"]}},…]}}"""


def classify_questions(client, model, questions, cats=None, kb_kps=None):
    """转录后批量归类：每题 → (年级, 专题类别, 细分知识点最多3个)。纯文本 LLM 一次调用，失败返回 []

    v1.2k：kps 从内置教材 KB 细分知识点清单（kb_kps）中选，最多 3 个——受控词表，
    LLM 每个输出 → 精确/包含匹配回清单名 → difflib 相似收敛（阈值 0.5）→
    都不行丢弃（绝不保留清单外原文）；去重、超 3 个截断。
    cats = [{"set_id","set_name","cat_id","cat_label"}]（专题类别清单）；
    kb_kps = [{"kp","chapter"}]（教材 KB 扁平化细分知识点）。"""
    import difflib
    grade_opts = ["八年级上册", "八年级下册", "九年级上册", "九年级下册", "其他"]
    cats = cats or []
    kb_kps = kb_kps or []
    lines = "\n".join("{0}. {1}".format(q.get("no"), (q.get("question") or "")[:150])
                      for q in questions)
    # 细分知识点清单按章分组展示（帮助 LLM 理解语境，输出仍只给知识点名）
    by_ch = {}
    for it in kb_kps:
        by_ch.setdefault(it.get("chapter", ""), []).append(it["kp"])
    kb_block = "\n".join("【{0}】{1}".format(ch, "；".join(ks))
                          for ch, ks in by_ch.items()) or "（无）"
    prompt = CLASSIFY_PROMPT.format(cats="\n".join(c["cat_label"] for c in cats) or "（无）",
                                    kb_kps=kb_block, questions=lines)
    try:
        data = ask_json_vision(client, model, prompt, None, max_tokens=4000)
    except Exception:
        return []
    by_label = {c["cat_label"]: c for c in cats}
    kp_names = {it["kp"] for it in kb_kps}
    kp_chapter = {it["kp"]: (it.get("chapter") or "") for it in kb_kps}   # v1.2o：kp → 教材章（工作台章节结构）
    out = []
    for c in (data.get("questions") or []):
        if not isinstance(c, dict):
            continue
        grade = c.get("grade") if c.get("grade") in grade_opts else "八年级上册"
        item = {"no": c.get("no"), "grade": grade, "set": None, "cat": None,
                "kps": [], "chapter": ""}
        m = by_label.get((c.get("cat") or "").strip())
        if m:
            item["set"], item["cat"] = m["set_id"], m["cat_id"]
        # kps 受控（v1.2k，收敛逻辑见 converge_kps）
        item["kps"] = converge_kps(c.get("kps"), kb_kps)
        # chapter（v1.2o）：首个命中 kp 所属的教材章（工作台章节结构 = knowledge_points.json）
        for hit in item["kps"]:
            if kp_chapter.get(hit):
                item["chapter"] = kp_chapter[hit]
                break
        out.append(item)
    return out


def converge_kps(raw_list, kb_kps, cap=3):
    """v1.2q：LLM 输出的知识点 → 受控收敛（精确/包含匹配回清单名 → difflib 相似收敛
    阈值 0.5 → 都不行丢弃，绝不保留清单外原文）；去重、最多 cap 个。
    kb_kps = [{"kp","chapter"}]；classify_questions 与专题例题分析共用。"""
    import difflib
    kp_names = {it["kp"] for it in (kb_kps or [])}
    out = []
    for raw in [str(k).strip() for k in (raw_list or []) if str(k).strip()]:
        hit = raw if raw in kp_names else None
        if not hit:
            hit = next((k for k in kp_names if k in raw or raw in k), None)
        if not hit:
            close = difflib.get_close_matches(raw, list(kp_names), n=1, cutoff=0.5)
            hit = close[0] if close else ""
        if hit and hit not in out:
            out.append(hit)
        if len(out) >= cap:
            break
    return out


def transcribe_photo(photo_bytes, source_note="", provider=None):
    """照片 → 草稿单（多题转录 + 几何题矢量图形 + 落盘）。返回草稿单 dict。
    视觉调用：provider 缺省用配置引擎；GLM 自动切视觉模型 glm-4.6v-flash。"""
    import figureflow
    # ① EXIF 转正（v0.5.1）+ 拍摄方向自动扶正（v1.3：照片内容横躺，EXIF 管不到）
    #    扶正后的照片作为转录/抓图/存盘的唯一输入 → 坐标系全链路一致
    _up = upright_image(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    auto_rot = detect_rotation(_up, provider)
    if auto_rot:
        _up = _up.rotate(-auto_rot, expand=True)   # PIL 逆时针为正 → 顺时针 rot 取负
    buf = io.BytesIO()
    _up.save(buf, "JPEG", quality=92)
    photo_bytes = buf.getvalue()

    # 跨页拼接长图（v1.3.1）：H/W≥1.5 → 放宽到 2400，两页文字保持可读
    max_side = 2400 if _up.size[1] / max(1, _up.size[0]) >= 1.5 else 1600
    uri, (W, H) = preprocess(photo_bytes, max_side=max_side)
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
    # 存【转正+拍摄扶正】的照片：浏览器显示方向 = 服务端像素坐标，
    # 用户手动框选的坐标才能和 crop_figures 对得上（photo_bytes 已完全扶正）。
    with Image.open(io.BytesIO(photo_bytes)) as _im:
        _im.save(photo_path, "JPEG", quality=92)
    sheet["photo"] = photo_path
    sheet["auto_rotated"] = auto_rot   # v1.3：拍摄方向扶正记录（人审视图展示）

    for q in questions_raw:
        t = str(q.get("type") or "").strip().lower()
        rec = {
            "no": q.get("no"),
            "type": t if t in ("single", "multiple", "fill", "short") else "fill",
            # v1.2m：剥掉转录混入的「【考点N】xxx（N分）」栏头（查重对齐，见 strip_kaoqian_title）
            "question": strip_kaoqian_title(str(q.get("question") or "").strip()),
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

    # ③ 年级/专题类别/细分知识点自动归类（纯文本 LLM 批量一次；失败留空由前端显示兜底）
    #    v1.2i：kp 从内置教材 KB 细分知识点清单中选（受控词表，与工作台知识点同口径）
    try:
        import topics as _topics
        import kbtree as _kbtree
        cats = []
        for s in _topics.load_topics()["sets"]:
            for c in s["categories"]:
                cats.append({"set_id": s["id"], "set_name": s["name"], "cat_id": c["id"],
                             "cat_label": "第{0}类 {1}".format(c["no"], c["name"])})
        kpt = _kbtree.knowledge_points()   # v1.2i：读精简知识点 json（首次自动从 KB 提炼落盘，不再每次解析两个大 json）
        kb_kps = [{"kp": k, "chapter": ch["title"]}
                  for ch in kpt["chapters"] for k in ch["kps"]]
        cls = {c.get("no"): c for c in classify_questions(client, model, sheet["questions"], cats, kb_kps)}
        for rec in sheet["questions"]:
            c = cls.get(rec.get("no"))
            rec["grade"] = c.get("grade") if c else "八年级上册"
            rec["tp_sel"] = ({"set": c["set"], "cat": c["cat"]}
                             if c and c.get("cat") else None)
            kps = [k for k in (c.get("kps") or []) if k][:3] if c else []   # v1.2k：最多3个（受控清单）
            rec["kps"] = kps
            rec["kp"] = kps[0] if kps else ""   # 兼容旧草稿/展示兜底：首个细分知识点（只读）
            rec["chapter"] = (c.get("chapter") or "") if c else ""   # v1.2o：教材章（受控 kp 反查，工作台章节=knowledge_points.json 结构）
    except Exception:
        for rec in sheet["questions"]:
            rec["grade"], rec["tp_sel"], rec["kps"], rec["kp"], rec["chapter"] = "八年级上册", None, [], "", ""

    from store import load_sheets, save_sheets
    db = load_sheets()
    for s in db["sheets"]:
        if s["id"] == sheet["id"]:
            s.update(sheet)
    save_sheets(db)
    return sheet
