#!/usr/bin/env bash
# WrongBook 激活验证服务一键部署（在 ECS 上执行）
# 兼容 Debian/Ubuntu(apt) 与 Alibaba Cloud Linux/CentOS/Anolis/Rocky(dnf/yum)
# 前置：本目录已 scp 到 /opt/wrongbook-license/，私钥已放到 /etc/wrongbook-license/private.pem
set -e

echo "==> 识别系统与包管理器"
if command -v apt-get >/dev/null 2>&1; then PM=apt
elif command -v dnf >/dev/null 2>&1; then PM=dnf
elif command -v yum >/dev/null 2>&1; then PM=yum
else
  echo "❌ 未识别到 apt-get/dnf/yum。请把以下输出发给开发者："; cat /etc/os-release 2>/dev/null; exit 1
fi
echo "    包管理器：$PM"

echo "==> 安装系统依赖"
case $PM in
  apt) apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip curl >/dev/null ;;
  dnf) dnf install -y -q python3 python3-pip curl ;;
  yum) yum install -y -q python3 python3-pip curl ;;
esac

echo "==> 检查 Python 版本（需 ≥3.8）"
PY=python3
VER=$($PY -c 'import sys;print("%d.%d"%(sys.version_info[0],sys.version_info[1]))' 2>/dev/null || echo "none")
OK=$($PY -c 'import sys;print(1 if sys.version_info>=(3,8) else 0)' 2>/dev/null || echo 0)
if [ "$OK" != "1" ]; then
  echo "    python3 = $VER 过旧，尝试安装 python3.11 …"
  case $PM in
    apt) apt-get install -y -qq python3.11 python3.11-venv >/dev/null && PY=python3.11 ;;
    dnf) dnf install -y -q python3.11 python3.11-pip >/dev/null 2>&1 && PY=python3.11 ;;
    yum) yum install -y -q python3.11 >/dev/null 2>&1 && PY=python3.11 ;;
  esac
  if ! command -v $PY >/dev/null 2>&1; then
    echo "❌ 仓库里没有 python3.11。请手动装 ≥3.8 的 Python 后重跑本脚本，或把 $(cat /etc/os-release 2>/dev/null | head -2) 发给开发者。"
    exit 1
  fi
fi
echo "    使用 Python：$PY ($($PY --version 2>&1))"

echo "==> 建目录与虚拟环境"
mkdir -p /var/lib/wrongbook-license /etc/wrongbook-license
cd /opt/wrongbook-license
rm -rf venv
$PY -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q flask cryptography gunicorn

echo "==> 检查私钥"
if [ ! -f /etc/wrongbook-license/private.pem ]; then
  echo "❌ 缺私钥：请先执行  scp data/license_ecs_private.pem root@147.139.167.18:/etc/wrongbook-license/private.pem"
  exit 1
fi
chmod 600 /etc/wrongbook-license/private.pem

echo "==> version.json（升级检查初始值）"
[ -f version.json ] || cat > version.json <<'JSON'
{"latest": "1.4.0", "url": "", "notes": "", "min_supported": "1.4.0"}
JSON

echo "==> systemd 常驻"
cp -f wrongbook-license.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wrongbook-license
sleep 1.5

echo "==> 探活"
curl -s http://127.0.0.1:8901/healthz && echo "  ✓ 服务已启动（0.0.0.0:8901）"
echo "==> 签发第一枚测试码"
./venv/bin/python license_admin.py issue --owner 冒烟测试 || true
echo
echo "完成。别忘了：阿里云控制台 → 安全组 → 放行 TCP 8901 入方向。"
