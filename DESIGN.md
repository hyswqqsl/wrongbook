# AI 错题本（数学）· 设计文档

> 版本 **V1.2**（专题模块 + 视频讲解）｜ 2026-11-05
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
> - V1.0.1 完全自包含：内联数学引擎 engine/（去父项目依赖），可独立搬移
> - **V1.0.2 AI 引擎注入式配置**：发布不带 key，用户自配；DeepSeek / 智谱GLM
>   二选一（网页「⚙ 设置」> 环境变量）；GLM 默认 glm-5.3-flash（原生多模态，
>   看图/解题同一模型，输入 ¥0.8/输出 ¥2.8 每百万 tokens）；新增测试 10 项（全量 106 项全绿）
> - V1.0.2a 反馈修复：设置弹窗仅「关闭/保存」按钮可关（点遮罩不再关闭，防丢未保存 key）；
>   工作台勾选集改 sessionStorage + 按全量题 id 剪枝（stats 返 all_ids），
>   根治“上次会话残留勾选 → 未勾选也能开始做题”假状态
> - **V1.0.3 功能瘦身（省 token）**：移除相似题生成与针对性出卷两个 AI 生成功能（API/前端/Python 代码全部删除）；菜单只剩「录入/工作台」两个 Tab；练习唯一入口 = 工作台勾选 → 开始做题 → 完成并批改（无会话时引导，不再随机刷题）；导出PDF（静态导出，不调 LLM）保留
- **V1.1 设计启动（GeoGebra 图形工作台）**：LLM 生成构造命令 + 本地 applet 执行 + 人审拖拽修正画法；命令脚本随题存档（入库后可再改图）；设计稿见 5.5
- **V1.1.0a 实施**：5.5 全部落地——`ggbflow.py`（纯文本 LLM 命令生成）+ 3 个新端点
  （`/api/ggb-script` 通用命令生成 / `ggb-figure` 草稿期保存 / `questions/<id>/figure` 入库后改图）+
  前端 GeoGebra 弹窗（geometry app，拖拽修正，失败行自动回传重试≤2 次）+
  `tools/fetch_geogebra.sh` bundle 按需拉取（32MB 下载/116MB 解压，不入 git）；
  入库链 `figure_ggb` 随题存档（可再编辑源文件）；真实 E2E 验证通过（等腰直角三角形中线题 12 行命令全对）；
  新增测试 8 项（全量 118 项全绿）
- **V1.1.0b 反馈修复**：**移除自动重试**（失败行只标红提示，重新生成由人手动点、才带 prev_errors 反馈——省 token）；
  点遮罩不再关闭弹窗（仅「关闭 ✕」，防误触丢画法）；prompt 修正：SetLineStyle 必须作用于
  已命名对象（sDE=Segment(D,E) 后再 SetLineStyle(sDE,2)），降低首失败率；
  **去左侧 Algebra 栏**（ready 后 `setPerspective("G")`——params 里写对 geometry app 无效，Playwright 实测）+ 画布加宽 560×420；
  **Set\* 样式命令翻译器**：web 版 evalCommand 不执行 Set\*（geometry/classic 实测均 false，构造命令正常）→
  前端翻译为 Apps API 直调（setLineStyle/setPointSize/setColor/setVisible/setLabelVisible/setCaption；
  API 方法返回 void，“不异常即成功”，XML 验证生效；SetPointSize(P,0) 语义翻译为隐藏）——
  用户报错的 6 行 SetPointSize 复验全部通过
- **V1.1.0c 反馈修复**：翻译器正则改为查表匹配（原 `^Set\w+` 前缀匹配漏了 **ShowLabel**——
  用户实测又报 5 行失败，修正后 0 失败）；补 ZoomIn→setCoordSystem 翻译；
  人审题卡按钮收敛：**无图题不显示图形按钮**、「🖼自动抓取原图」按钮移除（转录时每题已自动抓图，
  框偏了用手动框选；/recrop API 保留备用）；GeoGebra 弹窗「重新生成」加全局遮罩（生成期间锁操作）
- **V1.1.0d 反馈修复**：弹窗宽度改 `fit-content`（不再右侧留白）；**弹窗内嵌原题图形对照图**
  （解决弹窗挡住左栏原图无法对比——新字段 `figure_ggb_ref` 存档首次重绘前的原图路径，
  草稿期自动推断 _0/显式回传，入库后沿用覆盖前旧图；侧栏顶部缩略图，点击原图大小打开；
  重开后对照仍可用，不随重绘丢失）
- **V1.1.0e 反馈修复**：命令生成**附原题图形照片走视觉调用**（此前纯文本不看课本原图，
  布局/摆向易偏——D13 另一半根源；prompt 要求布局/摆向/相对位置与图一致，几何关系仍以题干为准；
  无对照图降级纯文本；端点回传 vision 标记）；
  弹窗布局三改：题干条置顶（KaTeX 渲染）、两栏底部平齐（行高随对照图 560/420）、命令按钮三等分占满侧栏
- **V1.1.0f 反馈修复**：**用户数据出库**——data/users/（题库/草稿单/照片/裁剪图）、users.json、
  *.bak 从 git 索引移除 + gitignore（本地个人数据不入库，KB 知识库保留跟踪）；
  applet 参数 errorDialogsActive:false（GeoGebra 自带错误弹窗不再打断，错误走统一失败行统计）；
  新增「🩺分析解决」按钮（只有报错了才出现）：把当前脚本+失败行发给 LLM 排错，
  返回修复后完整命令（新 fix_commands + /api/ggb-script 修复分支），修复过程全局遮罩锁操作
- **V1.1.0g 反馈修复**：**抓图自动对比度增强**——crop_figures 落盘前 ImageOps.autocontrast(cutoff=1)
  （偏暗纸张照片提亮拉对比：实测灰阶 (27,234)→(0,255)；展示与视觉输入都受益；
  recrop_all/手动框选同受益，幂等）；GeoGebra 弹窗题干条限宽 880px + overflow-wrap:anywhere
  （长题干换行，不再撑大 fit-content 弹窗，日志同防）；**图片放大查看器** viewImg
  （题卡/工作台详情图形点击 → 全屏大图，点任意处/Esc 关闭）
- **V1.1.0c**：翻译器正则只认 Set 前缀 → ShowLabel 漏翻（仍走 evalCommand 报假失败）；
  改查表匹配任意命令名，补 ZoomIn→setCoordSystem；用户 20 行脚本仿真验证全路由正确
- **V1.1.0d**：打开弹窗**不再自动调 LLM**（空脚本时提示手动生成，存档脚本仍本地重放 0 token）；
  布局修复：弹窗加宽 1080px、脚本栏 360px——画布+脚本同排、三按钮一行（原 880px 宽度不够触发 flex-wrap 掉行）
- **V1.2 专题模块 + 视频讲解**：新增菜单「专题」（专题集 → 类别 → 典型例题三级）；
  种入「初二中考必考专题」10 类（1-3 简单 / 4,5,7,8,9 中等 / 6,10 压轴，含分值）；
  例题录入 = 排版机转录后人审卡选「存入专题」（可与存错题本**多选**；仅存专题**零 LLM**，0 token）；
  专题 Tab：类别卡（难度徽章/分值/例题数/掌握进度）→ 例题卡（练习/整类练习/答案解析折叠）；
  练习复用做题页批改（连对 3 次=已掌握🏆）；做错例题**手动点**「📥存入错题本」；
  **视频讲解（可选）**：人审卡/例题卡可上传（通用 POST /api/videos，512MB 上限），
  详情弹窗与专题例题卡可播放；视频删除带交叉引用检查（错题/未处置草稿/专题例题共用不误删）；
  新增 topics.py + tests/test_topics.py（全量 **148 项全绿**）；Playwright 走查通过；
  修复：提交转圈（pipeline LLM 降级对齐 v1.1 + threaded + 前端超时，详见 §12）
- **V1.2b 双录入入口**：菜单拆为「📚录入错题本」「🧠录入专题」两个入口（共用转录流程，入口决定题卡模式）：
  主勾选 = 本入口的目的地（错题本+错因 / 专题+类别自动预选），附加勾选 = 可同时存入另一侧；
  数据层不变（selected/dest_topic 任意组合）
- **V1.2c 反馈修复**：① **双入口题目独立**——转录照片标记 mode，切换入口各自看各自的题
  （互不串场；空入口显示引导提示）；② **「✎编辑修正」移到题干正后方**（原埋在卡底）；
  ③ **知识点归类改用专题大类**——教材 KB 三级树（章/节/知识点太零碎）替换为「专题分类」下拉
  （转录时 LLM 从专题类别清单中自动预选，人审可改；与专题体系统一）；
  入库映射：analysis.chapter=「第N类 名」、kp_path=[专题集名, 类别, ""]；勾「同时存入专题」
  时归类自动跟随所选类别；kp_sel 保留旧草稿兼容；前端 KB 树代码移除（kbtree.py 与 /api/kb 保留备用）；
  新增 tp_sel 入库映射/classify 新契约测试（全量 **151 项全绿**）
- **V1.2d 编辑交互优化**：「✎编辑修正」按钮移到题干右下角（右对齐）；点击后按钮消失，
  题干原地变 textarea + **保存/取消**两按钮（取消=放弃重渲染回原文，不调接口）；
  保存后题卡即时显示编辑后的文本 + 「✎已手改」标记（原缺陷：edited 只在提交时生效，
  保存后页面看不出变化）
- **V1.2e 知识点定稿**：录入题卡字段定名「知识点」——**纯名称只读展示**（去「第N类」前缀，
  与专题大类同名，如「内角和与外角性质」），大模型转录时分析预选、**不可修改**；
  入库 analysis.chapter/kp_path 同步用纯名称；「同时存入专题」下拉保留「第N类」前缀
  （大模型预选 + 可下拉调整，预选优先跟随知识点）；tp_sel 仍随提交存档（用于映射）
- **V1.2f 专题录入入口下沉**：菜单去掉「录入专题」，录入入口移到**专题页各类别**：
  类别卡右上「＋录入」/ 类别详情页「＋录入例题」→ 切录入页并**锁定目标类别**
  （amber 提示条：目标类别 + 更换下拉 + 退出锁定；转录后自动勾选存入该类，
  已勾选题随换类同步）；菜单回归「录入错题本 / 工作台 / 专题」三项
- **V1.2g 双模式解耦定稿**：① 菜单「录入错题本」改名「**录入题目**」；
  ② **两模式互补干涉**：错题本录入不出现「同时存入专题」，专题录入不出现
  「同时存入错题本」/错因行（附加勾选 UI 删除）；
  ③ 知识点分模式展示（均只读）：错题本模式=专题大类单名称；**专题录入=LLM 分析的
  多个知识点**（transcribe 归类时同步输出 kps 数组，题卡用 tag 展示，不可修改）
- **V1.2h 录入页细修**：① 专题锁定去重复——upload-status 行不再显示锁定提示，
  只保留人审区 amber 锁定条（换类/退出都在条上）；② 知识点最多 3 个
  （prompt 强调 + 服务端 [:3] + 前端展示双保险）；③ 「✎编辑修正」文案改「✎修正」；
  「🎥上传视频/移除」按钮对齐手动框选按钮高度（紧凑样式）并加图标；
  ④ 点「＋录入」后立即渲染锁定条（不等转录，原来空 sheets 时不渲染造成困惑）
- **V1.2i 知识点定稿（终版）**：转录文案改「已转录 N 题（尚未提交入库，核对无误后点提交）」
  （转录≠录入）；知识点改回**细致的知识点**（与工作台同口径）：两模式统一显示
  **LLM 分析的单个细致知识点**（如「角平分线的性质」，prompt 要求非大类名、
  LLM 可分析多个只保留一个），只读不可修改；tp_sel 仍用于工作台章节归类（专题大类）；
  前端 tpName/kps 代码移除
- **V1.2j 知识点改受控（内置清单）**：kp 从**内置教材 KB 细分知识点清单**中选
  （用户内置的 data/kb/ 教材 json 提炼）：新增 kbtree.knowledge_points()——首次从两个
  ~300KB 教材大 json 提炼去重落盘 data/kb/knowledge_points.json（仅 1.6KB、38 个知识点
  按 5 章分组），之后转录直读精简文件，不再每次解析大 json（省解析与 prompt token）；
  classify prompt 附按章分组的细分清单，LLM 选出的 kp 受控匹配回清单名
  （精确 → 包含 → difflib 相似收敛阈值0.5 → 置空），**绝不显示清单外原文**；
  实测「全等三角形的判定」→收敛到全等相关条目、「将军饮马」等无关词置空；
  清单可直接手编 knowledge_points.json 补条目（立即生效）；测试 153 项全绿
- **V1.2k 知识点受控多选**：classify 升级输出 **kps 数组**（最多 3 个）——LLM 每个输出
  仍走受控收敛（精确 → 包含 → difflib 阈值0.5 → 丢弃），去重+截断；题卡用 tag 展示
  （旧草稿回落单 kp）；入库 **analysis.knowledge_points 优先用受控 kps**，
  LLM 自由分析仅作无 kps 旧草稿的兑底；新增 test_kps_cap_three 等（含去重/截断）
- **V1.2l 章节二级复合名**：入库 `analysis.chapter` 升级为「**专题集 · 类别**」二级名，
  `kp_path=[集名, "第N类 名", ""]`（与手动迁移后的存量格式一致，如「初二上学期期中专题 · 全等的性质与判定」）；
  工作台章节分布/筛选按二级名聚合，教材章节名兑底路径不变
- **V1.2m 考点标题行清洗**：转录 LLM 有时把卷面「【考点N】xxx（N分）」栏头并进题干，
  同一题带/不带前缀两份转录查重不中 → 重复入库；新增 `reviewflow.strip_kaoqian_title()`
  转录后统一剥掉（仅匹配题干开头的完整栏头，全/半角括号均可；普通「（8分）」分值标注、
  无分值裸【考点】不剥——宁保勿剥）
- **V1.2n 章节二级迁移工具**：`tools/migrate_chapter.py`（一次性、幂等）——历史单名
  chapter 在 topics.json 中反查所属集 → 升级二级复合名；含「 · 」跳过、找不到同名类别保持原样；
  当前库无待迁数据（存量已手迁）；全量 **160 项测试全绿**
- **V1.2o 工作台章节回归教材结构**：`analysis.chapter` 与工作台章节/知识点筛选改回
  **教材知识结构**（`data/kb/knowledge_points.json`：第一章~第五章 + 每章细分知识点）——
  v1.2c~l 的「专题大类 → 专题集 · 类别」实验结束：**专题类别只服务专题入库**（dest_topic），
  不再写工作台章节；转录期受控 kp 命中后**反查所属教材章**（classify 输出 chapter，
  首个命中 kp 的所属章）；入库优先级：教材章 > 旧草稿 kp_sel > LLM 分析（CHAPTERS 词表兜底）；
  `kp_path=[章, "", 首个受控kp]`；stats facets 章节候选**按 KB 章序**下发（中文按字典序
  会得「一三二五四」错序；Flask jsonify `sort_keys=False` 保插入序，前端去 `.sort()`）；
  `tools/migrate_chapter.py` 改写为「专题集 · 类别」→ 教材章（类别名匹配优先→kp 投票兜底，
  默认 dry-run，`--apply` 落盘带 .bak；存量 1 题已迁移）；测试 163 项全绿
- **V1.2p 视频讲解改链接**：**不再上传本地视频**——人审卡/例题卡「🔗贴视频链接」
  粘贴 B 站等 URL 存档（`POST /api/videos` 上传端点移除）；`video` 字段语义扩展：
  `http(s)://` = 外链（新常态），否则 = 历史本地文件（v1.2 存量，仍 `<video>` 播放+清理链保留）。
  **B 站链接自动内嵌播放并从指定时间开始**：`…?t=340` → iframe
  `player.bilibili.com/player.html?bvid=…&t=340&page=…`（BV/av 号+t/p 参数解析，
  autoplay=0/danmaku=0/high_quality=1）；YouTube → nocookie embed（start=）；其他链接 →
  新标签打开。链接无文件本体：删除题/例题跳过文件清理；测试 164 项全绿
- **V1.2q 图形交叉引用保护 + 交互细修**：① **丢图事故修复**——to_book 的错题与专题例题
  **共享同一 topic_crops 文件**，而删除链路只对视频做了引用检查、crop 是无条件删：
  「专题删例题/错题本删题」会把另一方图形连带删掉（q20261106-111019 图形丢失）。
  修复：`topics._remove_example_file` 与 `store.remove` 删 crop 前统一交叉引用检查
  （其余例题 crop_referenced / 其余错题 / 未处置草稿 crop_draft_ref，全无引用才删文件；
  文件保留时草稿坏引用不清；已处置草稿引用不阻塞）；丢图文件从草稿源裁剪图恢复。
  ② 详情弹窗「✎改图」按钮移除（`/api/questions/<id>/figure` API 保留备用）。
  ③ 视频链接改**面板内联输入**（人审卡/例题卡 onchange 即存，清空=移除；不再弹 prompt）。
  测试 167 项全绿（含 TestCropSharedProtection 事故场景回归）
- **V1.2r 例题分析补齐 + 视频交互细化**：① 专题例题与错题本详情**同口径**——数据模型扩
  kps/key_idea/pitfall；双目的地入库时错题本链的分析结果**自动回写专题例题**
  （0 额外 token）；仅存专题的例题走「🤖 AI 分析」**按需生成**（新端点
  POST /api/topics/examples/<id>/analyze，纯文本 LLM 一次调用，kps 受控收敛复用
  reviewflow.converge_kps）；例题卡新增知识点 tag、「💡思路与易错点」折叠块。
  ② 视频链接交互细化：**不再 onchange 自动写库**——无视频=粘贴+「➕添加」，已有=
  禁用输入+「✎编辑」（启用后「✅保存/取消」），「移除」弹 confirm（人审卡/例题卡同款）。
  ③ 例题卡删除按钮 ✕ → 「🗑 删除」（confirm 文案对齐 crop 交叉引用保护语义）。
  测试 170 项全绿
- **V1.2s 提交入库异步化**：**根除提交卡转圈超时**（§12 v1.2 同源问题的彻底解）——质量链
  （验伪+分析，慢引擎下每题可达数分钟）不再挂在同步 HTTP 里：`POST /api/sheets/<sid>/commit`
  立即返回并转后台线程执行（`_commit_jobs` 进程内任务表 + threading.Lock；同 sheet
  running 中重复提交 → **409**，根治"重复点提交"隐患）；新增
  `GET /api/commit-status/<sid>` 轮询进度（错题本/专题/跳过计数 + 当前题号 + 已用时）；
  前端遮罩改为轮询驱动（1.5s 间隔，单张兜底 20 分钟，服务重启丢 job 15s 后友好提示）；
  pipeline.ingest_selected 增加 progress 回调（每题处置后触发）。
  修复实现中的死锁：`_commit_job_gc` 在持锁块内重复加非重入锁（faulthandler 栈转储定位）。
  测试 172 项全绿（含异步 job 完成回读 / 运行中 409 拒绝重复提交）
- **V1.2t 专题/错题本解耦**：**移除「📥存入错题本」**（例题卡按钮、做题页做错例题按钮、
  `POST /api/topics/examples/<id>/to-book` 端点、`topics.to_book`）——专题（主动备考的
  典型例题集）与错题本（被动纠错）**两个体系相互独立**，不再互相转化，简化心智与数据链
  （消除共享 crop 文件的复杂度来源）；`to_book` 产生的**存量**错题仍保留（图形交叉引用
  保护 v1.2q 不变，守护历史共享文件）；录入期双目的地（dest_topic+selected 多选）不受影响
- **V1.3 拍摄方向自动扶正**：照片**内容横躺**（拍照时手机转了 90°，EXIF 无标记——v0.5.1
  只管 EXIF）→ 转录前自动检测并扶正。两层检测：**CV 行/列投影剖面差分能量零 token 预筛**
  （正立文档文字行水平 → 行剖面波动 ≫ 列；比值 ≥1.25 判正立直接放行，多数照片零开销）；
  判躺倒/不确定才 **640px 小图视觉调用**确认角度（±90°/180° 行剖面分不出，一次调用
  `{"rotate":0|90|180|270}`，失败降级不旋转）。扶正后的照片作为**转录/抓图/存盘唯一输入**
  → 坐标系全链路一致；sheet 记 auto_rotated，人审视图亮「↻ 已自动扶正 N°」标。
  测试 178 项全绿（test_rotation_detect.py：CV 判定/LLM 确认/失败降级/转录集成/EXIF 叠加）
- **V1.3.1 扶正检测定稿（四宫格→二选一）+ 人审兜底**：真实照片实测暴露 **LLM 角度感知
  不可靠**（开放提问 270° 误答 180°、四宫格指认 50~75%）——重构确认策略：CV 判躺后，
  生成【顺90/顺270】两个**已竖直**候选拼图，让模型做**二选一指认**（只需识别上下颠倒，
  无需角度感知；实测 4/6 仍非 100%）→ 自动扶正 + **人审兜底**：人审视图新增
  「↻ 旋转90°」按钮（`POST /api/sheets/<sid>/rotate`：照片顺转 90° + figure_boxes/
  manual_box 按 orientation=6 映射同步变换 + recrop_all 整单重裁，可连点直到正立，
  GeoGebra 导出图不受影响）。180° 倒拍为 CV 行剖面盲区，概率极低接受不处理。
  测试 180 项全绿（+rotate_sheet_photo：box 映射/连点回环/404）
- **V1.3.2 跨页题拼接**：一道题跨两页（两张照片）——上传缩略图新增「**⇧ 接上张（跨页题）**」
  开关：转录时前端把标记的照片与其上一张（组）**canvas 纵向拼接为长图**（等宽缩放 +
  页间 28px 白条），**一次转录产出完整一道题**（figure_boxes/裁剪图/人审/入库全链路
  自然工作，无合并数据结构）；后端配套：拼接长图（H/W≥1.5）`preprocess` 最长边
  1600→**2400**（两页文字保持可读），转录 prompt 增加跨页条款（截断题必须合并为
  一题完整转录，boxes 标注长图坐标）。测试 185 项全绿（test_crosspage.py）
- **V1.3.3 录入完成横幅限高**：多题入库后结果横幅（#result-banner）内容超屏、
  「知道了」按钮滚出视口无法关闭——限高 86vh + 内部滚动，「✅ 录入完成」标题
  与关闭按钮 **sticky 常驻**（滚动任意位置均可见可点）
- **V1.3.4 工作台微调**：筛选栏「知识点」紧跟「章节」（章节变→知识点候选联动，顺序与
  使用频率一致）；错题列表去「连对」列（连对信息在练习/详情仍可见），加「录入」列
  （created_at 保留到天）
- **V1.3.5 GeoGebra 功能整体移除（开发版+发布版）**：随发布决策（RELEASE_DESIGN.md）——
  D21 非商业许可边界 + bundle 体积/首跑下载负担，重画功能退出产品；
  删 ggbflow.py / tools/fetch_geogebra.sh / tests/test_ggb_figure.py / static/vendor/geogebra；
  删 3 端点（/api/ggb-script、sheets ggb-figure、questions figure）+ 前端整个工作台
  （弹窗/Set* 翻译器/按钮；viewImg 保留）；pipeline/topics 移除 figure_ggb 存档链
  （历史数据残留字段忽略不清洗）；run.sh 自检行移除；回归 172 项全绿（ggb 契约测试随之删除）
- **V1.3.6 数据外置改造（RELEASE_DESIGN.md R1 完成）**：新增 `paths.py` 收口——
  ① 目录解析三优先级：`WRONGBOOK_DATA` 环境变量 > 打包态平台数据目录
  （Win %APPDATA%/mac ~/Library/Application Support，升级覆盖安装不丢数据）> 开发态项目内
  data/（行为不变零迁移）；app_root() 区分程序资产（static/engine/KB 种子随包）与可变数据；
  ② store/topics/ai_config/kbtree/pipeline/app 全部路径改走收口（out/ 产物也归数据目录）；
  ③ KB 目录数据化：首次从随包种子播种到数据目录（手编 knowledge_points.json 升级不丢）；
  ④ JSON 写入加固：atomic_write_json = tmp+fsync+.bak 三代轮换（.bak/.bak.1/.bak.2）
  + 目录 fsync，覆盖 store 错题/草稿/users（原先无 bak）/topics/ai_config/kp 清单全部写入点；
  ⑤ data/meta.json 版本钩子（schema=1，首次写入幂等，降级保护提示）；
  新增 tests/test_paths.py 11 项（目录解析/轮换/meta/存储集成/KB 播种），全量 **183 项全绿**；
  真实服务冒烟通过（dev 行为不变：数据仍在项目 data/，meta.json 首次生成）
- **V1.3.7 激活客户端（RELEASE_DESIGN.md R2 完成）**：新增 `license_client.py` + `tools/license_keygen.py`（Ed25519 密钥对生成，开发期密钥已生成、发布前在 ECS 重签替换公钥）——
  ① 设备指纹：已存档优先 → Win MachineGuid/mac IOPlatformUUID 哈希 → 随机落盘；
  ② 凭证结构 v2：`license.json = {license(签发字段+签名,客户端不可变), receipt(最近服务端签名回执)}`——
  宽限期以回执 server_time 为基准（心跳续期不能改已签名字段，客户端无私钥不能伪造回执）；
  ③ 状态机 ok/grace/locked/unactivated：本机回拨被回执时间封底且回拨期不视为"新鲜"；
  ④ app 门禁：未激活/锁定时业务 API 全 403，首页/激活端点放行（打包态默认启用，
  开发态默认关闭、`WRONGBOOK_LICENSE=on` 联调）；启动/状态查询触发到期后台心跳（24h 间隔）；
  ⑤ 前端：激活页（独立视图+锁定原因文案）+ 宽限黄条（剩余天数）；
  ⑥ 传输层 urllib 零新依赖（自签证书场景内嵌公钥固定验证，`WRONGBOOK_LICENSE_SERVER` 可覆写地址）；
  新增 tests/test_license.py 25 项（验签/绑定/状态机/回拨/激活/心跳/门禁），全量 **208 项全绿**；
  mock ECS 端到端冒烟通过（未激活 403 → 激活 → 全功能）
- **V1.3.8 ECS 验证服务（RELEASE_DESIGN.md R3，服务端代码就绪）**：新增 `ecs/` 目录——
  `license_server.py`（activate/heartbeat/version/healthz + SQLite WAL + 每IP限流 + audit 留痕，
  gunicorn 0.0.0.0:8901）+ `license_admin.py`（issue/list/revoke/reset-seats/extend/audit CLI，
  码面去除易混字符）+ systemd 单元 + `setup_server.sh` 一键部署 + DEPLOY.md 步骤指南；
  正式 Ed25519 密钥对已生成（私钥 `data/license_ecs_private.pem` 已 gitignore，随部署包上传；
  公钥已烧入客户端，服务地址默认 http://147.139.167.18:8901）；
  本地全链路实测：激活/幂等重激活/席位上限 403/错码 401/续费延期下次心跳生效/吊销即锁，全部符合契约；
  **真实部署闭环（147.139.167.18:8901，HTTP 直连）**：公网探活/真实激活/心跳回执刷新/门禁放行全部通过；
  加固：license 流量强制直连不走系统代理（ProxyHandler({})）——本机代理环境曾致激活误报网络错误；
  冒烟码席位已用 1 个（如需回收：ECS 上 reset-seats）
- **V1.4.0 打包发布（RELEASE_DESIGN.md R4，mac 包完成）**：新增 `packaging/`（wrongbook.spec +
  build_mac.sh + BUILD_WINDOWS.md）——① 定稿：心跳回 24h、ENABLED 回 frozen/env 策略；
  ② APP_VERSION 唯一源化（paths.py）；③ common.py/app.py 冻结态路径改造（app_root 挂 sys.path）；
  ④ **export_paper 导出双通道**：开发态子进程 → 冻结态进程内 `export_paper.main()`
  （打包后 sys.executable 不再是 Python 解释器，子进程方式失效）；
  ⑤ 打包态启动：端口自动回退（8899→8891）+ 自开浏览器；
  ⑥ 构建脚本：回归门禁 → PyInstaller windowed onedir+.app → 资源复制（static/engine/kb 种子）→
  白名单校验（本地数据/密钥/geogebra 绝不入包）→ zip；
  ⑦ **冻结态真实冒烟通过**：App Support 数据外置 ✓ 激活门禁+真实 ECS 激活 ✓ KB 播种 ✓
  静态资源 ✓ 进程内 PDF 导出 ✓（修复：Timer daemon 参数/pipeline sys 导入）；
  产物 `dist/AI错题本-v1.4.0-mac.zip`（83MB）；Windows 包待 Windows 机按 packaging/BUILD_WINDOWS.md 构建
- **V1.4.0a Windows 实测反馈（单实例 + 退出按钮）**：Windows 下窗口化 exe 无窗口无终端，
  关浏览器不退出、重复双击会顺延新端口多实例——①**固定 8899 单实例**：启动探测
  （connect_ex→bind 双检），占用则探针 /api/license 区分自家实例（webbrowser 拉起已有页面 +
  原生弹窗提示后退出）与第三方占用（明确报“端口被占用”后退出，不再顺延新端口）；
  ②**页面右上角「⏻ 退出」**：POST /api/shutdown → 0.3s 后 os._exit(0) 释放端口
  （windowed exe 唯一退出入口；激活门禁豁免该端点——被锁用户也必须能退出）；
  ③加固：端点要求自定义头 X-WrongBook-Exit:1（跨站表单无法携带，阻断恶意网页
  静默关停本机服务）；门禁豁免/关停探测/退出调度各 1 项测试，全量 **211 项全绿**
- **V1.4.0b 吊销感知闭环（用户反馈：吊销后重启仍可进入）**：根因——吊销感知依赖心跳，
  而心跳带节流且刚验证过就不发，重启后 10 分钟内本地凭证仍新鲜。四处修复：
  ①启动时**无条件同步验证** validate_on_startup()（不看节流；离线短超时 4s 按宽限处理，
  不阻塞启动）——吊销/延期在**下次打开**即感知；②后台循环检查间隔 24h→**10 分钟**
  （持续挂机也能及时感知）；③吊销红字提示：心跳收到 code_revoked → 落提示标记，
  激活页显示「激活码已被停用，请联系管理员重新发码」，重新激活成功自动清除；
  ④前端（用户方案采纳+修正）：全局 fetch 覆盖层（403 license_* → 刷新回激活页；
  blob:/file: 静态预览 mock——修正原稿：**http 模式网络错误正常抛出不伪装成预览响应**）+
  **3 秒轮询 /api/license**（后端感知后 UI 秒级跳转激活页）；
  新增 5 项测试（启动无条件验证/吊销感知/离线容忍/未启用跳过/提示生命周期），全量 **216 项全绿**；
  mac 包重建（83MB）
- **V1.4.0c Windows 路径兼容 + 吊销恢复零操作**：① Windows 实测转录照片/图形不显示——
  根因是服务端存平台数据目录**绝对路径**（`C:\...\data\users\...`，反斜杠），前端旧正则
  `^.*data\/` 只认正斜杠不命中 → 整条绝对路径被拼进 `/data/` URL → 404。修复：前端新增
  `relDataUrl()`（反斜杠归一 + 剥离 data/ 前缀，幂等），替换全部 6 处 `/data/` 拼接点
  （人审原图/裁剪图/视频/详情弹窗/工作台/专题例题）——存量反斜杠路径无需迁移，渲染时归一；
  ② 吊销恢复零操作：停用提示标记升级为存原码，启动验证时若标记存在且无本地凭证 →
  **静默用原码重新激活**（管理员 enable 后，家长下次打开自动恢复全功能，零输入）；
  激活页红字文案同步改为「若管理员已重新启用，重启本程序即可自动恢复，或重新输入激活码」；
  ③ **修复静默恢复失效的真 bug**：validate_on_startup 调 activate(code, timeout=…) 而
  activate 无该参数 → TypeError 被 except 吞掉 → 自动恢复永远走不到（新测试当场抓住）；
  activate 增加 timeout 形参；新增 2 项测试（enable 后启动静默恢复 ✓ / 未 enable 保留提示 ✓），
  全量 **218 项全绿**
- **V1.4.0d 严重 bug 修复（专题入口旋转后题目消失，用户报告+定位）**：专题页「＋录入」
  转录的草稿单带**纯前端入口印记** `mode='topic'`（后端不存该字段，V1.2b 设计）；
  V1.3 加入的「↻ 旋转90°」响应处理用**整表替换** `sheets[i]=j`（j 为后端返回，无 mode）→
  印记丢失 → `(s.mode||'book')===INGEST_MODE` 过滤把它当错题本入口隐藏 → 题目"凭空消失"
  （错题本入口不触发——直接点菜单录入无此问题）。修复：整表替换前继承 `j.mode=sheets[i].mode`；
  排查确认 recrop/patchQ 均为字段级就地更新无此问题（全文件唯一整表替换点）；
  node 推演过滤逻辑 ✓，回归 218 项全绿；mac 包需重建，Windows 同步重打
- **V1.4.0e 勾选状态修正 + 编辑事故防御（用户修复 + 回归防御）**：① 移除转录后强制自动勾选
  （专题锁定入口的 dest_topic 预选改为**手动勾选时预选锁定类别**并实时 patchQ 落库；
  锁定条换类对已勾选题同步写盘；提交计数排除已 done 题）——根除"显示已选实际未选"
  的前端状态失真；② **编辑事故防御**：用户整文件覆盖编辑时把 relDataUrl/全局 403 感知层
  意外回退，已重新应用（两处均为用户已报 bug 的修复）；③ git 卫生：license_revoked.json/
  meta.json/topics.json.bak.* 等本地状态文件曾被误提交，已 git rm --cached + gitignore
  （data/*.bak.* 通配），回归 218 项全绿
- **V1.4.0f 两处体验修复（用户反馈）**：① **提交进度"第?题"**——cur_no 只在每题处置
  **完成后**的 progress 回调设置，首题质量链 0.5~2 分钟期间前端显示"当前第?题（0·0·0）"；
  修复：每题**开始处置时**先上报题号（`_tick(q.get("no"))`），事件序列变为
  [(1,0),(1,1),(2,1),(2,2)]，题号即时可见；② **已开启再双击的弹窗体验**：改为**静默
  webbrowser 拉起已有实例页面**后退出（不再弹"程序已开启"提示）；端口被第三方程序占用
  的分支保留弹窗（那种情况用户需要知道原因）；离线事件推演 ✓ 回归 218 项全绿
- **V1.4.0g Windows 单实例探针误判修复（用户实测）**：Windows 打包后"已开启再双击"
  仍提示"端口被占用"——根因：单实例探针 `_port_owner_is_ours` 用裸 urllib.urlopen，
  **Windows 下 urllib 读注册表系统代理设置**，127.0.0.1 请求被劫持到代理 → 探测失败 →
  误判为"第三方占用"（mac 本机 NO_PROXY 含 127.0.0.1 所以侥幸正常）。修复：探针
  `ProxyHandler({})` 强制直连 + localhost 双尝试兜底（与 license_client._http_post 同款；
  本次事故再次印证：所有本机回环 HTTP 一律强制直连，不信任系统代理）
- **V1.4.0i 退出按钮失效修复 + 版本号显示（用户反馈）**：① Windows/mac 实测点「⏻ 退出」
  显示"已安全退出"但端口未释放——根因：用户提交 index.html 时把 shutdownApp 的
  校验头 X-WrongBook-Exit 一起回退丢了 → 服务端 403 拒绝，而**前端不看响应直接渲染
  假成功页**（页面撒谎）。修复：恢复校验头 + 前端校验响应（403/失败时 alert 明示，
  不再假装退出）；确定性实测：带头 200 → 1.2s 内进程退出、端口释放 ✓
  （验证时的"进程未退出"假象是 --debug 重载器父进程，打包态无重载器不受影响）；
  ② 导航栏用户处显示版本号角标（v1.4.0，数据源 /api/license 的 app_version，
  dev/打包态一致）
- **V1.4.0h mac 双击已运行实例无反应（机制性修复）**：与 Windows 不同，macOS 的
  LaunchServices 对已运行 .app **只「激活」不重开进程**——单实例检测代码根本不会执行，
  纯 Flask 应用又没人接 LS 的 reopen 事件 → 双击表现为"毫无反应"。修复（R4.0h）：
  ① mac 打包态 **Flask 移后台线程，主线程跑 NSApplication runloop**，委托
  `applicationShouldHandleReopen` → webbrowser 重开页面（Dock 图标点击同理）；
  pyobjc 不可用时回退纯 Flask 主线程模式；② mac 打包依赖 `pyobjc-framework-Cocoa`
  （build_mac.sh 安装 + requirements.txt darwin 环境标记 + spec darwin hiddenimports）；
  ③ 顺带补**打包态文件日志** `_flog` → `data/logs/app.log`（windowed 无终端，
  远程排障/诊断包用）；实测：`open` 二次触发 reopen 事件 → 浏览器重开页面 ✓
  单实例进程数恒为 1 ✓，回归 218 项全绿

---

## 1. 目标与范围

为初中数学建立个人错题知识库，核心闭环：**录入 → 分析 → 练习 → 归档**，并以错题画像驱动针对性出卷。

| # | 功能 | 一句话描述 |
|---|---|---|
| F1a | **Word 排版机** | 照片 → 全题转录排版（题干/LaTeX公式）→ 图形来源分层（SVG为主/裁剪兜底）→ Word 供人审 |
| F1b | 选择性入库 | 人审：勾选要入库的错题 + 标注错因（马虎/不会/其他）→ 质量链 → 入库 |
| F2 | 错题分析 | 知识点/易错点/难度保留（录入时生成）；**相似题生成 V1.0.3 移除**（费 token） |
| F3 | 工作台 | 数量、题型/难度/章节/错因分布，每题练习次数与掌握度 |
| F4 | ~~针对性出卷~~ | **V1.0.3 移除**（AI 命卷费 token 且不实用）；打印需求走「导出PDF」（静态排版不调 LLM） |

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
├── ai_config.py           # AI 引擎注入式配置（DeepSeek/GLM；data/ai_config.json）
├── reviewflow.py          # ★ 排版机：照片→多题转录→图形来源分层→人审图形纠正→重裁
├── pipeline.py            # 入库/相似题/出卷流水线（质量链）
├── wordexport.py          # Word 导出：LaTeX→matplotlib渲染→PNG嵌入docx
├── figureflow.py          # 图形子链：spec生成→geocheck循环→SVG渲染
├── store.py               # JSON 存储层（错题库 + 草稿单）
├── topics.py              # 专题模块（v1.2）：专题集/类别/例题 + 每用户练习记录（v1.2t 起与错题本解耦）
├── stats.py               # 工作台统计
├── serve.sh               # Terminal.app 常驻启动（脱离 IDE shell 生命周期）
├── tests/                 # 回归测试 183 项（图形链/纠正入口/EXIF方向/整单重裁/入库图形源/数据外置）
├── tools/                 # 历史数据迁移：EXIF转正 / 错位裁剪图模板匹配恢复 / 章节二级迁移(v1.2n)
├── static/
│   ├── index.html         # 单页工作台（原生JS + KaTeX本地渲染）
│   └── vendor/katex/      # KaTeX 本地文件（离线可用）
├── run.sh                 # 启动脚本
├── engine/                  # 内联数学引擎（源自 quiz-demo，V1.0.1 起）
├── data/
│   ├── kb/                  # 教材知识库 JSON（浙教版八上全五章）
│   ├── topics.json          # 专题集/类别/例题（v1.2，全局共享，家长录一次；首跑自动种入）
│   ├── topic_crops/         # 专题例题图形（全局共享，与用户 crops 解耦）
│   ├── videos/              # 视频讲解（v1.2，可选上传，大文件不入 git）
│   ├── users.json         # 用户表（v0.7：{users:[{id,name,created_at}], current}）
│   └── users/
│       └── <uid>/         # 每用户一套（切换用户即整体切换数据源）
│           ├── wrongbook.json   # 该用户的错题库（主数据）
│           ├── drafts.json      # 草稿单（含 figure_mode/手动box存档/dest_topic/video）
│           ├── topics.json      # 专题练习记录（v1.2，按用户隔离）
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
  → EXIF 转正（v0.5.1）→ 拍摄方向自动扶正（v1.3：内容横躺时 CV 预筛+LLM 小图确认，
    顺时针旋转 0/90/180/270；扶正后照片为全链路唯一输入，sheet 记 auto_rotated）
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
                      grade, chapter, tp_sel, kps[], kp,  // v1.2c/k/o：归类（教材章+受控kp≤3，只读）
                      selected: false, reason: null, edited_text: null} ] }
      - 题干转录后统一剥掉卷面「【考点N】xxx（N分）」栏头（v1.2m，防查重不中重复入库）
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

> ⚠️ **V1.0.3 整体移除**（AI 命卷 1-3 分钟/次、费 token 且不实用）；打印需求由
> 「导出PDF」（`/api/questions/export-pdf`，勾选题静态排版，不调 LLM）承接。以下为历史设计记录。

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

### 5.5 GeoGebra 图形工作台（v1.1a~v1.3.4，**V1.3.5 已整体移除**）

> 设计与实施记录见 git 历史。移除动机：GeoGebra Apps 免费许可仅限非商业用途（对外分发
> 触碰许可边界，原 D21）；bundle 体积大且首跑下载是发布版的失败点（原 D22）。
> 图形策略回归 v0.6 定稿：**原题裁剪图为权威图形**（转录自动抓图 + 人审手动框选纠正，见 5.1）。

### 5.6 专题模块（v1.2 已实施）

**动机**：错题本是"被动纠错"，专题是"主动备考"——按期中考点清单组织典型例题，
先学典型再刷错题。专题集可扩展（初二中考必考 → 期末/下学期期中等）。

**三级结构**：专题集（如「初二中考必考专题」）→ 类别（第1类三边关系…第10类将军饮马，
含分值/难度徽章）→ 典型例题（题干/选项/答案/解析/图形/视频讲解）。

**种子**（首跑自动种入，幂等；`data/topics.json` 全局共享，家长录一次所有用户可见）：

| 类 | 名称 | 分值 | 难度 |
|---|---|---|---|
| 1-3 | 三边关系 / 中线、高、角平分线 / 内角和与外角性质 | 3/8/3 | 🟢 简单 |
| 4,5,7,8,9 | 角平分线模型 / 全等的性质与判定 / 角平分线的性质与判定 / 垂直平分线 / 三线合一 | 8 | 🟡 中等 |
| 6,10 | 全等模型 / 将军饮马 | 12/3 | 🔴 压轴 |

**例题录入（零 LLM，与「AI 给草稿，人管对错」同哲学）**：

```
排版机转录（复用现有全链）
  → 人审卡每题两个目的地（可多选）：
     ☑ 存错题本（原流程：错因+质量链+查重+验伪+分析）
     ☑ 存入专题（下拉：专题集>类别）
  → 提交：
     仅存专题 → 直接落库（0 LLM 调用）：题干/选项/照片答案已由排版机+人审定稿，
                类别人点选；裁剪图复制进全局 topic_crops/<eid>.png
     双选 → 先存专题再走错题本链（查重只守错题本，重复题仍可存专题）
  → 专题内查重：归一化相似度≥92% 跳过（防同一照片重复存入；算已处置不阻塞）
```

**练习闭环**：
- 例题卡「📝练习此题」/「▶整类练习」→ 复用做题页批改（单选/多选/填空自动评分、问答自判）
- 练习记录按用户隔离（`data/users/<uid>/topics.json`）；**连对 3 次 → 🏆已掌握**（对齐 P3 半自动哲学，但例题是典型题，达标即亮标不涉归档）
- ~~做错例题 → 手动存入错题本~~（**v1.2t 移除**：专题与错题本解耦，互不转化）

**视频讲解（可选，v1.2 → v1.2p 改链接）**：
- **存链接不存文件**：人审卡/例题卡「🔗贴视频链接」粘贴 B 站等 URL（v1.2 上传端点已移除）；
  B 站 App「分享→复制链接」或浏览器地址栏链接均可，链接里的 `t=340`（定位秒数）原样保留
- 双链透传：草稿题 video 字段 → 提交时随错题记录与/或专题例题存档
- 播放（前端 `videoBlockHtml()` 统一渲染）：
  - B 站 URL → iframe 内嵌 `player.bilibili.com/player.html?bvid=…&t=340&page=…`，
    **自动从链接里的 t= 秒开始播放**（BV/av 号 + t/p 参数解析；autoplay=0 点开即播不打扰）
  - YouTube → nocookie embed（t= → start=）；其他链接 → 🔗新标签打开
  - 历史本地文件（v1.2 存量）→ `<video>` 原样播放
- 播放入口：工作台详情弹窗「🎥 视频讲解」折叠块 + 专题例题卡（答案/解析折叠块同款交互）
- 删除清理：链接视频无文件本体，直接跳过；**历史本地文件**仍走交叉引用检查
  （其余题目/未处置草稿单/专题例题，全无引用才删文件）

**数据模型**：

```jsonc
// data/topics.json（全局）
{"version":1,"sets":[{"id":"ts…","name":"初二中考必考专题","categories":[
  {"id":"tc…","no":1,"name":"三边关系","points":3,"difficulty":"easy",   // easy|medium|final
   "examples":[{"id":"te…","type":"single","text":"…","choices":[…],
     "answer":"B","analysis":"解析","figure_crop":"…/topic_crops/te….png",
     "figure_spec":null,"video":null,"source":"来源","created_at":"…"}]}

// data/users/<uid>/topics.json（每用户练习记录）
{"version":1,"practice":{"te…":[{"at":"…","correct":true}]}}   // streak≥3=已掌握
```

**UI**（菜单双入口 v1.2b：「📚录入错题本」「🧠录入专题」+ 工作台/专题）：

两个录入入口共用同一转录/人审流程（一个 section），入口决定题卡模式：

| | 📚 录入错题本（默认） | 🧠 录入专题 |
|---|---|---|
| 主勾选（题头） | 存错题本 → 显示错因标注 | 存入专题 → 自动预选第一个类别（可改） |
| 附加勾选（题卡行） | ☐同时存入专题（勾后出类别下拉；零 AI 秒过） | ☐同时存入错题本（勾后出错因标注；走质量链） |

- 数据层不变：`selected`（错题本）/ `dest_topic`（专题）任意组合，两模式仅 UI 呈现不同
- 上传区标题/提示随模式切换（「上传错题照片」/「上传专题例题照片」）
- 提交：勾选的题逐个处置（双目的地同题各自落库），不勾 = 不录（保持 pending 可再处理）

**专题 Tab**（浏览/练习）：专题集总览（类别卡：难度徽章/分值/例题数/掌握进度）→
类别详情（例题列表：题卡 KaTeX+图形+答案解析折叠+练习/传视频/删除）；
管理：＋新建专题集 / ＋新增类别（名称/分值/难度）/ 删除（连带清理图形与无引用视频）。

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
| DELETE | `/api/questions/<id>` | 删除（连带裁剪图并清理草稿单引用；原图无其他题目/草稿引用时一并删除） |
| GET  | `/out/<file>` | 下载 Word/PDF/MD |
| GET  | `/api/kb` | 教材知识树（章/节/知识点） |
| GET  | `/api/ai-config` | AI 引擎配置视图（引擎/掩码 key/模型；key 永不明文回传） |
| POST | `/api/ai-config` | 保存配置 `{provider?, keys?, models?}`（key 空串=清除；落盘 data/ai_config.json） |
| POST | `/api/ai-config/test` | 连通性测试 `{provider, key?, model?, vision?}`（测试值不落盘） |
| GET  | `/api/license` | R2：激活状态视图（state/grace 剩余天数/掩码码/到期日；顺带触发到期后台心跳） |
| POST | `/api/license/activate` | R2：激活 `{code}` → `{ok}` 或 400 `{error}`（服务端错误码转人话） |
| POST | `/api/shutdown` | 🔒 R4.0a：安全退出（需自定义头 X-WrongBook-Exit:1，防跨站静默关停；激活门禁豁免——锁定态也可退出） |
| GET  | `/api/users` | 用户表 + 当前用户 |
| POST | `/api/users` | 新建用户（题库为空）并切换 `{name}` |
| POST | `/api/users/switch` | 切换当前用户 `{id}` |
| GET  | `/api/topics` | 📚 v1.2：专题全集（类别/例题 + 当前用户练习统计 + spec 渲染 SVG） |
| POST | `/api/topics/sets` | 📚 v1.2：新建专题集 `{name}` |
| DELETE | `/api/topics/sets/<sid>` | 📚 v1.2：删专题集（连带例题图形/无引用视频） |
| POST | `/api/topics/sets/<sid>/categories` | 📚 v1.2：新增类别 `{name,points,difficulty,no?}` |
| PATCH/DELETE | `/api/topics/sets/<sid>/categories/<cid>` | 📚 v1.2：改类别 / 删类别（连带例题） |
| PATCH | `/api/topics/examples/<eid>` | 📚 v1.2：手编例题 `{video?,answer?,analysis?,text?}` |
| DELETE | `/api/topics/examples/<eid>` | 📚 v1.2：删例题（连带图形/无引用视频） |
| POST | `/api/topics/examples/<eid>/practice` | 📚 v1.2：记一次练习 `{correct}` → `{streak,ready,count,correct}` |

端口：`127.0.0.1:8899`（run.sh 启动，自动打开浏览器）。

## 7. 页面设计（V1.0.3：单页 2 Tab + 做题页，原生 JS）

```
┌──────────────────────────────────────────────┐
│ AI错题本·数学  [录入(排版机)] [工作台] [专题]    │
├──────────────────────────────────────────────┤
│ 录入：拖拽/Ctrl⌘+V粘贴/点选照片（可多张）+来源   │
│   → 转录+图形分层进度 → 进入人审视图：           │
│     左：原图（拍照原件，方向已统一）             │
│     右：逐题卡片（KaTeX实时渲染）               │
│        [✂️手动框选]                             │
│        ☑存错题本+错因 · 存入专题(下拉可多选)     │
│        🔗视频讲解(贴B站链接,t=定位播放)          │
│        顶部粘性栏：已选N题（错题本X·专题Y）[提交] │
│ 工作台：三数字卡 + 四分布条形图(默认折叠)        │
│       错题列表：勾选 → [📝开始做题]（新标签页批改卷）│
│       / [🖨导出PDF]；连对3次弹"建议归档"         │
│ 专题：专题集总览(类别卡：难度/分值/进度)          │
│   → 类别详情：例题卡(答案解析折叠/练习/视频/删除)  │
│     视频讲解折叠播放)；[▶整类练习]；连对3次🏆     │
│ 做题页(#quiz，无菜单入口)：试卷式作答 →          │
│   ✅完成并批改：得分+逐题对错+错因/知识点汇总     │
└──────────────────────────────────────────────┘
```

> V1.0.3 起菜单仅「录入/工作台」；做题页由工作台「开始做题」新标签页直达（#quiz），
> 无会话时显示引导（不再随机刷题）；相似题/出卷入口已移除。
> 图形纠正唯一入口 = ✂️手动框选（转录时已自动抓图；GeoGebra 重画 V1.3.5 移除，见 5.5）。

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
| ~~出卷端到端~~ | ~~8题针对卷~~ | **V1.0.3 移除该功能，测试随之取消** |
| 统计正确性 | 手工构造小库对账 dashboard 数字（含错因分布） | 全对 |
| Web 冒烟 | 启动服务 curl 各端点 + 浏览器走查4 Tab | 全通 |
| 回归套件 | `pytest tests/`（图形链/纠正入口/EXIF方向/整单重裁/入库图形源/查重/多用户/知识树/AI引擎配置/专题+视频/考点清洗/知识点受控/跨页拼接/旋转扶正/数据外置） | 183 项全绿 |

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
| D1 | 存储选 JSON 而非 SQLite | 规模小+git友好；发布复审维持 JSON（RELEASE_DESIGN.md §3.6，迁移触发条件与写入加固 fsync/.bak 三代轮换见该节）；>2000题或单文件>20MB再议 |
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
| D20 | 发布给他人时不能携带/暴露开发者 API key | **注入式配置**：三层来源（网页「⚙ 设置」落盘 data/ai_config.json > 环境变量 > key.env 开发者便利文件），前两者均已 gitignore；`make_client` 统一注入（provider 缺省=用户选中引擎）；GLM 默认 glm-5.3-flash（原生多模态单模型，看图/解题同模型；视觉字段可改 glm-4.6v-flash 省钱）；key 前端只回显掩码，设置弹窗带「测试连接」（文本+视觉各一次最小真实调用） |
| D21~D24 | GeoGebra 工作台相关决策（非商业许可/Bundle 体积/命令成功率/改图覆盖） | **V1.3.5 功能整体移除，决策随之消解**（历史见 git；图形策略回归 v0.6：裁剪图为权威 + 手动框选纠正） |
| D25 | 专题例题不再过质量链（验伪/分析费 token 且类别已由人定） | 仅存专题零 LLM：题干/图形/答案已由排版机+人审定稿，提交即落库；双目的地时错题本副本仍走完整链；专题内查重≥92% 防重复存入；做错例题手动存错题本（对齐 P4 人管对错） |
| D26 | 视频讲解的存储形态 | **v1.2p 定稿：存外链不存文件**（B 站等 URL 含 t= 定位，B 站自动内嵌从指定秒播放）——上传端点移除、无大文件管理/清理负担；v1.2 存量本地文件保留 `<video>` 播放与交叉引用清理链（错题/未处置草稿/专题例题全无引用才删，gitignore 不入库） |

## 11. 已拍板决策（v0.2 拍板，v0.5.2 沿用）

| # | 议题 | 结论 |
|---|---|---|
| 1 | 归档门槛 | 连对 **3 次**建议归档，人工确认 |
| 2 | 难度分级 | **1-3 级** |
| 3 | ~~出卷模式~~ | 原定二选一；**V1.0.3 随出卷功能一并移除** |
| 4 | ~~相似题默认数~~ | 原定 2 道；**V1.0.3 移除相似题功能** |
| 5 | 公式渲染 | **直接 LaTeX**（Web 用 KaTeX 本地渲染；Word 用 matplotlib 渲图嵌入） |
| 6 | 刷题判定 | 选择/填空自动评分，问答要点比对；**V1.0.3 练习唯一入口=工作台勾选→开始做题** |
| 7 | 新增：Word 排版机 | 两阶段录入：排版机人审 → 选择性入库（勾选题+错因标注） |
| 8 | 新增：错因分类 | 马虎 / 不会 / 其他+备注；驱动工作台分布与出卷优先级 |

## 12. 数据事故记录（续）

> v0.7 当天事故见 §8。新条目按时间追加。

- **v1.2（2026-11-05）提交转圈 + 恢复记录**：用户提交双目的地题（错题本+专题），遮罩转圈不关。
  排查：服务端实际已完整成功（错题本+专题双写，质量链 LLM 用了约 3 分钟），fetch 挂在 pending——
  根因组合：① pipeline `_ask_text_json` 未对齐 v1.1 的 D12 修复（8000 tokens 被思考链烧光，三级降级每级都等满响应）；
  ② dev server 串行（threaded 默认 False），慢请求占住全站；③ 前端 fetch 无超时，慢=永久转圈。
  修复：pipeline 预算翻倍 16000 + 第 4 级 nothink 降级 + timeout 300→120s；`app.run(threaded=True)`；
  （该问题已于 v1.2s 彻底解决：提交异步化 + 进度轮询，同步 HTTP 不再长挂，见版本历史）
  前端 `fetchT()` 超时保护（提交 180s/转录 300s）+ 遮罩文案写明预期时长 + AbortError 友好提示。
  **附带数据事故**：排查中冒烟实例清理命令误删真实 `data/topics.json`/`topic_crops`/`videos`/用户练习文件。
  恢复：错题本记录完好未损，从其记录重建专题例题（保持 id 与草稿引用链一致）、图形从用户 crops 拷回；
  **视频文件本体丢失需用户重传**（引用路径保留）。教训：清理临时实例必须先校验 cwd 与数据目录归属，
  含 data/ 路径的 rm 一律先 ls 确认——与 v0.7 教训同源（测试/清理须隔离真实数据）。
