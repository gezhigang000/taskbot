#!/bin/bash
# FRP 服务端一键安装脚本
# 用法:
#   本地执行: bash install.sh
#   远程执行: ssh root@your-server 'bash -s' < install.sh
#
# 支持系统: Ubuntu/Debian, CentOS/RHEL, Alpine Linux
# 支持架构: x86_64 (amd64), aarch64 (arm64)

set -e

# 配置
FRP_VERSION="0.61.1"
INSTALL_DIR="/usr/local/frp"
CONFIG_DIR="/etc/frp"
LOG_FILE="/var/log/frps.log"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
    elif [ -f /etc/redhat-release ]; then
        OS="centos"
    else
        OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    fi

    case "$OS" in
        ubuntu|debian)
            PKG_MANAGER="apt-get"
            PKG_UPDATE="apt-get update -qq"
            ;;
        centos|rhel|fedora|rocky|almalinux)
            PKG_MANAGER="yum"
            PKG_UPDATE="yum makecache -q"
            ;;
        alpine)
            PKG_MANAGER="apk"
            PKG_UPDATE="apk update"
            ;;
        *)
            warn "未知操作系统: $OS，尝试继续安装..."
            PKG_MANAGER=""
            ;;
    esac

    info "检测到系统: $OS ${OS_VERSION:-}"
}

# 检测架构
detect_arch() {
    ARCH=$(uname -m)
    case $ARCH in
        x86_64)
            ARCH="amd64"
            ;;
        aarch64|arm64)
            ARCH="arm64"
            ;;
        armv7l)
            ARCH="arm"
            ;;
        *)
            error "不支持的架构: $ARCH"
            ;;
    esac
    info "检测到架构: $ARCH"
}

# 安装依赖
install_deps() {
    local deps="wget tar"

    for dep in $deps; do
        if ! command -v $dep &> /dev/null; then
            info "安装依赖: $dep"
            if [ -n "$PKG_MANAGER" ]; then
                $PKG_UPDATE 2>/dev/null || true
                $PKG_MANAGER install -y $dep
            else
                error "请手动安装 $dep"
            fi
        fi
    done
}

# 检测 FRP 是否已安装
check_installed() {
    if [ -f "$INSTALL_DIR/frps" ]; then
        CURRENT_VERSION=$("$INSTALL_DIR/frps" -v 2>/dev/null || echo "unknown")
        info "检测到已安装 FRP: $CURRENT_VERSION"

        if [ "$CURRENT_VERSION" = "$FRP_VERSION" ]; then
            info "版本已是最新 (v$FRP_VERSION)"
            return 0
        else
            warn "将从 $CURRENT_VERSION 升级到 v$FRP_VERSION"
            return 1
        fi
    fi
    return 1
}

# 下载 FRP
download_frp() {
    local url="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${ARCH}.tar.gz"
    local mirror_url="https://ghproxy.com/$url"

    info "下载 FRP v${FRP_VERSION}..."

    cd /tmp
    rm -rf frp.tar.gz frp_${FRP_VERSION}_linux_${ARCH} 2>/dev/null || true

    # 尝试直接下载，如果失败则尝试镜像
    if wget -q --timeout=30 "$url" -O frp.tar.gz 2>/dev/null; then
        info "从 GitHub 下载成功"
    elif wget -q --timeout=30 "$mirror_url" -O frp.tar.gz 2>/dev/null; then
        info "从镜像下载成功"
    else
        error "下载失败，请检查网络连接"
    fi

    tar xzf frp.tar.gz
    cd "frp_${FRP_VERSION}_linux_${ARCH}"
}

# 安装 FRP
install_frp() {
    info "安装 FRP 到 $INSTALL_DIR..."

    mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

    # 停止旧服务
    systemctl stop frps 2>/dev/null || true

    # 复制文件
    cp frps "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/frps"

    info "FRP 安装完成"
}

# 配置 FRP
configure_frp() {
    if [ -f "$CONFIG_DIR/frps.toml" ]; then
        info "配置文件已存在，保留现有配置"
        return
    fi

    info "创建默认配置文件..."

    # 生成随机 token
    TOKEN=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)
    ADMIN_PASS=$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 12)

    cat > "$CONFIG_DIR/frps.toml" << EOF
# FRP 服务端配置
# 文档: https://gofrp.org/docs/

bindPort = 7000
vhostHTTPPort = 8080

# 认证 (客户端需要使用相同的 token)
auth.method = "token"
auth.token = "$TOKEN"

# 管理面板 (仅本地访问)
webServer.addr = "127.0.0.1"
webServer.port = 7500
webServer.user = "admin"
webServer.password = "$ADMIN_PASS"

# 日志
log.to = "$LOG_FILE"
log.level = "info"
log.maxDays = 7
EOF

    chmod 600 "$CONFIG_DIR/frps.toml"

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  FRP 配置信息 (请妥善保存)${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "  认证令牌: ${YELLOW}$TOKEN${NC}"
    echo -e "  管理面板: http://localhost:7500"
    echo -e "  管理账号: admin"
    echo -e "  管理密码: ${YELLOW}$ADMIN_PASS${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
}

# 配置 systemd 服务
setup_service() {
    info "配置 systemd 服务..."

    cat > /etc/systemd/system/frps.service << EOF
[Unit]
Description=FRP Server Service
After=network.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/frps -c ${CONFIG_DIR}/frps.toml
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable frps
    systemctl restart frps

    sleep 2

    if systemctl is-active --quiet frps; then
        info "FRP 服务启动成功"
    else
        error "FRP 服务启动失败，请检查日志: journalctl -u frps"
    fi
}

# 配置防火墙
setup_firewall() {
    info "配置防火墙..."

    # UFW (Ubuntu/Debian)
    if command -v ufw &> /dev/null; then
        ufw allow 7000/tcp 2>/dev/null || true
        ufw allow 8080/tcp 2>/dev/null || true
        info "UFW 规则已添加"
    fi

    # firewalld (CentOS/RHEL)
    if command -v firewall-cmd &> /dev/null; then
        firewall-cmd --permanent --add-port=7000/tcp 2>/dev/null || true
        firewall-cmd --permanent --add-port=8080/tcp 2>/dev/null || true
        firewall-cmd --reload 2>/dev/null || true
        info "firewalld 规则已添加"
    fi

    # iptables
    if command -v iptables &> /dev/null && ! command -v ufw &> /dev/null && ! command -v firewall-cmd &> /dev/null; then
        iptables -I INPUT -p tcp --dport 7000 -j ACCEPT 2>/dev/null || true
        iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
        info "iptables 规则已添加"
    fi
}

# 清理
cleanup() {
    info "清理临时文件..."
    rm -rf /tmp/frp.tar.gz /tmp/frp_${FRP_VERSION}_linux_${ARCH} 2>/dev/null || true
}

# 显示状态
show_status() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  FRP 服务端安装完成${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "  版本: v$FRP_VERSION"
    echo "  状态: $(systemctl is-active frps)"
    echo "  配置: $CONFIG_DIR/frps.toml"
    echo "  日志: $LOG_FILE"
    echo ""
    echo "  常用命令:"
    echo "    查看状态: systemctl status frps"
    echo "    查看日志: journalctl -u frps -f"
    echo "    重启服务: systemctl restart frps"
    echo "    停止服务: systemctl stop frps"
    echo ""
    echo "  开放端口:"
    echo "    7000 - FRP 客户端连接端口"
    echo "    8080 - HTTP 代理端口"
    echo ""
}

# 主函数
main() {
    echo ""
    echo "========================================"
    echo "  FRP 服务端一键安装脚本 v${FRP_VERSION}"
    echo "========================================"
    echo ""

    # 检查 root 权限
    if [ "$EUID" -ne 0 ]; then
        error "请使用 root 用户运行此脚本"
    fi

    detect_os
    detect_arch
    install_deps

    # 检查是否需要安装/升级
    if check_installed; then
        read -p "是否重新安装? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            info "取消安装"
            exit 0
        fi
    fi

    download_frp
    install_frp
    configure_frp
    setup_service
    setup_firewall
    cleanup
    show_status
}

main "$@"
