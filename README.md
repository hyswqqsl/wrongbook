# AI 错题本（数学）

照片 → Word 排版机人审 → 错题知识库 → 针对性练习/出卷。设计详见 `DESIGN.md`。

## 启动（自包含工程，V1.0.1 起无外部依赖）

```bash
cd wrongbook
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                         # 打开 http://127.0.0.1:8899
```

## AI 引擎与 API Key（注入式，v1.0.2）

发布包内**不含任何 key**。两种配置方式，任选其一（同时存在时网页配置优先）：

1. **网页设置（推荐给使用者）**：页面右上角「⚙ 设置」→ 选引擎
   （DeepSeek / 智谱GLM 二选一）→ 粘贴自己的 API Key →「测试连接」→ 保存。
   仅存本机 `data/ai_config.json`（已 gitignore，不随代码发布/上传）；
   key 在界面里只显示掩码（`sk-4****77e5`），永不明文回传
2. **环境变量注入（部署/开发者）**：`export DEEPSEEK_API_KEY=…` / `GLM_API_KEY=…`，
   或写进 `key.env`（开发者便利文件，已 gitignore；run.sh 自动加载）

引擎说明：DeepSeek 文字/识图同为 `deepseek-flash`（原生多模态）；
智谱 GLM 默认 `glm-5.3-flash`（原生多模态，看图/解题同一个模型，
输入 ¥0.8 / 输出 ¥2.8 每百万 tokens，约为旗舰的 1/10；视觉字段可改
免费老模型 `glm-4.6v-flash` 或提速 `glm-5.3-flashx`），模型名均可在设置弹窗里改。
首次启动未配置 key 时页面会自动弹出设置向导。

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
| `ai_config.py` | AI 引擎注入式配置（DeepSeek/GLM 二选一；网页设置 > 环境变量；掩码回显） |
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
