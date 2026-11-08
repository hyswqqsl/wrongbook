# R3 部署指南（ECS：147.139.167.18，纯 HTTP:8901，免域名）

服务端三件套在本目录：`license_server.py`（服务）/ `license_admin.py`（发码 CLI）/
`wrongbook-license.service`（systemd）+ `setup_server.sh`（一键部署）+ 本指南。
客户端（你本机 wrongbook 工程）已烧入正式公钥与服务地址，无需再改。

## 0. 前置（一次性）

阿里云控制台 → ECS → 安全组 → **添加入方向规则：TCP 8901，源 0.0.0.0/0**（不放开客户端连不上）。

## 1. 上传（本机执行）

```bash
ssh root@147.139.167.18 "mkdir -p /opt/wrongbook-license /etc/wrongbook-license"
scp -r ecs/* root@147.139.167.18:/opt/wrongbook-license/
scp data/license_ecs_private.pem root@147.139.167.18:/etc/wrongbook-license/private.pem
```

## 2. 部署（ECS 上执行）

```bash
ssh root@147.139.167.18
bash /opt/wrongbook-license/setup_server.sh
```

脚本做的事：装 python3-venv → 建 venv 装 flask/cryptography/gunicorn → 校验私钥（0600）→
写 version.json → systemd 启动并设开机自启 → 探活 → 打印第一枚测试码。

## 3. 发码与验证

```bash
# ECS 上：
cd /opt/wrongbook-license
venv/bin/python license_admin.py issue --owner 张同学家            # 永久码
venv/bin/python license_admin.py issue --owner 体验家长 --days 7   # 7 天试用码
venv/bin/python license_admin.py list
```

把码微信发给家长 → 家长在激活页输入即可。

## 4. 本机真实验证（可选）

```bash
cd wrongbook 工程
WRONGBOOK_LICENSE=on .venv/bin/python app.py
# 浏览器激活页输入 ECS 上签发的真实码 → 激活成功后 /api/license 显示 state=ok
```

## 5. 日常运维速查（都在 ECS 上）

| 操作 | 命令 |
|---|---|
| 查码/绑定设备 | `venv/bin/python license_admin.py list` |
| 家长重装系统占位 | `venv/bin/python license_admin.py reset-seats --code WB-…` |
| 续费 | `venv/bin/python license_admin.py extend --code WB-… --days 365`（家长端下次心跳即生效） |
| 吊销（退款/异常） | `venv/bin/python license_admin.py revoke --code WB-…`（客户端心跳 401 即锁） |
| 追踪异常尝试 | `venv/bin/python license_admin.py audit` |
| 看服务日志 | `journalctl -u wrongbook-license -n 50` |
| 重启服务 | `systemctl restart wrongbook-license` |

## 6. 发新版（配合客户端升级检查）

改 `/opt/wrongbook-license/version.json`（latest/url/notes/min_supported）→ 客户端启动/每 7 天
检查到新版本 → 页面横幅提示下载。安装包放 ECS 同机即可（如 /opt/wrongbook-license/dl/，
再起一个静态服务或直接微信发文件）。

## 7. 备份

```bash
# 每日 SQLite 备份（加 crontab）
0 3 * * * sqlite3 /var/lib/wrongbook-license/license.db ".backup /var/backups/license-$(date +\%F).db"
```

数据库只有激活码与设备绑定（无业务数据），丢了可重发码，备份是锦上添花。

## 安全边界（如实说明）

纯 HTTP：激活码明文过网——靠长随机码（不可穷举）+ 接口限流 + 2 席位上限兜底；
残余风险是公共 WiFi 被动嗅探（家庭宽带场景概率很低，损失上限=占用席位，list 可见可吊销）。
服务端可解包客户端取出公钥签名验证逻辑不影响安全性（私钥只在 ECS）。
未来要升级 HTTPS：重签自签证书 + 客户端 CA_PEM_PATH 指向随包公钥即可，接口契约不变。
