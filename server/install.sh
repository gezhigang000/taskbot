#!/bin/bash
# FRP 服务端一键安装脚本
# 用法: ssh root@taskbot.com.cn 'bash -s' < install.sh

set -e

FRP_VERSION="0.61.1"
INSTALL_DIR="/usr/local/frp"
CONFIG_DIR="/etc/frp"

echo "=== 安装 FRP 服务端 v${FRP_VERSION} ==="

# 检测架构
ARCH=$(uname -m)
case $ARCH in
    x86_64)  ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    *)       echo "不支持的架构: $ARCH"; exit 1 ;;
esac

OS="linux"
URL="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_${OS}_${ARCH}.tar.gz"

# 下载
echo "下载: $URL"
cd /tmp
wget -q "$URL" -O frp.tar.gz
tar xzf frp.tar.gz
cd "frp_${FRP_VERSION}_${OS}_${ARCH}"

# 安装
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"
cp frps "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/frps"

# 配置
if [ ! -f "$CONFIG_DIR/frps.toml" ]; then
    cp "$(dirname "$0")/frps.toml" "$CONFIG_DIR/frps.toml" 2>/dev/null || cat > "$CONFIG_DIR/frps.toml" << 'EOF'
bindPort = 7000
vhostHTTPPort = 8080

auth.method = "token"
auth.token = "change-me-to-a-secure-token"

webServer.addr = "127.0.0.1"
webServer.port = 7500
webServer.user = "admin"
webServer.password = "admin123"

log.to = "/var/log/frps.log"
log.level = "info"
log.maxDays = 7
EOF
    echo "请修改 $CONFIG_DIR/frps.toml 中的 auth.token"
fi

# systemd 服务
cat > /etc/systemd/system/frps.service << EOF
[Unit]
Description=FRP Server
After=network.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/frps -c ${CONFIG_DIR}/frps.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable frps
systemctl restart frps

# 清理
rm -rf /tmp/frp.tar.gz /tmp/frp_${FRP_VERSION}_${OS}_${ARCH}

echo ""
echo "=== 安装完成 ==="
echo "  FRP 服务: systemctl status frps"
echo "  配置文件: $CONFIG_DIR/frps.toml"
echo "  管理面板: http://localhost:7500"
echo ""
echo "注意: 请确保防火墙开放端口 7000 和 8080"
