# AI 错题本（数学）

照片 → Word 排版机人审 → 错题知识库 → 刷题练习 / 导出PDF打印。
多用户数据隔离，教材知识树自动归类知识点；GeoGebra 图形工作台（AI 生成构造命令 +
人审拖拽修正，入库后可再改图）。当前版本 **V1.1.0a**，回归测试 118 项全绿。
完整设计（数据模型 / API / 图形链演进 / 决策记录）详见 `DESIGN.md`。

## 启动（自包含工程，V1.0.1 起无外部依赖）

```bash
cd wrongbook
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
tools/fetch_geogebra.sh           # 可选：下载 GeoGebra bundle（32MB，仅「🧠GeoGebra重画」功能需要）
./run.sh                         # 打开 http://127.0.0.1:8899（缺 bundle 时启动有提示，不影响其他功能）
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

## 使用流程（V1.0.3：菜单仅「录入 / 工作台」两个 Tab）

1. **录入（排版机）**：拖拽 / Ctrl⌘+V 粘贴 / 点选上传试卷照片（可多张，共用一个
   【提交入库】按钮）→ 自动转录全部题目（公式转 LaTeX）+ 按框抓取图形原图
   （0 token，原图即权威图形；表格/图象类题同样适用）→ 年级/知识点按教材知识树自动归类
   → 人审视图（左原图 / 右 KaTeX 渲染题卡）：对照原图确认/编辑、勾选要入库的错题、
   标注错因（马虎/不会/其他）、图形修正三入口：
   【🖼抓原图】【✂️手动框选】【🧠GeoGebra重画】（AI 生成构造命令 → 弹窗内拖拽修正 → 导出高清图，
   命令脚本随题存档）
   → 提交入库：先查重（相似度≥92% 跳过），勾选题逐题过独立解题验伪
   → 可导出 Word 排版稿（公式渲染成图，可打印可再编辑）
2. **工作台**：数量/题型/难度/章节/错因分布统计，错题列表（公式渲染、章节/难度/
   知识点列、详情弹窗），详情内可「✎改图」（GeoGebra 重放脚本再编辑），
   勾选 → 开始做题或导出 PDF
3. **做题与批改**：勾选题目 → 「📝 开始做题」新标签页试卷式作答 → 「完成并批改」
   得分+逐题对错+错因/知识点汇总（单选/多选/填空自动评分，问答看答案自判；
   每次作答计入练习记录，连对 3 次 → 建议归档，人工确认）；打印走「🖨 导出PDF」
   （静态排版，不调 LLM）

> V1.0.3 起移除了相似题生成与针对性出卷两个 AI 生成功能（费 token 且不实用）；
> 练习唯一入口 = 工作台勾选 → 开始做题。

## 多用户（v0.7）

页头下拉切换/新建用户，每个用户一套独立数据（题库/草稿单/照片/裁剪图）：

```
data/
├── users.json              # 用户表 + 当前用户
├── ai_config.json          # AI 引擎配置（gitignore）
├── kb/                     # 教材知识库（浙教版八上全五章）
└── users/<uid>/
    ├── wrongbook.json      # 错题库（主数据）
    ├── drafts.json         # 排版机草稿单
    ├── photos/             # 原始照片（EXIF 转正后存盘）
    └── crops/              # 裁剪图（自动 _0 / 手动 _m，权威原件）
```

## 模块

| 文件 | 职责 |
|---|---|
| `app.py` | Flask 服务 + API |
| `ai_config.py` | AI 引擎注入式配置（DeepSeek/GLM 二选一；网页设置 > 环境变量；掩码回显） |
| `reviewflow.py` | 排版机：多题转录（EXIF 转正统一坐标系）+ 自动抓图 + 知识点归类 + 草稿单 |
| `ggbflow.py` | GeoGebra 图形工作台（v1.1a）：题干→构造命令脚本（纯文本 LLM），前端本地执行/拖拽/导出 |
| `pipeline.py` | 入库质量链（查重→图形→分析→验伪）+ 导出PDF（相似题/出卷已于 V1.0.3 移除） |
| `wordexport.py` | Word 导出（LaTeX→matplotlib→PNG 嵌入 docx） |
| `figureflow.py` | 图形向量化子链（spec→geocheck 循环→SVG；人审期按需调用） |
| `kbtree.py` | 教材知识树（章/节/知识点；录入期 LLM 只在树内选，人审可改） |
| `store.py` / `stats.py` | JSON 存储（按用户隔离 + 查重）/ 工作台统计 |
| `engine/` | 内联数学引擎（geocheck/geoconstruct/make_figures/verify_answers/export_paper/generate_any/schema，源自 quiz-demo 出题系统） |
| `tools/` | 历史数据迁移（EXIF 转正 / 错位裁剪图模板匹配恢复）+ `fetch_geogebra.sh`（GeoGebra bundle 按需拉取） |
| `tests/` | 回归测试 118 项（图形链/纠正入口/EXIF方向/整单重裁/查重/多用户/知识树/AI引擎配置/GeoGebra图形等） |

跑测试：`.venv/bin/python -m pytest tests/`（全部离线，不调真实 API）。

## 已知限制

- 抓图框在特殊版面（图形紧贴文字/多图交错）可能偏移——人审界面有对照，可用
  【抓原图/手动框选】修正；重大偏差以重拍或忽略抓图处理；几何题更推荐【🧠GeoGebra重画】
- GeoGebra 重画需先运行 `tools/fetch_geogebra.sh` 下载 bundle（未下载时仅该按钮不可用，其余功能正常）；
  命令生成非 100% 成功：失败行标红提示（**不自动重试，省 token**），可手改命令重新执行，
  或手动点「重新生成」（带失败行反馈）；已有存档脚本时重新打开不调 LLM（本地重放，0 token）
- Word 公式为高清图片（不可编辑）；OMML 原生公式列后续版本
- 照片极度模糊/倾斜时转录可能失败，会明确报错而非入库脏数据
- 章节词表未覆盖的题（如九年级内容）按年级进度归类，章节显示"其他"，可手改
