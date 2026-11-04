#!/usr/bin/env bash
# tools/fetch_geogebra.sh —— 下载 GeoGebra Math Apps Bundle 到 static/vendor/geogebra/
#
# 为什么不进 git：bundle 几十 MB（DESIGN.md D22）；代码自包含、大资源按需取。
# 用法：tools/fetch_geogebra.sh        （幂等：已就绪直接退出）
# 下载源（官方）：https://download.geogebra.org/package/geogebra-math-apps-bundle
# 许可提示：GeoGebra Apps 免费仅限非商业用途，需保留官方署名（DESIGN.md D21）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/static/vendor/geogebra"
URL="https://download.geogebra.org/package/geogebra-math-apps-bundle"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -f "$DEST/deployggb.js" ]; then
  echo "✓ GeoGebra bundle 已就绪: $DEST"
  exit 0
fi

echo "↓ 下载 Math Apps Bundle（约几十 MB，请耐心等待）..."
curl -fL --retry 2 "$URL" -o "$WORK/bundle.zip"

echo "… 解压并规整目录结构"
unzip -q -o "$WORK/bundle.zip" -d "$WORK/x"
# 官方 zip 内部层级随版本变化：定位 deployggb.js，其所在目录即 bundle 根
SRC="$(dirname "$(find "$WORK/x" -name deployggb.js -print 2>/dev/null | head -1)")"
[ -n "$SRC" ] || { echo "✗ zip 内未找到 deployggb.js，结构可能已变化，请手动检查 $WORK/x"; exit 1; }

mkdir -p "$DEST"
# bundle 根含 deployggb.js + HTML5/5.0/（web3d 渲染器等），整体拷入目标
cp -R "$SRC/." "$DEST/"
[ -d "$DEST/HTML5/5.0" ] || { echo "✗ 未找到 HTML5/5.0（web3d），请检查 $DEST"; exit 1; }
echo "✓ 就绪: $DEST （deployggb.js + HTML5/5.0/web3d）"
