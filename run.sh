#!/bin/bash
# AI错题本启动（自包含工程）：cd 到工程目录，用自带 .venv 启动 Flask 并打开浏览器
cd "$(dirname "$0")"
[ -z "$DEEPSEEK_API_KEY" ] && [ -f key.env ] && export $(grep -o 'DEEPSEEK_API_KEY=[^ "]*' key.env)
URL="http://127.0.0.1:8899"
( sleep 1.2; open "$URL" ) >/dev/null 2>&1 &
exec .venv/bin/python app.py
