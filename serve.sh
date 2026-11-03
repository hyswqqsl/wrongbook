#!/bin/bash
# AI错题本常驻启动（Terminal.app，脱离 IDE shell 生命周期）
cd "$(dirname "$0")"
# key 注入：key.env（开发者本机，gitignore）→ 环境变量；正式发布用网页「⚙ 设置」或环境变量，无需 key.env
[ -f key.env ] && set -a && . ./key.env && set +a
exec .venv/bin/python app.py
