# AI 错题本（数学）

照片 → AI 排版机转录 → 人审 → 错题知识库 → 刷题练习 / 导出 PDF 打印。
**数据全部存本机，支持多用户隔离**；教材知识树自动归类知识点；专题模块（主动备考）
与错题本（被动纠错）双体系；激活码授权（ECS 公网验证，数据不出户）。
当前版本 **V1.4.0**，回归测试 **208 项全绿**。
完整设计（数据模型 / API / 版本演进 / 决策记录）见 `DESIGN.md`；发布与激活体系见 `RELEASE_DESIGN.md`。

## 功能一览

| 模块 | 说明 |
|---|---|
| 📚 录入题目 | 拖拽/粘贴/多张试卷照片 → AI 多题转录（公式转 LaTeX）→ 按框抓取图形原图 → 人审（对照原图逐题确认/修正/勾选入库+错因标注）→ 质量链（查重 → 独立解题验伪）入库；支持跨页题拼接、拍摄方向自动扶正+人工旋转兜底 |
| 📊 工作台 | 数量/题型/难度/章节/错因分布统计；错题列表（公式渲染、筛选联动、详情弹窗）；勾选 → 开始做题或导出 PDF |
| 🎯 专题 | 专题集→类别→典型例题三级（初二中考必考 10 类已预置）；录入入口下沉到类别（锁类别转录）；例题卡：练习/整类练习/答案解析/视频讲解（B 站链接自动内嵌定位播放）|
| 📝 做题批改 | 试卷式作答；单选/多选/填空自动评分、问答自判；连对 3 次 → 建议归档 |
| 🖨 导出 | 静态排版不调 LLM：练习卷 PDF（矢量公式）/ Word 排版稿 |
| 👥 多用户 | 页头切换/新建，题库/草稿/照片/专题练习记录按用户隔离 |

## 快速开始（开发态）

```bash
cd wrongbook
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
./run.sh                        # 打开 http://127.0.0.1:8899
./run.sh --debug                # 改代码自动重载
```

首次启动未配 AI key 时会自动弹出设置向导。激活门禁**仅在打包态默认启用**，
开发态默认关闭（联调真实激活：`export WRONGBOOK_LICENSE=on` 后启动）。

## AI 引擎与 API Key（注入式，key 不入包不入库）

发布包内**不含任何 key**。两种方式（同时存在时网页配置优先）：

1. **网页设置（推荐）**：右上角「⚙ 设置」→ 选引擎（DeepSeek / 智谱GLM）→
   粘贴自己的 API Key →「测试连接」→ 保存。仅存本机（key 只显掩码，永不明文回传）
2. **环境变量**：`GLM_API_KEY=…` / `DEEPSEEK_API_KEY=…`，或写 `key.env`（已 gitignore，run.sh 自动加载）

GLM 默认 `glm-5.3-flash`（原生多模态，看图/解题同模型，¥0.8/¥2.8 每百万 tokens）；
模型名均可在设置弹窗里改。

## 激活与发布版（R1~R4）

面向外地家庭分发：**每家本地安装，数据不出户；公网（阿里云 ECS）只做激活验证/心跳/版本检查**。

| 环节 | 位置 | 说明 |
|---|---|---|
| 客户端 | `license_client.py` | 设备指纹（Win MachineGuid / mac IOPlatformUUID 哈希）、Ed25519 验签、24h 心跳、**离线宽限 14 天**、锁定回激活页 |
| 验证服务 | `ecs/`（已部署 147.139.167.18:8901） | Flask+SQLite；激活/心跳/版本检查；限流+审计留痕；部署步骤见 `ecs/DEPLOY.md` |
| 发码 | ECS 上 `venv/bin/python license_admin.py issue --owner 张同学家` | 线下收款后发码；永久/按天/试用码；吊销/延期/清席位同 CLI |
| 打包 | `packaging/` | mac：`./packaging/build_mac.sh` → `dist/AI错题本-v1.4.0-mac.zip`；Windows：`packaging/BUILD_WINDOWS.md` |
| 单实例/退出 | `app.py` | 固定 8899 单实例：重复双击 → 自动拉起已有页面 + 原生弹窗提示（不再顺延新端口）；页面右上角「⏻ 退出」安全关闭后台并释放端口（打包态唯一退出入口，带防跨站校验） |
| 吊销感知 | `license_client.py` | 吊销/延期生效：**下次打开即感知**（启动无条件验证）+ 持续挂机每 10 分钟检查 + 页面 3 秒轮询秒级跳转；吊销后激活页红字提示「已被停用，联系管理员」 |

- 打包态数据目录外置：Win `%APPDATA%\WrongBook\` / mac `~/Library/Application Support/WrongBook/`
  ——**升级直接覆盖安装，题库/照片/激活状态不丢**
- 构建脚本自带门禁：回归不全绿不出包；白名单校验（本地数据/密钥绝不入包）

## 数据存储（JSON 文件，人类可读，原子写+三代备份）

开发态在项目 `data/`；打包态在平台数据目录（见上）。布局：

```
data/
├── meta.json               # 数据结构版本（升级迁移钩子）
├── ai_config.json          # AI 引擎配置（gitignore）
├── kb/                     # 教材知识库种子（浙教版八上全五章；首次运行播种到数据目录）
├── topics.json             # 专题集/类别/例题（全局共享，家长录一次全家可见）
├── topic_crops/            # 专题例题图形
├── users.json              # 用户表 + 当前用户
├── license.json            # 激活凭证（签名回执制；gitignore）
└── users/<uid>/
    ├── wrongbook.json      # 错题库（主数据）
    ├── drafts.json         # 排版机草稿单
    ├── topics.json         # 专题练习记录
    ├── photos/             # 原始照片（EXIF 转正后存盘）
    └── crops/              # 裁剪图（权威原件）
out/                        # 导出的 PDF/Word（产物，备份时一并带上）
```

## 模块

| 文件 | 职责 |
|---|---|
| `app.py` | Flask 服务 + API + 激活门禁 |
| `paths.py` | 目录解析与 JSON 原子写唯一收口（数据外置/打包态平台目录/fsync+.bak 三代轮透/meta 版本钩子） |
| `reviewflow.py` | 排版机：多题转录（EXIF 转正+方向扶正）、跨页拼接、自动抓图、知识点受控归类、草稿单 |
| `pipeline.py` | 入库质量链（查重→图形→分析→验伪）+ 导出 PDF（开发子进程/打包进程内双通道） |
| `topics.py` | 专题模块（专题集/类别/例题 + 按用户练习记录） |
| `figureflow.py` | 图形向量化子链（spec→geocheck 循环→SVG；人审期按需调用） |
| `wordexport.py` | Word 导出（LaTeX→matplotlib→PNG 嵌入 docx） |
| `kbtree.py` | 教材知识树 + 细分知识点受控清单（LLM 只在清单内选） |
| `store.py` / `stats.py` | JSON 存储层（多用户隔离 + 查重）/ 工作台统计 |
| `ai_config.py` | AI 引擎注入式配置（掩码回显 + 测试连接） |
| `license_client.py` | 激活客户端（设备指纹/验签/心跳/宽限/锁定） |
| `engine/` | 内联数学引擎（geocheck/geoconstruct/make_figures/verify_answers/export_paper/generate_any/schema） |
| `tools/` | 历史数据迁移 + `license_keygen.py`（激活密钥对生成） |
| `ecs/` | 激活验证服务（已部署）+ 发码 CLI + 部署指南 |
| `packaging/` | PyInstaller spec + mac 构建脚本 + Windows 打包指南 |
| `tests/` | 回归测试 208 项（全部离线，不调真实 API） |

跑测试：`.venv/bin/python -m pytest tests/`。

## 已知限制

- 抓图框在特殊版面可能偏移——人审界面有原图对照，【✂️手动框选】可修正
- Word 公式为高清图片（不可编辑）；OMML 原生公式列后续版本
- 照片极度模糊/倾斜时转录会明确报错，不入库脏数据
- 章节词表未覆盖的题按年级进度归类，章节显示"其他"，可手改
- 导出 PDF 的矢量渲染依赖本机 Chrome（缺失时降级出 md/html，可浏览器打印）
- 激活为"防君子"设计：Python 打包可被解包，定位是授权管理而非防破解（详见 RELEASE_DESIGN.md §4.6）
- Windows 安装包未签名：首次运行需 SmartScreen「更多信息→仍要运行」（见使用说明）

## 版本

详细演进（V1.0 功能闭环 → V1.2 专题/视频 → V1.3 录入体验 → V1.4 打包发布）见 `DESIGN.md` 版本历史。
