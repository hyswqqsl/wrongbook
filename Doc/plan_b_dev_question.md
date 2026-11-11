# 计划 B 开发准备 · ECS 云端 + 手机 H5 网页版

> 版本：**P1.2（评审修正定稿：防坑加固 + 现状勘误）** ｜ 日期：2026-11-11
> 关联文档：`Doc/miniprogram_plan.md`（方案总纲）、`Doc/development-B.md`（实战手册）、`DESIGN.md`（单机版设计）
> **拍板状态**：7 项决策已确认（见 §9）——AI Key 走模式 B、视频全线删除、
> 印尼轻量服务器 2G/40G + `flysic.cn` 免备案直接上 Caddy HTTPS、账户同步按 D-B7 执行、
> K-9 全学段沿用 V1.4.1 存量能力（18 册已在库，云端做种子核验）、架构取向按单应用双形态（D-B1）执行。
> 路线 B 定义：**手机自适应 H5 网页版**（微信内点 `https://flysic.cn` 即用，免备案零审核），
> 前置完成 **ECS 后端多租户化 + 异步化 + K-9 多学段支持**；小程序（路线 A）待未来国内备案后无缝切换。

---

## 1. 目标与范围

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| **B0** | 视频讲解全线移除 | 前后端及测试彻底清理 B 站视频逻辑，桌面与云端同步瘦身 | 0.5d |
| **B1** | ECS 后端多租户、异步化与 K-9 知识点 | 双家庭数据隔离测试全绿；后台线程 ContextVar 不串户；18 册知识点按年级动态加载 | 2d |
| **B2** | 手机 H5 竖屏适配 | 手机微信打开 `https://flysic.cn`：选年级 → 拍照 → 转录 → 触屏框选 → 入库 → 导出 PDF 全流程走通 | 1~1.5d |
| **B3** | 部署、Caddy HTTPS 与运维 | gunicorn + systemd + Caddy 自动 HTTPS（`flysic.cn`）+ 每日备份 + 账本同步定时任务 | 0.5~1d |

**不做**（本阶段）：微信小程序原生工程、wx.login/openid、MySQL/PostgreSQL、OSS（已留存储抽象接口供后期平滑升级）。

---

## 2. 现状盘点：可复用资产与缺口（代码级映射）

### 2.1 直接复用（改动极小）

| 资产 | 现状 | 云端复用方式 |
|---|---|---|
| `reviewflow.py` (708行) | 转录/扶正/抓图/框选重裁，全部以 bytes/相对路径为输入输出 | 复用核心流程；图片落盘路径改由租户目录解析；分类 Prompt 改为按所选年级动态注入对应册别知识点 |
| `pipeline.py` (520行) | 查重/验伪/受控知识点/入库；`ingest_selected(sheet, provider, progress)` 已带进度回调 | 原样复用（v1.2s 异步提交已验证，子线程入口补齐 `bind_tenant` 上下文绑定） |
| `stats.py` / `figureflow.py` / `kbtree.py` / `engine/` | 纯函数，走 store 接口 | 原样复用；`kbtree.py` 支持按年级读取 `data/kb/kp_<年级>.json` |
| `paths.py` | `WRONGBOOK_DATA` 环境变量已支持数据外置 | ECS 设 `WRONGBOOK_DATA=/var/lib/wrongbook/data` 即可 |
| `ai_config.py` | 注入式配置（D20）：provider/keys/models，key 掩码回传 | 保留为「平台级默认」；租户级配置见 §3 D-B5 |
| `ecs/license_server.py` | codes/activations/audit 表 + Ed25519 + 限流 | **激活码的签发源头**，Plan B 鉴权以它为账本 |
| v1.2s 异步提交 | `_commit_jobs` 进程内任务表 + `GET /api/commit-status/<sid>` 轮询 | **转录异步化直接复制此模式**（§3 D-B3），并统一加入 2 小时 TTL 自动清理 |

### 2.2 核心缺口（计划 B 的全部工作量）

| # | 缺口 | 现状证据 | 对应任务 |
|---|---|---|---|
| G1 | **多租户数据层** | `store.py` 是「模块级全局当前用户」（`current_uid()` + `_rebind_paths()`），多用户并发请求会串数据 | B1-T1 `cloud_store.py` |
| G2 | **topics.py 同样走全局态** | `topics.py:48-56` 直接读 `store._data_dir()` / `store.user_dir()` | B1-T2 topics 存储改造 |
| G3 | **鉴权门禁与后台线程上下文** | 无认证；且后台异步线程脱离 Flask 请求周期后 `flask.g` 会丢失 | B1-T3 `tenant_ctx.py` + `@auth_required` + Cookie/Header 双轨鉴权 |
| G4 | **媒体路由无租户隔离 & `<img>` 鉴权** | `app.py:641` `/data/<path:p>` 裸读目录；且原生 `<img src>` 无法带 Bearer 头 | B1-T4 租户目录前缀校验 + `HttpOnly Cookie` 自动鉴权 |
| G5 | **转录同步长请求** | `app.py:159 api_sheet_create` 内联调 `transcribe_photo`（20s~3min），手机切后台必断连超时 | B1-T5 异步转录 job + 轮询 + TTL 回收 |
| G6 | **AI Key 全局单份** | `ai_config.py` 读全局 `data/ai_config.json`，无按租户解析钩子 | B1-T6 租户级 key 上下文 |
| G7 | **无移动端布局与触屏事件** | index.html 仅 `@media print`；无 `capture=` 拍照入口；手动框选仅 mouse 事件 | B2 全部任务 |
| G8 | **云端部署物与 HTTPS** | 无 gunicorn/systemd/Caddy/备份（`ecs/` 只有 license 服务） | B3 |
| G9 | **K-9 云端种子与验证**（勘误：V1.4.1/1.4.1a 已落地 18 册全集、`/api/grades`、录题必选年级下拉、按年级受控归类——详见表后 D-B8；「写死八年级上册」仅为无 grade 旧调用的回落分支，云端必选年级后不可达） | `data/kb/` 已含 `kp_1_1.json~kp_9_2.json` 18 册派生文件；剩余缺口仅 ECS 种子核验 + 真机验证 | B1-T9（收缩）/ B2-T6（收缩） |
| G10 | **`/out/` 导出下载同样无鉴权直读**（评审补充） | `app.py` `/out/<path:p>` `send_from_directory(DATA_HOME/out, p)`，与 `/data/` 同级越权风险 | B1-T4 一并处理（租户目录 + realpath 校验） |

---

## 3. 关键架构决策（P1.1 加固定稿）

### D-B1 单应用双形态，不另起 cloud_app.py ★

`miniprogram_plan.md`/`development-B.md` 原建议新增独立 `cloud_app.py`。但 `app.py` 有约 50 个端点，独立文件必然复制路由导致双份漂移。**定稿采用单 Flask 应用 + 形态开关**：

```
WRONGBOOK_CLOUD=off（默认）→ 桌面单机形态：行为与 V1.4.1 完全一致，零回归风险
WRONGBOOK_CLOUD=on        → 云端形态：请求级/线程级租户解析 + @auth_required 门禁
                            + SQLite 存储 + 媒体校验路由；禁用 /api/shutdown 等本机端点
```

- 存储层按环境变量选择后端（`WRONGBOOK_STORE=json|sqlite`，云端强制 sqlite）
- 业务路由（录题/工作台/专题/做题/PDF）两个形态共用同一份代码
- 桌面打包链（`packaging/`）不受影响：云形态代码只在 ECS 进程里生效

### D-B2 租户上下文：`contextvars.ContextVar` 跨线程安全绑定 + 请求头指定成员 ★核心防坑

**隐患规避**：不能仅靠 `flask.g` 保存 `(code, uid)`，因为 **D-B3 异步转录线程** 和 **v1.2s 异步入库线程**（`threading.Thread`）脱离了 Flask HTTP 请求上下文，`flask.has_request_context()` 在子线程中为 `False`，会导致后台任务回落到桌面单机全局态而串户或丢失 AI Key。

**定稿方案（新建 `tenant_ctx.py`）**：
```python
from contextvars import ContextVar
from contextlib import contextmanager

_current_tenant: ContextVar[tuple[str, str] | None] = ContextVar("wb_tenant", default=None)

@contextmanager
def bind_tenant(code: str, uid: str):
    token = _current_tenant.set((code, uid))
    try:
        yield
    finally:
        _current_tenant.reset(token)

def get_tenant() -> tuple[str, str] | None:
    return _current_tenant.get()
```

1. **HTTP 请求入口**：`@auth_required` 校验通过后，在请求周期内进入 `with bind_tenant(code, uid):`。
2. **后台异步线程入口**：启动 `Thread` 时显式把当前 `(code, uid)` 作为参数传入，在子线程函数最外层包裹 `with bind_tenant(code, uid):`，确保 `store.py`、`topics.py`、`ai_config.py` 在主线程和子线程中完全一致地读取 `get_tenant()`。
3. **多成员（大宝/二宝）防并发冲突**：
   - 前端 `fetchT` 请求除携带 `Authorization: Bearer <token>` 外，额外携带 `X-Wrongbook-Uid: <uid>` 请求头（切换成员时同步写入 Cookie `wb_uid=<uid>`）。
   - 后端优先读取 `X-Wrongbook-Uid`（或 Cookie `wb_uid`）并校验该 `uid` 属于当前 `code` 家庭；未传时才 fallback 到 `sessions.current_uid`，彻底避免手机/电脑或多标签页同时操作大宝、二宝时互相覆盖。
4. **桌面形态回落**（评审补充）：`get_tenant()` 返回 `None` 时，`store/topics/ai_config` 走原有全局态分支——`WRONGBOOK_CLOUD=off` 下行为与 V1.4.1 完全一致，保证桌面零回归。

### D-B3 转录异步化 + 内存任务表 TTL 自动清理

- `POST /api/sheets`：存盘照片 + 建 `transcribing` 占位 sheet（写入所选年级 `grade`）→ 立即返回 `{sheet_id, status: "transcribing"}` → 后台线程包裹 `with bind_tenant(code, uid):` 跑 `transcribe_photo(photo_bytes, source, grade=grade)`（实参名以现有签名为准） → 前端 1.5s 轮询 `GET /api/sheets/<sid>`。
- **内存防泄漏加固**：`_transcribe_jobs` 与 `_commit_jobs` 均为进程内字典。在每次创建新任务时，顺带清理 `created_at < time.time() - 7200`（超过 2 小时）的已完成/失败任务记录，防止 2G 内存服务器长期运行后内存缓慢增长。

### D-B4 媒体路由租户隔离 + `<img src>` Cookie 双轨鉴权 ★核心防坑

**隐患规避**：前端 `index.html` 中的原图、题干裁剪图、手动框选底图全部使用原生 `<img src="/data/...">` 标签，浏览器发起图片 GET 请求时**不会携带 `fetchT` 的 `Authorization: Bearer` 请求头**。若只认 Bearer 头，全站图片将返回 401 裂图。

**定稿方案**：
1. **登录种入 HttpOnly Cookie**：`POST /api/auth/activate` 验证通过后，除在 JSON body 返回 `{token, ...}` 外，响应头同时设置：
   - `Set-Cookie: wb_token=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000`
   - `Set-Cookie: wb_uid=<default_uid>; SameSite=Lax; Path=/; Max-Age=2592000`
2. **双轨读取 Token**：`@auth_required` 优先取 `Authorization: Bearer <token>`，若无则读取 `request.cookies.get("wb_token")`。前端 `<img src="/data/...">` 与 PDF 导出新窗口零代码改动即可自动通过鉴权。
3. **路径防穿透校验**：`/data/<path:p>` 在云端形态下，将请求路径拼接到当前 `(code, uid)` 租户专属媒体目录后，用 `os.path.realpath` 校验必须以租户目录真实路径为前缀（严防 `../` 越权访问其他家庭照片），否则返回 404。
4. **`/out/` 导出下载同规则**（评审补充）：云端形态 PDF/Word 产物落 `<租户媒体目录>/out/`，`GET /out/<p>` 以同样的 realpath 前缀校验下发——现为无鉴权直读，与 `/data/` 同级风险（G10）。
5. **CSRF 与 Cookie 属性**（评审补充）：`wb_token` 必须 HttpOnly；`wb_uid` 不加 HttpOnly（前端需读它发 `X-Wrongbook-Uid` 头）。`SameSite=Lax` 已阻断跨站 POST 携带 Cookie，状态变更接口又优先 Bearer 头，双重隔离；token 服务端有效期、Cookie Max-Age（30d）与心跳续期三者对齐。

### D-B5 AI Key 策略：模式 B（家长自配 Key，平台零 Token 成本）

- `accounts.ai_config` 列存储该家庭的 JSON 配置（provider / GLM key / DeepSeek key）。
- `ai_config.py` 读取钩子检查 `get_tenant()`：存在租户时读写 `accounts.ai_config`（**不使用平台内置 Key 兜底**，确保平台零 Token 费用）；未配置时接口返回明确提示引导家长在「AI 设置」中填入自己的 Key。
- 复用现有 `/api/ai-config` GET/POST 掩码契约，前端零新开发。
- `accounts` 表预留 `quota_left` 列，未来如需开启体验配额可无缝扩展。

### D-B6 视频讲解：**全线删除**（已拍板，含桌面版）

做法对齐 V1.3.5 GeoGebra 移除先例：删功能与入口，**历史数据残留字段忽略不清洗**。
涉及面：`static/index.html`（人审卡/例题卡输入面板、`videoBlockHtml()` 渲染、详情弹窗折叠块）、
`app.py`（PATCH 白名单 `video` 字段）、`pipeline.py`（随题存档）、
`topics.py`（`video_draft_ref`/例题字段/删除清理链）、`store.py`（引用检查）、
`tests/test_topics.py`（相关断言改写）。`figure_crop` 的交叉引用保护（v1.2q）**保留不动**。

### D-B7 账户表与激活码联动（license 库为账本 + 单向同步）

**职责划分**：license 库（`license.db`，8901 服务 + `license_admin.py` 管理）是**签发账本**——只有管理员 CLI 写它；业务库（`wrongbook.db`）的 `accounts` 表是**业务账本**（另存 `ai_config` 等业务字段）。业务服务对 `license.db` **只读**，两个 systemd 服务不共库、不互写，避免 SQLite 跨进程写锁竞争。

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
     到期/备注变化更新）；**绝不覆盖 accounts.ai_config**（家长自配 Key 是业务侧数据）
   · codes.revoked=1 或码已从 license 库删除 → accounts.is_active=0（不删行，留审计痕迹）
             │
             │  ③ 登录与鉴权
             ▼
   POST /api/auth/activate {code}
   → 查 wrongbook.db accounts：存在 + is_active=1 + 未过期 → 签发 token 写 sessions + Set-Cookie
   → 每次业务请求鉴权时同查 accounts（一次主键查询）→ 吊销/过期即时断会话
```

### D-B8 K-9 全学段：**V1.4.1 存量能力，云端核验即可**（评审勘误定稿）

> **现状勘误**：本节原稿假设「年级写死八年级上册、仅加载单册」——实际 **V1.4.1 / V1.4.1a /
> V1.4.1d 已把 K-9 全学段做完**，勿重复开发：
> - `data/kb/` 已含 20 个 json：全集源 `primary_math_all.json`（小学 1~6 上下册）+
>   `junior_math_7_9_all.json`（初中 7~9 上下册）+ 派生 `kp_1_1.json ~ kp_9_2.json`
>   （命名沿用既有 slug 规则 `kp_<n>_<s>.json`，**不新造 `kp_一年级上册.json`**）
> - `GET /api/grades` 已返回 1~9 全年级 + 其他（v1.4.1d 容错永不 500）
> - 录入页年级下拉已存在（`#ing-grade`，`localStorage['wb_grade']` 记忆；v1.4.1a **未选年级禁止转录**）
> - `classify_questions(grade=…)` 已按所选年级固定 prompt + 该年级受控清单
>   （`kbtree.knowledge_points_for_grade` 首次提取落盘，之后直读精简文件）
> - KB 播种已按文件逐个补齐（V1.3.6）——升级覆盖安装自动把新全集 json 带给老数据目录

**剩余工作（B1-T9 收缩后）**：
1. **云端种子核验**：部署清单确认 20 个 kb json 全部播种到 `/var/lib/wrongbook/data/kb/`（播种逻辑现成，核验即可）
2. **真机验证**：传「四年级下册」照片 → 归类命中四下知识点，不回落初中词表
3. **明确不做**：原稿的「`GET /api/kb-tree?grade=` + 人审卡切年级联动刷新知识点下拉树」——与
   **V1.2i 知识点只读 tag** 定稿冲突（kp 不可手选），且年级选错已有既有路径
   （v1.4.1a：改年级重新转录）；该接口从契约表移除，仅当未来开放 kp 手编时再议
4. （可选清理）`classify_questions` 无 grade 旧回落词表保留不动——云端前端强制必选后该分支实际不可达，删除反增回归面

---

## 4. 数据层设计（cloud_store.py 接口映射）

SQLite WAL，路径 `<DATA_HOME>/wrongbook.db`。表结构按 `development-B.md` §2.1 DDL（7 张表），包含预留的 `StorageBackend` / `MediaStorage` 抽象层（便于未来扩内存后无缝切 MySQL 与阿里云 OSS）：

1. `questions.norm_text` 复用 `store.norm_text()` 现成归一化（前 80 字），查重阈值保持 difflib ≥92%（SQL 预筛同户同章 + 内存 difflib 复核）。
2. 专题拆 `topic_sets`（每户每个孩子一份树 JSON，首跑从 `data/topics.json` 种子克隆）+ `topic_practice`（每例题 streak/ready/history）。

**接口映射**（与 `store.py` 同名同签名，通过 `tenant_ctx.get_tenant()` 自动定位当前家庭与成员，上层零改动）：

| store.py 现函数 | cloud_store 行为 |
|---|---|
| `load/save/add/get/update/remove` | `questions` 表 + `data_json` 整存整取 |
| `load_sheets/save_sheets/add_sheet/get_sheet/patch_sheet_question/set_sheet_status` | `sheets` 表 |
| `find_duplicate/norm_text` | SQL 前筛 + difflib 复核 |
| `add_practice/streak` | `data_json` 内 practice 追加（保持现结构） |
| `list_users/add_user/switch_user` | `users` 表（云端结合 `X-Wrongbook-Uid` / Cookie `wb_uid` 与 `sessions.current_uid`） |
| `crop_draft_ref/remove` 引用检查 | 云端同逻辑（读 `data_json` 判断），保留 `figure_crop` 保护链 |
| `topics` 读写 | `topic_sets` / `topic_practice` 两表 |

---

## 5. API 契约要点（新增/调整）

| 方法 | 路径 | 变化 |
|---|---|---|
| POST | `/api/auth/activate` | **新增**：`{code, device_name}` → 返回 `{token, owner, users[], current_uid}` 并写 `Set-Cookie: wb_token, wb_uid` |
| POST | `/api/auth/heartbeat` | **新增**：token 续活（`last_seen` 刷新）；前端冷启动调用 |
| GET / POST | `/api/users` | 云端：列出/新建**本户**成员（大宝/二宝）；切换成员更新前端 `X-Wrongbook-Uid` 头及 `wb_uid` Cookie |
| POST | `/api/sheets` | 接收 `file` + `grade`（**v1.4.1 已有**）+ `source`；改**异步**：立即返回 `{sheet_id, status:"transcribing"}` |
| GET | `/api/sheets/<sid>` | 轮询终点：`status=transcribing` → `pending`（含按 `grade` 分类好的 `questions`） |
| ~~GET~~ | ~~`/api/kb-tree`~~ | **移除**（评审）：与知识点只读 tag 定稿冲突，年级错选走既有「改年级重新转录」路径；未来开放 kp 手编时再议（D-B8） |
| GET | `/data/<path>` | 云端：支持 Bearer 头或 `wb_token` Cookie 鉴权 + 租户目录 `realpath` 前缀校验（D-B4） |
| GET | `/out/<path>` | 云端：与 `/data/` 同规则（Bearer/Cookie 鉴权 + 租户 realpath 校验；导出产物落租户目录，G10） |
| GET / POST | `/api/ai-config` | 云端：读写当前租户 `accounts.ai_config`（掩码契约不变，无平台默认 Key 兜底） |
| — | `/api/shutdown` | 云端禁用（404/403） |
| — | `/api/license` | 云端形态下返回 `{state:"cloud"}` |

---

## 6. 分阶段任务清单

### B0 前置：视频讲解全线删除（✅ 已完成，2026-11-11；桌面版同步生效，已拍板 D-B6）

| # | 任务 | 要点 |
|---|---|---|
| T1 | 前端移除 | `index.html`：人审卡/例题卡视频输入面板、`videoBlockHtml()` 渲染、详情弹窗「视频讲解」折叠块全部删除 |
| T2 | 后端移除 | `app.py` PATCH 白名单去 `video`；`pipeline` 存档链去 `video`；`topics`/`store` 的 `video` 引用检查与删除清理链删除（**`figure_crop` 交叉引用保护 v1.2q 保留不动**） |
| T3 | 历史数据 | 残留 `video` 字段忽略不清洗；`data/videos/` 目录残留文件忽略 |
| T4 | 回归 | `test_topics.py` 视频相关断言改写；全量单元测试全绿 |

### B1 后端多租户、异步化与 K-9 知识点支持（约 2 天）

| # | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T1 | `cloud_store.py`：DDL + 接口映射 + 种子 + OSS/MySQL 抽象预留 | `cloud_store.py`、`store.py` | 双租户测试：A 写题 B 不可见 |
| T2 | `topics` 存储改造（脱钩 `store._data_dir` 直读） | `topics.py` | 专题测试云端后端下全绿 |
| T3 | `tenant_ctx.py` (`ContextVar`) + `@auth_required`（Header + Cookie 双轨）+ auth 端点 | `tenant_ctx.py`、`auth.py`、`app.py` | 无 token/Cookie 全 401；子线程内 `get_tenant()` 准确无误 |
| T4 | 媒体路由校验（Cookie 鉴权 + `realpath` 防穿透），**`/data/` 与 `/out/` 同规则**（G10） | `app.py` | `<img src="/data/...">` 与 PDF 下载正常；跨户路径或 `../` 穿透 404 |
| T5 | 转录异步 job + 轮询 + 2h TTL 内存清理 | `app.py`、`reviewflow.py` | 上传 <1s 返回；后台线程带租户上下文完成转录；过期 job 自动回收 |
| T6 | 租户级 `ai_config` 钩子（模式 B：家长自配 Key） | `ai_config.py` | 两户各配 Key 互不影响；未配时明确提示去设置页填写 |
| T7 | 桌面形态回归测试 | `tests/` | `WRONGBOOK_CLOUD=off` 下零行为退化 |
| T8 | `wrongbook_admin.py`：`sync-accounts` / `list-sessions`（D-B7） | `wrongbook_admin.py` | 同步幂等；不覆盖 `ai_config`；吊销码即时 401 |
| T9 | K-9 云端种子核验（**收缩**：V1.4.1 已落地，见 D-B8 勘误） | `data/kb/`（20 json）、`kbtree.py` | ECS 数据目录播种齐全；真机传四下照片归类命中四下清单 |

### B2 手机 H5 适配（约 1~1.5 天，`static/index.html` 单文件）

| # | 任务 | 要点 |
|---|---|---|
| T1 | 视口与布局 | viewport 收紧（`maximum-scale=1, viewport-fit=cover`）；`@media (max-width:768px)`：人审双栏→单列、commitbar 吸底 + `safe-area-inset-bottom` |
| T2 | 拍照入口 | `<input type="file" accept="image/*" capture="environment">` 专用按钮（桌面隐藏）；上传前 `canvas` 压缩到长边 ~1600 |
| T3 | 触屏框选 | 框选事件加 `touchstart/touchmove/touchend` 分支（`preventDefault` 防页面跟随滚动；坐标归一化复用现 box 逻辑） |
| T4 | 转录轮询 UI | 上传后立即出占位卡 + 进度态；1.5s 轮询 `GET /api/sheets/<sid>` 直到 `pending`；断网重连续轮 |
| T5 | 移动端鉴权与细节 | 登录/激活弹窗（存 token 到 `localStorage` + 后端写 Cookie；请求带 `Bearer` 与 `X-Wrongbook-Uid`）；触控按钮 ≥44px；**复用 V1.4.0b 全局 fetch 覆盖层**：云端 401/403 分支跳登录视图（原逻辑仅识别 license 403 跳激活页） |
| T6 | 年级选择器真机核验（**收缩**：`#ing-grade` 下拉 + 必选拦截 + localStorage 记忆均 V1.4.1 已有） | 真机确认：18 册下拉齐全、未选年级有拦截提示、上次选择自动回显 |

### B3 部署、Caddy HTTPS 与运维（约 0.5~1 天）

| # | 任务 | 要点 |
|---|---|---|
| T1 | gunicorn + systemd | 印尼轻量服务器 **2G 内存 / 40G 盘**：`1 worker + 8 threads + timeout 300`；设置 `WRONGBOOK_CLOUD=on WRONGBOOK_DATA=/var/lib/wrongbook/data` |
| T2 | **Caddy 自动 HTTPS（直接上线）** | `flysic.cn` 与 `www.flysic.cn` 已解析至 `147.139.167.18`（海外免备案），配置 Caddy 反向代理 `127.0.0.1:8899`，自动签发 Let's Encrypt 证书（**安全组/防火墙放行 80+443**：ACME HTTP-01 需 80），家长直接访问 `https://flysic.cn`；同机 license 服务 8901 不经 Caddy 保持原状 |
| T3 | 管理员 CLI 与定时同步 | `sync-accounts`（D-B7）+ crontab 每 30 分钟自动同步一次 |
| T4 | 每日自动备份 | `sqlite3 wrongbook.db ".backup ..."` 每日凌晨 3 点备份 + 媒体目录增量归档，保留 30 天 |

---

## 7. 测试与回归策略

- **桌面零回归是硬门禁**：全部现有测试不改动语义；`WRONGBOOK_CLOUD` 默认 `off`，现有桌面路径不受影响。
- **云端专项测试**：
  - `tests/test_cloud_store.py`（租户隔离/并发写/查重/引用检查）
  - `tests/test_cloud_auth.py`（激活/Cookie 与 Header 鉴权/跨户越权 404/子线程 `bind_tenant` 上下文保持）
  - `tests/test_cloud_sheets.py`（异步转录 job + 年级参数传递 + K-9 知识点匹配）
- **真机清单**：手机微信内打开 `https://flysic.cn` 实测：激活码登录 → 选「四年级下册」→ 拍照上传 → 异步轮询出题 → 原图/附图显示正常（无 401 裂图）→ 手指拖拽重框选 → 勾选入库（验证不自动跳专题）→ 导出 PDF。

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 后台异步线程脱离 Flask 请求导致 `g` 丢失而串户 | **采用 `contextvars.ContextVar`（`bind_tenant`）**，子线程启动时显式传入 `(code, uid)` 并在线程内绑定（D-B2） |
| 浏览器 `<img src="/data/...">` 不带 Bearer 头导致图片全 401 | **登录时同步下发 `HttpOnly Cookie (wb_token)`**，`/data/` 路由支持 Header 与 Cookie 双轨鉴权（D-B4） |
| 多设备/多标签页同时看大宝、二宝导致 `current_uid` 互相覆盖 | **前端请求头带 `X-Wrongbook-Uid`**，优先按请求头路由成员（D-B2） |
| gunicorn 多 worker 下进程内 job 表失效 | **固化 `worker=1, threads=8`**；进程内 job 表加 **2 小时 TTL 自动清理**防内存膨胀（D-B3） |
| 手机上传大图（10MB 原图直传）耗时高 | 前端 `canvas` 压缩到长边 1600px 先行；后端 `preprocess` 双保险 |
| 未来扩容迁移成本 | `cloud_store.py` 内置 `StorageBackend`（SQLite/MySQL）与 `MediaStorage`（本地磁盘/阿里云 OSS）抽象接口，后期平滑切换 |

---

## 9. 拍板决策记录（2026-11-11 更新定稿）

| # | 问题 | 决定 | 影响 |
|---|---|---|---|
| 1 | 服务器与域名/HTTPS | **印尼轻量服务器（2G/40G，免备案）** + `flysic.cn`（含 `@` 与 `www`）已解析 | **HTTPS 无需后置**，B3 直接用 Caddy 上线 `https://flysic.cn`，消除明文传输风险；路线 A 小程序待未来有需要时再做国内备案 |
| 2 | 后期存储扩容规划 | 当前用 SQLite WAL + 本地磁盘；**后期图片上阿里云 OSS、数据库换 MySQL 并扩充内存** | `cloud_store.py` 抽象 `StorageBackend` 与 `MediaStorage` 接口，业务代码与底层存储解耦 |
| 3 | AI Key 供给方式 | **模式 B：家长自填 GLM/DeepSeek Key**，平台零 Token 费用；付费发激活码后可用 | `accounts.ai_config` 按户隔离存储，不使用平台 Key 兜底 |
| 4 | 视频讲解 | **全线删除（含桌面版）** | B0 前置任务清理干净 |
| 5 | 账户同步 | 认可 `license.db` 为账本 + 单向同步至 `wrongbook.db` | `sync-accounts` CLI + crontab 30 分钟兜底 + 每请求查 `accounts` |
| 6 | 学段与知识点 | **K-9 全学段（18 册）——评审勘误：V1.4.1 已落地**（20 个 kb json、`/api/grades`、必选年级、按年级受控归类均在库） | B1-T9 收缩为 ECS 种子核验、B2-T6 收缩为真机核验；**不做** `/api/kb-tree`（与 kp 只读定稿冲突，D-B8） |
| 7 | 架构取向 | **单应用双形态（D-B1）+ `ContextVar` 跨线程上下文（D-B2）+ Header/Cookie 双轨鉴权（D-B4）** | `WRONGBOOK_CLOUD` 默认 `off` 保证桌面零回归；彻底封堵子线程丢上下文与 `<img>` 401 裂图漏洞 |
