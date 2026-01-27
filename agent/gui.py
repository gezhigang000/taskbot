#!/usr/bin/env python3
"""
Claude Code Remote - GUI 客户端
图形化界面，一键启动本地服务 + FRP 隧道

用法:
  python gui.py
"""

import asyncio
import json
import logging
import os
import platform
import secrets
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn

# 导入本地模块
try:
    from agent.server import create_app
    from agent.frp import FRPClient, download_frpc
except ImportError:
    from server import create_app
    from frp import FRPClient, download_frpc


# ============================================================================
# 配置管理
# ============================================================================

def get_config_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    d = base / "ClaudeCodeRemote"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_file() -> Path:
    return get_config_dir() / "config.json"


class AppConfig:
    """应用配置"""
    def __init__(self):
        self.frp_server = "taskbot.com.cn"
        self.frp_port = 7000
        self.frp_token = ""
        self.local_port = 8080
        self.workspace = str(Path.home())
        self.claude_path = ""
        self.agent_id = ""
        self.load()

    def load(self):
        f = get_config_file()
        if f.exists():
            try:
                data = json.loads(f.read_text("utf-8"))
                for k, v in data.items():
                    if hasattr(self, k):
                        setattr(self, k, v)
            except Exception:
                pass

    def save(self):
        data = {
            "frp_server": self.frp_server,
            "frp_port": self.frp_port,
            "frp_token": self.frp_token,
            "local_port": self.local_port,
            "workspace": self.workspace,
            "claude_path": self.claude_path,
            "agent_id": self.agent_id,
        }
        try:
            get_config_file().write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        except Exception as e:
            print(f"保存配置失败: {e}")


# ============================================================================
# GUI 主界面
# ============================================================================

COLORS = {
    'bg': '#FDF5E6',
    'bg_light': '#FFFAF0',
    'bg_dark': '#F5DEB3',
    'accent': '#D2691E',
    'accent_light': '#DEB887',
    'accent_hover': '#CD853F',
    'text': '#4A3728',
    'text_light': '#8B7355',
    'success': '#2E7D32',
    'error': '#C62828',
}


class AgentGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Claude Code Remote")
        self.root.geometry("800x600")
        self.root.minsize(650, 500)
        self.root.configure(bg=COLORS['bg'])

        self.config = AppConfig()
        self.is_running = False
        self.server_thread: Optional[threading.Thread] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.frp_client: Optional[FRPClient] = None
        self.access_token: Optional[str] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        self._setup_styles()
        self._setup_ui()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('TFrame', background=COLORS['bg'])
        style.configure('TLabelframe', background=COLORS['bg'])
        style.configure('TLabelframe.Label',
                        background=COLORS['bg'],
                        foreground=COLORS['accent'],
                        font=('', 12, 'bold'))
        style.configure('TLabel',
                        background=COLORS['bg'],
                        foreground=COLORS['text'],
                        font=('', 11))
        style.configure('TEntry',
                        fieldbackground=COLORS['bg_light'],
                        foreground=COLORS['text'],
                        borderwidth=2, relief='solid')

        style.configure('TButton',
                        background=COLORS['accent_light'],
                        foreground=COLORS['text'],
                        borderwidth=0, focuscolor='none',
                        font=('', 10), padding=(10, 5))
        style.map('TButton',
                  background=[('active', COLORS['accent_hover']),
                              ('pressed', COLORS['accent'])],
                  foreground=[('active', 'white')])

        style.configure('Accent.TButton',
                        background=COLORS['accent'],
                        foreground='white',
                        borderwidth=0, focuscolor='none',
                        font=('', 12, 'bold'), padding=(12, 6))
        style.map('Accent.TButton',
                  background=[('active', COLORS['accent_hover']),
                              ('pressed', '#A0522D')])

        style.configure('Stop.TButton',
                        background=COLORS['error'],
                        foreground='white',
                        borderwidth=0, focuscolor='none',
                        font=('', 12, 'bold'), padding=(12, 6))
        style.map('Stop.TButton',
                  background=[('active', '#D32F2F'),
                              ('pressed', '#B71C1C')])

        style.configure('Small.TButton',
                        background=COLORS['bg_dark'],
                        foreground=COLORS['text'],
                        font=('', 9), padding=(6, 3))

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding="15")
        main.pack(fill=tk.BOTH, expand=True)

        # --- 标题栏 ---
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 12))

        tk.Label(header, text="Claude Code Remote",
                 font=('', 22, 'bold'), fg=COLORS['accent'], bg=COLORS['bg']).pack(side=tk.LEFT)

        ttk.Button(header, text="设置", command=self._show_settings,
                   style='Small.TButton').pack(side=tk.RIGHT, padx=5)

        # --- 工作目录 ---
        ws_frame = ttk.Frame(main)
        ws_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(ws_frame, text="工作目录:").pack(side=tk.LEFT)
        self.workspace_var = tk.StringVar(value=self.config.workspace)
        ttk.Entry(ws_frame, textvariable=self.workspace_var, font=('', 11)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 5))
        ttk.Button(ws_frame, text="...", width=3, command=self._browse_workspace,
                   style='Small.TButton').pack(side=tk.LEFT)

        # --- 启动/停止按钮 ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(5, 12))

        self.start_btn = ttk.Button(btn_frame, text="启动服务",
                                     command=self._toggle_service, style='Accent.TButton')
        self.start_btn.pack(side=tk.LEFT)

        self.status_label = tk.Label(btn_frame, text="未运行",
                                     font=('', 11), fg=COLORS['text_light'], bg=COLORS['bg'])
        self.status_label.pack(side=tk.LEFT, padx=(15, 0))

        # --- 日志区域 ---
        log_frame = ttk.LabelFrame(main, text=" 运行日志 ", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=15, state=tk.DISABLED,
            font=("Menlo", 11),
            bg=COLORS['bg_light'], fg=COLORS['text'],
            relief='solid', borderwidth=1,
            insertbackground=COLORS['text'])
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure('info', foreground=COLORS['text'])
        self.log_text.tag_configure('success', foreground=COLORS['success'])
        self.log_text.tag_configure('error', foreground=COLORS['error'])
        self.log_text.tag_configure('highlight', foreground=COLORS['accent'],
                                     font=('Menlo', 11, 'bold'))

        # 底部操作
        bottom = ttk.Frame(log_frame)
        bottom.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bottom, text="清空日志", command=self._clear_log,
                   style='Small.TButton').pack(side=tk.LEFT)
        self.copy_url_btn = ttk.Button(bottom, text="复制访问地址", command=self._copy_url,
                   style='Small.TButton', state='disabled')
        self.copy_url_btn.pack(side=tk.LEFT, padx=5)

    def _browse_workspace(self):
        path = filedialog.askdirectory(initialdir=self.workspace_var.get())
        if path:
            self.workspace_var.set(path)

    def _show_settings(self):
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.geometry("500x480")
        win.configure(bg=COLORS['bg'])
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        # 使用实例变量存储 StringVar，防止被垃圾回收
        self._setting_vars = {}
        entries = {}

        def add_field(row, label, key, hint=""):
            ttk.Label(frame, text=label, font=('', 10, 'bold')).grid(
                row=row, column=0, sticky=tk.W, pady=(0, 2))

            var = tk.StringVar(value=str(getattr(self.config, key)))
            self._setting_vars[key] = var  # 保存引用防止 GC

            entry = tk.Entry(frame, textvariable=var, width=50, font=('', 10),
                           bg=COLORS['bg_light'], fg=COLORS['text'],
                           relief='solid', bd=1)
            entry.grid(row=row + 1, column=0, sticky=tk.EW, pady=(0, 2))
            entries[key] = entry

            if hint:
                tk.Label(frame, text=hint, font=('', 8),
                         fg=COLORS['text_light'], bg=COLORS['bg']).grid(
                    row=row + 2, column=0, sticky=tk.W, pady=(0, 8))
            return row + 3

        row = 0
        row = add_field(row, "FRP 服务器:", "frp_server",
                        "远程服务器域名，如 taskbot.com.cn")
        row = add_field(row, "FRP 端口:", "frp_port",
                        "FRP 服务端口，默认 7000")
        row = add_field(row, "FRP 令牌:", "frp_token",
                        "服务器认证令牌（从服务端获取）")
        row = add_field(row, "本地端口:", "local_port",
                        "本地 HTTP 服务端口，默认 8080")
        row = add_field(row, "Claude CLI 路径:", "claude_path",
                        "留空自动检测")

        def save():
            try:
                self.config.frp_server = self._setting_vars["frp_server"].get().strip()
                self.config.frp_port = int(self._setting_vars["frp_port"].get().strip() or "7000")
                self.config.frp_token = self._setting_vars["frp_token"].get().strip()
                self.config.local_port = int(self._setting_vars["local_port"].get().strip() or "8080")
                self.config.claude_path = self._setting_vars["claude_path"].get().strip()
                self.config.save()
                self.log("设置已保存")
                win.destroy()
            except ValueError as e:
                messagebox.showerror("错误", f"端口必须是数字: {e}")

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, pady=(10, 0))
        ttk.Button(btn_frame, text="保存", command=save,
                   style='Accent.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=win.destroy,
                   style='Small.TButton').pack(side=tk.LEFT, padx=5)

        frame.columnconfigure(0, weight=1)

    def _toggle_service(self):
        if self.is_running:
            self._stop_service()
        else:
            self._start_service()

    def _start_service(self):
        workspace = self.workspace_var.get().strip()
        if not workspace:
            messagebox.showerror("错误", "请选择工作目录")
            return

        if not os.path.isdir(workspace):
            messagebox.showerror("错误", "工作目录不存在")
            return

        self.config.workspace = workspace
        self.config.save()

        self.log("正在启动服务...")
        self.start_btn.configure(text="正在启动...", state="disabled")

        def run_server():
            try:
                # 生成访问令牌
                self.access_token = secrets.token_urlsafe(16)

                # 创建 FastAPI 应用
                self.log("创建服务器...")
                app = create_app(
                    workspace=workspace,
                    claude_path=self.config.claude_path or None,
                    access_token=self.access_token,
                )

                # 配置 uvicorn
                config = uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=self.config.local_port,
                    log_level="warning",
                )
                self.uvicorn_server = uvicorn.Server(config)

                # 创建事件循环
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)

                self.is_running = True
                self.root.after(0, lambda: self._update_ui_running(True))

                # 显示本地地址
                local_url = f"http://localhost:{self.config.local_port}?token={self.access_token}"
                self.root.after(0, lambda: self.log(""))
                self.root.after(0, lambda: self.log("=" * 50))
                self.root.after(0, lambda: self.log(f"  本地地址: {local_url}"))

                # 启动 FRP 隧道（在后台线程中，不阻塞）
                if self.config.frp_server:
                    threading.Thread(target=self._start_frp, daemon=True).start()

                self.root.after(0, lambda: self.log("=" * 50))
                self.root.after(0, lambda: self.log(""))

                # 运行服务器（阻塞）
                self.loop.run_until_complete(self.uvicorn_server.serve())

            except FileNotFoundError as e:
                self.root.after(0, lambda: self.log(f"错误: {e}", "error"))
            except OSError as e:
                if "Address already in use" in str(e):
                    self.root.after(0, lambda: self.log(f"错误: 端口 {self.config.local_port} 已被占用", "error"))
                else:
                    self.root.after(0, lambda: self.log(f"错误: {e}", "error"))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"启动失败: {e}", "error"))
            finally:
                self.is_running = False
                self.root.after(0, lambda: self._update_ui_running(False))
                if self.loop:
                    self.loop.close()
                    self.loop = None

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

    def _start_frp(self):
        """启动 FRP 隧道"""
        try:
            self.log("正在启动 FRP 隧道...")

            # 检查/下载 frpc
            frpc_path = download_frpc()
            if not frpc_path:
                self.log("警告: 无法下载 frpc，仅使用本地访问", "error")
                self.root.after(0, lambda: self.copy_url_btn.configure(text="复制本地地址"))
                return

            # 创建 FRP 客户端
            frp_client = FRPClient(
                server_addr=self.config.frp_server,
                server_port=self.config.frp_port,
                auth_token=self.config.frp_token,
                agent_id=self.config.agent_id,
                local_port=self.config.local_port,
            )

            # 启动 FRP
            if frp_client.start(frpc_path):
                self.frp_client = frp_client  # 只在成功时保存
                public_url = f"{self.frp_client.public_url}?token={self.access_token}"
                self.log(f"  FRP 隧道已建立")
                self.log(f"  远程地址: {public_url}", "highlight")
                self.root.after(0, lambda: self.copy_url_btn.configure(text="复制远程地址"))

                # 保存 agent_id
                if self.frp_client.agent_id != self.config.agent_id:
                    self.config.agent_id = self.frp_client.agent_id
                    self.config.save()
            else:
                self.log("警告: FRP 隧道启动失败，仅使用本地访问", "error")
                self.root.after(0, lambda: self.copy_url_btn.configure(text="复制本地地址"))
                frp_client.stop()  # 清理进程

        except Exception as e:
            self.log(f"FRP 错误: {e}", "error")
            self.root.after(0, lambda: self.copy_url_btn.configure(text="复制本地地址"))

    def _stop_service(self):
        self.log("正在停止服务...")

        # 停止 FRP
        if self.frp_client:
            try:
                self.frp_client.stop()
            except Exception:
                pass
            self.frp_client = None

        # 停止 uvicorn
        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True
            self.uvicorn_server = None

        self.is_running = False
        self._update_ui_running(False)
        self.log("服务已停止")

    def _update_ui_running(self, running: bool):
        if running:
            self.start_btn.configure(text="停止服务", style='Stop.TButton', state="normal")
            self.status_label.configure(text="运行中", fg=COLORS['success'])
            self.copy_url_btn.configure(text="复制本地地址", state="normal")
        else:
            self.start_btn.configure(text="启动服务", style='Accent.TButton', state="normal")
            self.status_label.configure(text="未运行", fg=COLORS['text_light'])
            self.copy_url_btn.configure(text="复制访问地址", state="disabled")

    def _copy_url(self):
        if not self.is_running or not self.access_token:
            messagebox.showinfo("提示", "请先启动服务")
            return

        # 优先使用远程地址
        if self.frp_client and self.frp_client.public_url:
            url = f"{self.frp_client.public_url}?token={self.access_token}"
            url_type = "远程"
        else:
            url = f"http://localhost:{self.config.local_port}?token={self.access_token}"
            url_type = "本地"

        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.log(f"{url_type}地址已复制: {url}")

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def log(self, message: str, level: str = "info"):
        def _log():
            self.log_text.configure(state=tk.NORMAL)
            tag = 'info'
            if any(w in message for w in ['ERROR', '失败', '错误']):
                tag = 'error'
            elif any(w in message for w in ['成功', '已启动', '已建立']):
                tag = 'success'
            elif 'token=' in message or '====' in message:
                tag = 'highlight'
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{ts}] {message}\n", tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.root.after(0, _log)

    def run(self):
        # 设置日志处理器，将日志输出到 GUI
        self._setup_logging()

        self.log("Claude Code Remote GUI 已启动")
        self.log(f"配置文件: {get_config_file()}")
        self.log("")
        self.log("使用步骤:")
        self.log("  1. 选择工作目录")
        self.log("  2. 点击「设置」配置 FRP 服务器地址")
        self.log("  3. 点击「启动服务」")
        self.log("  4. 用手机浏览器打开显示的地址")
        self.log("")

        def on_closing():
            if self.is_running:
                self._stop_service()
            self.root.destroy()

        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        self.root.mainloop()

    def _setup_logging(self):
        """将 Python 日志重定向到 GUI"""

        class GUILogHandler(logging.Handler):
            def __init__(self, gui):
                super().__init__()
                self.gui = gui

            def emit(self, record):
                msg = self.format(record)
                self.gui.root.after(0, lambda: self.gui.log(msg))

        handler = GUILogHandler(self)
        handler.setFormatter(logging.Formatter("%(message)s"))

        for name in ["claude-remote", "uvicorn", "uvicorn.error"]:
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)


if __name__ == "__main__":
    app = AgentGUI()
    app.run()
