# AI 错题本（数学）· 设计文档

> 版本 **V1.0** ｜ 2026-11-02
> 父项目：`../`（quiz-demo 出题系统，本设计大量复用其已验证的质量链）
>
> 版本历史：
> - v0.2 排版机+人审两阶段录入设计定稿；M1-M7 实现 v1
> - v0.3 图形链重构：矢量 SVG 为主 + 裁剪图兜底，人审期图形纠正
> - v0.4 图形来源分层定稿（figure_mode: svg/crop/none，A 方案）
> - v0.5 人审期图形纠正三入口落地（重新生成SVG / 自动重定位 / 手动框选）
> - v0.5.1 EXIF 方向统一（全链路转正空间）+ 历史草稿单迁移工具
> - v0.5.2 手动框选 box 存档 + recrop_all 幂等重裁 + 错位图恢复工具；测试套件 57 项全绿
> - v0.6 图形策略反转：转录期不再自动向量化（crop 兜底，省 token 提速）；UI 简化：移除重新生成SVG/导出Word按钮
> - v0.6.1 页面打开不再自动显示历史草稿（后又按用户反馈去掉手动载入按钮）
> - v0.6.2 录入整合：跨照片共用一个【提交入库】按钮 + 录入全程锁屏遮罩 + 题库查重（重复题跳过不重录）+ 录入完成横幅汇总
> - v0.6.3 按题提交：一张照片可分多次入库（按题记录处置 ingested_id/skipped_dup），未处置完保持 pending
> - v0.7 多用户：data/users/<uid>/ 按用户隔离题库/草稿单/照片；服务端当前用户；旧数据自动迁移至「测试账号」
> - v0.7.1 教材知识树：全书五章 KB 建成（build_kb 复用，101-201 卷 101 页补齐）；
>   录入自动归类年级+知识点（文本 LLM 批量），树形选择器人审可改；入库写 grade/kp_path
> - v0.7.2~0.7.3 工作台：错题列表（公式渲染/章节难度知识点列/详情弹窗/统计折叠/筛选联动）
> - v0.7.4 刷题会话：工作台多选/全选→新标签页按队列刷题；单选/多选/填空自动评分
>   （问答自判）；答题后即时状态（连对 n/3、建议归档、重复做错、做相似题、归档入口）
> - **V1.0 定稿**：功能闭环完整（录入/管理/刷题/做题/出卷/多用户/导出），
>   96 项回归全绿，转入日常使用+反馈驱动迭代

---

## 1. 目标与范围

为初中数学建立个人错题知识库，核心闭环：**录入 → 分析 → 练习 → 归档**，并以错题画像驱动针对性出卷。

| # | 功能 | 一句话描述 |
|---|---|---|
| F1a | **Word 排版机** | 照片 → 全题转录排版（题干/LaTeX公式）→ 图形来源分层（SVG为主/裁剪兜底）→ Word 供人审 |
| F1b | 选择性入库 | 人审：勾选要入库的错题 + 标注错因（马虎/不会/其他）→ 质量链 → 入库 |
| F2 | 错题分析 | 知识点、易错点、难度(1-3)、相似题（AI 生成，图形合法） |
| F3 | 工作台 | 数量、题型/难度/章节/错因分布，每题练习次数与掌握度 |
| F4 | 针对性出卷 | 按难度分布/知识点筛选，原题或变式二选一（AI 生成，图形合法） |

**非目标**（v1 不做）：OCR 手写批改、多用户、移动端适配、自动判卷。

## 2. 核心设计原则

**P1 质量链复用** —— 所有 AI 生成的题目必须走父项目验证过的三道工序，禁止裸奔：

```
生成 → geocheck 图形校验（含垂直平分线三重约束）
     → verify_answers 答案验伪（独立解题对账 + 数值实例验证）
     → export_paper 矢量导出（SVG 内联 → PDF）
```

**P2 数据即文件** —— JSON 存储（人类可读、git 友好、原子写 + .bak），数百题规模不引入 SQLite。

**P3 掌握判定半自动** —— "连续答对 3 次"触发**建议归档**，人工确认才归档；错题的掌握本质是人的判断。

**P4 人审前置** —— 照片转录不确定度高（多题/手写干扰/印刷质量），所有转录结果先经**排版机人审**（对照原图逐题确认/修正/勾选入库），才进质量链。错题的界定也由人决定（照片里做对的题不入库）。

**P5 不信任照片答案** —— 照片里的印刷答案仅作参考，一律过独立解题对账；对账冲突以审题模型答案为准并留痕。

## 3. 目录与复用方式

```
wrongbook/                 # 本项目（V1.0.1 起完全自包含，可独立搬移）
├── DESIGN.md              # 本文档
├── app.py                 # Flask 服务 + API 路由
├── reviewflow.py          # ★ 排版机：照片→多题转录→图形来源分层→人审图形纠正→重裁
├── pipeline.py            # 入库/相似题/出卷流水线（质量链）
├── wordexport.py          # Word 导出：LaTeX→matplotlib渲染→PNG嵌入docx
├── figureflow.py          # 图形子链：spec生成→geocheck循环→SVG渲染
├── store.py               # JSON 存储层（错题库 + 草稿单）
├── stats.py               # 工作台统计
├── serve.sh               # Terminal.app 常驻启动（脱离 IDE shell 生命周期）
├── tests/                 # 回归测试 57 项（图形链/纠正三入口/EXIF方向/整单重裁/入库图形源）
├── tools/                 # 历史数据迁移：EXIF转正 / 错位裁剪图模板匹配恢复
├── static/
│   ├── index.html         # 单页工作台（原生JS + KaTeX本地渲染）
│   └── vendor/katex/      # KaTeX 本地文件（离线可用）
├── run.sh                 # 启动脚本
├── engine/                  # 内联数学引擎（源自 quiz-demo，V1.0.1 起）
├── data/
│   ├── kb/                  # 教材知识库 JSON（浙教版八上全五章）
│   ├── users.json         # 用户表（v0.7：{users:[{id,name,created_at}], current}）
│   └── users/
│       └── <uid>/         # 每用户一套（切换用户即整体切换数据源）
│           ├── wrongbook.json   # 该用户的错题库（主数据）
│           ├── drafts.json      # 草稿单（含 figure_mode/手动box存档）
│           ├── photos/          # 原始照片（转正后存盘）
│           └── crops/           # 裁剪图（自动 _0 / 手动 _m；权威原件）
└── out/                   # Word 排版稿 + 生成的练习卷
```

**复用父项目**（`sys.path.insert` 指向父目录后直接 import）：

| 父资产 | 用途 |
|---|---|
| `generate_any.PROVIDERS` | DeepSeek/Qwen/GLM 多引擎客户端构造 |
| `geocheck.check` | 图形与题干约束数值校验（九类约束+垂直平分线） |
| `geoconstruct.render_svg` | 纯矢量 SVG 渲染（8方向标签避让+视窗防裁切） |
| `verify_answers.verify_llm` | 独立解题对账（开思考、不看卷面答案） |
| `verify_answers.layer1_rules / verify_numeric_instance` | 规则自检 + 数值实例验证 |
| `schema.QuizBank` | 出卷数据契约（与 export 无缝对接） |
| `export_paper.py`（子进程调用） | 试卷导出 md/html/pdf |
| `build_kb` 的视觉转录经验 | prompt 结构、json/plain 降级、thinking 开关策略 |

## 4. 数据模型

### 4.1 错题记录 `data/wrongbook.json`

```jsonc
{
  "version": 1,
  "questions": [
    {
      "id": "q20261101-153000-a1b2c3",       // 生成规则：时间+随机后缀
      "created_at": "2026-11-01 15:30:00",   // 录入时间
      "mastered_at": null,                    // 归档时间（已充分理解）；null=在库
      "source": "2025年期中卷",               // 手输优先，缺省用转录猜测
      "type": "single",                       // single|multiple|fill|short
      "question": {
        "text": "如图，在△ABC中，…",
        "choices": [{"key":"A","text":"…"}],  // 选择题才有
        "figure_spec": {"points":[…],"segments":[…]},  // 矢量图形（geocheck合法版；无图null）
        "figure_crop": "data/crops/…png"       // 原题照片裁剪图（兜底；SVG失败/表格类题时为唯一图形）
      },
      "printed_answer": "B",                  // 照片里的印刷答案（可 null）
      "verified_answer": "B",                 // 审题模型独立解得的答案（入库存此值）
      "verify_status": "pass",                // pass|conflict|bad_question|pending
      "mistake_reason": {"kind": "careless",  // careless(马虎)|unknown(不会)|other(其他)
                          "note": "把角平分线看成垂直平分线"},
      "analysis": {
        "chapter": "第二章 特殊三角形",        // 受控词表（见4.2）
        "knowledge_points": ["等腰三角形三线合一", "垂直平分线性质"],
        "pitfall": "把角平分线当成了垂直平分线…",  // 易错点描述
        "difficulty": 2                        // 1|2|3
      },
      "similar": [                            // F2 相似题（按需生成，缓存在此）
        {"id":"s1","text":"…","answer":"…","explanation":"…",
         "figure_spec":{…},"verified":true}
      ],
      "practice": [                           // F3 练习记录（追加式）
        {"at":"2026-11-02 20:11:00","correct":true,"variant":"original"}
      ],
      "photo": "data/photos/q20261101-….jpg",
      "flags": {"needs_review": false}        // 转录存疑/题干疑似矛盾
    }
  ]
}
```

### 4.2 章节受控词表

`analysis.chapter` 从固定列表取值（保证工作台分布可聚合）：

```
第一章 三角形｜第二章 特殊三角形｜第三章 一元一次不等式
第四章 图形与坐标｜第五章 一次函数（对应教材KB卷2，届时并入）
其他（手输兜底）
```

> 来源：教材 KB 的 `chapters` 字段；待 `_101-201.pdf` 建 KB 后自动扩展。

### 4.3 掌握度规则

- `streak(q)`：当前连续答对次数（`store.py` 已实现）
- `streak ≥ 3` → 工作台亮"建议归档"徽标 → 人工点归档 → 写 `mastered_at`
- 归档可逆（"再练一次"重新激活，`mastered_at` 置 null，practice 保留）
- 练习来源标注 `variant`: `original`（原题重做）| `similar`（相似题）

## 5. 功能模块设计

### 5.1 F1a Word 排版机（photo → 人审单）

**背景**：照片里常有多道题（有对有错）、含手写痕迹、印刷质量参差——直接自动入库不可靠。

```
照片上传（可多张）
  → 预处理：最长边压到1600px、JPEG q85、去EXIF
  → 【多题转录】视觉模型一次调用（开思考）：
      输出题目列表，每题：
      { no, text(题干全文，公式用$..$LaTeX), choices[],
        figure_boxes: [[left,top,right,bottom]...]（归一化框，可多个）,
        printed_answer(照片可见的印刷答案/解析，无则null),
        has_handwriting: bool(是否有手写痕迹) }
      - 只转录印刷体；手写内容（含错误解答）不转录，只标记存在
      - 一张照片 N 道题 → N 条记录
  → 【自动抓图】按 figure_boxes 用 PIL 裁剪原图 → data/crops/<sheet_id>_q<no>_k.png
    （权威原件，永远保留；SVG 失败或题型不适用时作为兜底图形）
  → 【图形】默认全部用自动抓取的原图（不向量化，省 token）
  → 【排版草稿单】存 data/drafts.json：
      { sheet_id, photo, created_at, status: "pending",
        questions: [ {no, text, choices, has_figure, figure_boxes, figure_crops[],
                      figure_spec, figure_svg, figure_mode, figure_note,
                      printed_answer, has_handwriting,
                      selected: false, reason: null, edited_text: null} ] }
```

**图形来源分层（v0.6 定稿：原图为主，SVG 按需）**：

| figure_mode | 触发条件 | 图形呈现 | 字段 |
|---|---|---|---|
| `crop` | **默认**：所有配图题（自动抓图，0 token） | 原题照片裁剪图 | `figure_crops[]` |
| `svg` | 仅历史草稿单残留；v0.6 起正常流程不再产生（regen API 备用） | 矢量 SVG | `figure_spec` + `figure_svg` |
| `none` | 纯文字题 | 无图 | — |

- v0.6 起转录期不再自动向量化：向量化是最费 token、最慢的一步（每题 1~3 次
  LLM 调用 + geocheck 失败重试，每次重复发送图片）；几何题需要规范图形时
  人审期一键生成，入库链尊重 crop 选择不重复向量化
- `figure_crops[]` 无论 SVG 是否成功都保留（权威原件，供对照与兜底）

**人审界面（Web，与原图对照）**：左侧原图，右侧逐题卡片（KaTeX 实时渲染 LaTeX）：
- 识别是否正确：✓ 确认 / ✎ 直接编辑修正（题干、选项、答案）
- **是否入库**：勾选（做对的题不勾，不进知识库）
- **错因单选（勾选入库后出现）**：马虎 / 不会 / 其他+备注文本
- 每题图形直接展示 + **图形来源可见 + 纠正入口**（v0.6 起两个：抓原图/手动框选，见下）

**图形纠正（v0.5，人审期）**

自动生成的矢量图形常常"几何对但画法不对"（辅助线该虚线画成实线、比例失调、
位置与课本原图不一致）。因此图形不是一次生成定死，而是人审期可反复纠正：

| 入口 | 行为 | API |
|---|---|---|
| 🖼 自动抓取原图 | 复用该题 `figure_boxes` 重新定位裁剪，原图取代 SVG | `POST …/recrop`（无 body） |
| ✂️ 手动框选原图 | 在左侧原图上拖拽框选 → 裁剪 → 原图取代 SVG（存 `_m` 后缀，不覆盖自动图） | `POST …/recrop`（`{"box":[l,t,r,b]}`） |

> 🔄 重新生成SVG 已从 UI 移除（v0.6 图形默认原图后无必要）；API
> `POST /api/sheets/<sid>/questions/<no>/regen-figure` 保留备用。

- 切换为原图后：`figure_spec=None`、`figure_svg=None`、`figure_mode="crop"`
- **入库时尊重用户选择**：`resolve_figure_spec()` —— 用户显式选了原图就不再
  向量化覆盖（`pipeline.resolve_figure_spec`）
- 入库记录 `figure_spec`（权威，SVG 才有）+ `figure_crop`（兜底原图）

**照片方向统一（v0.5.1，EXIF orientation）**

手机拍摄的照片带 EXIF orientation（如 orientation=6 = 显示时需顺时针转 90°）。
浏览器 `<img>` 会自动应用 EXIF，但服务端不会 → **两个坐标系错位**：

| 空间 | 尺寸(4032×3024 的照片) | 谁在用 |
|---|---|---|
| 原始像素 | 4032×3024（横） | 旧版：LLM 看图、crop_figures 裁剪 |
| 转正空间 | 3024×4032（竖） | 浏览器显示、用户拖拽框选 |

后果：自动抓图 → 区域对但图横躺 90°；手动框选 → 区域完全错位。

修复（全链路统一到转正空间）：
- `preprocess()` 给 LLM 的图转正 → LLM 返回的 box 即转正空间
- 存盘照片转正（EXIF 已应用）→ 浏览器显示方向 = 服务端像素坐标
- `crop_figures()` 转正后裁剪 → box 直接可用，无需再变换
- 历史草稿单迁移：`tools/migrate_orientation.py`（照片转正 + figure_boxes 同步变换，幂等）

**手动框选的 box 存档与整单重裁（v0.5.2）**

手动框选的 box 属于【显示空间】= 转正空间。修复方向问题后，box 本身一直是对的，
错的只是"拿 box 去裁原始像素图"。为杜绝再次错位：

- 框选时把 box 存档到 `figure_manual_box`（转正空间）→ 可原样重放
- `recrop_all(sheet)`：按【当前】照片重裁所有图（自动 + 手动）→
  图源唯一权威，必然与照片一致（幂等）。API：`POST /api/sheets/<sid>/recrop-all`
- 旧代码产出的错位手动图：用 `tools/recover_manual_crops.py` 恢复 ——
  模板匹配在原始像素图上定位旧裁剪图 → 反推用户的原始 box → 用转正图重裁
  （4 张已恢复，均为正立且区域正确）

**Word 导出**（`wordexport.py`，排版机名字的由来）：
- 任何时候可把草稿单/已入库错题导出 .docx：题干段落（LaTeX 公式用
  matplotlib mathtext 渲染成 300dpi PNG 嵌入，独立公式居中、行内公式随字号）
  + 选项列表 + 自动抓取的图形图片 + 印刷答案（灰色小字）
- 用途：打印、长期归档、与老师同学交流（Word 可再编辑文字）

### 5.2 F1b 选择性入库（人审确认 → 质量链 → 错题库）

```
人审提交（勾选的题 + 错因 + 可能有手改的题干；多张照片共用一个提交按钮，逐张处理）
  → 逐题走质量链：
      ⓪ 查重：store.find_duplicate 归一化相似度≥92%（含已归档题）
         → 重复则跳过（不调 LLM 不入库），完成横幅里报告
      ① 图形：排版机阶段已生成的 figure_spec 直接采纳（已过 geocheck）
         → 无 spec 时补向量化一次（LLM 把裁剪图/题干 → figure_spec → geocheck 校验）
         → 仍败 → 保留照片裁剪图作为图形（不入 needs_review，因为原图本身就是权威）
         → 表格/图象类题（SVG 表达不了）直接用裁剪图，跳过向量化
      ② 分析：章节(受控词表)/知识点/易错点/难度 —— 错因标注影响分析侧重
         （马虎→易错点写"易算错环节"；不会→写"思路卡点"）
      ③ 答案验伪：verify_llm 独立解题 vs 印刷答案
         ├─ 一致 → verified_answer, status=pass
         ├─ 不一致 → verified_answer=审题答案, status=conflict（UI红标）
         └─ 判题不合格 → needs_review=true（可能是转录错或教辅勘误）
  → 入库（source=用户输入或草稿单来源；mistake_reason=勾选错因；
         photo、figure_crop、figure_spec 都留档）
  → 草稿单 status=processed（保留可追溯）
```

### 5.2 F2 错题分析 + 相似题

分析字段在录入时已生成（5.1 一次拿全）。相似题按需生成：

```
触发：题详情页点"生成相似题"（count 默认 2）
上下文：原题题干+答案+知识点+易错点
生成约束：
  - 围绕同一知识点与易错点，正用/逆用/换数据/换设问
  - 难度允许 ±1 级（错题难度2 → 相似题可 1~3，供递进练习）
  - 题型默认同原题
质量链：figureflow（图形校验循环）+ verify_llm（答案对账）
产物：存入 record.similar[]（含 verified 标记）；未过验伪的相似题丢弃重生成（最多2轮）
```

### 5.3 F3 工作台

聚合视图（`stats.dashboard()`，已起草）：

| 指标 | 说明 |
|---|---|
| 总数 / 在库 / 已归档 | 三个大数字 |
| 题型分布 | 单选/多选/填空/问答（在库口径）|
| 难度分布 | ①简单 ②中等 ③困难 |
| 章节分布 | 受控词表聚合，未分类单列 |
| **错因分布** | 马虎/不会/其他（在库口径；出卷时"不会"优先 |
| 知识点 TOP12 | 录入时模型标的 kp 聚合 |
| 练习概览表 | 每题：做过几次/对几次/当前连对/距建议归档还差几次 |
| 零练习数 | 录入后从未练过的题（提醒）|

### 5.4 F4 针对性出卷

```
参数：
  - chapters[]：章节筛选（可多选，默认全部在库错题的章节）
  - kps[]：知识点筛选（可多选，默认全部）
  - difficulty_mix：{"1":30,"2":50,"3":20}（百分比）
  - count：题量（默认 8）
  - mode：original（围绕易错点全新原创）| variant（错题变式：改数据/换设问/正逆互换）

流程：
  1. 从错题库筛选目标题集（章节/知识点过滤）
  2. 汇总该题集的知识点+易错点清单 → 作为命题重点注入 prompt
  3. 分块生成（workers 并发，题量>6 时分块）
  4. 每题过 figureflow + verify_llm（复用 verify_answers.verify_bank 整卷批量）
     FAIL → 带审题意见重生成（1轮）→ 仍 FAIL 剔除并补题
  5. 组装 QuizBank schema（chapter="错题针对卷 · <筛选条件>"）
  6. 子进程调用 ../export_paper.py → out/错题针对卷_<时间戳>.pdf
  7. 同时生成在线练习 session（可在页面直接刷）
```

- 换题约束继承父项目：章节约束注入 + 填空不产选项 + 难度透传。

## 6. API 设计（Flask）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/` | 单页工作台 |
| POST | `/api/sheets` | 上传照片→多题转录+图形来源分层→草稿单（返回草稿全文） |
| GET  | `/api/sheets` | 草稿单列表 `?status=pending\|processed` |
| GET  | `/api/sheets/<id>` | 草稿单详情（含裁剪图URL） |
| PATCH| `/api/sheets/<id>/questions/<no>` | 人审编辑：text修正/勾选入库/reason错因/figure_manual_box |
| POST | `/api/sheets/<id>/questions/<no>/regen-figure` | 🔄 重新生成矢量SVG（v0.6 起 UI 无入口，API 保留备用） |
| POST | `/api/sheets/<id>/questions/<no>/recrop` | 🖼 自动重定位 / ✂️ 手动框选（body `{"box":[l,t,r,b]}` 则为手动） |
| POST | `/api/sheets/<id>/recrop-all` | 整单幂等重裁（按当前照片重裁全部自动+手动图） |
| POST | `/api/sheets/<id>/commit` | 提交：勾选题逐个走质量链入库（返回入库结果+验伪报告） |
| GET  | `/api/sheets/<id>/docx` | 导出 Word 排版稿 |
| GET  | `/api/stats` | 工作台聚合数据 |
| GET  | `/api/questions` | 列表 `?filter=active\|mastered\|needs_review` |
| GET  | `/api/questions/<id>` | 详情（含相似题、练习史） |
| PATCH| `/api/questions/<id>` | 改 source/chapter/difficulty 等手编字段 |
| POST | `/api/questions/<id>/practice` | `{correct, variant}` 记一次作答 |
| POST | `/api/questions/<id>/master` | 归档（写 mastered_at） |
| POST | `/api/questions/<id>/unmaster` | 重新激活 |
| POST | `/api/questions/<id>/similar` | 生成相似题 `{count:2}` |
| DELETE | `/api/questions/<id>` | 删除（连带照片） |
| POST | `/api/paper` | 出卷 `{chapters,kps,difficulty_mix,count,mode}` |
| GET  | `/out/<file>` | 下载 Word/PDF/MD |
| GET  | `/api/kb` | 教材知识树（章/节/知识点） |
| GET  | `/api/users` | 用户表 + 当前用户 |
| POST | `/api/users` | 新建用户（题库为空）并切换 `{name}` |
| POST | `/api/users/switch` | 切换当前用户 `{id}` |

端口：`127.0.0.1:8899`（run.sh 启动，自动打开浏览器）。

## 7. 页面设计（单页 4 Tab，原生 JS）

```
┌──────────────────────────────────────────────────┐
│ AI错题本·数学  [录入(排版机)] [工作台] [刷题] [出卷] │
├──────────────────────────────────────────────────┤
│ 录入：拖拽/选择照片（可多张排队）+ 来源(可选)          │
│   → 转录+图形分层进度 → 进入人审视图：                    │
│     左：原图（拍照原件，方向已统一）                  │
│     右：逐题卡片（KaTeX实时渲染）                     │
│        [🖼抓原图] [✂️手动框选]                        │
│        [✓识别正确/✎编辑] [入库勾选] [错因:马虎/不会/其他] │
│        顶部粘性栏：已勾选N题·共X张照片 [提交入库]（全局唯一）│
│        录入中：全屏遮罩锁屏；完成：醒目横幅（入库/跳过重复）  │
│ 工作台：三数字卡 + 四分布条形图(题型/难度/章节/错因)     │
│       + 知识点TOP + 练习概览表（建议归档徽标）          │
│ 刷题：在库题随机/按筛选抽题 → 答题 → 自判对错           │
│       → 记录practice；连对3次弹"建议归档"             │
│ 出卷：章节多选+知识点多选+难度滑块+题量+模式(二选一)     │
│       → 生成进度 → 打开PDF / 在线刷                   │
└──────────────────────────────────────────────────┘
```

公式渲染：KaTeX 本地文件（static/vendor/katex，离线可用），v1 直接渲染 LaTeX。

## 8. 质量保证与测试计划

| 测试 | 方法 | 通过标准 |
|---|---|---|
| 排版机端到端 | 用现有卷的题渲染成"假照片"（多题拼图+手写痕迹干扰） | 多题全部识别、bbox抓图准确、公式LaTeX正确 |
| 人审→入库 | 勾选其中两题+错因提交 | 仅勾选题入库；错因入库；质量链报告齐全 |
| Word导出 | 导出人审单 docx 打开目检 | 题干/选项/公式(图片)/抓图齐全 |
| 转录降级 | 故意上传模糊/无关图片 | 不崩溃，空草稿单或明确报错 |
| 图形链回归 | 入库题与相似题图形全过 `geocheck` | 0 ❌ |
| 答案验伪回归 | 相似题过 `verify_answers` | 0 ❌（或修复后0） |
| 出卷端到端 | 8题针对卷 | PDF 产出、五层防线全绿 |
| 统计正确性 | 手工构造小库对账 dashboard 数字（含错因分布） | 全对 |
| Web 冒烟 | 启动服务 curl 各端点 + 浏览器走查4 Tab | 全通 |
| 回归套件 | `pytest tests/`（图形链/纠正三入口/EXIF方向/整单重裁/入库图形源/查重/多用户/知识树） | 85 项全绿 |

> v0.7.2 工作台：错题列表补章节/难度/知识点列+公式渲染+「查看」详情弹窗（全部信息/练习历史）；统计图默认折叠
> 数据事故记录：v0.7 上线当天 test_dedup 因未开 set_test_mode 覆盖真实题库，已从 .bak 恢复并全测试加保险（bfe510b）

## 9. 实施阶段

### v1 主体（M 系列，已全部完成）

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| M0 | 设计评审（本文档） | ✅ 定稿 | — |
| M1 | store/stats 对齐新schema + drafts存储 + 单测 | ✅ 数据层 | 0.5d |
| M2 | reviewflow：多题转录+抓图+草稿单 | ✅ 排版机后端 | 1d |
| M3 | wordexport：LaTeX渲染PNG嵌入docx | ✅ Word导出 | 0.5d |
| M4 | pipeline：入库质量链（图形向量化+分析+验伪） | ✅ 入库链 | 0.5d |
| M5 | Flask app + index.html 四Tab（KaTeX） | ✅ 可用站点 | 1d |
| M6 | 相似题生成 + 刷题循环 | ✅ 练习闭环 | 0.5d |
| M7 | 出卷 + export 联动 + 全量测试 | ✅ 完整闭环（E2E全部通过） | 1d |

### 图形链迭代（v0.3 → v0.5.2，真实照片使用反馈驱动）

| 版本 | 内容 |
|---|---|
| v0.3 | 图形链重构：矢量 SVG 为主 + 裁剪图兜底；废弃纯 CV 定位（真实照片上把斜拍文字误判为图形，IoU 实测不可靠） |
| v0.4 | 图形来源分层定稿（figure_mode: svg/crop/none，A 方案） |
| v0.5 | 人审期图形纠正三入口（重新生成SVG / 自动重定位 / 手动框选）+ regen 失败保留旧图 |
| v0.5.1 | EXIF 方向统一：全链路转正空间 + tools/migrate_orientation.py 历史迁移 |
| v0.5.2 | 手动 box 存档（figure_manual_box）+ recrop_all 幂等重裁 + tools/recover_manual_crops.py 恢复错位图；测试套件 57 项全绿 |
| v0.6 | 图形策略反转：转录默认不向量化（原图 crop 兜底，省 token 提速），SVG 改为人审期按需【🔄 重新生成SVG】；测试套件 64 项全绿 |

（v0.3+ 由真实照片使用反馈驱动迭代；CV 行剖面定位在假照片上 IoU 95.9%，真实照片上失效，两轮实测后废弃。）

## 10. 风险与已定决策

| # | 决策/风险 | 处置 |
|---|---|---|
| D1 | 存储选 JSON 而非 SQLite | 规模小+git友好；>2000题再迁 |
| D2 | `.venv/bin/pip` shebang 指旧路径 | 一律用 `.venv/bin/python -m pip`（已记录） |
| D3 | 照片含手写解答/多题干扰 | 排版机人审前置：只转录印刷体+标记手写；逐题勾选 |
| D4 | 照片本身题目有误（教辅勘误） | bad_question → needs_review 人工核对 |
| D5 | 多题转录 bbox 不准（抓图偏移） | 裁剪图仅作兜底/对照，权威图形为 SVG；v1.1 支持拖拽修正 |
| D6 | Word 公式原生 OMML 不可行（无 pandoc） | matplotlib mathtext 渲染 300dpi PNG 嵌入；OMML 列 v1.1 |
| D7 | 章节词表覆盖不到的题 | "其他"兜底 + PATCH 手改 |
| D8 | 相似题与原题重复 | 题干相似度≥90% 丢弃重生成（父项目经验） |
| D9 | 依赖父项目路径 | run.sh 固定 cd；sys.path 相对解析 |
| D10 | 图形向量化失败 | 自动回落照片裁剪图作为图形，不阻塞入库 |
| D11 | 图形来源分层（A 方案 v0.4） | SVG 为主（几何题）+ 裁剪图兜底（表格/图象类题与向量化失败）；裁剪图永久保留作权威原件 |
| D12 | DeepSeek 思考链烧光 max_tokens 导致 content='' | gen_spec 三策略降级：json_object → 关思考重试 → plain；失败信息带 finish_reason 供人审判因（实测 645s→16s） |
| D13 | 自动生成的矢量图形"几何对但画法不对" | 人审期纠正入口（自动抓原图 / 手动框选原图；v0.6 起原图即默认权威图形，重新生成SVG 仅存 API）；入库尊重用户选择 |
| D14 | 照片带 EXIF orientation，浏览器显示方向 ≠ 服务端像素坐标 → 抓图转 90°、手动框选完全错位 | 全链路统一到【转正空间】：存盘前转正、LLM 输入转正、裁剪时转正；历史草稿单用 tools/migrate_orientation.py 迁移（照片转正 + figure_boxes 同步变换） |
| D15 | 向量化（SVG）是最费 token/最慢的一步（每题 1~3 次 LLM 调用 + geocheck 重试） | v0.6 起转录默认 crop（0 token 抓图），UI 移除重新生成SVG 按钮；入库链尊重 crop 不重复向量化 |
| D16 | 同一道题重复拍照/重复录入 | v0.6.2 入库第⓪步查重：归一化（去 LaTeX 命令名/标点/空白/大小写）后 difflib 相似度≥92% 即跳过；含已归档题；结果在完成横幅报告 |
| D17 | 多个学习者共用一份题库 | v0.7 按用户分目录存储（users/<uid>/），服务端保存当前用户（users.json）；页头下拉切换/新建用户；存量数据自动迁移进「测试账号」（JSON 内绝对路径同步改写） |
| D19 | 刷题判卷方式 | 按题型混合：单选/多选点选自动评分；填空文本输入（归一化+数值等价）自动评分；问答看答案自判。连对计数/建议归档/重复做错即时反馈（v0.7.4） |
| D18 | 知识点需要来自教材而非 LLM 自由发挥 | v0.7.1 全书五章 KB（build_kb 视觉转录，可断点续跑）→ kbtree 构建章/节/知识点树；录入期 LLM 只在树内选（超出八上→按年级进度给九年级）；人审树形选择器可改，入库以人审值为权威 |

## 11. 已拍板决策（v0.2 拍板，v0.5.2 沿用）

| # | 议题 | 结论 |
|---|---|---|
| 1 | 归档门槛 | 连对 **3 次**建议归档，人工确认 |
| 2 | 难度分级 | **1-3 级** |
| 3 | 出卷模式 | **二选一**（original / variant） |
| 4 | 相似题默认数 | **2 道** |
| 5 | 公式渲染 | **直接 LaTeX**（Web 用 KaTeX 本地渲染；Word 用 matplotlib 渲图嵌入） |
| 6 | 刷题判定 | **自判对错**（无自动判卷） |
| 7 | 新增：Word 排版机 | 两阶段录入：排版机人审 → 选择性入库（勾选题+错因标注） |
| 8 | 新增：错因分类 | 马虎 / 不会 / 其他+备注；驱动工作台分布与出卷优先级 |
