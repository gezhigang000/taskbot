#!/usr/bin/env python3
"""
Claude Code Remote - 命令行入口
启动本地 HTTP 服务 + FRP 隧道

用法:
  python -m agent.cli                          # 仅启动本地服务
  python -m agent.cli --server taskbot.com.cn  # 启动本地服务 + FRP 隧道
  python -m agent.cli --port 9090              # 指定端口
"""

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

import uvicorn

from .server import create_app
from .frp import FRPClient, get_frpc_path, download_frpc


def setup_logging(debug: bool = False):
    """配置日志"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def print_banner(port: int, token: str, frp_url: str = ""):
    """打印启动信息"""
    print()
    print("=" * 58)
    print("  Claude Code Remote")
    print("=" * 58)
    print()
    print(f"  本地地址: http://localhost:{port}?token={token}")
    if frp_url:
        print(f"  远程地址: {frp_url}?token={token}")
    print()
    print("  在手机浏览器中打开上述地址即可使用")
    print()
    print("=" * 58)
    print()


def main():
    parser = argparse.ArgumentParser(description="Claude Code Remote")
    parser.add_argument("--port", "-p", type=int, default=8080, help="本地端口 (默认: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--workspace", "-w", default=os.getcwd(), help="工作目录")
    parser.add_argument("--claude-path", help="Claude CLI 路径")
    parser.add_argument("--server", "-s", help="FRP 服务器地址 (如: taskbot.com.cn)")
    parser.add_argument("--server-port", type=int, default=7000, help="FRP 服务器端口 (默认: 7000)")
    parser.add_argument("--frp-token", default="", help="FRP 认证令牌")
    parser.add_argument("--agent-id", default="", help="Agent ID (默认自动生成)")
    parser.add_argument("--token", default="", help="访问令牌 (默认自动生成)")
    parser.add_argument("--debug", "-d", action="store_true", help="调试模式")
    args = parser.parse_args()

    setup_logging(args.debug)
    logger = logging.getLogger("claude-remote")

    # 创建 FastAPI 应用
    app = create_app(
        workspace=args.workspace,
        claude_path=args.claude_path,
        access_token=args.token or None,
    )
    token = app.state.access_token

    # 启动 FRP（如果指定了服务器）
    frp_client = None
    frp_url = ""

    if args.server:
        logger.info(f"正在配置 FRP 隧道到 {args.server}...")

        # 检查/下载 frpc
        frpc_path = get_frpc_path()
        if not frpc_path:
            logger.info("未找到 frpc，正在下载...")
            try:
                frpc_path = download_frpc(progress_callback=lambda msg: logger.info(msg))
            except Exception as e:
                logger.error(f"下载 frpc 失败: {e}")
                logger.error("请手动安装 frpc: https://github.com/fatedier/frp/releases")
                sys.exit(1)

        frp_client = FRPClient(
            server_addr=args.server,
            server_port=args.server_port,
            auth_token=args.frp_token,
            agent_id=args.agent_id,
            local_port=args.port,
        )

        if frp_client.start(frpc_path):
            frp_url = frp_client.public_url
            logger.info(f"FRP 隧道已建立: {frp_url}")
        else:
            logger.error("FRP 启动失败")

    # 打印启动信息
    print_banner(args.port, token, frp_url)

    # 处理退出信号
    def cleanup(signum=None, frame=None):
        logger.info("正在停止...")
        if frp_client:
            frp_client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 启动 HTTP 服务
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info" if not args.debug else "debug",
        )
    finally:
        if frp_client:
            frp_client.stop()


if __name__ == "__main__":
    main()
