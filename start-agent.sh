#!/bin/bash
# 启动本地代理
#
# 用法:
#   ./start-agent.sh -s http://服务器地址:8080 [-n 代理名称] [-w 工作目录]
#
# 示例:
#   ./start-agent.sh -s http://relay.example.com:8080
#   ./start-agent.sh -s http://relay.example.com:8080 -n "我的电脑"
#

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "请先运行: bash install.sh"
    exit 1
fi

source venv/bin/activate
python agent/agent.py "$@"
