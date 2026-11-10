# AI 错题本（数学）· 客户端自动升级与 ECS 分发设计文档

> 版本：**U0.1（初稿待审）** ｜ 日期：2026-11-09  
> 上游文档：`DESIGN.md`（V1.4.0e）、`RELEASE_DESIGN.md`（R0.4，ECS：`147.139.167.18:8901`）  
> 目标：在不引入第三方云存储、不破坏本地数据自治的前提下，基于现有阿里云 ECS 验证服务实现**新版本自动检测 → 页面提示 → 下载校验 → 自动替换安装与重启**的全闭环。

---

## 1. 背景与设计目标

当前版本（v1.4.0）已完备实现 **R1 数据目录外置**（`paths.DATA_HOME` 与程序目录 `paths.app_root()` 完全物理隔离）以及 **R2/R3 激活验证体系**（客户端内置 Ed25519 公钥 `LICENSE_PUBKEY_PEM`，ECS 持有私钥并提供 `:8901` HTTP 接口）。

在 `RELEASE_DESIGN.md` §6.1 的早期规划中，升级仅停留在「手动下载压缩包解压覆盖」。随着外地家庭用户增加，手动解压覆盖存在两个实际痛点：
1. **家长操作门槛高**：容易解压成嵌套子目录（如 `AI错题本/AI错题本/`）或找不到原安装位置，增加远程支持负担；
2. **整包体积与迭代频率不匹配**：PyInstaller 完整包约 83MB~120MB（99% 为 Python 运行时与 Matplotlib/PyMuPDF 等不变依赖），而日常修复（如 `static/index.html`、`pipeline.py`、`reviewflow.py`）仅几百 KB。

### 核心设计指标

| 指标 | 设计要求 |
| :--- | :--- |
| **数据绝对安全** | 升级仅替换程序目录（`app_root()`），绝不触碰外置数据目录（`DATA_HOME`）；升级前自动生成轻量 JSON 快照 |
| **防篡改安全（纯 HTTP 可信）** | 复用现有 Ed25519 密钥对：`version.json`（含版本号、SHA256、包大小）由 ECS 私钥签名，客户端用内置公钥验签后再校验包体 SHA256 |
| **零额外云成本** | 直接复用现有 ECS（`147.139.167.18:8901`）提供清单查询与带鉴权流式下载，无需额外交钱买 OSS/CDN 或域名 |
| **双轨包体支持** | 同时支持 **轻量热补丁包（< 1MB，秒级升级）** 与 **全量安装包（~85MB，跨大版本/依赖变更用）** |
| **操作零门槛 + 失败可兜底** | 页面横幅一键触发「下载 → 校验 → 替换 → 重启 → 浏览器自动重连」；若被杀软拦截，自动回滚并提供浏览器直链下载兜底 |

---

## 2. 总体架构与升级全链路

```
┌─────────────── 开发者本机 ───────────────┐
│ 1. 打包全量包 (full.zip) 或补丁包 (patch) │
│ 2. scp 上传至 ECS /opt/wrongbook-license │
│ 3. 执行 license_admin.py release 签名发版 │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌───────── 阿里云 ECS (147.139.167.18:8901) ─────────┐
│ ├─ POST /v1/heartbeat  响应顺带捎回 latest_version │
│ ├─ GET  /v1/version    返回 Ed25519 签名版本清单    │
│ ├─ GET  /v1/download/* 校验激活态 + Range 流式下发 │
│ └─ releases/           存放全量包与热补丁包        │
└────────────────────┬───────────────────────────────┘
                     │ HTTP (Ed25519 签名防篡改 + SHA256 校验)
                     ▼
┌─────────────────── 用户家庭客户端 ───────────────────┐
│ ① 检测：启动/心跳发现 latest > APP_VERSION          │
│ ② 提示：顶部升级横幅（展示更新说明 + [立即升级]）   │
│ ③ 下载：后台流式下载至 DATA_HOME/updates/*.part     │
│ ④ 校验：Ed25519 验签清单 + 校验文件 SHA256 + 预解压 │
│ ⑤ 安装：拉起进程外独立脚本 (bat/sh) + 主进程退出    │
│ ⑥ 重启：脚本覆盖程序目录 → 启动新进程 → 前端自动刷新│
└─────────────────────────────────────────────────────┘
```

---

## 3. 升级包形态设计：双轨制（轻量热补丁 + 全量包）

为了兼顾「日常小步快跑修复」与「底层依赖更新」，设计采用**自适应双轨制**：

### 3.1 轨道对比

| 维度 | 轨道 A：轻量业务补丁包（`patch`） | 轨道 B：全量安装包（`full`） |
| :--- | :--- | :--- |
| **包含内容** | 业务 `.py` 源码、`static/` 前端资源、`engine/*.py`、`data/kb/*.json` | 完整 PyInstaller `onedir` 产物（含 `.exe`/`.app`、`_internal/` 全部依赖） |
| **包体大小** | **约 200KB ~ 600KB** | **约 80MB ~ 110MB** |
| **ECS 下载耗时** | **1 ~ 3 秒**（即使 1Mbps 带宽也瞬间完成） | 约 1 ~ 5 分钟（视 ECS 带宽上限而定） |
| **适用场景** | 95% 以上的日常迭代（前端 UI 修复、Prompt 优化、业务逻辑调整） | Python 版本升级、新增第三方库（`requirements.txt` 变更）、改打包 spec |
| **跨平台性** | **Win / macOS 通用同一个补丁包** | 需分别构建 `win64.zip` 与 `mac.zip` |

### 3.2 轻量热补丁（轨道 A）的实现机制

PyInstaller 默认将主工程 `.py` 编译进二进制归档（`PYZ`），若不调整加载顺序，`sys.meta_path` 中的 `PyImporter` 会优先于文件系统加载旧字节码。要让百 KB 级热补丁直接生效，只需在客户端做一处极简改造：

1. **外置热更新覆盖层（`DATA_HOME/hotfix/` 或程序目录覆盖）**：
   - `static/`（含 `index.html`）、`engine/`、`data/kb/` 本来就是程序目录下的普通文件，直接覆盖文件即可即时生效；
   - 对于主目录 `.py` 模块（如 `reviewflow.py`、`pipeline.py`、`topics.py`、`store.py`、`stats.py`），在 `packaging/wrongbook.spec` 中将其作为源文件随包放到程序目录（或由 `app.py` 启动时扫描热补丁目录动态加载），即可在不重打 85MB 依赖包的情况下，用几百 KB 补丁包完成全栈更新。
2. **版本水位线控制（`min_full_version`）**：
   - 服务端的 `version.json` 中声明 `min_full_version`（例如 `"1.4.0"`）；
   - 若客户端当前版本 `>= min_full_version`，客户端自动选择下载轻量 `patch` 包（秒级升级）；
   - 若客户端当前版本 `< min_full_version`（说明中间隔了底层依赖大改），客户端自动回落下载对应平台的 `full` 全量包。

---

## 4. ECS 服务端设计（基于 `147.139.167.18:8901`）

### 4.1 签名版 `version.json` 数据契约

由于当前 ECS 采用免域名纯 HTTP（`http://147.139.167.18:8901`），为防止网络劫持篡改下载链接，**`version.json` 必须使用 ECS 上的 Ed25519 私钥进行签名**（复用 `license_server.py` 现有的 `_sign()` 函数，零新增密钥管理成本）：

```json
{
  "latest": "1.4.1",
  "min_supported": "1.4.0",
  "min_full_version": "1.4.0",
  "published_at": 1762700000,
  "notes": [
    "修复从专题跳转录入时题目自动选中及提交提示异常的问题",
    "新增客户端在线检查更新与一键升级功能"
  ],
  "patch": {
    "filename": "wrongbook-patch-1.4.1.zip",
    "url": "/v1/download/wrongbook-patch-1.4.1.zip",
    "size": 245760,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  },
  "full": {
    "win32": {
      "filename": "AI错题本-v1.4.1-win64.zip",
      "url": "/v1/download/AI错题本-v1.4.1-win64.zip",
      "size": 89128960,
      "sha256": "a1b2c3d4..."
    },
    "darwin": {
      "filename": "AI错题本-v1.4.1-mac.zip",
      "url": "/v1/download/AI错题本-v1.4.1-mac.zip",
      "size": 87031808,
      "sha256": "f9e8d7c6..."
    }
  },
  "sig": "base64(ed25519_sign(canonical_json_without_sig))"
}
```

- **客户端验签规则**：客户端收到 `/v1/version` 响应后，直接调用 `license_client.py` 同款的 `LICENSE_PUBKEY_PEM.verify(sig, _canonical(version_dict))`。验签不通过直接丢弃，杜绝任何伪造更新包注入。
- **URL 灵活性**：`url` 若以 `/` 开头，客户端自动拼接 `SERVER_BASE`（即从本机 ECS 下载）；未来若某个全量包想放对象存储或网盘直链，直接填 `https://...` 即可，由于 `sha256` 已被 Ed25519 签名锁定，存放在任何静态节点均同样安全。

### 4.2 ECS 接口扩展（`ecs/license_server.py`）

| 方法 | 路径 | 职责与安全策略 |
| :--- | :--- | :--- |
| `POST` | `/v1/heartbeat` | **顺带捎回版本号**：在现有 `{receipt, license, server_time}` 基础上增加 `"latest_version": "1.4.1"` 字段（读内存缓存的 `version.json` mtime，零额外开销）。客户端心跳时即可感知新版本，无需多发一次轮询请求 |
| `GET` | `/v1/version` | 返回完整的签名版 `version.json` 内容（限流：30 次/分钟/IP） |
| `GET` | `/v1/download/<filename>` | **安装包/补丁包流式下载接口**：<br>1. **路径穿越防护**：严格校验 `filename` 仅含安全字符且位于 `/opt/wrongbook-license/releases/` 下；<br>2. **激活态鉴权（防盗刷带宽）**：要求 Query 参数附带 `?code=WB-...&device_id=...`，校验该激活码有效且未吊销才允许下载，防止公网扫描器刷爆 ECS 流量；<br>3. **断点续传**：使用 Flask `send_from_directory(RELEASE_DIR, filename, conditional=True)`，原生支持 HTTP `Range` 分片与断点续传；<br>4. **审计留痕**：下载开始时记入 SQLite `audit` 表（`event="download_pkg"`） |

> **ECS Gunicorn 配置微调**：Gunicorn 默认 worker 超时为 30 秒。为支持慢速网络下 85MB 全量包的流式下载，在 `wrongbook-license.service` 的 `ExecStart` 中增加 `--timeout 300`（即 `gunicorn -w 1 --threads 8 --timeout 300 -b 0.0.0.0:8901 license_server:app`）。

### 4.3 发版管理 CLI（扩展 `ecs/license_admin.py`）

在 `license_admin.py` 中新增 `release` 子命令，避免手工编辑 JSON 或手算哈希出错：

```bash
# 仅发轻量热补丁（日常 95% 场景）：
venv/bin/python license_admin.py release \
  --version 1.4.1 \
  --notes "1. 修复专题转录自动勾选问题；2. 新增在线一键升级" \
  --patch releases/wrongbook-patch-1.4.1.zip

# 发全量包 + 补丁包：
venv/bin/python license_admin.py release \
  --version 1.5.0 \
  --min-full 1.5.0 \
  --notes "升级排版与数学公式渲染引擎" \
  --win releases/AI错题本-v1.5.0-win64.zip \
  --mac releases/AI错题本-v1.5.0-mac.zip
```

`release` 命令内部自动完成：检查文件存在 → 计算 `size` 与 `sha256` → 读取 `/etc/wrongbook-license/private.pem` 进行 Ed25519 签名 → 原子写入 `/opt/wrongbook-license/version.json` → 打印验签结果。

---

## 5. 客户端自动检测与前端交互设计

### 5.1 检测时机与版本比对

1. **语义化版本比对函数 `compare_version(v1, v2)`**：
   - 将 `"1.4.0e"` 解析为 `(1, 4, 0, "e")`，`"1.4.1"` 解析为 `(1, 4, 1, "")`，严格支持数字主次版本与补丁字母后缀的序关系。
2. **三个检测触发点**：
   - **启动首查**：`boot()` 调用 `/api/license` 时，后台异步检查一次 `/v1/version`（超时 5 秒，失败静默忽略，绝不阻塞启动）；
   - **心跳捎带**：运行中每 10 分钟心跳检查收到 `latest_version > APP_VERSION` 时，自动拉取 `/v1/version` 完整清单并缓存至内存；
   - **手动触发**：顶栏「⚙ 设置」弹窗底部展示 `当前版本：v1.4.0` 及 `🔍 检查更新` 按钮，点击立即向服务端查询。

### 5.2 前端提示分级与防打断机制

| 场景 | 条件 | 前端交互表现 |
| :--- | :--- | :--- |
| **常规可选更新** | `latest > APP_VERSION` 且 `APP_VERSION >= min_supported` | 顶部显示蓝色通知条（位于 `#lic-banner` 下方）：<br>`🚀 发现新版本 v1.4.1（当前 v1.4.0）：1. 修复专题转录自动勾选问题…` 右侧按钮：`[立即升级]` `[暂不提醒]` |
| **强制最低版本更新** | `APP_VERSION < min_supported`（如重大协议变更） | 弹出不可关闭的升级卡片，说明当前版本已停止支持，提供 `[立即升级]` 与 `[浏览器下载安装包]` 按钮 |
| **作业中防打断保护** | 用户点击 `[立即升级]` 时正在做题（`paperData` 非空）或正在转录/录入中 | 弹窗拦截提示：`当前正在录入题目或做题中，请先提交/完成后再执行升级，以免丢失当前页面未提交的草稿。` |

### 5.3 升级过程进度展示（复用全屏遮罩 `#overlay`）

点击 `[立即升级]` 后，前端开启锁屏遮罩并每 500ms 轮询 `GET /api/update/status`，分阶段展示清晰进度：
1. **下载阶段**：`⬇ 正在下载更新包 v1.4.1（42% · 36.5 MB / 87.0 MB · 3.2 MB/s）…`（若为热补丁包则 1~2 秒闪过）
2. **校验与准备阶段**：`🔒 正在校验数字签名与完整性，并备份题库数据…`
3. **替换与重启阶段**：`⚙️ 正在安装新版本并重启服务（约 5~10 秒，页面将自动刷新，请勿关闭窗口）…`
4. **重连完成**：前端轮询到新服务就绪且版本号已变为 `1.4.1` 后，自动 `location.reload()` 并在顶部弹出绿色提示 `✅ 已成功升级至 v1.4.1！`。

---

## 6. 客户端下载、校验与进程外安装设计（核心工程细节）

### 6.1 为什么必须采用「进程外独立脚本（Out-of-Process Updater）」？

在桌面端自升级中，运行中的进程不能直接覆盖自身可执行文件：
- **Windows 文件锁死**：Windows 内核会锁定正在运行的 `AI错题本.exe` 以及 `_internal/` 下已加载的所有 `.dll` / `.pyd` 动态库，进程内直接覆盖必报 `PermissionError: [WinError 32]`；
- **macOS 签名与隔离属性**：macOS 替换 `.app` 包后若不清除 Quarantine 属性或重新拉起进程，会导致动态库签名偏移崩溃（`SIGKILL - Code Signature Invalid`）。

因此，标准且最稳妥的做法是：**主进程负责「下载 + 验签 + 预解压 + 数据快照」，然后生成并启动一个独立的轻量脚本（Windows `.bat` / macOS `.sh`），主进程立即退出释放文件锁与 8899 端口，由独立脚本完成目录替换并拉起新主进程。**

### 6.2 升级工作目录结构（位于 `DATA_HOME/updates/`）

```
DATA_HOME/
├── updates/
│   ├── version_manifest.json       # 已验签的最新版本清单缓存
│   ├── pkg_1.4.1.zip.part          # 下载中临时文件（支持断点续传）
│   ├── pkg_1.4.1.zip               # SHA256 校验通过的完整包
│   ├── staging/                    # 预解压目录（替换前先在此完整解压验证）
│   ├── rollback/                   # 替换前移出的旧版文件（失败自动还原）
│   ├── run_update.bat / .sh        # 进程外替换与重启脚本
│   └── update.log                  # 升级脚本执行日志（排障用）
└── backups/
    └── pre_update_1.4.0_to_1.4.1.zip  # 升级前自动生成的题库 JSON 快照
```

### 6.3 五步原子升级流水线（`updater.py`）

#### 第一步：流式下载与断点续传（`downloading`）
- 检查磁盘可用空间：`shutil.disk_usage(DATA_HOME).free` 必须大于 `包体积 × 3 + 200MB`；
- 检查程序目录写权限：检测 `os.access(paths.app_root(), os.W_OK)`（若 Windows 用户安装在受保护目录无写权限，提前切换为提权脚本模式或提示）；
- 使用 `urllib.request` 直连（绕过系统代理，与 `license_client.py` 一致），若本地存在同版本 `.part` 文件则发送 `Range: bytes=<offset>-` 断点续传，每 64KB 块更新内存进度状态。

#### 第二步：双重密码学校验与预解压（`verifying`）
1. **Ed25519 清单验签**：确认 `version.json` 签名合法；
2. **SHA256 校验**：流式计算下载文件 SHA256，与清单中的 `sha256` 严格一致；不匹配则删除 `.part` 并报错中止；
3. **预解压检验（Staging）**：将 zip 解压到 `DATA_HOME/updates/staging/`，校验关键入口文件（如 `static/index.html` 或 `AI错题本.exe` / `AI错题本.app`）存在且非空。**绝不在原程序目录直接解压**，防止解压中途断电导致程序半残。

#### 第三步：用户题库 JSON 自动快照（`backing_up`）
- 遍历 `DATA_HOME` 下的所有 `.json` 文件（`users.json`、`topics.json`、`ai_config.json`、`license.json` 及 `users/*/*.json`），打包为 `DATA_HOME/backups/pre_update_<old>_to_<new>.zip`；
- 仅备份 JSON 文本（合计 < 1MB，耗时 < 50ms），不重复打包数百 MB 的照片目录；保留最近 5 次升级快照自动轮换。

#### 第四步：生成并拉起跨平台替换脚本（`applying`）

**Windows 端脚本逻辑（`run_update.bat`）**：
```bat
@echo off
chcp 65001 >nul
set "LOG=%~dp0update.log"
echo [%date% %time%] Waiting for PID %1 to exit... > "%LOG%"

:: 1. 等待旧主进程退出（最多等待 15 秒，超时强杀兜底）
for /L %%i in (1,1,30) do (
    tasklist /FI "PID eq %1" 2>NUL | find /I "%1" >NUL
    if errorlevel 1 goto :pid_gone
    timeout /t 1 /nobreak >nul
)
taskkill /F /PID %1 >> "%LOG%" 2>&1
:pid_gone

:: 2. 将 staging 目录下的文件同步覆盖到程序目录 APP_ROOT
echo [%date% %time%] Applying update files... >> "%LOG%"
xcopy /E /Y /I "%STAGING_DIR%\*" "%APP_ROOT%\" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: xcopy failed! >> "%LOG%"
    exit /b 1
)

:: 3. 拉起新版本主程序（带静默重启标记：不弹窗、不额外新开多余浏览器标签页）
echo [%date% %time%] Starting new version... >> "%LOG%"
set "WRONGBOOK_SILENT_RESTART=1"
start "" "%APP_ROOT%\AI错题本.exe"
exit /b 0
```
- Python 启动脚本方式：使用 `subprocess.Popen(["cmd.exe", "/c", bat_path, str(os.getpid())], creationflags=0x00000008 | 0x00000200, close_fds=True)`（`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` + 无黑框隐藏执行），随后 0.3 秒定时调用 `os._exit(0)`。

**macOS 端脚本逻辑（`run_update.sh`）**：
```bash
#!/usr/bin/env bash
LOG="$(dirname "$0")/update.log"
OLD_PID="$1"
echo "[$(date)] Waiting for PID $OLD_PID..." > "$LOG"

# 1. 等待旧进程退出
for i in {1..30}; do
  kill -0 "$OLD_PID" 2>/dev/null || break
  sleep 0.5
done
kill -9 "$OLD_PID" 2>/dev/null || true

# 2. 覆盖更新（区分补丁包与全量 .app 包）
if [ -d "$STAGING_DIR/AI错题本.app" ]; then
  rm -rf "$APP_BUNDLE_PATH.old"
  mv "$APP_BUNDLE_PATH" "$APP_BUNDLE_PATH.old" && \
  mv "$STAGING_DIR/AI错题本.app" "$APP_BUNDLE_PATH" || \
  { mv "$APP_BUNDLE_PATH.old" "$APP_BUNDLE_PATH"; exit 1; }
  rm -rf "$APP_BUNDLE_PATH.old"
else
  cp -Rf "$STAGING_DIR/"* "$APP_ROOT/" >> "$LOG" 2>&1
fi

# 3. 清除 macOS Gatekeeper 隔离属性（关键！防止升级后系统弹窗拦截“已损坏”）
xattr -cr "$APP_BUNDLE_PATH" 2>/dev/null || true

# 4. 重新启动应用（带静默重启环境变量）
WRONGBOOK_SILENT_RESTART=1 open -n "$APP_BUNDLE_PATH" >> "$LOG" 2>&1
```

#### 第五步：新进程启动与前端无缝重连（`reconnecting`）
- `app.py` 启动时检测环境变量 `WRONGBOOK_SILENT_RESTART == "1"`：
  - 若为升级后自动重启，**不再调用 `webbrowser.open(url)` 新开标签页**（因为用户原来的浏览器标签页正开着并在轮询等待重连！避免每次升级多开一个重复标签页）；
  - 启动时等待最多 3 秒绑定 `8899` 端口（防止旧进程套接字 `TIME_WAIT` 瞬间未释放）。
- 原浏览器标签页轮询 `/api/license` 收到 200 且 `app_version` 更新后，自动刷新页面并展示升级成功提示。

---

## 7. 客户端与服务端改动清单

| 文件 | 改动类型 | 具体内容 |
| :--- | :--- | :--- |
| `updater.py` | **新增模块** | 客户端升级核心（~220 行）：版本比对、`version.json` 验签、后台流式下载（支持 Range）、SHA256 校验、预解压、JSON 快照、生成并拉起 Win/mac 更新脚本 |
| `app.py` | 少量修改 | 1. `_license_gate` 放行 `/api/update/*`；<br>2. 新增 `GET /api/update/check`、`POST /api/update/start`、`GET /api/update/status`；<br>3. 启动时识别 `WRONGBOOK_SILENT_RESTART=1` 跳过重复打开浏览器标签页 |
| `license_client.py` | 少量修改 | `status()` 附带缓存的 `update_info`（若有新版本，前端首屏 `/api/license` 一次请求即可拿到升级提示）；`heartbeat()` 保存服务端捎回的 `latest_version` |
| `static/index.html` | 少量修改 | 1. 顶部新增 `#update-banner` 升级提示条；<br>2. 「⚙ 设置」弹窗底部增加版本号显示与「检查更新」按钮；<br>3. `startUpdate()` 下载进度轮询与服务重启自动重连逻辑 |
| `ecs/license_server.py` | 少量修改 | 1. `/v1/heartbeat` 响应附带 `latest_version`；<br>2. 新增 `GET /v1/download/<filename>`（激活码校验 + Range 流式传输 + 审计日志） |
| `ecs/license_admin.py` | 少量修改 | 新增 `release` 子命令：自动计算包大小与 SHA256、Ed25519 私钥签名并生成 `version.json` |
| `tests/test_updater.py` | **新增测试** | 覆盖版本号比较、清单篡改验签拒绝、SHA256 不匹配中止、断点续传、JSON 快照备份、补丁包/全量包路由选型、脚本生成等回归测试 |

---

## 8. 异常防御与兜底矩阵（FMEA）

| 异常场景 | 防御与恢复机制 | 用户体验 |
| :--- | :--- | :--- |
| **下载中途断网 / 超时** | `.part` 文件保留，下次点「立即升级」通过 HTTP `Range` 断点续传；不影响当前正在运行的旧版任何功能 | 提示「网络中断，请重试（支持断点续传）」 |
| **下载包残缺或被篡改** | Ed25519 验签 + SHA256 校验双重拦截，在 `staging` 阶段直接丢弃，绝不触发替换脚本 | 提示「更新包校验未通过，已自动取消，当前程序完好」 |
| **Windows 杀毒软件（360/Defender）拦截脚本或新 exe** | 脚本先备份再替换，失败自动还原旧版文件；前端轮询超 45 秒未连上新服务时，遮罩自动给出**「📥 浏览器直接下载安装包」**按钮 | 一键退化为浏览器下载 zip 手动解压，不卡死 |
| **macOS 用户未拖入 Applications，直接在只读 DMG 中运行** | `POST /api/update/start` 前检测 `os.access(app_root(), os.W_OK)`，发现只读立即拦截并提示 | 提示「请先将 AI错题本.app 拖入『应用程序』文件夹后再升级」 |
| **升级后发现新版本有严重 Bug 需紧急回退** | ECS 执行 `license_admin.py release` 将 `latest` 指回稳定版，或用户在本地从 `DATA_HOME/backups/` 恢复数据快照 | 数据向下兼容（`DATA_SCHEMA_VERSION` 守护） |
| **旧版出现 Bug 导致激活状态异常** | `app.py` 的 `_license_gate` 门禁白名单放行 `/api/update/*`，即使处于未激活或锁定页，也能检测到新版本并执行升级自愈 | 锁定/激活页同样可收到升级修复推送 |

---

## 9. 待您审查拍板的决策项（Checklist）

在进入代码实现前，请您审查以下 4 个关键设计点是否符合预期：

1. **升级包策略（推荐：双轨制）**
   - **方案 A（推荐）**：同时支持**轻量补丁包（< 500KB，改前端/业务 Python 代码时只传补丁，3 秒升完）**与**全量包（~85MB，改底层依赖时用）**；
   - **方案 B**：只做全量包下载替换（每次发版都上传 85MB 完整包）。
2. **ECS 下载接口鉴权（推荐：开启激活码校验）**
   - 下载接口 `/v1/download/<filename>` 要求客户端带上本地已激活的 `code` + `device_id`（未激活时若在激活页升级，可放行 `patch` 小包、仅对 85MB 大包鉴权或按 IP 限流 5 次/小时），防止外网爬虫耗尽 ECS 流量。
3. **升级触发方式（推荐：自动提示 + 用户点一下确认升级）**
   - 发现新版本后，在页面顶部弹出醒目升级条，用户点击「立即升级」后全自动完成下载、替换与重启（避免在用户不知情时突然重启打断看题）。
4. **开发态（非打包源码运行）行为**
   - 开发态（您本机源码调试 `python app.py`）默认跳过自动覆盖替换（只展示版本检测结果，防止把您本机正在写的源码覆盖掉），仅在打包态（`sys.frozen`）或设置 `WRONGBOOK_TEST_UPDATE=1` 时执行真实替换。
