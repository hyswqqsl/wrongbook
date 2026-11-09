# -*- coding: utf-8 -*-
"""app —— AI错题本 Flask 服务（本地单用户）"""
import os, sys, threading, traceback

import paths

BASE = paths.app_root()   # R1：静态资源/引擎随程序目录（frozen 态取 exe 目录）

BASE = paths.app_root()   # R1/R4：静态资源/引擎随程序目录（frozen 态取 exe 目录）

sys.path.insert(0, BASE)   # R4：冻结态下模块目录与数据目录分离，引擎/资源按程序目录找
from common import ROOT  # noqa

from flask import Flask, request, jsonify, send_file, send_from_directory
import store, stats
import figureflow
import ai_config
import license_client

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
app.json.sort_keys = False   # v1.2o：保留 dict 插入序（工作台 kp_by_chapter 按 KB 章序下发，Flask 默认字典序会得「一三二五四」错序）
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024   # 视频讲解上传可达数百 MB（v1.2）


# ---------- 激活门禁（R2）：未激活/锁定时仅放行首页与激活相关，其余 API 全 403 ----------

@app.before_request
def _license_gate():
    if not license_client.ENABLED:
        return None
    p = request.path
    if p == "/" or p.startswith("/static") or p.startswith("/api/license") or p == "/api/shutdown":
        return None
    st, _info = license_client.state()
    if st in ("ok", "grace"):
        return None
    return jsonify({"error": "license_" + st}), 403


@app.get("/api/license")
def api_license_status():
    """激活状态视图（前端启动首查；顺带触发到期后台心跳）"""
    license_client.maybe_heartbeat_async()
    return jsonify(license_client.status())


@app.post("/api/license/activate")
def api_license_activate():
    body = request.get_json(force=True, silent=True) or {}
    ok, err = license_client.activate(body.get("code", ""))
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


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
                "grade", "kp_sel", "tp_sel", "dest_topic", "video", "skip")
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


@app.post("/api/sheets/<sid>/rotate")
def api_sheet_rotate(sid):
    """↻ v1.3 人审兜底：照片顺时针旋转 90°（自动扶正转错方向时人工一键修正，可连点）"""
    try:
        import reviewflow
        return jsonify(reviewflow.rotate_sheet_photo(sid))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
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


# ---- 异步提交（v1.2r）：质量链（LLM 验伪+分析）每题可达数分钟，同步 HTTP 必超时——
# 改为后台线程执行 + 前端轮询进度；同 sheet 运行中拒绝重复提交（根治"重复点提交"隐患）
_commit_jobs = {}          # {sid: job dict}（进程内；服务重启丢失 → 前端轮询兜底超时提示）
_commit_lock = __import__("threading").Lock()
_COMMIT_KEEP = 30          # 保留最近 N 个已完成 job（供前端读取结果）


def _commit_job_gc():
    """清理多余已完成 job（调用方必须已持有 _commit_lock；Lock 不可重入）"""
    dones = [k for k, v in _commit_jobs.items() if v.get("state") in ("done", "error")]
    if len(dones) > _COMMIT_KEEP:
        for k in sorted(dones, key=lambda k: _commit_jobs[k].get("started", 0))[:-_COMMIT_KEEP]:
            _commit_jobs.pop(k, None)


@app.post("/api/sheets/<sid>/commit")
def api_sheet_commit(sid):
    import pipeline
    sheet = store.get_sheet(sid)
    if not sheet:
        return jsonify({"error": "不存在"}), 404
    if sheet.get("status") == "processed":
        return jsonify({"error": "该草稿单已处理"}), 400
    with _commit_lock:
        job = _commit_jobs.get(sid)
        if job and job.get("state") == "running":
            return jsonify({"error": "该照片正在录入中，请勿重复提交", "running": True}), 409
        job = {"state": "running", "started": __import__("time").time(),
               "ingested": 0, "topics": 0, "skipped": 0, "cur_no": None,
               "results": None, "error": None}
        _commit_jobs[sid] = job
        _commit_job_gc()

    def _run():
        try:
            def _progress(d):
                job["ingested"], job["topics"], job["skipped"] = d.get("ingested", 0), d.get("topics", 0), d.get("skipped", 0)
                job["cur_no"] = d.get("no")
            res = pipeline.ingest_selected(sheet, progress=_progress)
            job["results"] = res.get("results")
            job["state"] = "done"
        except Exception as e:
            job["error"] = str(e)[:300]
            job["state"] = "error"

    __import__("threading").Thread(target=_run, daemon=True).start()
    return jsonify({"job": sid, "state": "running"})


@app.get("/api/commit-status/<sid>")
def api_commit_status(sid):
    with _commit_lock:
        job = _commit_jobs.get(sid)
        if not job:
            return jsonify({"state": "idle"})
        return jsonify({k: job.get(k) for k in
                        ("state", "ingested", "topics", "skipped", "cur_no", "error", "results")})


@app.get("/api/sheets/<sid>/docx")
def api_sheet_docx(sid):
    try:
        import wordexport
        sheet = store.get_sheet(sid)
        if not sheet:
            return jsonify({"error": "不存在"}), 404
        out = os.path.join(paths.DATA_HOME, "out", "排版稿_{0}.docx".format(sid))
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


# ---------- 专题（V1.2：专题集 → 类别 → 典型例题 + 每用户练习） ----------

@app.get("/api/topics")
def api_topics():
    """专题全集（含例题 + 当前用户练习统计 + spec 渲染 SVG）"""
    try:
        import topics
        return jsonify(topics.view())
    except Exception as e:
        return _err(e)


@app.post("/api/topics/sets")
def api_topics_set_create():
    try:
        import topics
        body = request.get_json(force=True)
        return jsonify(topics.add_set(body.get("name", "")))
    except Exception as e:
        return _err(e)


@app.delete("/api/topics/sets/<sid>")
def api_topics_set_delete(sid):
    try:
        import topics
        return jsonify({"ok": topics.delete_set(sid)})
    except Exception as e:
        return _err(e)


@app.post("/api/topics/sets/<sid>/categories")
def api_topics_cat_create(sid):
    try:
        import topics
        b = request.get_json(force=True)
        return jsonify(topics.add_category(sid, b.get("name", ""), b.get("points", 3),
                                           b.get("difficulty", "medium"), b.get("no")))
    except Exception as e:
        return _err(e)


@app.patch("/api/topics/sets/<sid>/categories/<cid>")
def api_topics_cat_patch(sid, cid):
    try:
        import topics
        b = request.get_json(force=True)
        return jsonify(topics.patch_category(sid, cid, b))
    except Exception as e:
        return _err(e)


@app.delete("/api/topics/sets/<sid>/categories/<cid>")
def api_topics_cat_delete(sid, cid):
    try:
        import topics
        return jsonify({"ok": topics.delete_category(sid, cid)})
    except Exception as e:
        return _err(e)


@app.delete("/api/topics/examples/<eid>")
def api_topics_example_delete(eid):
    try:
        import topics
        return jsonify({"ok": topics.delete_example(eid)})
    except Exception as e:
        return _err(e)


@app.post("/api/topics/examples/<eid>/analyze")
def api_topics_example_analyze(eid):
    """🤖 v1.2q：例题按需分析（思路/易错点/受控知识点）——纯文本 LLM 一次调用"""
    try:
        import topics
        return jsonify(topics.analyze_example(eid))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return _err(e)


@app.patch("/api/topics/examples/<eid>")
def api_topics_example_patch(eid):
    """手编例题：video（视频讲解路径）/answer/analysis/text/choices"""
    try:
        import topics
        b = request.get_json(force=True)
        return jsonify(topics.patch_example(eid, b))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return _err(e)


@app.post("/api/topics/examples/<eid>/practice")
def api_topics_example_practice(eid):
    """记一次例题练习 {correct} → {streak, ready, count, correct}"""
    try:
        import topics
        b = request.get_json(force=True)
        return jsonify(topics.record_practice(eid, bool(b.get("correct"))))
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
    return send_from_directory(paths.DATA_HOME, p)


@app.get("/out/<path:p>")
def api_out(p):
    return send_from_directory(os.path.join(paths.DATA_HOME, "out"), p)


@app.post("/api/shutdown")
def api_shutdown():
    """关闭后台服务（前端右上角「退出」按钮调用）
    需自定义头 X-WrongBook-Exit: 1（跨站表单无法携带 → 阻断恶意网页静默关停本机服务）"""
    if request.headers.get("X-WrongBook-Exit") != "1":
        return jsonify({"error": "missing exit token"}), 403
    if request.remote_addr not in ("127.0.0.1", "::1", None):
        return jsonify({"error": "仅允许本机关闭服务"}), 403
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


def _is_port_in_use(port):
    """检测本机端口是否已被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _port_owner_is_ours(port):
    """探测占用者是否为本应用实例（/api/license 应答含 app_version 标识）"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/license" % port, timeout=2) as r:
            return "app_version" in json.loads(r.read().decode("utf-8") or "{}")
    except Exception:
        return False


def _native_alert(msg, title="AI错题本"):
    """原生弹窗提示（Windows MessageBoxW / macOS osascript）；失败静默（console 仍有输出）"""
    try:
        if sys.platform == "win32":
            import ctypes
            # 0x40 = MB_ICONINFORMATION, 0x1000 = MB_SYSTEMMODAL（置顶提示）
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x00000040 | 0x00001000)
        elif sys.platform == "darwin":
            import subprocess
            safe = msg.replace('"', "").replace("\\", "")
            subprocess.run(
                ["osascript", "-e", 'display alert "%s" message "%s"' % (title, safe)],
                check=False,
            )
    except Exception:
        pass


if __name__ == "__main__":
    # debug 模式：./run.sh --debug 或 FLASK_DEBUG=1 ./run.sh（改代码自动重载；仅开发用）
    port = 8899
    url = "http://127.0.0.1:%d" % port
    license_client.validate_on_startup()     # R4.0b：启动无条件同步验证（吊销/延期即时感知，最多阻塞 4s）
    license_client.start_heartbeat_loop()    # 运行中周期检查补发（10 分钟）
    debug = "--debug" in sys.argv or os.environ.get("FLASK_DEBUG") == "1"

    if getattr(sys, "frozen", False):
        # R4 打包态：固定端口单实例；已开启 → 拉起页面+弹窗提示后退出；端口被其他程序占用 → 明确报错
        import webbrowser
        if _is_port_in_use(port):
            if _port_owner_is_ours(port):
                webbrowser.open(url)   # 直接拉起已有实例的页面
                _native_alert(
                    "AI错题本程序已开启，请勿重复运行！\n\n当前服务地址：%s\n"
                    "（如需退出程序，请在浏览器页面右上角点击「⏻ 退出」）" % url,
                    "AI错题本 - 程序已开启")
                sys.exit(0)
            _native_alert(
                "端口 %d 已被其他程序占用，AI错题本无法启动。\n"
                "请关闭占用该端口的程序后重试（或重启电脑）。" % port,
                "AI错题本 - 启动失败")
            sys.exit(1)
        license_client.maybe_heartbeat_async()   # 启动时若已到期，先补一跳
        license_client.start_heartbeat_loop()    # 运行中周期心跳（定稿 24h）
        _t = threading.Timer(1.5, lambda: webbrowser.open(url))
        _t.daemon = True
        _t.start()
        print("AI错题本 →", url)
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
    else:
        if not debug and _is_port_in_use(port):
            if _port_owner_is_ours(port):
                _native_alert("AI错题本程序已开启，请勿重复运行！\n当前服务地址：%s" % url,
                              "AI错题本 - 程序已开启")
            else:
                _native_alert("端口 %d 已被其他程序占用，启动失败。" % port,
                              "AI错题本 - 启动失败")
            print("AI错题本已在运行中或端口被占 →", url)
            sys.exit(0)
        license_client.maybe_heartbeat_async()
        license_client.start_heartbeat_loop()
        print("AI错题本 → http://127.0.0.1:8899" + ("（debug）" if debug else ""))
        app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)
