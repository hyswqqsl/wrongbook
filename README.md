# AI 错题本（数学）

照片 → Word 排版机人审 → 错题知识库 → 针对性练习/出卷。设计详见 `DESIGN.md`。

## 启动（自包含工程，V1.0.1 起无外部依赖）

```bash
cd wrongbook
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp key.env.example key.env       # 填入你的 DEEPSEEK_API_KEY（或直接 export）
./run.sh                         # 打开 http://127.0.0.1:8899
```

## 使用流程

1. **录入（排版机）**：上传试卷照片 → 自动转录全部题目 + 抓取图形（默认原图，
   几何题可一键生成规范 SVG）→ 人审视图
   （对照原图确认/编辑、勾选要入库的错题、标注错因：马虎/不会/其他）→ 提交入库（自动过
   独立解题验伪）→ 可导出 Word 排版稿
2. **工作台**：数量/题型/难度/章节/错因分布、练习概览（连对 3 次→建议归档）
3. **刷题**：随机抽题 → 自判对错 → 连对 3 次弹归档建议；可生成相似题（正逆互换变式）
4. **出卷**：按章节/知识点/难度占比生成变式卷或原创卷 → 矢量 PDF

## 模块

| 文件 | 职责 |
|---|---|
| `app.py` | Flask 服务 + API |
| `reviewflow.py` | 排版机：多题转录 + 自动抓图（图形默认原图，SVG 按需生成）+ 草稿单 |
| `pipeline.py` | 入库质量链 / 相似题 / 出卷（复用父项目 geocheck·verify_answers·export_paper） |
| `wordexport.py` | Word 导出（LaTeX→matplotlib→PNG 嵌入 docx） |
| `figureflow.py` | 图形向量化子链（spec→geocheck 循环→SVG） |
| `engine/` | 内联数学引擎（geocheck/geoconstruct/make_figures/verify_answers/export_paper/generate_any/schema，源自 quiz-demo 出题系统） |
| `store.py` / `stats.py` | JSON 存储 / 工作台统计 |

## 已知限制（v1）

- 抓图框在特殊版面（图形紧贴文字/多图交错）可能偏移——人审界面有红框对照，可用
  【抓原图/手动框选】修正；重大偏差以重拍或忽略抓图处理
- Word 公式为高清图片（不可编辑）；OMML 原生公式列 v1.1
- 照片极度模糊/倾斜时转录可能失败，会明确报错而非入库脏数据
