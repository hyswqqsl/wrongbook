# Windows 打包指南（R4）

spec 与代码跨平台通用，在一台 Windows 机器上执行以下步骤即可产出 Windows 安装包。
（本机是 mac，Windows 构建需要一台 Windows 实机/虚拟机；这是唯一无法在本机代劳的部分。）

## ⚡ 一键打包（推荐）

拷贝工程到 Windows 机器后（不要带 `data/users/`、`data/ai_config.json`、`key.env`、`data/license*`），
双击或在 cmd/PowerShell 里执行：

```bat
packaging\build_windows.bat
```

脚本自动完成：首次自动建 venv 装依赖 → 全量回归门禁 → PyInstaller → 复制静态资源 →
白名单校验（敏感文件绝不入包）→ 压 `dist\AI错题本-v<版本>-win64.zip`。

**免解压实测**：`dist\AI错题本\` 就是解压后的最终目录，构建完直接运行
`dist\AI错题本\AI错题本.exe` 冒烟（也可 `packaging\build_windows.bat run` 构建完自动启动）。

### ⚠️ 编码坑（改脚本前务必看）

`cmd.exe` 解释 `.bat` 时**只认当前控制台代码页**，且**完全不支持 BOM**。
UTF-8 编码的 `.bat` 里只要出现非 ASCII 行（哪怕只是 `echo` 文案），叠加 `chcp 65001`
就会让 cmd 的字节偏移与字符偏移错位，整行被从中间截断当成命令执行，报：

```
'I' is not recognized as an internal or external command,
operable program or batch file.
```

因此本仓库的约定：

| 文件 | 编码约束 | 原因 |
|---|---|---|
| `packaging/build_windows.bat` | **纯 ASCII**（无 BOM） | cmd 解析安全；只做 venv 引导 + 调 `build_win.py` |
| `packaging/build_win.py` | UTF-8 | 中文文案与中文目录名「AI错题本」全在这里；Python 走宽字符 API，任何代码页都不乱码 |

改 `build_windows.bat` 时**不要写中文**，也不要加 `chcp 65001`。
中文提示统一写在 `build_win.py` 的 `log()` 里。

以下手动步骤与脚本等价（排障时用）。

## 1. 准备环境

1. 装 Python 3.11+（官网安装包，勾选 **Add python.exe to PATH**）
2. 拷贝工程（**不要带** `data/users/`、`data/ai_config.json`、`key.env`、`data/license*`）

## 2. 构建（cmd/PowerShell，在工程目录）

```bat
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller

:: 门禁：回归必须全绿
.venv\Scripts\python -m pytest tests -q

:: 打包
.venv\Scripts\python -m PyInstaller packaging\wrongbook.spec --noconfirm --clean
```

## 3. 组装发布目录

产物在 `dist\AI错题本\`（exe + _internal\）。把静态资源复制进去：

```bat
xcopy /E /I /Y static dist\AI错题本\static\; xcopy /E /I /Y engine dist\AI错题本\engine\; xcopy /E /I /Y data\kb dist\AI错题本\data\kb\
```

> 手动复制时若控制台代码页不是中文（GBK/936），`xcopy` 的中文路径会被转义坏，
> 直接用脚本里的 Python 版（`packaging/build_win.py`，走宽字符 API）更稳：
> `.venv\Scripts\python packaging\build_win.py`

检查包内**不得出现**：`ai_config.json`、`key.env`、`license*.pem`、`wrongbook.json`、`users` 目录（与 mac 构建脚本的白名单一致）。

然后整体压缩 `dist\AI错题本\` 为 `AI错题本-v1.4.0-win64.zip`。
```
tar -a -c -f AI错题本-v1.4.0-win64.zip -C dist AI错题本
```

## 4. 用户侧预期（写进使用说明）

- 解压到任意目录（如桌面），双击 `AI错题本.exe`
- **SmartScreen 提示"已保护你的电脑"**：点"更多信息 → 仍要运行"（未购买代码签名证书的通病，说明文档要有截图）
- 首次运行 Windows 防火墙弹窗：允许（服务只绑 127.0.0.1，仅本机访问）
- 浏览器自动打开激活页 → 输码激活 → ⚙ 设置里配 GLM key → 开始使用
- 数据在 `%APPDATA%\WrongBook\`（升级覆盖安装不丢数据；重装系统前先跑「备份错题本」脚本）

## 5. Windows 特有回归点（构建后手动走查）

| 项 | 方法 |
|---|---|
| 录入→转录→人审→入库 全链 | 拍一张真实试卷照走一遍 |
| 导出 PDF/Word | matplotlib Agg 渲染公式 + 中文字体目检 |
| GeoGebra 按钮 | **不应出现**（已移除，UI 与包内都不该有） |
| 激活 → 心跳 → 拔网 → 黄条 → 恢复 | 同 mac 冒烟清单 |
| 升级覆盖 | 装新版覆盖旧目录，确认题库/照片/激活状态完好 |

## 6. ECS上开启，停用key

```
[root@iZk1a0m08ukt0odm2i4vpnZ ~]# cd /opt/wrongbook-license/
[root@iZk1a0m08ukt0odm2i4vpnZ wrongbook-license]# venv/bin/python license_admin.py revoke --code WB-2UFW-DP8R-8Q9W
[root@iZk1a0m08ukt0odm2i4vpnZ ~]# cd /opt/wrongbook-license/
[root@iZk1a0m08ukt0odm2i4vpnZ wrongbook-license]# venv/bin/python license_admin.py enable --code WB-2UFW-DP8R-8Q9W


# ① 先看这个码绑定了哪些设备
venv/bin/python license_admin.py list --code WB-2UFW-DP8R-8Q9W

# ② 清空该码的设备绑定
venv/bin/python license_admin.py reset-seats --code WB-2UFW-DP8R-8Q9W

# 生成新的key
venv/bin/python license_admin.py issue --owner 张同学家

# 重置
venv/bin/python license_admin.py reset-seats --code WB-XXXX-XXXX-XXXX
```

把码改为限期/延期	extend --code WB-… --days 180
吊销码（锁定家长端）	revoke --code WB-…
重启服务	systemctl restart wrongbook-license
看服务日志	journalctl -u wrongbook-license -n 50

# 发布
- 1.4.1：
增加1-9年级上下册数学知识点收集，在录入时先要明确选择年级（下次进入会记忆），出题增加乱序排序（是阿郑呀 网友），明确了智普api key接入方式。