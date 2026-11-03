#!/bin/bash
# AI错题本常驻启动（Terminal.app，脱离 IDE shell 生命周期）
cd "$(dirname "$0")"
[ -z "$DEEPSEEK_API_KEY" ] && [ -f key.env ] && export $(grep -o 'DEEPSEEK_API_KEY=[^ "]*' key.env)
exec .venv/bin/python app.py
