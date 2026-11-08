#!/usr/bin/env bash
# AI错题本 mac 打包（R4）
# 产物：dist/AI错题本-v<版本>-mac.zip（内含 AI错题本.app，拖入"应用程序"即装）
# 门禁：全量回归必须全绿；包内白名单校验（本地数据/密钥绝不入包）
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python

echo "==> 门禁：全量回归"
$PY -m pytest tests/ -q

VERSION=$($PY -c "import paths; print(paths.APP_VERSION)")
echo "==> 版本：v$VERSION"

echo "==> PyInstaller 构建"
$PY -m PyInstaller packaging/wrongbook.spec --noconfirm --clean >/dev/null
APP="dist/AI错题本.app"
[ -d "$APP" ] || { echo "❌ 构建产物缺失：$APP"; exit 1; }

echo "==> 复制静态资源到 .app（程序目录布局：static/ engine/ data/kb 种子）"
MACOS="$APP/Contents/MacOS"
rm -rf "$MACOS/static" "$MACOS/engine" "$MACOS/data"
cp -R static "$MACOS/static"
cp -R engine "$MACOS/engine"
mkdir -p "$MACOS/data"
cp -R data/kb "$MACOS/data/kb"

echo "==> 白名单校验（本地数据/密钥绝不入包）"
LEAK=$(find "$APP" \( -name 'ai_config.json' -o -name 'key.env' \
    -o -name 'license*.pem' -o -name 'wrongbook.json' -o -name 'drafts.json' \
    -o -name 'users.json' -o -name 'topics.json' \
    -o -iname '*geogebra*' \) -print 2>/dev/null | head -3 || true)
if [ -n "$LEAK" ]; then
  echo "❌ 包内发现敏感文件：$LEAK"; exit 1
fi
echo "    干净 ✓"

echo "==> 打 zip"
cd dist
rm -f "AI错题本-v$VERSION-mac.zip"
zip -qry "AI错题本-v$VERSION-mac.zip" "AI错题本.app"
cd ..
ls -lh "dist/AI错题本-v$VERSION-mac.zip"

echo
echo "完成：dist/AI错题本-v$VERSION-mac.zip"
echo "冒烟：open dist/AI错题本.app → 应自动开浏览器进入激活页（数据目录：~/Library/Application Support/WrongBook/）"
