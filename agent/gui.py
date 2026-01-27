#!/usr/bin/env python3
"""
Claude Code Remote - GUI 客户端
图形化界面，一键启动本地服务 + FRP 隧道

用法:
  python gui.py
"""

import json
import logging
import os
import platform
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from datetime import datetime
from pathlib import Path
from typing import Optional


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
        self.server_process: Optional[subprocess.Popen] = None
        self.is_running = False

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
                        font=('', 10), padding=(15, 8))
        style.map('TButton',
                  background=[('active', COLORS['accent_hover']),
                              ('pressed', COLORS['accent'])],
                  foreground=[('active', 'white')])

        style.configure('Accent.TButton',
                        background=COLORS['accent'],
                        foreground='white',
                        borderwidth=0, focuscolor='none',
                        font=('', 13, 'bold'), padding=(20, 12))
        style.map('Accent.TButton',
                  background=[('active', COLORS['accent_hover']),
                              ('pressed', '#A0522D')])

        style.configure('Stop.TButton',
                        background=COLORS['error'],
                        foreground='white',
                        borderwidth=0, focuscolor='none',
                        font=('', 13, 'bold'), padding=(20, 12))
        style.map('Stop.TButton',
                  background=[('active', '#D32F2F'),
                              ('pressed', '#B71C1C')])

        style.configure('Small.TButton',
                        background=COLORS['bg_dark'],
                        foreground=COLORS['text'],
                        font=('', 9), padding=(8, 4))

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
        ttk.Button(bottom, text="复制访问地址", command=self._copy_url,
                   style='Small.TButton').pack(side=tk.LEFT, padx=5)

    def _browse_workspace(self):
        path = filedialog.askdirectory(initialdir=self.workspace_var.get())
        if path:
            self.workspace_var.set(path)

    def _show_settings(self):
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.geometry("500x420")
        win.configure(bg=COLORS['bg'])
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        row = 0
        fields = {}

        def add_field(label, key, default_val, hint=""):
            nonlocal row
            ttk.Label(frame, text=label, font=('', 11, 'bold')).grid(
                row=row, column=0, sticky=tk.W, pady=(0, 3))
            row += 1
            var = tk.StringVar(value=str(getattr(self.config, key)))
            entry = ttk.Entry(frame, textvariable=var, width=50, font=('', 11))
            entry.grid(row=row, column=0, sticky=tk.EW, pady=(0, 3))
            fields[key] = var
            row += 1
            if hint:
                tk.Label(frame, text=hint, font=('', 9),
                         fg=COLORS['text_light'], bg=COLORS['bg']).grid(
                    row=row, column=0, sticky=tk.W, pady=(0, 12))
                row += 1

        add_field("FRP 服务器:", "frp_server", self.config.frp_server,
                  "远程服务器域名，如 taskbot.com.cn")
        add_field("FRP 端口:", "frp_port", self.config.frp_port,
                  "FRP 服务端口，默认 7000")
        add_field("FRP 令牌:", "frp_token", self.config.frp_token,
                  "服务器认证令牌（可选）")
        add_field("本地端口:", "local_port", self.config.local_port,
                  "本地 HTTP 服务端口，默认 8080")
        add_field("Claude CLI 路径:", "claude_path", self.config.claude_path,
                  "留空自动检测")

        def save():
            self.config.frp_server = fields["frp_server"].get().strip()
            self.config.frp_port = int(fields["frp_port"].get().strip() or "7000")
            self.config.frp_token = fields["frp_token"].get().strip()
            self.config.local_port = int(fields["local_port"].get().strip() or "8080")
            self.config.claude_path = fields["claude_path"].get().strip()
            self.config.save()
            self.log("设置已保存")
            win.destroy()

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, pady=(15, 0))
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

        self.config.workspace = workspace
        self.config.save()

        self.log("正在启动服务...")
        self.start_btn.configure(text="正在启动...", state="disabled")

        def run():
            try:
                cmd = [
                    sys.executable, "-m", "agent.cli",
                    "--port", str(self.config.local_port),
                    "--workspace", workspace,
                ]

                if self.config.frp_server:
                    cmd.extend(["--server", self.config.frp_server])
                    cmd.extend(["--server-port", str(self.config.frp_port)])
                    if self.config.frp_token:
                        cmd.extend(["--frp-token", self.config.frp_token])

                if self.config.claude_path:
                    cmd.extend(["--claude-path", self.config.claude_path])

                # 从项目根目录运行
                project_dir = str(Path(__file__).parent.parent)

                self.server_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=project_dir,
                    bufsize=1,
                )

                self.is_running = True
                self.root.after(0, lambda: self._update_ui_running(True))

                # 读取输出
                for line in self.server_process.stdout:
                    line = line.rstrip()
                    if line:
                        self.root.after(0, lambda l=line: self.log(l))

                # 进程结束
                self.is_running = False
                self.root.after(0, lambda: self._update_ui_running(False))
                self.root.after(0, lambda: self.log("服务已停止", "error"))

            except Exception as e:
                self.is_running = False
                self.root.after(0, lambda: self._update_ui_running(False))
                self.root.after(0, lambda: self.log(f"启动失败: {e}", "error"))

        threading.Thread(target=run, daemon=True).start()

    def _stop_service(self):
        self.log("正在停止服务...")
        if self.server_process:
            try:
                self.server_process.send_signal(signal.SIGTERM)
                self.server_process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self.server_process.kill()
            self.server_process = None
        self.is_running = False
        self._update_ui_running(False)

    def _update_ui_running(self, running: bool):
        if running:
            self.start_btn.configure(text="停止服务", style='Stop.TButton', state="normal")
            self.status_label.configure(text="运行中", fg=COLORS['success'])
        else:
            self.start_btn.configure(text="启动服务", style='Accent.TButton', state="normal")
            self.status_label.configure(text="未运行", fg=COLORS['text_light'])

    def _copy_url(self):
        # 从日志中查找地址
        content = self.log_text.get("1.0", tk.END)
        for line in content.split("\n"):
            if "token=" in line and ("http://" in line or "https://" in line):
                url = line.strip().split()[-1] if line.strip() else ""
                if not url:
                    for word in line.split():
                        if "http" in word:
                            url = word
                            break
                if url:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(url)
                    self.log("地址已复制到剪贴板")
                    return
        messagebox.showinfo("提示", "请先启动服务")

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


if __name__ == "__main__":
    app = AgentGUI()
    app.run()
