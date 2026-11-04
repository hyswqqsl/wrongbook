#!/bin/bash
# AI错题本启动（自包含工程）：cd 到工程目录，用自带 .venv 启动 Flask 并打开浏览器
cd "$(dirname "$0")"
# key 注入：key.env（开发者本机，gitignore）→ 环境变量；正式发布用网页「⚙ 设置」或环境变量，无需 key.env
[ -f key.env ] && set -a && . ./key.env && set +a
# GeoGebra bundle 自检（仅提示不阻断；缺失时录入页「🧠GeoGebra重画」不可用）
[ -f static/vendor/geogebra/deployggb.js ] || echo "⚠ GeoGebra bundle 未就绪，运行 tools/fetch_geogebra.sh 下载（仅影响图形重画功能）"
URL="http://127.0.0.1:8899"
( sleep 1.2; open "$URL" ) >/dev/null 2>&1 &
exec .venv/bin/python app.py "$@"   # 透传参数：--debug 等
