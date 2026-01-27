#!/bin/bash
# 启动中继服务器

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "请先运行: bash install.sh"
    exit 1
fi

source venv/bin/activate
python relay/server.py "$@"
