# AI 错题本（数学）· 微信小程序与 ECS 云端化实施方案

> 版本：**M0.1（方案初稿）** ｜ 日期：2026-11-10  
> 适用背景：在现有 Python 桌面版（V1.4.0）与阿里云 ECS（`147.139.167.18`）基础上，响应试用家长诉求，优先推出手机端（微信小程序），实现**手机直接拍照录题、数据集中保存在 ECS、多端实时同步**。

---

## 1. 核心疑虑解答：做微信小程序，还能不能用 Python？

**结论：完全可以！不仅能用，而且你现有的 Python 代码可以复用 85% 以上，绝不需要推倒重写。**

微信小程序采用的是标准的**「前后端分离」**架构：
- **小程序前端（运行在家长手机微信里）**：确实不能运行 Python，必须使用微信官方前端技术栈（`WXML + WXSS + JavaScript` 或 `uni-app`）。但它只是一层很薄的**「界面外壳」**——只负责调起手机摄像头拍照、展示题目列表、勾选复选框、展示练习卷，相当于替代你现在的单个文件 `static/index.html`。
- **云端后端（运行在你的阿里云 ECS 上）**：**100% 继续使用你现在的 Python + Flask + Gunicorn**！
  - 你花大量精力打磨的视觉转录与方向扶正（`reviewflow.py`）、错题验伪与受控知识点归类（`pipeline.py` + `kbtree.py`）、初二必考专题体系（`topics.py`）、矢量几何渲染（`figureflow.py`）、PDF 试卷排版导出（`engine/export_paper.py`）以及激活码体系（`license_server.py`），**全部原封不动运行在 ECS 上**，小程序只需通过 HTTPS 调接口即可。

---

## 2. 微信生态的硬性门槛与两条落地路线对比

在动手写小程序代码前，有一个**微信官方的硬性合规要求**必须提前知晓：
> 微信小程序正式版/体验版强制要求所有后端请求（`wx.request` / `wx.uploadFile`）必须使用 **已完成中国大陆 ICP 备案的域名 + HTTPS（443 端口）**，**不允许直接请求纯 IP（如 `http://147.139.167.18:8901`）**。

根据你当前是否已有备案域名，提供两条可无缝衔接的路线：

| 维度 | 路线 A：微信小程序（正式目标形态） | 路线 B：手机自适应 H5 网页版（极速尝鲜过渡） |
| :--- | :--- | :--- |
| **使用入口** | 微信下拉小程序 / 微信群分享卡片直接进 | 微信内点击链接 / 手机浏览器添加到桌面图标 |
| **拍照体验** | 原生 `wx.chooseMedia`（支持连拍、相册、秒级压缩） | `<input type="file" capture="environment">` 调起手机相机 |
| **域名与备案要求** | **必须有已 ICP 备案域名 + HTTPS** | **零要求**（纯 IP `http://147.139.167.18:8901` 直接可用） |
| **微信审核周期** | 个人/企业主体注册 + 微信审核约 3~7 天 | 零审核，改完代码刷新即用 |
| **后端代码差异** | **完全共用同一套 ECS Python 后端与数据库** | **完全共用同一套 ECS Python 后端与数据库** |

**💡 最佳推进策略**：
先花 1~2 天把 **ECS Python 后端 + 数据库多租户化** 跑通；如果域名已备案，直接对接微信小程序；如果域名正在备案中，可顺手把现有网页加上手机竖屏适配，让试用家长**当周就能在手机微信里点链接拍照录题**，等域名备案下来无缝切入小程序，两端数据完全互通。

---

## 3. 总体架构设计（小程序前端 + ECS Python 云端）

```
┌───────────────── 家长/学生手机端（微信小程序） ─────────────────┐
│                                                               │
│  1. 拍照录题页         2. 错题工作台        3. 专题与在线做题   │
│  ├─ 相机拍照/相册连选   ├─ 章节/知识点筛选   ├─ 10大必考专题练习 │
│  ├─ 手机端前置压缩     ├─ 查看解析/B站视频  ├─ 在线批改计连对   │
│  └─ 触屏框选/勾选入库   └─ 勾选生成PDF试卷   └─ 一键转发PDF打印  │
└───────────────────────────────┬───────────────────────────────┘
                                │ HTTPS (JSON / Multipart)
                                │ 鉴权 Header: Authorization: Bearer <token>
                                ▼
┌───────────── 阿里云 ECS (Caddy/Nginx HTTPS 443) ──────────────┐
│                                                               │
│  Python Flask + Gunicorn 业务服务 (复用 85% 现有代码)          │
│  ├─ 认证网关：激活码 (WB-XXXX) 绑定微信 OpenID → 签发会话 Token │
│  ├─ 异步任务池：后台线程执行「视觉转录」与「质量链验伪」       │
│  ├─ 核心引擎：reviewflow / pipeline / topics / figureflow     │
│  └─ PDF 引擎：engine/export_paper.py 生成试卷供微信内预览转发  │
│                                                               │
│  ┌───────────────────── ECS 存储层 ─────────────────────────┐ │
│  │ 1. 数据库 (SQLite WAL / PostgreSQL)：                    │ │
│  │    存储所有家庭的账号、草稿单、错题、专题例题、练习记录    │ │
│  │ 2. 媒体文件目录 (/var/lib/wrongbook/media/<account_id>/) │ │
│  │    存储转正后的试卷原图 (.jpg) 与题目裁剪图 (.png)        │ │
│  └──────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

---

## 4. ECS 数据库与存储层设计（从本地 JSON 到云端数据库）

试题保存在 ECS 后，所有家庭的数据都在同一台服务器上，必须实现严格的**租户隔离（按激活码/家庭隔离）**与**并发安全**。

### 4.1 数据库选型建议

| 方案 | 适用规模 | 优点 | 改造工作量 |
| :--- | :--- | :--- | :--- |
| **方案一：ECS SQLite（WAL 模式，推荐）** | **1 ~ 500 个家庭**<br>（题量 < 50 万道） | Python 内置 `sqlite3` 零外部服务内存开销（省 ECS 内存）；支持 SQL 索引筛选与事务；单文件每日备份极简；复杂字段（如 `choices`、`figure_spec`、`practice`）可用 JSON 列存储，兼顾关系查询与现有数据结构 | **小（约 1.5 天）** |
| **方案二：PostgreSQL / MySQL** | 500 个家庭以上 | 高并发写入更强 | 中（约 3 天，且占 ECS 300MB+ 内存） |

> **定稿建议**：直接采用 **SQLite（开启 `PRAGMA journal_mode=WAL`）**，将核心业务表合入 ECS 数据库中，既解决多用户并发读写锁问题，又不占用宝贵的 ECS 内存。

### 4.2 ECS 核心数据表设计（SQLite 6 张表）

为了最小化对现有 `pipeline.py`、`stats.py`、`topics.py` 的改动，表结构采用**「主检索字段关系化 + 嵌套详情字段 JSON 化」**的混合设计（读取出来组装成与现在完全一致的 Python `dict`，上层业务代码零改动）：

```sql
-- 1. 家庭账户表（与现有激活码 codes 表联动：一码一户）
CREATE TABLE accounts (
    account_id   TEXT PRIMARY KEY,          -- 直接使用激活码 WB-XXXX-XXXX-XXXX
    owner        TEXT,                      -- 家庭备注（如：张同学家）
    ai_config    TEXT DEFAULT '{}',         -- 该家庭的 AI 引擎配置 JSON（或使用平台统一 Key）
    created_at   INTEGER
);

-- 2. 微信绑定与家庭成员表（支持一家多孩：如“大宝”、“二宝”）
CREATE TABLE users (
    user_id      TEXT PRIMARY KEY,          -- u2026...
    account_id   TEXT NOT NULL,             -- 所属家庭激活码
    name         TEXT NOT NULL,             -- 成员名称（默认“默认用户”）
    created_at   INTEGER
);
CREATE INDEX idx_users_account ON users(account_id);

-- 3. 微信登录会话表（小程序免密自动登录）
CREATE TABLE wx_sessions (
    token        TEXT PRIMARY KEY,          -- 小程序端存储的登录凭证
    wx_openid    TEXT,                      -- 微信用户唯一标识
    account_id   TEXT NOT NULL,             -- 绑定的激活码
    current_uid  TEXT NOT NULL,             -- 当前选中的孩子 user_id
    updated_at   INTEGER
);

-- 4. 排版机草稿单表（替代原 drafts.json）
CREATE TABLE sheets (
    sheet_id     TEXT PRIMARY KEY,          -- s20261110-...
    account_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    mode         TEXT DEFAULT 'book',       -- 'book' (错题本) | 'topic' (专题)
    status       TEXT DEFAULT 'pending',    -- 'transcribing' | 'pending' | 'processed' | 'error'
    source       TEXT,
    photo_path   TEXT,                      -- ECS 相对路径
    data_json    TEXT NOT NULL,             -- 完整 sheet dict (含 questions 数组、figure_crops 等)
    created_at   INTEGER
);
CREATE INDEX idx_sheets_user ON sheets(account_id, user_id, created_at DESC);

-- 5. 错题本主表（替代原 wrongbook.json）
CREATE TABLE questions (
    qid          TEXT PRIMARY KEY,          -- q20261110-...
    account_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    chapter      TEXT,                      -- 索引列：加速工作台按章筛选
    difficulty   INTEGER,                   -- 索引列：1/2/3
    reason_kind  TEXT,                      -- 索引列：careless/unknown/other
    mastered     INTEGER DEFAULT 0,         -- 索引列：0=在库, 1=已归档
    norm_text    TEXT,                      -- 归一化题干前80字：加速查重
    data_json    TEXT NOT NULL,             -- 完整题目 dict (与现有 wrongbook.json 单条结构 100% 相同)
    created_at   INTEGER
);
CREATE INDEX idx_q_filter ON questions(account_id, user_id, mastered, chapter, difficulty);

-- 6. 专题与例题表（替代原 topics.json + topic_practice.json）
CREATE TABLE topic_sets (
    account_id   TEXT PRIMARY KEY,          -- 每户独立一份专题树 JSON（初始从种子克隆）
    sets_json    TEXT NOT NULL,             -- 含 sets -> categories -> examples
    prac_json    TEXT DEFAULT '{}'          -- 按 user_id 记录的例题练习连对数据
);
```

**✨ 这个数据库设计的精妙之处**：
`questions.data_json` 和 `sheets.data_json` 存的内部 JSON 结构与你现在的 `wrongbook.json`、`drafts.json` **完全一模一样**！这意味着你只需重写 `store.py` 里的 `load()` / `save()` / `add()` 等十几个读写函数（总共不到 200 行代码），而复杂的 `pipeline.py`、`reviewflow.py`、`stats.py` **几乎一行都不用改**！

### 4.3 试卷图片在 ECS 上的容量与存储计算

很多开发者担心图片放 ECS 磁盘会爆，我们算一笔精确账：
- 手机拍照上传后，`reviewflow.py` 会将转正后的照片以 JPEG `quality=88`、长边 `1600px~2000px` 存盘，单张试卷照片约 **180KB ~ 250KB**；
- 每道题的几何裁剪图（PNG）约 **20KB ~ 40KB**；
- 一个学生一学期录入 200 张试卷、500 道错题，总图片体积仅约 **60MB**；
- 哪怕你有 **50 个试用/付费家庭**，总图片占用也只有 **3GB**，你现有的 ECS 云盘（通常 40GB 起步）绰绰有余！
- 存储路径规范：`/var/lib/wrongbook/media/<account_id>/photos/` 与 `crops/`，通过带 Token 校验的静态路由下发，确保 A 家庭绝不可能访问到 B 家庭的照片。

---

## 5. 手机端（微信小程序）四大核心环节体验设计

手机屏幕小、竖屏操作、网络延迟波动大，不能简单把桌面端网页硬塞进手机，针对性优化如下：

### 5.1 登录与家庭激活（一次输码，永久免密）
1. 家长首次打开小程序 → 调用 `wx.login()` 获取微信 `openid`；
2. 输入你发放的激活码 `WB-XXXX-XXXX-XXXX` → 后端验证激活码有效后，将该微信 `openid` 与激活码绑定；
3. 之后家长或孩子每次在手机上打开小程序，**零输入自动进首页**；右上角下拉仍可切换「大宝 / 二宝」。
4. **多端互通**：同一个激活码，妈妈在手机小程序拍完照入库，晚上回家在电脑上打开网页版/客户端，题目已经同步在工作台里了！

### 5.2 手机拍照与「异步转录」改造（关键防超时）
- **痛点**：微信小程序 `wx.uploadFile` 默认超时时间是 60 秒，且用户在手机上等待时可能会切回微信聊天回消息。原先 `POST /api/sheets` 是同步等待视觉大模型返回（20~50 秒），在手机网络下极易超时断联。
- **方案**：
  1. **前端先压缩**：调用 `wx.chooseMedia({ mediaType: ['image'], sourceType: ['camera', 'album'] })` 拍照或选图后，先用小程序原生 `wx.compressImage` 将 10MB 原图秒压到 ~300KB，上传只需 **0.5 秒**；
  2. **跨页题开关**：第 2 张及以后的缩略图下方保留 `「⇧ 接上张（跨页题）」` 开关，勾选后由 ECS 后端 `PIL` 自动纵向拼接（免去手机端 Canvas 内存限制）；
  3. **后端异步转录**：`POST /api/sheets` 收到图片后立即存盘、创建一条 `status="transcribing"` 的草稿记录并立刻返回 `{sheet_id, status: "transcribing"}`（耗时 < 0.2s），后台线程调大模型转录；
  4. **前端轮询**：小程序每 1.5 秒轮询 `GET /api/sheets/<sid>`，即使家长中途切出去回微信消息，切回来也能看到转录完成的题目！

### 5.3 手机端「人审题卡」与「触屏手动框选图形」
- **上下流式布局（替代表格双栏）**：
  - 电脑端是「左原图、右题卡」双栏；手机竖屏改为**「顶部可折叠/可双指缩放的原图预览条 + 下方流式题目卡片」**。
- **默认不自动勾选（延续 V1.4.0e 定稿）**：
  - 无论从「录入错题」还是「专题类别 ＋录入」进入，转录完成后题目默认均**不勾选**；家长看中哪道题，点击题卡头部的复选框即可勾选（专题模式下勾选即自动绑定当前锁定的专题类别）；底部悬浮栏实时显示 `已选 N 题 [提交入库]`。
- **手机触屏重新框选图形（`✂️ 手动框选`）**：
  - 点击题卡上的 `✂️ 手动框选原图` → 弹出全屏原图裁剪组件：家长直接用**一根手指在原图上划出矩形框**（监听小程序 `bindtouchstart` / `bindtouchmove` / `bindtouchend` 计算相对宽高比例 `[x0, y0, x1, y1]`）→ 松手点击「确定抓取」→ 调后端 `/recrop` 接口即时换图。

### 5.4 数学公式（LaTeX）渲染与微信内一键打印 PDF
1. **小程序里的 LaTeX 公式怎么渲染？**
   - 微信小程序没有浏览器的完整 DOM，不能直接跑 `auto-render.min.js`。
   - **最佳方案**：使用小程序界成熟的开源组件 **`mp-html`（内置 latex 扩展插件）** 或由后端结合 KaTeX 样式渲染。这能让 `$ \triangle ABC \cong \triangle DEF $`、分式、根号在小程序里与电脑端一样清晰美观。
2. **手机端怎么打印错题卷？（杀手锏体验）**
   - 家长在小程序「工作台」勾选错题 → 点击底部 `🖨 生成打印卷 (PDF)`；
   - ECS 后端调用现有的 `engine/export_paper.py` 生成高清矢量 PDF；
   - 小程序调用 `wx.downloadFile` + **`wx.openDocument({ filePath, showMenu: true })`** 直接在微信内打开 PDF 预览；
   - 家长点右上角 `···` 即可**直接转发给微信好友/文件传输助手，或直接调用微信绑定的家庭打印机（惠普/爱普生/兄弟等微信小程序打印）一键打印**！这比在电脑上导出来再打印还要方便十倍。

---

## 6. AI 大模型 Key 管理策略（云端版的新优势）

迁移到 ECS 云端后，关于 DeepSeek / 智谱 GLM 的 API Key，你可以同时支持两种模式：

| 模式 | 运作方式 | 商业化优势 |
| :--- | :--- | :--- |
| **模式 A：平台统一配 Key（推荐对试用/付费家长默认开启）** | 你在 ECS 的 `key.env` 配置好你的 GLM/DeepSeek Key。家长激活后**无需去注册智谱/充钱/配 Key，打开小程序直接就能拍照转录**！你可以在 `codes` 表加一个 `quota_Left` 字段（如每个试用码送 50 次拍照，年费码送 2000 次） | **彻底消灭 R4 最大痛点「家长不会申请 API Key」**，转化率提升 10 倍；且单次转录成本仅几分钱，完全可控 |
| **模式 B：家长自配 Key（保留设置入口）** | 小程序「设置」页仍保留 API Key 输入框，若该家庭配置了自己的 Key，优先用家长的 Key（不扣平台额度） | 给高频重度用户或懂技术的家长留足自由度 |

---

## 7. 项目目录结构与代码复用全景图

我们不需要新建一个割裂的项目，而是在现有仓库上平滑演进，让**云端服务**与**单机版**共享同一套核心引擎：

```
wrongbook/
├── engine/                 # [100% 复用] PDF 导出、几何检验、向量化引擎
├── data/kb/                # [100% 复用] 浙教版教材知识树种子 JSON
├── reviewflow.py           # [98% 复用] 方向扶正、多题转录、自动裁图（仅图片保存路径改为传参）
├── pipeline.py             # [98% 复用] 查重、验伪、知识点归类、入库流
├── topics.py               # [95% 复用] 10 大必考专题与例题逻辑
├── stats.py                # [100% 复用] 工作台筛选与统计计算
├── figureflow.py           # [100% 复用] 几何 SVG 渲染
│
├── cloud_store.py          # [新增 ~220行] ECS SQLite 多租户存储层（接口与 store.py 完全同名同签名！）
├── cloud_app.py            # [新增 ~350行] ECS 云端 Flask 服务（合并激活验证 + 业务 API + 异步转录队列）
│
├── static/index.html       # [保留 + 响应式微调] 电脑浏览器直接访问 ECS 也能登录看题/打卷！
└── miniprogram/            # [新增] 微信小程序前端工程（微信开发者工具直接打开）
    ├── app.js / app.json / app.wxss
    ├── pages/
    │   ├── login/          # 激活码绑定与家庭成员切换
    │   ├── ingest/         # 拍照/选图 → 异步转录 → 人审勾选 + 触屏框选
    │   ├── workbench/      # 错题筛选、详情弹窗、生成 PDF 预览转发
    │   ├── topics/         # 专题集 → 10 类必考 → 例题详情与视频讲解
    │   └── quiz/           # 手机在线做题与批改
    └── components/
        ├── latex-text/     # 数学公式渲染组件
        └── cropper-modal/  # 手机触屏手指框选原图组件
```

---

## 8. 分阶段落地计划（预计总工期：7 ~ 10 天）

| 阶段 | 核心任务 | 产出与验收标准 | 预计耗时 |
| :--- | :--- | :--- | :--- |
| **阶段 1：ECS 后端多租户与异步化** | 1. 编写 `cloud_store.py`（SQLite 多租户隔离开箱即用）；<br>2. 将 `POST /api/sheets` 改为异步任务队列；<br>3. 增加激活码 Token 鉴权中间件；<br>4. 支持服务端默认 AI Key + 按激活码配额统计 | 用 `pytest` 跑通多家庭数据隔离测试；电脑浏览器连 ECS IP 即可多账号登录使用全部功能 | **2 天** |
| **阶段 2：手机 H5 竖屏快速适配（可选过渡）** | 为 `static/index.html` 增加 `@media (max-width: 768px)` 竖屏布局与手机相机拍照按钮 + Touch 触屏框选支持 | **无需等微信审核与域名备案**，试用家长在手机微信里点开链接就能直接拍照录题 | **1 ~ 1.5 天** |
| **阶段 3：微信小程序前端开发** | 搭建 `miniprogram/` 原生小程序工程，实现「登录激活、拍照人审、错题工作台、专题练习、PDF 导出分享」5 个页面 | 微信开发者工具真机预览跑通全流程：手机拍照 → 转录 → 勾选入库 → 导出 PDF 转发微信 | **3 ~ 4 天** |
| **阶段 4：HTTPS 部署与体验版发布** | 1. ECS 配置域名解析 + Caddy 自动申请免费 HTTPS 证书；<br>2. 上传小程序代码，将试用家长加入「体验成员」名单直接扫码使用，同步提交微信审核 | 试用家长在手机微信小程序中正式使用，手机与电脑网页数据实时同步 | **1 ~ 2 天** |

---

## 9. 启动前请您拍板的 4 个问题

为了帮您以最快速度落地手机版，请确认以下 4 个问题：

1. **域名与小程序账号现状**：
   - 您手头目前是否已经有**已完成国内 ICP 备案的域名**以及**微信小程序账号（AppID）**？
   - *（如果还没有备案域名，建议我们先花 1~2 天把「阶段 1 + 阶段 2（手机自适应网页版）」部署到您的 ECS 上，试用家长明天就能在手机微信里点链接直接拍照用；同时您去提交域名备案，备案一过立刻上小程序。）*
2. **ECS 服务器配置确认**：
   - 您当前的阿里云 ECS（`147.139.167.18`）是几核几 G 内存、硬盘多大、地域在国内还是海外？
3. **AI Key 供给方式**：
   - 手机版是否采用**「ECS 服务端内置您的统一 GLM/DeepSeek Key + 每个激活码限制调用次数上限」**（让试用家长免填 Key 开箱即用）？
4. **视频讲解在小程序中的呈现**：
   - 微信小程序个人主体对内嵌外链 `<iframe>`（如 B 站播放器）有限制，在小程序里对于附了 B 站链接的题，采用「一键复制 B 站链接 / 跳转 B 站小程序播放」是否可行？
