# -*- coding: utf-8 -*-
"""GeoGebra 图形工作台（v1.1a）回归测试：DESIGN.md 5.5

覆盖契约：
  1. ggbflow.gen_commands：题干→命令列表；prev_errors 注入重试 prompt；空题干拒绝
  2. POST /api/ggb-script：通用命令生成端点（stub LLM）
  3. POST /api/sheets/<sid>/questions/<no>/ggb-figure：_g 文件幂等覆盖 + figure_ggb 存档
     + 字段联动（figure_crops/spec/svg/mode/note）
  4. POST /api/questions/<id>/figure（入库后改图）：覆盖原 figure_crop / 无图老题新建
     <qid>_g.png + 清 spec + 存 ggb
  5. 删除入库题连带删除改图新文件（引用清理链不断）

全部离线：LLM 调用一律 stub。
"""
import base64
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


def _png_bytes(w=80, h=60):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def _data_url(w=80, h=60):
    return "data:image/png;base64," + base64.b64encode(_png_bytes(w, h)).decode()


_recorded = {"uri": None}


def _stub_client_returns(commands):
    """造一个 stub：替换 reviewflow.ask_json_vision，记录 prompt/uri 供断言（不发真实请求）。"""
    import reviewflow

    def fake_ask(client, model, prompt, uri, max_tokens=16000):
        _recorded["prompt"] = prompt
        _recorded["uri"] = uri
        return {"commands": commands}

    reviewflow.ask_json_vision = fake_ask


class TestGenCommands(unittest.TestCase):

    def tearDown(self):
        import reviewflow
        if hasattr(reviewflow, "_orig_ask"):
            reviewflow.ask_json_vision = reviewflow._orig_ask

    def test_commands_extracted_and_prev_errors_injected(self):
        import ggbflow
        import reviewflow
        reviewflow._orig_ask = reviewflow.ask_json_vision
        _stub_client_returns(["A=(0,0)", "B=(4,0)", "Segment(A,B)"])
        cmds = ggbflow.gen_commands("如图，AB=4 …", client="stub", model="stub")
        self.assertEqual(cmds, ["A=(0,0)", "B=(4,0)", "Segment(A,B)"])
        self.assertIn("AB=4", _recorded["prompt"])
        self.assertIsNone(_recorded["uri"])   # 无对照图 → 纯文本调用

        _stub_client_returns(["A=(0,0)"])
        ggbflow.gen_commands("如图", prev_errors=["第3行: Segment(A,B)"], client="stub", model="stub")
        self.assertIn("Segment(A,B)", _recorded["prompt"])   # 失败行进入重试 prompt
        self.assertIn("执行失败", _recorded["prompt"])

    def test_uri_sends_image_with_hint(self):
        """v1.1.0e：有对照图 → 视觉调用（uri 透传 + prompt 布局对齐提示）"""
        import ggbflow
        import reviewflow
        reviewflow._orig_ask = reviewflow.ask_json_vision
        _stub_client_returns(["A=(0,0)"])
        uri = "data:image/png;base64,iVBORw0KGgo="
        ggbflow.gen_commands("如图", client="stub", model="stub", uri=uri)
        self.assertEqual(_recorded["uri"], uri)
        self.assertIn("附图", _recorded["prompt"])
        self.assertIn("与图一致", _recorded["prompt"])

    def test_empty_question_rejected(self):
        import ggbflow
        with self.assertRaises(ValueError):
            ggbflow.gen_commands("  ", client="stub", model="stub")

    def test_empty_commands_raises(self):
        import ggbflow
        import reviewflow
        reviewflow._orig_ask = reviewflow.ask_json_vision
        _stub_client_returns([])
        with self.assertRaises(RuntimeError):
            ggbflow.gen_commands("如图", client="stub", model="stub")


class TestGgbFigureApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import store
        store.set_test_data_dir(self.tmp)
        from app import app
        app.config["TESTING"] = True
        self.app = app
        self.c = app.test_client()
        # ggbflow.gen_commands stub（命令生成端点）
        import reviewflow
        if not hasattr(reviewflow, "_orig_ask"):
            reviewflow._orig_ask = reviewflow.ask_json_vision
            reviewflow.ask_json_vision = lambda c, m, p, u, max_tokens=16000: {
                "commands": ["A=(0,0)", "Segment(A,B)"]}
        self.sheet = store.add_sheet({"source": "测试卷", "questions": [
            {"no": 1, "question": "如图，在△ABC中…", "has_figure": True,
             "figure_crops": [], "figure_mode": "none", "selected": False}]})

    def tearDown(self):
        import store
        import reviewflow
        if hasattr(reviewflow, "_orig_ask"):
            reviewflow.ask_json_vision = reviewflow._orig_ask
            del reviewflow._orig_ask
        store.set_test_data_dir(None)

    def test_ggb_script_endpoint(self):
        r = self.c.post("/api/ggb-script", json={"question": "如图，AB=AC"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["commands"], ["A=(0,0)", "Segment(A,B)"])

    def test_ggb_script_endpoint_with_ref_sends_vision(self):
        """v1.1.0e：端点读 ref_crop 文件转 data URI → 视觉调用；文件不存在降级纯文本"""
        import os
        import store
        import reviewflow
        reviewflow._orig_ask = reviewflow.ask_json_vision
        _stub_client_returns(["A=(0,0)"])
        orig = os.path.join(store.CROP_DIR, "{0}_q1_0.png".format(self.sheet["id"]))
        os.makedirs(store.CROP_DIR, exist_ok=True)
        with open(orig, "wb") as f:
            f.write(_png_bytes())
        r = self.c.post("/api/ggb-script", json={"question": "如图", "ref_crop": orig})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["vision"])
        self.assertTrue(_recorded["uri"].startswith("data:image/png;base64,"))
        r2 = self.c.post("/api/ggb-script", json={"question": "如图", "ref_crop": "/no/such.png"})
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.get_json()["vision"])
        self.assertIsNone(_recorded["uri"])

    def test_ggb_figure_saves_and_archives_script(self):
        import os
        import store
        sid, no = self.sheet["id"], 1
        # 预置自动抓图 _0 文件（转录时已有）→ 导出后应存档为对照图 figure_ggb_ref
        os.makedirs(store.CROP_DIR, exist_ok=True)
        orig = os.path.join(store.CROP_DIR, "{0}_q1_0.png".format(sid))
        with open(orig, "wb") as f:
            f.write(_png_bytes())
        r = self.c.post("/api/sheets/{0}/questions/1/ggb-figure".format(sid),
                        json={"png": _data_url(), "commands": ["A=(0,0)"]})
        self.assertEqual(r.status_code, 200)
        path = r.get_json()["figure_crops"][0]
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.endswith("_q1_g.png"))
        q = store.get_sheet(sid)["questions"][0]
        self.assertEqual(q["figure_crops"], [path])
        self.assertEqual(q["figure_ggb"], ["A=(0,0)"])
        self.assertEqual(q["figure_ggb_ref"], orig)   # 对照图自动推断为 _0
        self.assertIsNone(q["figure_spec"])
        self.assertEqual(q["figure_mode"], "crop")
        self.assertIn("GeoGebra", q["figure_note"])
        # 显式 ref_crop 优先于自动推断
        r2 = self.c.post("/api/sheets/{0}/questions/1/ggb-figure".format(sid),
                         json={"png": _data_url(90, 70), "commands": ["A=(1,1)"],
                               "ref_crop": orig})
        self.assertEqual(r2.get_json()["figure_crops"], [path])   # 幂等覆盖
        self.assertEqual(store.get_sheet(sid)["questions"][0]["figure_ggb_ref"], orig)

    def test_ggb_figure_bad_png_rejected(self):
        r = self.c.post("/api/sheets/{0}/questions/1/ggb-figure".format(self.sheet["id"]),
                        json={"png": "data:text/plain;base64,SGk=", "commands": []})
        self.assertEqual(r.status_code, 400)

    def _ingest_record(self, with_crop=True):
        """造一条入库记录（模拟 build_question_payload 产物）"""
        import store
        inner = {"text": "如图…", "choices": None, "figure_spec": {"points": [{"name": "A", "x": 0, "y": 0}]},
                 "figure_crop": None}
        if with_crop:
            p = os.path.join(store.CROP_DIR, "old_q6_0.png")
            os.makedirs(store.CROP_DIR, exist_ok=True)
            with open(p, "wb") as f:
                f.write(_png_bytes())
            inner["figure_crop"] = p
        return store.add({"source": "", "type": "fill", "question": inner,
                          "printed_answer": None, "verified_answer": "B",
                          "verify_status": "pass", "mistake_reason": {"kind": "careless", "note": ""},
                          "analysis": {}, "photo": None, "flags": {"needs_review": False}})

    def test_record_figure_overwrites_existing_crop_and_clears_spec(self):
        import os
        import store
        rec = self._ingest_record(with_crop=True)
        old_path = rec["question"]["figure_crop"]
        r = self.c.post("/api/questions/{0}/figure".format(rec["id"]),
                        json={"png": _data_url(120, 90), "commands": ["A=(2,2)"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["figure_crop"], old_path)   # 覆盖同一路径（图源唯一权威）
        self.assertTrue(os.path.exists(old_path))
        q = store.get(rec["id"])
        self.assertIsNone(q["question"]["figure_spec"])            # spec 清空（对齐 use_crop 语义）
        self.assertEqual(q["question"]["figure_ggb"], ["A=(2,2)"]) # 脚本存档 → 可再编辑
        self.assertEqual(q["question"]["figure_ggb_ref"], old_path) # 对照图 = 覆盖前的原裁剪图

    def test_record_figure_creates_new_file_when_no_crop(self):
        import os
        import store
        rec = self._ingest_record(with_crop=False)
        self.assertIsNone(rec["question"]["figure_crop"])
        r = self.c.post("/api/questions/{0}/figure".format(rec["id"]),
                        json={"png": _data_url(), "commands": ["A=(0,0)"]})
        self.assertEqual(r.status_code, 200)
        path = r.get_json()["figure_crop"]
        self.assertTrue(path.endswith(rec["id"] + "_g.png"))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(store.get(rec["id"])["question"]["figure_crop"], path)
        # 删除题目 → 新文件连带清理（引用链不断）
        self.assertTrue(store.remove(rec["id"]))
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
