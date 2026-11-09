# Windows 打包指南（R4）

spec 与代码跨平台通用，在一台 Windows 机器上执行以下步骤即可产出 Windows 安装包。
（本机是 mac，Windows 构建需要一台 Windows 实机/虚拟机；这是唯一无法在本机代劳的部分。）

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
xcopy /E /I static  dist\AI错题本\static\
xcopy /E /I engine  dist\AI错题本\engine\
xcopy /E /I data\kb dist\AI错题本\data\kb\
xcopy /E /I /Y static dist\AI错题本\static\; xcopy /E /I /Y engine dist\AI错题本\engine\; xcopy /E /I /Y data\kb dist\AI错题本\data\kb\
```

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
[root@iZk1a0m08ukt0odm2i4vpnZ wrongbook-license]# venv/bin/python license_admin.py revoke --code WB-T6HZ-FHF8-VQ9A
[root@iZk1a0m08ukt0odm2i4vpnZ ~]# cd /opt/wrongbook-license/
[root@iZk1a0m08ukt0odm2i4vpnZ wrongbook-license]# venv/bin/python license_admin.py enable --code WB-T6HZ-FHF8-VQ9A
```