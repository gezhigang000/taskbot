#!/usr/bin/env python3
"""
Claude Code Remote - 本地 HTTP/SSE 服务器
直接在本地提供终端服务，通过 FRP 隧道暴露给手机访问

协议：
  GET  /              终端页面 (xterm.js)
  GET  /sse           SSE 输出流
  POST /input         接收键盘输入
  POST /resize        终端尺寸调整
  GET  /health        健康检查
"""

import asyncio
import json
import logging
import os
import pty
import secrets
import select
import signal
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

logger = logging.getLogger("claude-remote")

# ============================================================================
# Claude 进程管理
# ============================================================================

class ClaudeProcess:
    """管理 Claude Code CLI 进程"""

    def __init__(self, workspace: str, claude_path: Optional[str] = None):
        self.workspace = workspace
        self.claude_path = claude_path or self._find_claude()
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self.output_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._reader_task: Optional[asyncio.Task] = None

    @staticmethod
    def _find_claude() -> str:
        """查找 claude 命令"""
        import shutil
        paths = [
            Path.home() / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
            Path.home() / ".npm-global" / "bin" / "claude",
        ]
        found = shutil.which("claude")
        if found:
            return found
        for p in paths:
            if p.exists() and os.access(p, os.X_OK):
                return str(p)
        raise FileNotFoundError(
            "未找到 Claude Code CLI。\n"
            "请先安装: npm install -g @anthropic-ai/claude-code"
        )

    def start(self):
        """启动 Claude 进程"""
        self.master_fd, self.slave_fd = pty.openpty()

        env = os.environ.copy()
        extra_paths = [
            str(Path.home() / ".local" / "bin"),
            str(Path.home() / ".npm-global" / "bin"),
            "/usr/local/bin",
            "/opt/homebrew/bin",
        ]
        env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = "120"
        env["LINES"] = "40"

        self.pid = os.fork()
        if self.pid == 0:
            # 子进程
            os.close(self.master_fd)
            os.setsid()
            os.dup2(self.slave_fd, 0)
            os.dup2(self.slave_fd, 1)
            os.dup2(self.slave_fd, 2)
            os.close(self.slave_fd)
            os.chdir(self.workspace)
            os.execvpe(self.claude_path, [self.claude_path], env)
        else:
            # 父进程
            os.close(self.slave_fd)
            self.slave_fd = None
            logger.info(f"Claude 进程已启动 (PID: {self.pid})")

    async def start_reader(self):
        """启动异步输出读取"""
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self):
        """持续读取 Claude 输出"""
        loop = asyncio.get_event_loop()
        while self.master_fd is not None:
            try:
                ready = await loop.run_in_executor(
                    None, lambda: select.select([self.master_fd], [], [], 0.1)
                )
                if ready[0]:
                    data = os.read(self.master_fd, 4096)
                    if not data:
                        break
                    await self.output_queue.put(data.decode("utf-8", errors="replace"))
            except (OSError, ValueError):
                break
        logger.info("Claude 输出读取结束")

    def write_input(self, data: str):
        """写入输入到 Claude"""
        if self.master_fd is not None:
            os.write(self.master_fd, data.encode("utf-8"))

    def resize(self, rows: int, cols: int):
        """调整终端大小"""
        if self.master_fd is not None:
            import fcntl
            import struct
            import termios
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    def stop(self):
        """停止 Claude 进程"""
        if self._reader_task:
            self._reader_task.cancel()
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, 0)
            except (OSError, ChildProcessError):
                pass
            self.pid = None
        logger.info("Claude 进程已停止")


# ============================================================================
# HTTP/SSE 服务器
# ============================================================================

def create_app(
    workspace: str,
    claude_path: Optional[str] = None,
    access_token: Optional[str] = None,
) -> FastAPI:
    """创建 FastAPI 应用"""

    app = FastAPI(title="Claude Code Remote", docs_url=None, redoc_url=None)
    token = access_token or secrets.token_urlsafe(16)
    claude: Optional[ClaudeProcess] = None

    # 加载终端 HTML
    html_path = Path(__file__).parent / "terminal.html"
    terminal_html = ""
    if html_path.exists():
        terminal_html = html_path.read_text(encoding="utf-8")

    # --- 认证中间件 ---
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # 健康检查不需要认证
        if request.url.path == "/health":
            return await call_next(request)

        # 检查 token
        req_token = (
            request.query_params.get("token")
            or request.cookies.get("access_token")
        )
        if req_token != token:
            return Response("Unauthorized", status_code=401)

        response = await call_next(request)

        # 首次通过 URL token 访问时设置 cookie
        if request.query_params.get("token") and "access_token" not in request.cookies:
            response.set_cookie(
                "access_token", token,
                httponly=True, samesite="lax", max_age=86400
            )
        return response

    # --- 启动/关闭 ---
    @app.on_event("startup")
    async def startup():
        nonlocal claude
        claude = ClaudeProcess(workspace, claude_path)
        claude.start()
        await claude.start_reader()
        logger.info(f"服务已启动，工作目录: {workspace}")

    @app.on_event("shutdown")
    async def shutdown():
        if claude:
            claude.stop()

    # --- 路由 ---
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return terminal_html

    @app.get("/sse")
    async def sse_stream():
        """SSE 输出流"""
        async def generate():
            while True:
                try:
                    output = await asyncio.wait_for(
                        claude.output_queue.get(), timeout=30
                    )
                    yield f"data: {json.dumps({'type': 'output', 'data': output})}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保持连接
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                except Exception:
                    break

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Nginx 禁用缓冲
            },
        )

    @app.post("/input")
    async def receive_input(request: Request):
        """接收键盘输入"""
        body = await request.json()
        data = body.get("data", "")
        if data and claude:
            claude.write_input(data)
        return {"status": "ok"}

    @app.post("/resize")
    async def resize_terminal(request: Request):
        """调整终端尺寸"""
        body = await request.json()
        rows = body.get("rows", 40)
        cols = body.get("cols", 120)
        if claude:
            claude.resize(rows, cols)
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        """健康检查"""
        return {
            "status": "healthy",
            "claude_running": claude is not None and claude.pid is not None,
        }

    # 保存 token 供外部使用
    app.state.access_token = token

    return app
