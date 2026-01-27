#!/bin/bash
#
# Claude Code Remote - 一键安装脚本
#
# 用法:
#   curl -fsSL https://your-domain/install.sh | bash
#
# 或者本地运行:
#   bash install.sh
#

set -e

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Claude Code Remote - 一键安装                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# 检测操作系统
OS="$(uname -s)"
case "$OS" in
    Linux*)  OS_TYPE="linux";;
    Darwin*) OS_TYPE="mac";;
    *)       OS_TYPE="unknown";;
esac

echo "检测到系统: $OS_TYPE"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python3，请先安装 Python 3.8+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Python 版本: $PYTHON_VERSION"

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo ""
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境并安装依赖
echo "安装依赖..."
source venv/bin/activate
pip install -q --upgrade pip
pip install -q fastapi uvicorn websockets qrcode

echo ""
echo "✓ 安装完成！"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "下一步操作:"
echo ""
echo "  1. 启动中继服务器 (在有公网IP的服务器上):"
echo "     ./start-relay.sh"
echo ""
echo "  2. 启动本地代理 (在有Claude Code的电脑上):"
echo "     ./start-agent.sh -s http://服务器IP:8080"
echo ""
echo "     代理会自动注册并显示二维码，用手机扫描即可访问"
echo ""
echo "════════════════════════════════════════════════════════════════"
