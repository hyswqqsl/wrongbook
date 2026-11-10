# AI 错题本（数学）· ECS 云端与移动端开发实战手册

> 版本：**v1.0** ｜ 面向对象：独立开发者 / 后续维护者  
> 适用技术栈：Python 3.11+ / Flask / SQLite (WAL) / 移动端 H5 (微信自适应) / 微信小程序  
> 关联文件：`cloud_store.py`、`app.py`、`reviewflow.py`、`pipeline.py`、`topics.py`、`static/index.html`

---

## 1. 总体架构与数据流向

整个系统采用**多租户前后端分离**架构。用户在手机端通过微信内置浏览器（或后续的小程序）进行拍照录题、在线做题或导出 PDF；所有业务逻辑、视觉识别、试卷排版与数据持久化均集中在阿里云 ECS 上运行。

```
┌────────────────── 手机端（家长/学生使用） ──────────────────┐
│                                                            │
│  微信网页版（当前阶段） ｜ 微信小程序（备案后无缝切换）          │
│  ├─ 1. 激活与设置：输入激活码 WB-XXXX + 自填 GLM/DeepSeek Key │
│  ├─ 2. 拍照录题：调起手机相机拍照、多张上传、触屏手指框选    │
│  ├─ 3. 人审入库：逐题核对勾选、错因标注、零 LLM 存入专题     │
│  ├─ 4. 错题工作台：章节/知识点检索、生成 PDF 直接微信打印    │
│  └─ 5. 专题练习：初二必考 10 类例题练习、连对 3 次已掌握      │
└─────────────────────────────┬──────────────────────────────┘
                              │ HTTPS / HTTP 请求 (携带 Bearer Token)
                              ▼
┌────────────── 阿里云 ECS 云端服务 (147.139.167.18) ─────────────┐
│                                                            │
│  Nginx / Caddy 反向代理 (端口 80/443 或当前 8899)            │
│  └─ Gunicorn (1-2 Workers, 8 Threads) 驱动 Flask 业务应用   │
│                                                            │
│  ┌──────────────────── 核心业务应用 ────────────────────┐  │
│  │ ├─ 鉴权门禁：验证 Authorization: Bearer <token>      │  │
│  │ ├─ 存储抽象层：cloud_store.py (当前 SQLite，预留 MySQL)│  │
│  │ ├─ 媒体抽象层：MediaStorage (当前磁盘，预留 OSS)      │  │
│  │ ├─ 视觉转录引擎：reviewflow.py (大模型转录+自动扶正)  │  │
│  │ ├─ 质检入库引擎：pipeline.py (去重+验伪+受控知识点)  │  │
│  │ ├─ 专题教学引擎：topics.py (必考10类例题题库)        │  │
│  │ └─ 试卷排版引擎：engine/export_paper.py (矢量 PDF)   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌──────────────────── 数据存储与外设 ──────────────────┐  │
│  │ ├─ 数据库：/var/lib/wrongbook/data/wrongbook.db (WAL)│  │
│  │ └─ 图片存储：/var/lib/wrongbook/media/<code_hash>/   │  │
│  │    ├─ photos/ (转正后的试卷原图 JPEG)                │  │
│  │    └─ crops/  (题目几何原图 PNG)                    │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────┬──────────────────────────────┘
                              │ 异步流式请求（使用用户自填的 API Key，直连供应商）
                              ▼
           ┌──────────────────────────────────────┐
           │   大模型供应商（智谱 GLM / DeepSeek）  │
           │   开发者零垫资，Token 费用用户自理     │
           └──────────────────────────────────────┘
```

---

## 2. 数据库设计与多租户隔离规范

系统以**专属激活码 `code`（格式 `WB-XXXX-XXXX-XXXX`）**作为租户的顶层隔离边界。任何查询与写入都必须带上租户凭证，严防越权。

### 2.1 SQLite WAL 模式表结构（DDL）

数据库文件建议存放在 `/var/lib/wrongbook/data/wrongbook.db`。初始化时执行以下建表语句：

```sql
PRAGMA journal_mode = WAL;          -- 开启写前日志模式，高并发读写不阻塞
PRAGMA synchronous = NORMAL;         -- 保证写入安全且兼顾速度
PRAGMA foreign_keys = ON;

-- 1. 家庭账户表（由管理员发售生成）
CREATE TABLE IF NOT EXISTS accounts (
    code         TEXT PRIMARY KEY,   -- 激活码：WB-XXXX-XXXX-XXXX
    owner        TEXT NOT NULL,      -- 家长备注（如：初二李同学家）
    expires_at   INTEGER,            -- 到期 Unix 秒（NULL 或 4102444800 为永久）
    is_active    INTEGER DEFAULT 1,  -- 1=有效, 0=停用/吊销
    ai_config    TEXT DEFAULT '{}',  -- 独立保存该家庭填写的 GLM/DeepSeek Key (JSON)
    created_at   INTEGER
);

-- 2. 客户端登录会话表（手机端免密持久登录）
CREATE TABLE IF NOT EXISTS sessions (
    token        TEXT PRIMARY KEY,   -- 客户端 localStorage 保存的凭证 (tk_...)
    code         TEXT NOT NULL,      -- 关联的激活码
    device_name  TEXT,               -- 设备名称（如：iPhone 15 Pro / Chrome Win）
    last_seen    INTEGER,            -- 最近活跃时间戳
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);

-- 3. 家庭成员表（支持一家多孩，如“大宝”、“二宝”）
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT NOT NULL,      -- u2026... 或 test
    code         TEXT NOT NULL,
    name         TEXT NOT NULL,      -- 成员显示名
    created_at   INTEGER,
    PRIMARY KEY(code, user_id),
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);

-- 4. 排版机草稿单表（替代原 drafts.json）
CREATE TABLE IF NOT EXISTS sheets (
    sheet_id     TEXT NOT NULL,      -- s20261110-...
    code         TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    mode         TEXT DEFAULT 'book',-- 'book'=录入题目 | 'topic'=专题录入
    status       TEXT DEFAULT 'pending', -- 'pending' | 'processed'
    source       TEXT,               -- 来源标注（如：期中卷）
    photo_path   TEXT,               -- 试卷原图本地存储路径或 OSS URL
    data_json    TEXT NOT NULL,      -- 完整草稿结构 (包含 questions 数组与图形信息)
    created_at   INTEGER,
    PRIMARY KEY(code, sheet_id),
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sheets_code_uid ON sheets(code, user_id, created_at DESC);

-- 5. 错题本题库表（替代原 wrongbook.json）
CREATE TABLE IF NOT EXISTS questions (
    qid          TEXT NOT NULL,      -- q20261110-...
    code         TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    chapter      TEXT,               -- 浙教版教材章名称（工作台筛选）
    difficulty   INTEGER,            -- 1=简单, 2=中等, 3=困难
    reason_kind  TEXT,               -- careless/unknown/other
    mastered     INTEGER DEFAULT 0,  -- 0=在库, 1=已归档
    norm_text    TEXT,               -- 归一化特征字（前80字，用于加速查重）
    data_json    TEXT NOT NULL,      -- 完整题目结构 dict
    created_at   INTEGER,
    PRIMARY KEY(code, qid),
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_q_filter ON questions(code, user_id, mastered, chapter);

-- 6. 专题例题表（替代原 topics.json）
CREATE TABLE IF NOT EXISTS topic_sets (
    code         TEXT PRIMARY KEY,
    sets_json    TEXT NOT NULL,      -- 该家庭的 10 大必考专题树结构 JSON
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);

-- 7. 专题例题练习记录表（替代原 topic_practice.json）
CREATE TABLE IF NOT EXISTS topic_practice (
    code         TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    example_id   TEXT NOT NULL,      -- 专题例题 ID (te_...)
    streak       INTEGER DEFAULT 0,  -- 当前连对次数
    ready        INTEGER DEFAULT 0,  -- 是否已掌握 (连对3次 = 1)
    history_json TEXT DEFAULT '[]',  -- 练习流水记录
    PRIMARY KEY(code, user_id, example_id),
    FOREIGN KEY(code) REFERENCES accounts(code) ON DELETE CASCADE
);
```

### 2.2 后期平滑迁移到 MySQL 的设计要点

系统在 `cloud_store.py` 中实现了 `StorageBackend` 适配器模式。未来服务器扩容内存（如升级至 4G/8G 内存）切换至 MySQL 时，上层代码完全零改动：
1. **字段对应**：SQLite 的 `INTEGER` 对应 MySQL 的 `BIGINT`，`TEXT` 对应 `VARCHAR` 或 `LONGTEXT`；
2. **JSON 列**：MySQL 5.7+ 原生支持 `JSON` 类型，直接映射 `data_json`；
3. **切换机制**：在 `cloud_store.py` 中新增 `MySQLBackend` 类，实现与 `SQLiteBackend` 完全相同的接口方法，修改环境变量 `STORAGE_BACKEND=mysql` 即可一键完成引擎热切换。

---

## 3. 媒体存储规范与未来迁移阿里云 OSS 指南

### 3.1 当前阶段（ECS 本地磁盘存储）

试卷照片与裁剪图存放在 `/var/lib/wrongbook/media/` 目录下，并使用**激活码 SHA256 前 16 位**做二级目录隔离：
```
/var/lib/wrongbook/media/
├── 8f4a3c2b1e0d9a78/               -- 家庭 A (WB-2026-MATH-0001 的哈希)
│   ├── photos/
│   │   └── s20261110-101520-abc123.jpg  -- 试卷原图 (长边1800px, JPEG q88, 约200KB)
│   └── crops/
│       └── s20261110_q1_0.png      -- 题目几何原图 (PNG, 约30KB)
└── e7d8c9b0a1f2e3d4/               -- 家庭 B (WB-2026-MATH-0002 的哈希)
```

### 3.2 后期无缝迁移阿里云 OSS 步骤

代码已预留 `MediaStorage` 抽象基类与 `AliyunOSSStorage` 存根：
1. **安装依赖**：`pip install oss2`；
2. **在 `cloud_store.py` 启用适配器**：
   ```python
   import oss2

   class AliyunOSSStorage(MediaStorage):
       def __init__(self, endpoint, bucket_name, ak, sk):
           auth = oss2.Auth(ak, sk)
           self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
           self.base_url = f"https://{bucket_name}.{endpoint}"

       def save_photo(self, code: str, filename: str, data: bytes) -> str:
           key = f"wrongbook/{code_hash(code)}/photos/{filename}"
           self.bucket.put_object(key, data)
           return f"{self.base_url}/{key}"   # 直接返回公网 CDN/OSS 链接

       def save_crop(self, code: str, filename: str, data: bytes) -> str:
           key = f"wrongbook/{code_hash(code)}/crops/{filename}"
           self.bucket.put_object(key, data)
           return f"{self.base_url}/{key}"
   ```
3. **业务解耦优势**：`sheets` 和 `questions` 表里的 `photo_path` 和 `figure_crop` 列本身保存的就是字符串。切换至 OSS 后，字符串从本地绝对路径变成 `https://...` 链接，前端 `<img src="...">` 自动兼容加载，业务代码无需修改任何逻辑。

---

## 4. 后端 API 契约与接口实现手册

所有 API 统一挂载在 Flask 服务上。除登录激活接口外，其余接口均由 `@auth_required` 装饰器拦截，校验请求头的 `Authorization: Bearer <token>`。

### 4.1 认证与基础设置接口

#### 1. 专属激活码登录/激活
- **请求**：`POST /api/auth/activate`
- **Body**：`{ "code": "WB-2026-MATH-0001", "device_name": "iPhone 15" }`
- **逻辑**：校验 `accounts` 表激活码存在、未过期且已启用；生成 40 位安全 Token 写入 `sessions` 表。
- **响应**：`200 OK`
  ```json
  {
    "ok": true,
    "token": "tk_a1b2c3d4e5f6...",
    "code": "WB-2026-MATH-0001",
    "owner": "初二李同学家"
  }
  ```

#### 2. AI Key 获取与保存（用户自配，平台零垫资）
- **获取配置**：`GET /api/ai-config`
  - 返回掩码后的配置：`{ "provider": "glm", "ready": true, "glm": { "key_masked": "d8a1***3f" } }`
- **保存配置**：`POST /api/ai-config`
  - **Body**：`{ "provider": "glm", "glm": { "key": "d8a123456789abcdef" } }`
  - 保存至当前租户的 `accounts.ai_config` 字段中。

---

### 4.2 排版机录题与人审接口

#### 1. 上传试卷照片（支持手机相机拍摄）
- **请求**：`POST /api/sheets`（`multipart/form-data`）
  - 表单项：`photo`（二进制图片文件）、`source`（来源备注，可选）
- **处理流水线**：
  1. 调用 `reviewflow.auto_relocate_exif()` 扶正拍摄角度；
  2. 压缩保存至媒体存储目录；
  3. 调用大模型视觉转录（使用该用户的 API Key）；
  4. 清洗题干前缀，剥掉卷面考点标题行；
  5. 按识别出的 `figure_boxes` 自动裁切原图几何图形；
  6. 存入 `sheets` 表，状态记为 `pending`。
- **响应**：`200 OK`，返回完整的草稿单结构（包含识别出的多道题目、题型、图形路径）。

#### 2. 手动框选重裁原图（手机触屏核心交互）
- **请求**：`POST /api/sheets/<sid>/questions/<int:no>/recrop`
- **Body**：`{ "box": [0.12, 0.34, 0.56, 0.78] }`（相对原图宽高比例 0.0~1.0）
- **处理**：读取原图，按指定坐标切图覆盖，并重写该题的 `figure_crops`。

#### 3. 人审勾选提交入库
- **请求**：`POST /api/sheets/<sid>/commit`
- **逻辑**：
  1. 遍历当前草稿单中被用户手动勾选的题目；
  2. 仅存专题的题目：**零 LLM 调用**，直接存入专题例题库，耗时 < 0.1s；
  3. 存错题本的题目：进入质量链，查重 → 解题验伪 → 提取受控知识点 → 写入 `questions` 表；
  4. 返回处置汇总（入库数、跳过重复数）。

---

### 4.3 工作台与试卷生成接口

#### 1. 工作台错题筛选与统计
- **请求**：`GET /api/stats?chapter=...&difficulty=...&mastered=0`
- **响应**：按条件聚合的题目分布数据及待练习错题清单。

#### 2. 导出打印练习卷（PDF 格式）
- **请求**：`POST /api/questions/export-pdf`
- **Body**：`{ "ids": ["q1", "q2", "q3"], "with_answer": true }`
- **处理**：调用 `engine/export_paper.py` 使用 ReportLab/Typst 排版生成矢量 PDF。
- **响应**：`{ "pdf": "/data/out/错题练习卷_20261110.pdf", "n": 3 }`，手机端收到后可直接全屏预览并调起微信打印。

---

## 5. 手机端 H5 响应式与触屏适配实操

当前阶段优先采用响应式 H5 架构，支持手机微信点开即用。在 `static/index.html` 中落地的关键技术点如下：

### 5.1 视口声明与底部操作栏沉底防遮挡

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
```

在 CSS 中增加媒体查询，手机屏幕下将底部提交栏变为**悬浮吸底**，并为 iPhone 底部小白条预留安全距离：

```css
@media (max-width: 768px) {
  .review {
    grid-template-columns: 1fr; /* 电脑端左右双栏改为手机端单列排版 */
    gap: 10px;
  }
  .commitbar {
    position: fixed;
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 85;
    border-radius: 0;
    box-shadow: 0 -2px 14px rgba(0,0,0,.1);
    padding: 10px 14px;
    padding-bottom: calc(10px + env(safe-area-inset-bottom)); /* 适配全面屏底部安全区 */
  }
  #tab-ingest {
    padding-bottom: 76px; /* 防止内容被吸底操作栏遮挡 */
  }
  .photo-wrap img {
    max-height: 280px;
    object-fit: contain;
    background: #f8f9fa;
  }
}
```

### 5.2 调起手机原生摄像头直接拍摄

在普通文件选择按钮旁，提供带有 `capture="environment"` 属性的原生相机控件：

```html
<!-- 移动端专属拍照按钮 -->
<button class="pri mobile-only" onclick="document.getElementById('camera-photo').click()">📸 手机拍照</button>
<input type="file" id="camera-photo" accept="image/*" capture="environment" class="hidden">
```
用户在手机微信中点击此按钮，会直接唤起系统后置摄像头，拍完确认后自动压入待上传队列。

### 5.3 手机触屏单指手动框选原图（Touch 事件兼容）

将原先基于鼠标的 `mousedown / mousemove / mouseup` 重构成兼容触摸事件：

```javascript
// 统一获取鼠标或单指触摸的屏幕相对坐标
function _getEvPos(e, rect) {
  const t = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || e;
  return {
    x: Math.min(Math.max((t.clientX || 0) - rect.left, 0), rect.width),
    y: Math.min(Math.max((t.clientY || 0) - rect.top, 0), rect.height)
  };
}

function startPick(ev, sid) {
  if (!pickState || !pickState.picking) return;
  ev.preventDefault();
  const pw = ev.currentTarget, img = pw.querySelector('img');
  const r = img.getBoundingClientRect();
  const pos = _getEvPos(ev, r);
  pickState.startX = pos.x; pickState.startY = pos.y; pickState.rect = r;
  
  const move = e => {
    if (e.cancelable) e.preventDefault(); // 关键：拖拽框选时阻止手机整屏滚动
    const p = _getEvPos(e, r);
    sb.style.left = Math.min(pos.x, p.x) + 'px';
    sb.style.top = Math.min(pos.y, p.y) + 'px';
    sb.style.width = Math.abs(p.x - pos.x) + 'px';
    sb.style.height = Math.abs(p.y - pos.y) + 'px';
  };

  const up = async e => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    document.removeEventListener('touchmove', move);
    document.removeEventListener('touchend', up);
    // 计算归一化坐标并上报后端 /recrop 接口
    const p = _getEvPos(e, r);
    const box = [Math.min(pos.x, p.x) / r.width, Math.min(pos.y, p.y) / r.height,
                 Math.max(pos.x, p.x) / r.width, Math.max(pos.y, p.y) / r.height];
    await sendRecrop(sid, pickState.no, box);
  };

  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
  document.addEventListener('touchmove', move, { passive: false });
  document.addEventListener('touchend', up);
}
```

---

## 6. 后续微信小程序封装指引（域名备案通过后实施）

当您在阿里云完成域名 ICP 备案后，小程序只需搭建极简外壳：

### 6.1 方案 A：小程序 Web-View 极速内嵌（半天完成）
在微信小程序中直接放一个全屏 `<web-view>` 组件，指向您的 HTTPS 域名：
```xml
<!-- pages/index/index.wxml -->
<web-view src="https://yourdomain.com/?token={{token}}"></web-view>
```
此方案由于网页端已经完成移动端竖屏适配与拍照调用，小程序几乎零开发量，两端维护一套代码即可。

### 6.2 方案 B：微信原生组件工程（体验更极致）
使用微信原生 API 实现原生导航条与拍照：
1. **拍照/相册**：调用微信原生 `wx.chooseMedia({ count: 9, mediaType: ['image'], sourceType: ['camera', 'album'] })`，支持前置相册预选与秒级压缩；
2. **公式渲染**：使用成熟开源组件 `mp-html` 搭配 `latex` 插件渲染数学公式；
3. **微信一键打印**：
   ```javascript
   wx.downloadFile({
     url: 'https://yourdomain.com/data/out/错题卷.pdf',
     success: function (res) {
       wx.openDocument({
         filePath: res.tempFilePath,
         showMenu: true // 微信内直接唤起系统菜单，可直接转发给微信好友或发送到家庭打印机
       });
     }
   });
   ```

---

## 7. ECS 生产环境部署与运维配置

### 7.1 针对 2G 内存的 Gunicorn 配置

在 2G 内存的 ECS 上，严禁开启过多的 Gunicorn Worker，建议采用 **1 个 Master + 1 个 Worker + 8 个线程** 的轻量配置，并加入 `--timeout 300` 防止多题视觉转录长耗时被判定为假死：

编辑 `/etc/systemd/system/wrongbook.service`：
```ini
[Unit]
Description=WrongBook Cloud Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/wrongbook
Environment=PYTHONPATH=/opt/wrongbook
ExecStart=/opt/wrongbook/venv/bin/gunicorn -w 1 --threads 8 --timeout 300 -b 0.0.0.0:8899 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### 7.2 数据库与数据每日自动备份脚本

在 ECS 上添加定时任务，每天凌晨 3 点自动热备份 SQLite 数据库并压缩保留 30 天：

```bash
#!/usr/bin/env bash
# /opt/wrongbook/backup_db.sh
BACKUP_DIR="/var/backups/wrongbook"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)

# 使用 SQLite 的在线备份 API 保证读写事务一致性
sqlite3 /var/lib/wrongbook/data/wrongbook.db ".backup '$BACKUP_DIR/wrongbook_$DATE.db'"
gzip "$BACKUP_DIR/wrongbook_$DATE.db"

# 清理 30 天前的旧备份
find "$BACKUP_DIR" -name "wrongbook_*.db.gz" -mtime +30 -delete
```
在 `crontab -e` 中添加：
```cron
0 3 * * * bash /opt/wrongbook/backup_db.sh >/dev/null 2>&1
```

---

## 8. 开发自测与上线检查清单（Checklist）

在交付给试用家长使用前，请按以下清单逐项验证：

- [ ] **激活码隔离验证**：在两个隐身窗口分别用两个不同激活码激活，确认 A 账号上传的照片与录入的错题在 B 账号中完全不可见；
- [ ] **自填 API Key 验证**：在设置页填写智谱 GLM 或 DeepSeek API Key，点击「测试连接」能够成功收到服务端的联通确认；
- [ ] **手机微信内拍照实测**：用真实手机在微信中打开网址，点击「📸 手机拍照」能够顺利唤起系统相机并上传；
- [ ] **触屏手指框选实测**：手机屏幕上拖拽裁剪几何图形，确认松手后图形能够准确按手指选框替换；
- [ ] **转录后不自动勾选**：转录完成后题目卡片默认未勾选，手动勾选题头复选框后底部浮动栏正确累加已选计数；
- [ ] **微信内 PDF 导出实测**：工作台勾选 3 道题导出 PDF，能在手机端正常预览并调起微信转发或打印；
- [ ] **B 站视频完全剥离**：确认题卡、弹窗、例题卡中无任何视频外链输入框与播放标签。
