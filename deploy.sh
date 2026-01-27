#!/bin/bash
#
# Claude Code Relay Server - 一键部署脚本
# 用法:
#   ./deploy.sh start    # 启动服务
#   ./deploy.sh stop     # 停止服务
#   ./deploy.sh restart  # 重启服务
#   ./deploy.sh status   # 查看状态
#   ./deploy.sh logs     # 查看日志
#

set -e

# 配置
APP_NAME="relay-server"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
RELAY_DIR="$APP_DIR/relay"
PID_FILE="$APP_DIR/relay.pid"
LOG_FILE="$APP_DIR/relay.log"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
HEALTH_URL="http://127.0.0.1:$PORT/health"

# 自动检测虚拟环境
if [ -z "$PYTHON" ]; then
    if [ -f "$APP_DIR/venv/bin/python" ]; then
        PYTHON="$APP_DIR/venv/bin/python"
    elif [ -f "$APP_DIR/.venv/bin/python" ]; then
        PYTHON="$APP_DIR/.venv/bin/python"
    else
        PYTHON="python3"
    fi
fi

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查依赖
check_deps() {
    if ! command -v $PYTHON &> /dev/null; then
        log_error "Python3 未安装"
        exit 1
    fi

    if [ ! -f "$RELAY_DIR/server.py" ]; then
        log_error "找不到 relay/server.py"
        exit 1
    fi
}

# 获取进程 PID
get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        # 尝试通过进程名查找
        pgrep -f "python.*relay/server.py" 2>/dev/null || echo ""
    fi
}

# 检查进程是否运行
is_running() {
    local pid=$(get_pid)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

# 健康检查
health_check() {
    local max_attempts=${1:-10}
    local attempt=1

    log_info "执行健康检查..."

    while [ $attempt -le $max_attempts ]; do
        if curl -s --connect-timeout 2 "$HEALTH_URL" > /dev/null 2>&1; then
            local response=$(curl -s "$HEALTH_URL")
            log_info "健康检查通过: $response"
            return 0
        fi

        log_warn "等待服务启动... ($attempt/$max_attempts)"
        sleep 1
        attempt=$((attempt + 1))
    done

    log_error "健康检查失败"
    return 1
}

# 启动服务
do_start() {
    check_deps

    if is_running; then
        log_warn "服务已在运行 (PID: $(get_pid))"
        return 0
    fi

    log_info "启动 $APP_NAME..."
    log_info "监听地址: $HOST:$PORT"
    log_info "日志文件: $LOG_FILE"

    # 切换到项目目录
    cd "$APP_DIR"

    # 后台启动
    nohup $PYTHON -u "$RELAY_DIR/server.py" --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
    local pid=$!
    echo $pid > "$PID_FILE"

    log_info "进程已启动 (PID: $pid)"

    # 健康检查
    sleep 2
    if health_check 10; then
        log_info "服务启动成功!"
        echo ""
        echo "╔══════════════════════════════════════════════════════════╗"
        echo "║              服务已启动                                  ║"
        echo "╠══════════════════════════════════════════════════════════╣"
        echo "║  地址: http://$HOST:$PORT                              ║"
        echo "║  管理后台: http://$HOST:$PORT/admin                    ║"
        echo "║  用户名: admin                                          ║"
        echo "║  密码: taskbot2024                                      ║"
        echo "╚══════════════════════════════════════════════════════════╝"
    else
        log_error "服务启动失败，请检查日志: $LOG_FILE"
        do_stop
        exit 1
    fi
}

# 停止服务
do_stop() {
    log_info "停止 $APP_NAME..."

    local pid=$(get_pid)

    if [ -z "$pid" ]; then
        log_warn "服务未运行"
        rm -f "$PID_FILE"
        return 0
    fi

    # 优雅停止
    kill "$pid" 2>/dev/null || true

    # 等待进程结束
    local count=0
    while kill -0 "$pid" 2>/dev/null && [ $count -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done

    # 强制停止
    if kill -0 "$pid" 2>/dev/null; then
        log_warn "强制停止进程..."
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    log_info "服务已停止"
}

# 重启服务
do_restart() {
    log_info "重启 $APP_NAME..."
    do_stop
    sleep 2
    do_start
}

# 查看状态
do_status() {
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  $APP_NAME 状态"
    echo "═══════════════════════════════════════════════════════════"

    if is_running; then
        local pid=$(get_pid)
        echo -e "  状态:   ${GREEN}运行中${NC}"
        echo "  PID:    $pid"
        echo "  端口:   $PORT"

        # 获取健康信息
        if curl -s --connect-timeout 2 "$HEALTH_URL" > /dev/null 2>&1; then
            local health=$(curl -s "$HEALTH_URL")
            local agents=$(echo "$health" | grep -o '"agents_online":[0-9]*' | cut -d':' -f2)
            local clients=$(echo "$health" | grep -o '"clients_connected":[0-9]*' | cut -d':' -f2)
            echo "  代理:   ${agents:-0} 个在线"
            echo "  客户端: ${clients:-0} 个连接"
        fi

        # 进程信息
        echo ""
        echo "  进程信息:"
        ps -p "$pid" -o pid,user,%cpu,%mem,etime,command 2>/dev/null | tail -1 | awk '{
            printf "    CPU: %s%%  内存: %s%%  运行时间: %s\n", $3, $4, $5
        }'
    else
        echo -e "  状态:   ${RED}未运行${NC}"
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════════"
}

# 查看日志
do_logs() {
    local lines=${1:-50}

    if [ ! -f "$LOG_FILE" ]; then
        log_warn "日志文件不存在: $LOG_FILE"
        return 1
    fi

    echo "═══════════════════════════════════════════════════════════"
    echo "  最近 $lines 行日志 ($LOG_FILE)"
    echo "═══════════════════════════════════════════════════════════"
    tail -n "$lines" "$LOG_FILE"
}

# 实时日志
do_tail() {
    if [ ! -f "$LOG_FILE" ]; then
        log_warn "日志文件不存在: $LOG_FILE"
        return 1
    fi

    log_info "实时日志 (Ctrl+C 退出)..."
    tail -f "$LOG_FILE"
}

# 主函数
main() {
    case "${1:-}" in
        start)
            do_start
            ;;
        stop)
            do_stop
            ;;
        restart)
            do_restart
            ;;
        status)
            do_status
            ;;
        logs)
            do_logs "${2:-50}"
            ;;
        tail)
            do_tail
            ;;
        health)
            if health_check 3; then
                exit 0
            else
                exit 1
            fi
            ;;
        *)
            echo ""
            echo "用法: $0 {start|stop|restart|status|logs|tail|health}"
            echo ""
            echo "  start   - 启动服务"
            echo "  stop    - 停止服务"
            echo "  restart - 重启服务"
            echo "  status  - 查看状态"
            echo "  logs    - 查看日志 (可选参数: 行数)"
            echo "  tail    - 实时日志"
            echo "  health  - 健康检查"
            echo ""
            echo "环境变量:"
            echo "  HOST    - 监听地址 (默认: 0.0.0.0)"
            echo "  PORT    - 监听端口 (默认: 8080)"
            echo "  PYTHON  - Python 路径 (默认: python3)"
            echo ""
            exit 1
            ;;
    esac
}

main "$@"
