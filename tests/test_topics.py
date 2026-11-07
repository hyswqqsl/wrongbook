# -*- coding: utf-8 -*-
"""专题模块回归测试（V1.2）：DESIGN.md 5.6

覆盖契约：
  1. 种子：首次加载种入「初二中考必考专题」10 类（名称/分值/难度），幂等不重种
  2. 专题集/类别 CRUD：新建、删除（连带孤儿图形清理）、难度受控词表
  3. 例题：add_example 落库 + 裁剪图复制进全局 topic_crops；同类查重跳过；
     删除例题连带删图形文件
  4. 练习记录（按用户）：连对 3 次 ready（已掌握）；答错重置连对
  5. 存入错题本：字段映射（source/难度/key_idea）；错题本查重拦截
  6. 提交链 ingest_selected：仅存专题零 LLM（make_client 不被调用）、
     草稿单写回 topic_example_id；双向多选（错题本+专题）；专题内重复算已处置
  7. API：GET /api/topics、集合/类别/练习 端点契约；异步提交（v1.2r）

全部离线：LLM 一律 stub（仅专题零 LLM 路径 + stub 质量链）。
"""
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image  # noqa: E402

import store  # noqa: E402
import topics  # noqa: E402
import pipeline  # noqa: E402


def _png_file(d, name="crop.png", color=(255, 255, 255)):
    fp = os.path.join(d, name)
    Image.new("RGB", (80, 60), color).save(fp, "PNG")
    return fp


def _temp_all():
    """整个数据根目录重定向到临时目录（先 set_test_mode 防意外，再 set_test_data_dir）。

    topics/topics.json、topic_crops/、users/（含每用户练习记录）全部随
    store._data_dir() 走 → 一处重定向全部隔离，绝不碰真实数据
    （借鉴 v0.7 test_dedup 数据事故的教训：测试必须先开 test 模式）。"""
    store.set_test_mode()
    tmp = tempfile.mkdtemp()
    store.set_test_data_dir(tmp)   # 重置 _INIT：下次 store 调用会在 tmp 内初始化用户表并重绑路径
    return tmp


def _stub_llm(ans="B", chapter="第二章 特殊三角形"):
    """stub 掉 pipeline 的 LLM 问答与客户端构造；记录 make_client 是否被调"""
    calls = {"make_client": 0}

    class _Msg:
        content = ('{"chapter":"%s","knowledge_points":["x"],"pitfall":"y",'
                   '"difficulty":2,"key_idea":"k","answer":"%s","ok":true,"issues":[]}'
                   % (chapter, ans))

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    class _Completions:
        def create(self, **kw):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    def fake_make_client(provider=None, vision=False):
        calls["make_client"] += 1
        return _Client(), "stub-model"

    pipeline.make_client = fake_make_client
    return calls


def _mk_sheet(questions):
    return store.add_sheet({"photo": None, "source": "测试卷", "questions": questions})


Q1 = "如图，在三角形ABC中，AB=AC，求证底角相等。"
Q2 = "已知等腰三角形周长为20，腰长为6，求底边长。"


class TestSeed(unittest.TestCase):
    def setUp(self):
        self.tmp = _temp_all()

    def test_seed_creates_10_categories(self):
        db = topics.load_topics()
        self.assertEqual(len(db["sets"]), 1)
        s = db["sets"][0]
        self.assertEqual(s["name"], "初二中考必考专题")
        self.assertEqual(len(s["categories"]), 10)
        by_no = {c["no"]: c for c in s["categories"]}
        self.assertEqual(by_no[1]["name"], "三边关系")
        self.assertEqual(by_no[6]["name"], "全等模型")
        self.assertEqual(by_no[10]["name"], "将军饮马")
        # 难度分层：1-3 简单；4,5,7,8,9 中等；6,10 压轴
        for no in (1, 2, 3):
            self.assertEqual(by_no[no]["difficulty"], "easy", "第%d类应为简单" % no)
        for no in (4, 5, 7, 8, 9):
            self.assertEqual(by_no[no]["difficulty"], "medium", "第%d类应为中等" % no)
        for no in (6, 10):
            self.assertEqual(by_no[no]["difficulty"], "final", "第%d类应为压轴" % no)
        # 分值抽查（与考点清单一致）
        self.assertEqual(by_no[6]["points"], 12)
        self.assertEqual(by_no[10]["points"], 3)
        # 每类都带空例题列表与 id
        for c in s["categories"]:
            self.assertEqual(c["examples"], [])
            self.assertTrue(c["id"].startswith("tc"))

    def test_seed_idempotent(self):
        s1 = topics.load_topics()["sets"][0]
        sid = s1["id"]
        topics.load_topics()   # 再读不重种
        db = topics.load_topics()
        self.assertEqual(len(db["sets"]), 1)
        self.assertEqual(db["sets"][0]["id"], sid)
        # 新增自定义集后，重读也不会再种入种子
        topics.add_set("自定义集")
        self.assertEqual(len(topics.load_topics()["sets"]), 2)


class TestSetCatCrud(unittest.TestCase):
    def setUp(self):
        self.tmp = _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]

    def test_add_set_and_category(self):
        s = topics.add_set("初二上学期期末")
        c = topics.add_category(s["id"], "一次函数", points=12, difficulty="final")
        self.assertEqual(c["no"], 1)   # 新集第一类
        self.assertEqual(c["examples"], [])
        got = topics.find_category(s["id"], c["id"])
        self.assertEqual(got[1]["name"], "一次函数")

    def test_add_category_bad_difficulty_rejected(self):
        with self.assertRaises(ValueError):
            topics.add_category(self.sid, "x", difficulty="hard")

    def test_delete_set_removes_orphan_crops(self):
        crops = os.path.join(self.tmp, "crops")
        os.makedirs(crops, exist_ok=True)
        src = _png_file(crops, "sheet_q1.png")
        cat = topics.load_topics()["sets"][0]["categories"][0]
        ex = topics.add_example(self.sid, cat["id"], Q1, answer="B",
                                crop_src=src)
        self.assertTrue(os.path.exists(ex["figure_crop"]))
        self.assertIn("topic_crops", ex["figure_crop"])
        self.assertTrue(topics.delete_set(self.sid))
        self.assertFalse(os.path.exists(ex["figure_crop"]))   # 孤儿图形被清理
        self.assertFalse(topics.delete_set("no-such"))


class TestExamples(unittest.TestCase):
    def setUp(self):
        self.tmp = _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]

    def test_add_example_fields_and_crop_copy(self):
        crops = os.path.join(self.tmp, "crops")
        os.makedirs(crops, exist_ok=True)
        src = _png_file(crops, "q9.png")
        ex = topics.add_example(
            self.sid, self.cat["id"], Q1, choices=[{"key": "A", "text": "3"}],
            answer="A", ex_type="single", analysis="等腰性质", crop_src=src,
            spec={"points": []}, source="测试卷")
        self.assertTrue(ex["id"].startswith("te"))
        self.assertEqual(ex["type"], "single")
        self.assertEqual(ex["answer"], "A")
        self.assertEqual(ex["figure_spec"], {"points": []})
        # 图形复制进全局 topic_crops/<eid>.png（与用户 crops 解耦）
        self.assertTrue(os.path.exists(ex["figure_crop"]))
        self.assertEqual(os.path.basename(ex["figure_crop"]), ex["id"] + ".png")

    def test_duplicate_in_category_skipped(self):
        topics.add_example(self.sid, self.cat["id"], Q1, answer="B")
        with self.assertRaises(topics.DuplicateExample):
            topics.add_example(self.sid, self.cat["id"],
                               "如图,在三角形ABC中,AB=AC,求证:底角相等。", answer="B")
        # 不同题不拦
        ex2 = topics.add_example(self.sid, self.cat["id"], Q2, answer="8")
        self.assertTrue(ex2["id"].startswith("te"))

    def test_delete_example_removes_file(self):
        crops = os.path.join(self.tmp, "crops")
        os.makedirs(crops, exist_ok=True)
        ex = topics.add_example(self.sid, self.cat["id"], Q1, crop_src=_png_file(crops))
        fp = ex["figure_crop"]
        self.assertTrue(topics.delete_example(ex["id"]))
        self.assertFalse(os.path.exists(fp))
        self.assertFalse(topics.delete_example("ghost"))


class TestPractice(unittest.TestCase):
    def setUp(self):
        _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]
        self.ex = topics.add_example(self.sid, self.cat["id"], Q1, answer="B")

    def test_streak_and_ready(self):
        r1 = topics.record_practice(self.ex["id"], True)
        self.assertEqual((r1["streak"], r1["ready"], r1["count"]), (1, False, 1))
        topics.record_practice(self.ex["id"], True)
        r3 = topics.record_practice(self.ex["id"], True)
        self.assertEqual((r3["streak"], r3["ready"]), (3, True))   # 连对3次=已掌握
        r4 = topics.record_practice(self.ex["id"], False)          # 答错重置连对
        self.assertEqual((r4["streak"], r4["ready"]), (0, False))
        self.assertEqual(r4["count"], 4)
        self.assertEqual(r4["correct"], 3)

    def test_view_merges_user_practice(self):
        topics.record_practice(self.ex["id"], True)
        v = topics.view()
        ex = v["sets"][0]["categories"][0]["examples"][0]
        self.assertEqual(ex["practice"]["count"], 1)
        self.assertEqual(ex["practice"]["streak"], 1)
        self.assertFalse(ex["practice"]["ready"])
        self.assertIsNone(ex["figure_svg"])   # 无有效 spec → 无 SVG


class TestCommitFlow(unittest.TestCase):
    def setUp(self):
        _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]

    def _draft_q(self, no, text, **kw):
        q = {"no": no, "type": "single", "question": text,
             "choices": [{"key": "A", "text": "3"}, {"key": "B", "text": "8"}],
             "printed_answer": "B", "has_figure": False,
             "figure_crops": [], "selected": False}
        q.update(kw)
        return q

    def test_topic_only_zero_llm(self):
        """仅存专题：make_client 不被调用（0 token），草稿单写回 topic_example_id"""
        calls = _stub_llm()
        sheet = _mk_sheet([self._draft_q(1, Q1, dest_topic={"set": self.sid, "cat": self.cat["id"]})])
        out = pipeline.ingest_selected(sheet)
        self.assertEqual(calls["make_client"], 0)   # 全程零 LLM
        self.assertEqual(len(out["results"]), 1)
        r = out["results"][0]
        self.assertTrue(r["topic_id"].startswith("te"))
        self.assertNotIn("id", r)   # 未入错题本
        ex = topics.find_example(r["topic_id"])[2]
        self.assertEqual(ex["answer"], "B")   # 照片答案作例题答案
        self.assertEqual(ex["text"], Q1)
        # 草稿单：该题已处置；单内无剩余 → processed
        sh = store.get_sheet(sheet["id"])
        self.assertEqual(sh["questions"][0].get("topic_example_id"), r["topic_id"])
        self.assertEqual(sh["status"], "processed")

    def test_both_dest_book_and_topic(self):
        """双向多选：先存专题（零 LLM 部分）再走错题本质量链"""
        calls = _stub_llm(ans="B")
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         dest_topic={"set": self.sid, "cat": self.cat["id"]})])
        out = pipeline.ingest_selected(sheet)
        self.assertEqual(calls["make_client"], 1)   # 错题本链需要 LLM
        r = out["results"][0]
        self.assertTrue(r["id"])   # 错题记录 id
        self.assertTrue(r["topic_id"].startswith("te"))
        self.assertEqual(len(store.load()["questions"]), 1)
        self.assertEqual(len(topics.find_category(self.sid, self.cat["id"])[1]["examples"]), 1)
        sh = store.get_sheet(sheet["id"])
        self.assertEqual(sh["status"], "processed")

    def test_topic_dup_counts_disposed(self):
        """专题内重复：跳过但算已处置（不阻塞 processed）"""
        topics.add_example(self.sid, self.cat["id"], Q1, answer="B")
        _stub_llm()
        sheet = _mk_sheet([self._draft_q(1, Q1, dest_topic={"set": self.sid, "cat": self.cat["id"]})])
        out = pipeline.ingest_selected(sheet)
        r = out["results"][0]
        self.assertIsNone(r.get("topic_id"))   # 未存入
        self.assertIn("重复", "".join(r["report"]))
        self.assertEqual(store.get_sheet(sheet["id"])["status"], "processed")

    def test_unselected_no_dest_untouched(self):
        """未勾选未选专题的题保持未处置 → pending"""
        _stub_llm()
        sheet = _mk_sheet([self._draft_q(1, Q1)])
        out = pipeline.ingest_selected(sheet)
        self.assertEqual(out["results"], [])
        self.assertEqual(store.get_sheet(sheet["id"])["status"], "pending")

    def test_topic_failure_not_blocking_book(self):
        """专题类别不存在：报错行入 report，错题本链不受影响"""
        _stub_llm()
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         dest_topic={"set": self.sid, "cat": "ghost"})])
        out = pipeline.ingest_selected(sheet)
        r = out["results"][0]
        self.assertIn("id", r)
        self.assertTrue(any("存入失败" in x for x in r["report"]))

    def test_manual_skip_disposes_question(self):
        """v1.2 显式跳过：每题必须明确去向；跳过算已处置（processed），不入库不入专题"""
        _stub_llm()
        sheet = _mk_sheet([
            self._draft_q(1, Q1, selected=True),                       # 入错题本
            self._draft_q(2, Q2, skip=True),                           # 显式跳过
        ])
        out = pipeline.ingest_selected(sheet)
        self.assertEqual(len(out["results"]), 2)
        r2 = [r for r in out["results"] if r["no"] == 2][0]
        self.assertTrue(r2.get("skipped_manual"))
        self.assertEqual(len(store.load()["questions"]), 1)   # 只有第1题入库
        self.assertEqual(topics.view()["sets"][0]["categories"][0]["examples"], [])
        sh = store.get_sheet(sheet["id"])
        self.assertTrue(sh["questions"][1].get("skip"))
        self.assertEqual(sh["status"], "processed")   # 全部处置完

    def test_chapter_from_kb_structure(self):
        """v1.2o：chapter 回归教材章（knowledge_points.json 结构）——转录期受控 kp 反查所属章，
        kp_path=[章, "", 首kp]；专题类别（tp_sel）不再写工作台章节"""
        _stub_llm()
        cat = self.cat
        sid = self.sid
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         tp_sel={"set": sid, "cat": cat["id"]},
                                         chapter="第一章 三角形",
                                         kps=["全等三角形的性质", "三线合一"])])
        pipeline.ingest_selected(sheet)
        an = store.load()["questions"][0]["analysis"]
        self.assertEqual(an["chapter"], "第一章 三角形")            # 教材章（非专题类别）
        self.assertEqual(an["kp_path"], ["第一章 三角形", "", "全等三角形的性质"])
        self.assertEqual(an["knowledge_points"], ["全等三角形的性质", "三线合一"])

    def test_tp_sel_alone_no_longer_drives_chapter(self):
        """v1.2o：只有 tp_sel（无教材章/受控 kp）→ 章节回落 LLM 分析（stub 输出第二章），
        不再写「专题集 · 类别」专题结构"""
        _stub_llm()
        cat = self.cat
        sid = self.sid
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         tp_sel={"set": sid, "cat": cat["id"]})])
        pipeline.ingest_selected(sheet)
        an = store.load()["questions"][0]["analysis"]
        self.assertEqual(an["chapter"], "第二章 特殊三角形")   # LLM 分析兜底（CHAPTERS 受控词表）
        self.assertNotIn("·", an["chapter"])                  # 不含专题二级名

    def test_kps_without_chapter_falls_to_analysis(self):
        """有受控 kp 但无章名（旧草稿）→ 章走 LLM 分析兜底，kp 仍落 knowledge_points"""
        _stub_llm()
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         kps=["等腰三角形的性质"])])
        pipeline.ingest_selected(sheet)
        an = store.load()["questions"][0]["analysis"]
        self.assertEqual(an["chapter"], "第二章 特殊三角形")
        self.assertEqual(an["knowledge_points"], ["等腰三角形的性质"])
        self.assertEqual(an["kp_path"], [])

    def test_kps_controlled_land_in_knowledge_points(self):
        """v1.2k 转录期受控 kps → analysis.knowledge_points（LLM 自由分析仅作兜底）"""
        _stub_llm()
        cat = self.cat
        sid = self.sid
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         chapter="第一章 三角形",
                                         kps=["等腰三角形的性质", "三线合一"])])
        pipeline.ingest_selected(sheet)
        an = store.load()["questions"][0]["analysis"]
        self.assertEqual(an["knowledge_points"], ["等腰三角形的性质", "三线合一"])
        # 无受控 kps 的旧草稿 → 回落 LLM 自由分析（stub 输出 knowledge_points=["x"]；换题避免查重跳过）
        sheet2 = _mk_sheet([self._draft_q(1, Q2, selected=True)])
        pipeline.ingest_selected(sheet2)
        an2 = store.load()["questions"][-1]["analysis"]   # 第二次入库追加在库尾
        self.assertEqual(an2["knowledge_points"], ["x"])

    def test_kp_sel_legacy_still_works(self):
        """旧草稿兼容：无 tp_sel 时 kp_sel 仍生效"""
        _stub_llm()
        sheet = _mk_sheet([self._draft_q(1, Q1, selected=True,
                                         kp_sel={"chapter": "第一章 三角形",
                                                 "section": "1.1 认识三角形", "kp": "内角和"})])
        pipeline.ingest_selected(sheet)
        rec = store.load()["questions"][0]
        self.assertEqual(rec["analysis"]["chapter"], "第一章 三角形")
        self.assertEqual(rec["analysis"]["kp_path"], ["第一章 三角形", "1.1 认识三角形", "内角和"])


class TestApi(unittest.TestCase):
    def setUp(self):
        _temp_all()
        from app import app
        self.app = app
        self.c = app.test_client()

    def test_topics_view_endpoint(self):
        r = self.c.get("/api/topics")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j["sets"][0]["name"], "初二中考必考专题")

    def test_set_category_practice_tobook_endpoints(self):
        # 新建专题集 + 类别
        r = self.c.post("/api/topics/sets", json={"name": "期末专题"})
        self.assertEqual(r.status_code, 200)
        sid = r.get_json()["id"]
        r = self.c.post(f"/api/topics/sets/{sid}/categories",
                        json={"name": "一次函数", "points": 12, "difficulty": "final"})
        cid = r.get_json()["id"]
        # 手工造例题（pipeline 场景已测；此处直接调模块）
        crops = tempfile.mkdtemp()
        ex = topics.add_example(sid, cid, Q1, answer="B", crop_src=_png_file(crops))
        # 练习
        r = self.c.post(f"/api/topics/examples/{ex['id']}/practice", json={"correct": True})
        self.assertEqual(r.get_json()["streak"], 1)
        # 删除类别（连带例题；图形无其他引用 → 一并删除）
        r = self.c.delete(f"/api/topics/sets/{sid}/categories/{cid}")
        self.assertTrue(r.get_json()["ok"])
        self.assertFalse(os.path.exists(ex["figure_crop"]))


class TestExampleAnalysis(unittest.TestCase):
    """v1.2q 例题分析（思路/易错点/受控知识点）与错题本详情同口径"""

    def setUp(self):
        self.tmp = _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]
        from app import app
        self.c = app.test_client()

    def test_add_and_patch_analysis_fields(self):
        """add_example 带 kps；patch_example 允许 key_idea/pitfall/kps"""
        ex = topics.add_example(self.sid, self.cat["id"], Q1, answer="B", kps=["角平分线的性质"])
        self.assertEqual(ex["kps"], ["角平分线的性质"])
        r = self.c.patch(f"/api/topics/examples/{ex['id']}",
                         json={"key_idea": "双角平分线夹角模型", "pitfall": "漏了直角", "kps": ["角平分线的性质"]})
        j = r.get_json()
        self.assertEqual(j["key_idea"], "双角平分线夹角模型")
        self.assertEqual(j["pitfall"], "漏了直角")
        self.assertEqual(j["kps"], ["角平分线的性质"])

    def test_dual_destination_backfills_example_analysis(self):
        """双目的地：错题本链分析完成后回写专题例题（思路/易错/受控kps，0 额外 token）"""
        _stub_llm()
        sheet = _mk_sheet([{"no": 1, "type": "single", "question": Q1,
                            "choices": [{"key": "A", "text": "3"}],
                            "printed_answer": "B", "has_figure": False,
                            "figure_crops": [], "selected": True,
                            "kps": ["角平分线的性质"],
                            "dest_topic": {"set": self.sid, "cat": self.cat["id"]}}])
        out = pipeline.ingest_selected(sheet)
        ex = topics.find_example(out["results"][0]["topic_id"])[2]
        self.assertEqual(ex["key_idea"], "k")            # stub 分析输出 key_idea="k"
        self.assertEqual(ex["pitfall"], "y")
        self.assertIn("角平分线的性质", ex["kps"])
        self.assertTrue(any("例题已带思路" in line for line in out["results"][0]["report"]))

    def test_analyze_endpoint_stubbed(self):
        """🤖 分析端点：stub LLM 输出 → 受控 kps 收敛 → 写回例题"""
        ex = topics.add_example(self.sid, self.cat["id"], Q1, answer="B")
        import reviewflow as rf
        real_ask = rf.ask_json_vision
        kb = [{"kp": "角平分线的性质", "chapter": "第一章 三角形"}]
        rf.ask_json_vision = lambda *a, **kw: {"key_idea": "见角平分线想对称",
                                               "pitfall": "忘证相等就用性质",
                                               "kps": ["角平分线的性质定理"]}
        import kbtree
        real_kp = kbtree.knowledge_points
        kbtree.knowledge_points = lambda: {"grade": "八上", "chapters": [
            {"title": "第一章 三角形", "kps": ["角平分线的性质"]}]}
        try:
            r = self.c.post(f"/api/topics/examples/{ex['id']}/analyze")
            j = r.get_json()
        finally:
            rf.ask_json_vision = real_ask
            kbtree.knowledge_points = real_kp
        self.assertEqual(j["key_idea"], "见角平分线想对称")
        self.assertEqual(j["kps"], ["角平分线的性质"])   # 「角平分线的性质定理」包含匹配回清单名
        ex2 = topics.find_example(ex["id"])[2]
        self.assertEqual(ex2["pitfall"], "忘证相等就用性质")


class TestAsyncCommit(unittest.TestCase):
    """v1.2r 异步提交：POST 立即返回 job，后台线程执行，GET 轮询进度/结果；
    同 sheet 运行中重复提交 → 409（根治"重复点提交"隐患）"""

    def setUp(self):
        self.tmp = _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]
        from app import app
        self.c = app.test_client()

    def test_commit_returns_immediately_and_job_completes(self):
        _stub_llm()
        sheet = _mk_sheet([self._dq(1, Q1, selected=True)])
        r = self.c.post(f"/api/sheets/{sheet['id']}/commit")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("state"), "running")
        # 轮询至完成（stub LLM 秒级）
        import time as _t
        for _ in range(50):
            st = self.c.get(f"/api/commit-status/{sheet['id']}").get_json()
            if st["state"] in ("done", "error"):
                break
            _t.sleep(0.05)
        self.assertEqual(st["state"], "done")
        self.assertEqual(len(st["results"]), 1)
        self.assertEqual(st["ingested"], 1)
        self.assertTrue(st["results"][0].get("id"))   # 题目已入库带 id

    def test_duplicate_commit_while_running_rejected(self):
        _stub_llm()
        sheet = _mk_sheet([self._dq(1, Q1, selected=True)])
        import pipeline as _pl
        real = _pl.ingest_selected
        blocked = __import__("threading").Event()
        release = __import__("threading").Event()

        def slow_ingest(sheet, provider=None, progress=None):
            blocked.set()
            release.wait(10)
            return real(sheet, provider, progress)

        _pl.ingest_selected = slow_ingest
        try:
            r1 = self.c.post(f"/api/sheets/{sheet['id']}/commit")
            self.assertEqual(r1.status_code, 200)
            blocked.wait(5)
            r2 = self.c.post(f"/api/sheets/{sheet['id']}/commit")
            self.assertEqual(r2.status_code, 409)
            self.assertTrue(r2.get_json().get("running"))
            release.set()
            import time as _t
            for _ in range(100):
                st = self.c.get(f"/api/commit-status/{sheet['id']}").get_json()
                if st["state"] in ("done", "error"):
                    break
                _t.sleep(0.05)
            self.assertEqual(st["state"], "done")
        finally:
            _pl.ingest_selected = real

    def _dq(self, no, text, **kw):
        q = {"no": no, "type": "single", "question": text,
             "choices": [{"key": "A", "text": "3"}, {"key": "B", "text": "8"}],
             "printed_answer": "B", "has_figure": False,
             "figure_crops": [], "selected": True}
        q.update(kw)
        return q


class TestCropSharedProtection(unittest.TestCase):
    """v1.2q 图形交叉引用保护：历史 to_book/双目的地使错题记录与专题例题共享
    topic_crops 文件（v1.2t 起 to_book 入口移除，但存量共享数据仍需守护），
    删任一方不得丢另一方图形（q20261106-111019 图形丢失事故回归）"""

    def setUp(self):
        self.tmp = _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]

    def _png(self):
        import struct, zlib
        path = os.path.join(self.tmp, "src.png")
        def chunk(typ, data):
            c = struct.pack(">I", len(data)) + typ + data
            return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
        open(path, "wb").write(png)
        return path

    def test_deleting_book_copy_keeps_example_figure(self):
        """删 to_book 产出的错题 → 例题图形保留；再删例题 → 文件才删（事故场景正向）"""
        ex = topics.add_example(self.sid, self.cat["id"], Q1, answer="B", crop_src=self._png())
        crop = ex["figure_crop"]
        self.assertTrue(os.path.exists(crop))
        # 手动构造历史共享形态（= 旧 to_book 产出的错题记录：question.figure_crop 指向例题图形）
        rec = store.add({"type": "single", "source": "专题 · 历史",
                         "question": {"text": ex["text"], "choices": None,
                                      "figure_crop": crop},
                         "printed_answer": "B", "verified_answer": "B",
                         "verify_status": "pass", "mistake_reason": {"kind": "unknown"},
                         "analysis": {"chapter": "", "kp_path": [], "knowledge_points": [],
                                      "difficulty": 2},
                         "practice": [], "flags": {}})
        self.assertTrue(store.remove(rec["id"]))                 # 删错题副本
        self.assertTrue(os.path.exists(crop))                    # 例题仍引用 → 保留（旧代码此处丢图）
        self.assertTrue(topics.delete_example(ex["id"]))         # 再删例题
        self.assertFalse(os.path.exists(crop))                   # 全无引用 → 连带删除


class TestVideos(unittest.TestCase):
    """v1.2p 视频讲解改链接：端点移除 / 链接双链透传 / 历史文件清理链保留"""

    def setUp(self):
        self.tmp = _temp_all()
        self.sid = topics.load_topics()["sets"][0]["id"]
        self.cat = topics.load_topics()["sets"][0]["categories"][0]
        from app import app
        self.c = app.test_client()

    def test_upload_endpoint_removed(self):
        """v1.2p：视频上传端点已移除（改存链接，不再上传文件）"""
        r = self.c.post("/api/videos", data={"file": (io.BytesIO(b"fakevideo"), "a.mp4")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 404)

    def test_video_flows_to_topic_and_book(self):
        """v1.2p：视频改存链接（B站 URL 含 t= 定位）——双链透传不变"""
        vpath = "https://www.bilibili.com/video/BV1N9Jt6zE67/?spm_id_from=333.337&t=340"
        _stub_llm()
        sheet = _mk_sheet([{"no": 1, "type": "single", "question": Q1,
                            "choices": [{"key": "A", "text": "3"}],
                            "printed_answer": "B", "has_figure": False,
                            "figure_crops": [], "selected": True,
                            "video": vpath,
                            "dest_topic": {"set": self.sid, "cat": self.cat["id"]}}])
        out = pipeline.ingest_selected(sheet)
        r = out["results"][0]
        self.assertTrue(r["topic_id"])
        self.assertEqual(topics.find_example(r["topic_id"])[2]["video"], vpath)  # 专题例题带视频
        rec = store.load()["questions"][0]
        self.assertEqual(rec["video"], vpath)   # 错题记录带视频
        # 详情视图返回 video 字段
        rec_view = store.get(rec["id"])
        self.assertEqual(rec_view["video"], vpath)

    def test_example_video_patch(self):
        """v1.2p：PATCH 例题视频链接；删除例题无文件清理（链接无本体）也不报错"""
        ex = topics.add_example(self.sid, self.cat["id"], Q1, answer="B")
        self.assertIsNone(ex["video"])
        vurl = "https://www.bilibili.com/video/BV1N9Jt6zE67/?t=340"
        r = self.c.patch(f"/api/topics/examples/{ex['id']}", json={"video": vurl})
        self.assertEqual(r.get_json()["video"], vurl)
        self.assertTrue(topics.delete_example(ex["id"]))   # 链接视频：无文件可删，不异常
        # 清空链接 = 移除
        ex2 = topics.add_example(self.sid, self.cat["id"], Q2, answer="8")
        r2 = self.c.patch(f"/api/topics/examples/{ex2['id']}", json={"video": None})
        self.assertIsNone(r2.get_json()["video"])

    def test_video_url_in_wrongbook_delete_no_crash(self):
        """错题带链接视频：删除错题不做文件清理、不异常"""
        vurl = "https://www.bilibili.com/video/BV1N9Jt6zE67/?t=340"
        rec = store.add({"type": "fill", "question": {"text": Q1, "choices": None},
                         "analysis": {"chapter": "", "kp_path": [], "knowledge_points": [],
                                      "difficulty": 2}, "video": vurl})
        self.assertTrue(store.remove(rec["id"]))

    def test_video_shared_between_book_and_topic(self):
        """历史本地文件视频（v1.2 上传）：双目的地删错题 → 例题仍引用，文件保留；
        再删例题 → 才删文件（v1.2p 起仅存量文件走此清理链，链接视频不涉及）"""
        vpath = os.path.join(self.tmp, "videos", "shared.mp4")
        os.makedirs(os.path.dirname(vpath), exist_ok=True)
        open(vpath, "wb").write(b"v")
        _stub_llm()
        sheet = _mk_sheet([{"no": 1, "type": "single", "question": Q2,
                            "choices": None, "printed_answer": "8", "has_figure": False,
                            "figure_crops": [], "selected": True, "video": vpath,
                            "dest_topic": {"set": self.sid, "cat": self.cat["id"]}}])
        out = pipeline.ingest_selected(sheet)
        qid = out["results"][0]["id"]
        eid = out["results"][0]["topic_id"]
        store.remove(qid)                       # 删错题
        self.assertTrue(os.path.exists(vpath))  # 专题例题仍引用 → 保留
        topics.delete_example(eid)              # 删例题（无引用）→ 删除
        self.assertFalse(os.path.exists(vpath))


if __name__ == "__main__":
    unittest.main()
