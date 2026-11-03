# -*- coding: utf-8 -*-
"""app —— AI错题本 Flask 服务（本地单用户）"""
import os, sys, io, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT  # noqa

from flask import Flask, request, jsonify, send_file, send_from_directory
import store, stats
import figureflow
import ai_config

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


def _err(e):
    traceback.print_exc()
    return jsonify({"error": str(e)[:300]}), 500


def _enrich(q):
    """题目 → 前端视图（补 SVG 图形）"""
    view = dict(q)
    spec = (q.get("question") or {}).get("figure_spec")
    if isinstance(spec, dict) and spec.get("points"):
        try:
            view["figure_svg"] = figureflow.render(spec)
        except Exception:
            view["figure_svg"] = None
    else:
        view["figure_svg"] = None
    return view


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.get("/api/kb")
def api_kb():
    """教材知识树（浙教版八上：章/节/知识点）——录入页树形选择器数据源"""
    try:
        import kbtree
        return jsonify(kbtree.build_tree())
    except Exception as e:
        return _err(e)


@app.get("/api/stats")
def api_stats():
    a = request.args
    return jsonify(stats.dashboard(
        chapter=a.get("chapter") or None,
        difficulty=a.get("difficulty") or None,
        kp=a.get("kp") or None,
        reason=a.get("reason") or None,
        date_from=a.get("from") or None,
        date_to=a.get("to") or None,
        include_mastered=a.get("mastered") in ("1", "true")))


# ---------- AI 引擎设置（注入式 key：网页配置 > 环境变量；key 永不明文回传） ----------

@app.get("/api/ai-config")
def api_ai_config_get():
    """引擎配置视图（掩码）：{provider, ready, providers:{deepseek/glm:{has_key,key_masked,models...}}}"""
    try:
        return jsonify(ai_config.describe())
    except Exception as e:
        return _err(e)


@app.post("/api/ai-config")
def api_ai_config_post():
    """保存配置 {provider?, keys?:{deepseek?,glm?}, models?:{...}}；key 传空串 = 清除"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        ai_config.save_patch(body)
        return jsonify(ai_config.describe())
    except Exception as e:
        return _err(e)


@app.post("/api/ai-config/test")
def api_ai_config_test():
    """连通性测试 {provider, key?, model?, vision?}（测试值不落盘）→ {ok, model|error}"""
    try:
        b = request.get_json(force=True, silent=True) or {}
        return jsonify(ai_config.test_provider(b.get("provider"), b.get("key"),
                                               b.get("model"), bool(b.get("vision"))))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]})


# ---------- 排版机（草稿单） ----------

@app.post("/api/sheets")
def api_sheet_create():
    try:
        f = request.files.get("photo")
        if not f:
            return jsonify({"error": "缺少 photo 文件"}), 400
        import reviewflow
        sheet = reviewflow.transcribe_photo(f.read(), request.form.get("source", ""))
        return jsonify(sheet)
    except Exception as e:
        return _err(e)


@app.get("/api/sheets")
def api_sheets():
    status = request.args.get("status")
    sheets = [s for s in store.load_sheets()["sheets"]
              if not status or s.get("status") == status]
    return jsonify({"sheets": [{k: s.get(k) for k in
                                ("id", "created_at", "source", "status", "photo")} |
                               {"n": len(s.get("questions", []))}
                               for s in reversed(sheets)]})


@app.get("/api/sheets/<sid>")
def api_sheet(sid):
    s = store.get_sheet(sid)
    if not s:
        return jsonify({"error": "不存在"}), 404
    return jsonify(s)


@app.patch("/api/sheets/<sid>/questions/<int:no>")
def api_sheet_patch(sid, no):
    fields = request.get_json(force=True)
    allowed = {k: fields[k] for k in
               ("selected", "reason", "edited", "question", "choices", "printed_answer",
                "figure_mode", "figure_spec", "figure_svg", "figure_crops", "figure_note",
                "grade", "kp_sel")
               if k in fields}
    s = store.patch_sheet_question(sid, no, allowed)
    if not s:
        return jsonify({"error": "不存在"}), 404
    return jsonify(s)


# ---------- 人审期图形纠正 ----------

def _sheet_and_q(sid, no):
    sheet = store.get_sheet(sid)
    if not sheet:
        return None, None
    q = None
    for x in sheet.get("questions", []):
        if x.get("no") == no:
            q = x
            break
    return sheet, q


@app.post("/api/sheets/<sid>/questions/<int:no>/regen-figure")
def api_regen_figure(sid, no):
    """重新生成矢量图形（用题干+裁剪图重跑向量化）"""
    try:
        import reviewflow
        sheet, q = _sheet_and_q(sid, no)
        if not q:
            return jsonify({"error": "不存在"}), 404
        if sheet.get("status") == "processed":
            return jsonify({"error": "该草稿单已处理"}), 400
        text = (q.get("edited") or {}).get("question") if isinstance(q.get("edited"), dict) else None
        text = text or q.get("question") or ""
        out = reviewflow.regen_figure(text, q.get("has_figure"), q.get("figure_crops") or [])
        if out["ok"]:
            fields = {"figure_spec": out["figure_spec"], "figure_svg": out["figure_svg"],
                      "figure_mode": "svg", "figure_note": out["note"]}
            store.patch_sheet_question(sid, no, fields)
        else:
            # 失败：只更新提示，保留原图形不破坏
            store.patch_sheet_question(sid, no, {"figure_note": out["note"]})
        return jsonify(out)
    except Exception as e:
        return _err(e)


@app.post("/api/sheets/<sid>/questions/<int:no>/use-crop")
def api_use_crop(sid, no):
    """把该题权威图形切回原图裁剪（原图取代 SVG）"""
    try:
        import reviewflow
        sheet, q = _sheet_and_q(sid, no)
        if not q:
            return jsonify({"error": "不存在"}), 404
        if sheet.get("status") == "processed":
            return jsonify({"error": "该草稿单已处理"}), 400
        out = reviewflow.use_crop(q)
        fields = {k: out[k] for k in
                  ("figure_spec", "figure_svg", "figure_mode", "figure_crops", "figure_note")}
        store.patch_sheet_question(sid, no, fields)
        return jsonify({"ok": True, **{k: out[k] for k in
                                        ("figure_spec", "figure_svg", "figure_mode",
                                         "figure_crops", "figure_note")}})
    except Exception as e:
        return _err(e)


@app.post("/api/sheets/<sid>/questions/<int:no>/recrop")
def api_recrop(sid, no):
    """重新抓取原图：手动框选（给 box）或自动重定位（复用 figure_boxes）"""
    try:
        import reviewflow
        sheet, q = _sheet_and_q(sid, no)
        if not q:
            return jsonify({"error": "不存在"}), 404
        if sheet.get("status") == "processed":
            return jsonify({"error": "该草稿单已处理"}), 400
        photo = sheet.get("photo")
        if not photo or not os.path.exists(photo):
            return jsonify({"error": "原图缺失，无法重新抓取"}), 400
        body = request.get_json(silent=True) or {}
        box = body.get("box")
        photo_bytes = open(photo, "rb").read()
        fields = {}
        if box:
            paths = reviewflow.manual_crop(photo_bytes, sid, no, box)
            # 存档手动 box（转正空间）→ 之后照片再迁移也能重裁出同样结果
            fields["figure_manual_box"] = [float(v) for v in box]
        else:
            paths = reviewflow.auto_relocate(photo_bytes, sid, no, q.get("figure_boxes") or [])
        if not paths:
            return jsonify({"error": "抓取失败：请检查框选区域或重试自动定位"}), 400
        fields.update({"figure_crops": paths, "figure_spec": None, "figure_svg": None,
                       "figure_mode": "crop",
                       "figure_note": "已抓取原题图形（原图取代矢量图形）"})
        store.patch_sheet_question(sid, no, fields)
        return jsonify({"ok": True, "figure_crops": paths, "figure_mode": "crop"})
    except Exception as e:
        return _err(e)


@app.post("/api/sheets/<sid>/recrop-all")
def api_recrop_all(sid):
    """整单重裁：按当前照片重新裁所有图形（照片方向变更/迁移后用）"""
    try:
        import reviewflow
        sheet = store.get_sheet(sid)
        if not sheet:
            return jsonify({"error": "不存在"}), 404
        if sheet.get("status") == "processed":
            return jsonify({"error": "该草稿单已处理"}), 400
        out = reviewflow.recrop_all(sheet)
        db = store.load_sheets()
        for s in db["sheets"]:
            if s["id"] == sid:
                s.update(sheet)
        store.save_sheets(db)
        return jsonify({"ok": True, "auto": len(out["auto_paths"]),
                        "manual": len(out["manual_paths"])})
    except Exception as e:
        return _err(e)


@app.post("/api/sheets/<sid>/commit")
def api_sheet_commit(sid):
    try:
        import pipeline
        sheet = store.get_sheet(sid)
        if not sheet:
            return jsonify({"error": "不存在"}), 404
        if sheet.get("status") == "processed":
            return jsonify({"error": "该草稿单已处理"}), 400
        return jsonify(pipeline.ingest_selected(sheet))
    except Exception as e:
        return _err(e)


@app.get("/api/sheets/<sid>/docx")
def api_sheet_docx(sid):
    try:
        import wordexport
        sheet = store.get_sheet(sid)
        if not sheet:
            return jsonify({"error": "不存在"}), 404
        out = os.path.join(BASE, "out", "排版稿_{0}.docx".format(sid))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        wordexport.export_sheet_docx(sheet, out)
        return send_file(out, as_attachment=True, download_name=os.path.basename(out))
    except Exception as e:
        return _err(e)


# ---------- 错题库 ----------

@app.get("/api/questions")
def api_questions():
    flt = request.args.get("filter", "active")
    qs = store.load()["questions"]
    if flt == "active":
        qs = [q for q in qs if not q.get("mastered_at")]
    elif flt == "mastered":
        qs = [q for q in qs if q.get("mastered_at")]
    elif flt == "needs_review":
        qs = [q for q in qs if q.get("flags", {}).get("needs_review")]
    return jsonify({"questions": [_brief(q) for q in qs]})


def _brief(q):
    return {"id": q["id"], "type": q.get("type"), "text": (q.get("question") or {}).get("text", "")[:60],
            "chapter": (q.get("analysis") or {}).get("chapter"),
            "difficulty": (q.get("analysis") or {}).get("difficulty"),
            "kps": (q.get("analysis") or {}).get("knowledge_points", []),
            "reason": q.get("mistake_reason"), "verify_status": q.get("verify_status"),
            "practice_n": len(q.get("practice", [])), "streak": store.streak(q),
            "mastered_at": q.get("mastered_at"),
            "needs_review": q.get("flags", {}).get("needs_review", False)}


@app.get("/api/questions/<qid>")
def api_question(qid):
    q = store.get(qid)
    if not q:
        return jsonify({"error": "不存在"}), 404
    return jsonify(_enrich(q))


@app.patch("/api/questions/<qid>")
def api_question_patch(qid):
    fields = request.get_json(force=True)
    q = store.get(qid)
    if not q:
        return jsonify({"error": "不存在"}), 404
    for k in ("source", "type"):
        if k in fields:
            q[k] = fields[k]
    if "analysis" in fields and isinstance(fields["analysis"], dict):
        q.setdefault("analysis", {}).update(fields["analysis"])
    store.update(qid, **{k: q[k] for k in ("source", "type", "analysis") if k in q})
    return jsonify(store.get(qid))


@app.post("/api/questions/export-pdf")
def api_export_pdf():
    """勾选题 → 练习卷 PDF（父项目 export_paper 矢量导出）"""
    try:
        import pipeline
        body = request.get_json(force=True)
        ids = body.get("ids") or []
        if not ids:
            return jsonify({"error": "缺少 ids"}), 400
        out = pipeline.export_selected_pdf(ids, with_answer=body.get("with_answer", True))
        return jsonify(out)
    except Exception as e:
        return _err(e)


@app.post("/api/questions/<qid>/practice")
def api_practice(qid):
    body = request.get_json(force=True)
    q = store.add_practice(qid, bool(body.get("correct")), body.get("variant", "original"))
    if not q:
        return jsonify({"error": "不存在"}), 404
    return jsonify({"streak": store.streak(q), "ready": store.streak(q) >= 3})


@app.post("/api/questions/<qid>/master")
def api_master(qid):
    import time
    q = store.update(qid, mastered_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    if not q:
        return jsonify({"error": "不存在"}), 404
    return jsonify(q)


@app.post("/api/questions/<qid>/unmaster")
def api_unmaster(qid):
    q = store.update(qid, mastered_at=None)
    if not q:
        return jsonify({"error": "不存在"}), 404
    return jsonify(q)


@app.delete("/api/questions/<qid>")
def api_delete(qid):
    ok = store.remove(qid)
    return jsonify({"ok": ok})


@app.post("/api/questions/<qid>/similar")
def api_similar(qid):
    try:
        import pipeline
        n = int(request.get_json(force=True).get("count", 2)) if request.data else 2
        return jsonify({"items": pipeline.gen_similar(qid, n)})
    except Exception as e:
        return _err(e)


# ---------- 出卷 ----------

@app.post("/api/paper")
def api_paper():
    try:
        import pipeline
        body = request.get_json(force=True) or {}
        out = pipeline.gen_paper(
            chapters=body.get("chapters") or None,
            kps=body.get("kps") or None,
            difficulty_mix=body.get("difficulty_mix") or None,
            count=int(body.get("count", 8)),
            mode=body.get("mode", "variant"))
        return jsonify(out)
    except Exception as e:
        return _err(e)


# ---------- 用户管理（v0.7：按用户隔离题库/草稿单/照片） ----------

@app.get("/api/users")
def api_users():
    return jsonify(store.list_users())


@app.post("/api/users")
def api_user_create():
    """新建用户（题库为空）并切换为当前用户"""
    try:
        body = request.get_json(force=True)
        return jsonify(store.add_user(body.get("name", "")))
    except Exception as e:
        return _err(e)


@app.post("/api/users/switch")
def api_user_switch():
    try:
        body = request.get_json(force=True)
        return jsonify(store.switch_user(body.get("id", "")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return _err(e)


# ---------- 静态产物 ----------

@app.get("/data/<path:p>")
def api_data(p):
    return send_from_directory(os.path.join(BASE, "data"), p)


@app.get("/out/<path:p>")
def api_out(p):
    return send_from_directory(os.path.join(BASE, "out"), p)


if __name__ == "__main__":
    print("AI错题本 → http://127.0.0.1:8899")
    app.run(host="127.0.0.1", port=8899, debug=False)
