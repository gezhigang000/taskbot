#!/bin/bash
# 启动图形化客户端
#
# 用法:
#   ./start-gui.sh
#

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "请先运行: bash install.sh"
    exit 1
fi

source venv/bin/activate
python agent/gui.py "$@"
