# 计划 B 开发准备 · ECS 云端 + 手机 H5 网页版

> 版本：**P1（已被 `Doc/plan_b_dev_question.md` P1.2 取代，保留作评审底稿）** ｜ 日期：2026-11-10
> ⚠️ **最新定稿见 `Doc/plan_b_dev_question.md`**（P1.2：ContextVar 防串户、Cookie 双轨鉴权、
> 印尼服务器 flysic.cn 直接上 HTTPS、K-9 现状勘误、/out/ 越权补充）
> 关联文档：`Doc/miniprogram_plan.md`（方案总纲）、`Doc/development-B.md`（实战手册）、`DESIGN.md`（单机版设计）
> **拍板状态**：6 项决策已确认（见 §9）——AI Key 走模式 B、视频全线删除、
> 域名备案中（HTTPS 后置）、ECS 2G/40G 国内、账户同步按 D-B7 执行；架构取向按推荐默认执行
> 路线 B 定义：**手机自适应 H5 网页版**（微信内点链接即用，零备案零审核），
> 前置完成 **ECS 后端多租户化 + 异步化**；小程序（路线 A）待域名备案后无缝切换。

---

## 1. 目标与范围

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| **B1** | ECS 后端多租户与异步化 | 双家庭数据隔离测试全绿；电脑浏览器连 ECS 多账号可用全部功能 | 2d |
| **B2** | 手机 H5 竖屏适配 | 手机微信打开网址：拍照 → 转录 → 人审框选 → 入库 → 导出 PDF 全流程走通 | 1~1.5d |
| **B3** | 部署与运维 | gunicorn + systemd + 每日备份；（备案后）Caddy HTTPS | 0.5~1d |

**不做**（本阶段）：微信小程序原生工程、wx.login/openid、ICP 域名依赖、PostgreSQL。

---

## 2. 现状盘点：可复用资产与缺口（代码级映射）

### 2.1 直接复用（改动极小）

| 资产 | 现状 | 云端复用方式 |
|---|---|---|
| `reviewflow.py` (708行) | 转录/扶正/抓图/框选重裁，全部以 bytes/相对路径为输入输出 | 原样复用；图片落盘路径改由租户目录解析 |
| `pipeline.py` (520行) | 查重/验伪/受控知识点/入库；`ingest_selected(sheet, provider, progress)` 已带进度回调 | 原样复用（v1.2s 异步提交已验证） |
| `stats.py` / `figureflow.py` / `kbtree.py` / `engine/` | 纯函数，走 store 接口 | 原样复用 |
| `paths.py` | `WRONGBOOK_DATA` 环境变量已支持数据外置 | ECS 设 `WRONGBOOK_DATA=/var/lib/wrongbook/data` 即可 |
| `ai_config.py` | 注入式配置（D20）：provider/keys/models，key 掩码回传 | 保留为「平台级默认」；租户级配置见 §3 D-B5 |
| `ecs/license_server.py` | codes/activations/audit 表 + Ed25519 + 限流 | **激活码的签发源头**，Plan B 鉴权以它为账本 |
| v1.2s 异步提交 | `_commit_jobs` 进程内任务表 + `GET /api/commit-status/<sid>` 轮询 | **转录异步化直接复制此模式**（§3 D-B3） |

### 2.2 核心缺口（计划 B 的全部工作量）

| # | 缺口 | 现状证据 | 对应任务 |
|---|---|---|---|
| G1 | **多租户数据层** | `store.py` 是「模块级全局当前用户」（`current_uid()` + `_rebind_paths()`），多用户并发请求会串数据 | B1-T1 `cloud_store.py` |
| G2 | **topics.py 同样走全局态** | `topics.py:48-56` 直接读 `store._data_dir()` / `store.user_dir()` | B1-T2 topics 存储改造 |
| G3 | **鉴权门禁** | 除 license 相关端点外全部 API 无认证（桌面本机模型） | B1-T3 `@auth_required` + sessions 表 |
| G4 | **媒体路由无租户隔离** | `app.py:641` `/data/<path:p>` 直接 `send_from_directory(DATA_HOME, p)`，A 家庭可读 B 家庭照片 | B1-T4 带校验的媒体路由 |
| G5 | **转录同步长请求** | `app.py:159 api_sheet_create` 内联调 `transcribe_photo`（20s~3min），前端 `fetchT 300s`（index.html:454）——手机网络必超时 | B1-T5 异步转录 job + 轮询 |
| G6 | **AI Key 全局单份** | `ai_config.py` 读全局 `data/ai_config.json`，无按租户解析钩子 | B1-T6 租户级 key 上下文 |
| G7 | **无移动端布局** | index.html 仅 `@media print`；无 `capture=` 拍照入口；手动框选仅 mouse 事件 | B2 全部任务 |
| G8 | **云端部署物** | 无 gunicorn/systemd/备份（`ecs/` 只有 license 服务） | B3 |

---

## 3. 关键架构决策（建议方案，开工前拍板）

### D-B1 单应用双形态，不另起 cloud_app.py ★

`miniprogram_plan.md`/`development-B.md` 建议新增独立 `cloud_app.py`（约 350 行）。但 app.py 有
~50 个端点，独立文件必然复制路由 → 双份漂移。**建议改为单 Flask 应用 + 形态开关**：

```
WRONGBOOK_CLOUD=off（默认）→ 桌面单机形态：行为与 V1.4.1 完全一致，零回归风险
WRONGBOOK_CLOUD=on        → 云端形态：请求级租户解析 + @auth_required 门禁
                            + SQLite 存储 + 媒体校验路由；禁用 /api/shutdown 等本机端点
```

- 存储层按环境变量选择后端（`WRONGBOOK_STORE=json|sqlite`，云端强制 sqlite）
- 业务路由（录题/工作台/专题/做题/PDF）两个形态共用同一份代码
- 桌面打包链（packaging/）不受影响：云形态代码只在 ECS 进程里生效

### D-B2 租户上下文：store 全局态 → 请求级（g.code, g.uid）★最大改造点

现 `store.current_uid()` 全局唯一。云端并发请求必须按 `Authorization: Bearer <token>` 解析
`(code, uid)` 并放进 Flask `g`。改法（最小侵入）：

- `store.current_uid()` / `user_dir()` 内部改为「先读 `flask.has_request_context()` 的 g，
  无 g 时回落桌面全局态」→ 桌面形态行为不变
- `topics.py` 的 `_topics_path` / `_practice_path` 同样走该钩子（不再直接读 `_data_dir()`）
- 全部业务 API 前置 `@auth_required`（云端）填充 g；测试用 fake 上下文注入

### D-B3 转录异步化（复刻 v1.2s 模式）

`POST /api/sheets`：存盘照片 + 建 `transcribing` 占位 sheet → 立即返回 `{sheet_id, status}`
→ 后台线程跑 `transcribe_photo` → 前端 1.5s 轮询 `GET /api/sheets/<sid>`。
桌面 H5 与未来小程序同一协议；手机切后台回微信再回来不丢结果。

### D-B4 媒体路由租户隔离

`/data/<path:p>` 云端形态改为：路径必须落在 `g` 租户目录内（规范化后前缀校验，防 `../`），
否则 404。桌面形态保持直读。

### D-B5 AI Key 策略：先 B 后 A 两步走

- **B1 先实现模式 B（家长自配 Key）**：`accounts.ai_config` 列存该家庭配置；
  `ai_config` 读取钩子加请求级覆盖（g 有租户 → 读该户配置，无 → 平台全局）。
  复用现有 `/api/ai-config` GET/POST 掩码契约，零前端新开发。
- **模式 A（平台统一 Key + 配额）** 留接口（accounts 加 `quota_left` 列），
  待试用家长反馈后再决定是否开启——试用家庭少时自配 Key 成本可控。

### D-B6 视频讲解：**全线删除**（已拍板，含桌面版）

做法对齐 V1.3.5 GeoGebra 移除先例：删功能与入口，**历史数据残留字段忽略不清洗**。
涉及面（grep 盘点）：`static/index.html`（15 处：人审卡/例题卡输入面板、`videoBlockHtml()` 渲染、
详情弹窗折叠块）、`app.py`（PATCH 白名单 `video` 字段）、`pipeline.py`（随题存档）、
`topics.py`（11 处：video_draft_ref/例题字段/删除清理链）、`store.py`（10 处：引用检查）、
`tests/test_topics.py`（21 处相关断言改写）。`figure_crop` 的交叉引用保护（v1.2q）**保留不动**。

### D-B7 账户表与激活码联动（已拍板：license 库为账本 + 单向同步）

**职责划分**：license 库（`license.db`，8901 服务 + `license_admin.py` 管理的）是**签发账本**——
只有管理员 CLI 写它；业务库（`wrongbook.db`）的 `accounts` 表是**业务账本**（另存 ai_config
等业务字段）。业务服务对 license.db **只读**，写回永远经由 license_admin CLI——两个 systemd
服务不共库、不互写，避免 SQLite 跨进程写锁竞争与升级耦合。

**同步机制**（新增 `wrongbook_admin.py sync-accounts` CLI，幂等可反复跑）：

```
① 发码/续费/吊销（现有，不变）
   ECS: python license_admin.py issue/revoke/enable/extend --code WB-…
        → 写 license.db codes(code, owner, max_devices, issued_at, expires_at, revoked)
             │
             │  ② 单向同步（新 CLI，只读打开 license.db → upsert wrongbook.db）
             ▼
   sync-accounts 规则：
   · codes.revoked=0 → accounts upsert {code, owner, expires_at, is_active=1}（新增码插入，
     到期/备注变化更新）；**不覆盖 accounts.ai_config**（家长自配 Key 是业务侧数据）
   · codes.revoked=1 或码已从 license 库删除 → accounts.is_active=0（不删行，留审计痕迹）
             │
             │  ③ 登录与鉴权
             ▼
   POST /api/auth/activate {code}
   → 查 wrongbook.db accounts：存在 + is_active=1 + 未过期 → 签发 token 写 sessions
   → 每次业务请求鉴权时同查 accounts（一次索引查询）→ 吊销/过期即时断会话，
     不需要等 token 自然过期
```

**同步触发时机**（三道，按需启用）：
1. 手动：管理员发/停码后跑一次 `sync-accounts`（发码即同步，最常用）
2. 定时：crontab 每 30 分钟自动同步（兜底，防止忘跑；吊销最迟 30 分钟生效于新登录）
3. 实时（可选加固档）：登录时向 license 服务 `127.0.0.1:8901` 查一次码状态
   （license_server 新增轻量只读端点 `/code-status`）；license 超时/不可达 → **降级用
   accounts 表放行**（可用性优先），audit 留痕。试用家庭规模小，先上 1+2，实时档视需要再加

**sessions 与 license activations 的关系**：两回事——activations 记桌面版设备激活
（Ed25519 体系，V1.3.7/8），sessions 是 Web/H5 会话（Bearer token）；同一个码可以
桌面已激活 + 手机网页同时用，互不影响，`max_devices` 语义只约束桌面激活。

**CLI 其余子命令**（仿 license_admin 风格）：`list-sessions`（在线会话/踢下线）、
`set-owner`（改户备注）、`quota`（预留：模式 A 配额管理，本期不实现）。

---

## 4. 数据层设计（cloud_store.py 接口映射）

SQLite WAL，路径 `<DATA_HOME>/wrongbook.db`。表结构按 `development-B.md` §2.1 DDL（7 张表），
微调两点：

1. `questions.norm_text` 复用 `store.norm_text()` 现成归一化（前 80 字），
   查重阈值逻辑保持 difflib ≥92%（`find_duplicate` 云端版改为 SQL 预筛同户同章 + 内存 difflib 复核，
   单户题量 <2万 无性能问题）
2. 专题拆 `topic_sets`（每户一份树 JSON，首跑从 `data/topics.json` 种子克隆）+
   `topic_practice`（每户每例题 streak/ready/history）——对齐 `topics.py` 现有读写粒度

**接口映射**（与 store.py 同名同签名，上层零改动）：

| store.py 现函数 | cloud_store 行为 |
|---|---|
| `load/save/add/get/update/remove` | questions 表 + data_json 整存整取 |
| `load_sheets/save_sheets/add_sheet/get_sheet/patch_sheet_question/set_sheet_status` | sheets 表 |
| `find_duplicate/norm_text` | SQL 前筛 + difflib 复核 |
| `add_practice/streak` | data_json 内 practice 追加（保持现结构） |
| `list_users/add_user/switch_user` | users 表（云端无「当前用户」全局态，`switch_user` 仅桌面形态有效） |
| `crop_draft_ref/video_draft_ref/remove` 引用检查 | 云端同逻辑（读 data_json 判断），删除链语义不变 |
| topics 读写 | topic_sets / topic_practice 两表 |

---

## 5. API 契约要点（新增/调整）

| 方法 | 路径 | 变化 |
|---|---|---|
| POST | `/api/auth/activate` | **新增**：`{code, device_name}` → `{token, owner, users[]}`；写 sessions |
| POST | `/api/auth/heartbeat` | **新增**：token 续活（last_seen 刷新）；前端 24h 或每次冷启动调用 |
| GET | `/api/users` / POST `/api/users` | 云端：列出/新建**本户**成员（大宝/二宝）；切换通过重新登录选择或 PATCH sessions.current_uid |
| POST | `/api/sheets` | 改**异步**：立即返回 `{sheet_id, status:"transcribing"}`（D-B3） |
| GET | `/api/sheets/<sid>` | 轮询终点：status=transcribing → pending（含 questions） |
| GET | `/data/<path>` | 云端：租户校验后下发（D-B4）；桌面：不变 |
| GET/POST | `/api/ai-config` | 云端：读写当前租户 accounts.ai_config（掩码契约不变） |
| — | `/api/shutdown` | 云端禁用（404/403） |
| — | license 桌面端点 | 云端形态下 `/api/license` 返回 `{state:"cloud"}` 或直接放行 |

---

## 6. 分阶段任务清单

### B0 前置：视频讲解全线删除（约 0.5 天，桌面版同步生效，已拍板 D-B6）

| # | 任务 | 要点 |
|---|---|---|
| T1 | 前端移除 | index.html：人审卡/例题卡视频输入面板、`videoBlockHtml()` 渲染、详情弹窗「🎥 视频讲解」折叠块全部删除 |
| T2 | 后端移除 | app.py PATCH 白名单去 `video`；pipeline 存档链去 video；topics/store 的 video 引用检查与删除清理链删除（**crop 交叉引用保护 v1.2q 保留不动**） |
| T3 | 历史数据 | 残留 video 字段忽略不清洗（对齐 V1.3.5 GeoGebra 先例）；data/videos/ 目录残留文件手动处理 |
| T4 | 回归 | test_topics.py 视频相关断言改写；全量 pytest 全绿；桌面手工冒烟录入/专题/删除链 |

### B1 后端多租户与异步化（约 2 天）

| # | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T1 | `cloud_store.py`：DDL + 接口映射 + 种子（专题树/KB） | 新文件；`store.py` 加后端选择 | 双租户 pytest：A 写题 B 不可见 |
| T2 | topics 存储改造（脱钩 `store._data_dir` 直读） | `topics.py` | 专题测试云端后端下全绿 |
| T3 | 租户上下文 + `@auth_required` + auth 端点 | `app.py`、新 `auth.py` | 无 token 全 401；错码 401；A 的 sid B 访问 404 |
| T4 | 媒体路由校验 | `app.py:641` | 越权路径 404；`../` 穿透 404 |
| T5 | 转录异步 job + 轮询（复刻 v1.2s） | `app.py`、`reviewflow` 调用侧 | 上传 <1s 返回；轮询拿到完整草稿；重复提交 409 |
| T6 | 租户级 ai_config 钩子（**已拍板模式 B**：家长自配 Key，无平台统一 Key/配额） | `ai_config.py` | 两户各配 key 互不影响；未配时报错文案指引家长去设置页填写 |
| T7 | 桌面形态回归：`pytest tests/`（含 B0 改写后）全绿 + 手工冒烟 | — | 零行为变化（除视频功能移除） |
| T8 | `wrongbook_admin.py`：sync-accounts / list-sessions（D-B7） | 新文件 | 同步幂等；吊销码登录 401 |

### B2 手机 H5 适配（约 1~1.5 天，`static/index.html` 单文件）

| # | 任务 | 要点 |
|---|---|---|
| T1 | 视口与布局 | viewport 收紧（`maximum-scale=1, viewport-fit=cover`）；`@media (max-width:768px)`：人审双栏→单列、原图条可折叠、commitbar 吸底 + `safe-area-inset-bottom` |
| T2 | 拍照入口 | `<input type="file" accept="image/*" capture="environment">` 专用按钮（桌面隐藏）；上传前 `canvas` 压缩到长边 ~1600（对齐后端 preprocess） |
| T3 | 触屏框选 | 框选事件加 touch 分支（`touchmove` `preventDefault` 防滚动；坐标归一化复用现 box 逻辑，dev-B §5.3 参考实现） |
| T4 | 转录轮询 UI | 上传后立即出占位卡 + 进度态；轮询 `GET /api/sheets/<sid>` 直到 pending；断网重连续轮 |
| T5 | 移动端细节 | 登录/激活视图（输码 → 存 token 到 localStorage）；底部操作按钮触控目标 ≥44px；题目卡片 KaTeX 横向溢出处理 |

### B3 部署与运维（约 0.5~1 天）

| # | 任务 | 要点 |
|---|---|---|
| T1 | gunicorn + systemd | ECS **2G 内存/40G 盘/国内**：`1 worker + 8 threads + timeout 300`（dev-B §7.1）；`WRONGBOOK_CLOUD=on WRONGBOOK_DATA=/var/lib/wrongbook/data`；40G 盘按 §4.3 容量估算（50 户 ≈3GB）充足 |
| T2 | 备份 | sqlite3 `.backup` 每日 3 点 + media 目录 rsync，保留 30 天 |
| T3 | 管理员 CLI | `sync-accounts`（D-B7）+ crontab 30 分钟兜底同步 |
| T4 | HTTPS（**后置：域名已购、备案中**） | 本期按 `http://IP:8899` 试用；提前备好 Caddyfile 与切换步骤，备案通过后 30 分钟内切 HTTPS + 改域名访问；备案下来前小程序（路线 A）无法开工 |

---

## 7. 测试与回归策略

- **桌面零回归是硬门禁**：全部现有 226 项测试不改动语义；`WRONGBOOK_CLOUD` 默认 off，
  现有测试路径不受影响
- 新增 `tests/test_cloud_store.py`（租户隔离/并发写/查重/引用检查）、
  `tests/test_cloud_auth.py`（激活/会话/门禁/越权）、`tests/test_cloud_sheets.py`（异步转录 job）
- 并发测试：threading 两客户端交叉写两租户（WAL 下无锁冲突）
- 真机清单：沿用 `development-B.md` §8 Checklist（隔离/拍照/框选/导出 PDF/不自动勾选），逐项打勾

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| gunicorn 多 worker 下进程内 job 表失效（转录/提交 job 丢失） | **worker=1**（8 线程足够；LLM 调用是 IO 等待）；文档写死，升级多 worker 前须先改 Redis/DB 任务表 |
| store 全局态改造遗漏调用点（串户事故） | grep 全部 `store.current_uid|user_dir|_data_dir` 调用点逐一过钩子；隔离测试覆盖所有读端点 |
| 手机上传大图（10MB 原图直传） | 前端 canvas 压缩先行；后端 preprocess 已压 1600，双保险 |
| 微信内置浏览器兼容性（iOS/安卓差异） | 真机清单逐项过；避免新语法，index.html 保持原生 JS |
| HTTP 明文传输激活码与 token | 备案前试用家庭知悉仅试用数据；备案后立即切 HTTPS |
| V1.2s job 表与 SQLite 并发写 | 提交 job 仍单户串行（现语义）；SQLite WAL 写锁以 retry 处理 |

## 9. 拍板决策记录（2026-11-10 已确认）

| # | 问题 | 决定 | 影响 |
|---|---|---|---|
| 1 | 域名/备案/AppID | 域名已购、**备案中**；小程序 AppID 未注册 | B3-T4 HTTPS 后置（预配置不启用）；路线 A 小程序延后到备案+AppID 就绪 |
| 2 | ECS 规格 | 国内，**2G 内存 / 40G 硬盘** | gunicorn 1w+8t 固化；磁盘容量充足（50 户约 3GB） |
| 3 | AI Key | **模式 B：家长自配** | B1-T6 按此实现；模式 A 配额仅留列结构不实现 |
| 4 | 视频讲解 | **全线删除（含桌面版）** | 新增 B0 前置任务；D-B6 定稿 |
| 5 | 账户同步 | 认可 license 库为账本 + 单向同步 | D-B7 已详述：sync-accounts CLI + crontab 30 分钟 + 每请求查 accounts |
| 6 | 架构取向 | （未明确反对）**按推荐默认执行：单应用双形态**（D-B1） | `WRONGBOOK_CLOUD` 默认 off 保证桌面零回归；如开工后有异议，B1-T1/T3 改造点可平移到独立 cloud_app.py，无沉没成本 |
