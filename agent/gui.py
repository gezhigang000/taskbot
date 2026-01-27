#!/usr/bin/env python3
"""
Claude Code Remote - GUI 客户端
图形化界面，支持多实例管理

用法:
  python gui.py
"""

import asyncio
import json
import os
import platform
import pty
import select
import signal
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass, field, asdict

try:
    import websockets
except ImportError:
    os.system(f"{sys.executable} -m pip install -q websockets")
    import websockets


# ============================================================================
# 配置管理
# ============================================================================

def get_config_dir() -> Path:
    """获取配置目录"""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"

    config_dir = base / "ClaudeCodeRemote"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    """获取配置文件路径"""
    return get_config_dir() / "config.json"


def find_claude_command() -> Optional[str]:
    """查找 claude 命令路径"""
    import shutil

    # 常见安装路径
    search_paths = [
        # 用户本地安装
        Path.home() / ".local" / "bin" / "claude",
        # npm 全局安装
        Path.home() / ".npm-global" / "bin" / "claude",
        # Homebrew (macOS)
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
        # Linux 系统路径
        Path("/usr/bin/claude"),
        # Windows
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
        Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
    ]

    # 先检查 PATH
    claude_in_path = shutil.which("claude")
    if claude_in_path:
        return claude_in_path

    # 检查常见路径
    for path in search_paths:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    return None


@dataclass
class AppConfig:
    """应用配置"""
    server_url: str = "http://taskbot.com.cn"
    default_name: str = ""
    default_workspace: str = ""
    claude_path: str = ""  # Claude CLI 路径
    saved_agents: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.default_name:
            self.default_name = platform.node()
        if not self.default_workspace:
            self.default_workspace = str(Path.home())
        if not self.claude_path:
            self.claude_path = find_claude_command() or ""

    @classmethod
    def load(cls) -> "AppConfig":
        """从文件加载配置"""
        config_file = get_config_file()
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return cls(**data)
            except Exception as e:
                print(f"加载配置失败: {e}")
        return cls()

    def save(self):
        """保存配置到文件"""
        config_file = get_config_file()
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class AgentInstance:
    """代理实例"""
    agent_id: str
    agent_key: str
    name: str
    workspace: str
    server_url: str
    status: str = "disconnected"
    process: Optional[asyncio.subprocess.Process] = None
    master_fd: Optional[int] = None
    slave_fd: Optional[int] = None
    websocket: object = None
    terminal_url: str = ""


# ============================================================================
# Agent Manager (Async Backend)
# ============================================================================

class AgentManager:
    """管理多个代理实例"""

    def __init__(self, log_callback=None):
        self.instances: Dict[str, AgentInstance] = {}
        self.log_callback = log_callback
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.running = False

    def log(self, message: str, level: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] {level}: {message}"
        if self.log_callback:
            self.log_callback(log_msg)
        print(log_msg)

    def register_agent(self, server_url: str, name: str, workspace: str) -> Optional[AgentInstance]:
        """注册新代理"""
        self.log(f"正在注册代理: {name}")
        self.log(f"服务器: {server_url}")
        self.log(f"工作目录: {workspace}")

        try:
            url = f"{server_url.rstrip('/')}/api/agents?name={urllib.parse.quote(name)}"
            req = urllib.request.Request(url, method='POST')
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

                instance = AgentInstance(
                    agent_id=data['agent_id'],
                    agent_key=data['agent_key'],
                    name=name,
                    workspace=workspace,
                    server_url=server_url,
                    terminal_url=f"{server_url}/terminal/{data['agent_id']}"
                )

                self.instances[instance.agent_id] = instance
                self.log(f"注册成功! Agent ID: {instance.agent_id}")
                return instance

        except urllib.error.HTTPError as e:
            self.log(f"注册失败 - HTTP {e.code}: {e.reason}", "ERROR")
            try:
                self.log(f"响应: {e.read().decode()}", "ERROR")
            except:
                pass
        except urllib.error.URLError as e:
            self.log(f"注册失败 - 网络错误: {e.reason}", "ERROR")
        except Exception as e:
            self.log(f"注册失败: {e}", "ERROR")

        return None

    async def start_instance(self, instance: AgentInstance):
        """启动实例"""
        self.log(f"启动代理 {instance.name}...")
        instance.status = "connecting"

        # 查找 Claude CLI
        claude_cmd = self.config.claude_path or find_claude_command()
        if not claude_cmd:
            self.log("=" * 50, "ERROR")
            self.log("错误: 未找到 Claude Code CLI", "ERROR")
            self.log("", "ERROR")
            self.log("请先安装 Claude Code CLI:", "ERROR")
            self.log("  npm install -g @anthropic-ai/claude-code", "ERROR")
            self.log("", "ERROR")
            self.log("或在设置中指定 claude 命令路径", "ERROR")
            self.log("=" * 50, "ERROR")
            instance.status = "error"
            return

        self.log(f"使用 Claude CLI: {claude_cmd}")

        # 启动 Claude Code 进程
        try:
            os.makedirs(instance.workspace, exist_ok=True)
            instance.master_fd, instance.slave_fd = pty.openpty()

            # 扩展 PATH 以包含常见安装路径
            env = os.environ.copy()
            extra_paths = [
                str(Path.home() / ".local" / "bin"),
                str(Path.home() / ".npm-global" / "bin"),
                "/usr/local/bin",
                "/opt/homebrew/bin",
            ]
            env['PATH'] = ':'.join(extra_paths) + ':' + env.get('PATH', '')
            env['TERM'] = 'xterm-256color'

            instance.process = await asyncio.create_subprocess_exec(
                claude_cmd,
                stdin=instance.slave_fd,
                stdout=instance.slave_fd,
                stderr=instance.slave_fd,
                cwd=instance.workspace,
                env=env
            )
            self.log(f"Claude Code 已启动 (PID: {instance.process.pid})")
        except FileNotFoundError:
            self.log(f"错误: 无法执行 {claude_cmd}", "ERROR")
            self.log("请检查 Claude CLI 是否正确安装", "ERROR")
            instance.status = "error"
            return
        except Exception as e:
            self.log(f"启动 Claude Code 失败: {e}", "ERROR")
            instance.status = "error"
            return

        # 连接到中继服务器
        ws_url = instance.server_url.replace('http://', 'ws://').replace('https://', 'wss://')
        url = f"{ws_url}/ws/agent/{instance.agent_id}?key={instance.agent_key}"

        self.log(f"正在连接 WebSocket: {ws_url}/ws/agent/...")

        try:
            instance.websocket = await websockets.connect(
                url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5
            )
            instance.status = "online"
            self.log(f"代理 {instance.name} 已连接到中继服务器")
            self.log(f"")
            self.log(f"{'='*50}")
            self.log(f"手机访问地址: {instance.terminal_url}")
            self.log(f"{'='*50}")
            self.log(f"")

            # 启动转发任务
            await asyncio.gather(
                self._output_loop(instance),
                self._receive_loop(instance),
                self._heartbeat_loop(instance),
            )

        except websockets.exceptions.InvalidStatusCode as e:
            self.log(f"WebSocket 连接被拒绝: HTTP {e.status_code}", "ERROR")
            if e.status_code == 403:
                self.log("服务器可能被防火墙阻止或需要认证", "ERROR")
            instance.status = "error"
        except websockets.exceptions.ConnectionClosedError as e:
            if e.code == 4001:
                self.log("代理凭证无效，服务器可能已重启", "ERROR")
                self.log("请删除此代理并重新创建", "ERROR")
            else:
                self.log(f"WebSocket 连接关闭: code={e.code}, reason={e.reason}", "ERROR")
            instance.status = "error"
        except websockets.exceptions.InvalidURI as e:
            self.log(f"WebSocket 地址无效: {url}", "ERROR")
            instance.status = "error"
        except ConnectionRefusedError:
            self.log(f"WebSocket 连接被拒绝，服务器可能未启动", "ERROR")
            instance.status = "error"
        except asyncio.TimeoutError:
            self.log(f"WebSocket 连接超时", "ERROR")
            instance.status = "error"
        except OSError as e:
            self.log(f"网络错误: {e}", "ERROR")
            instance.status = "error"
        except Exception as e:
            self.log(f"连接失败: {type(e).__name__}: {e}", "ERROR")
            instance.status = "error"
        finally:
            # 清理 Claude 进程
            if instance.process and instance.process.returncode is None:
                try:
                    instance.process.terminate()
                    self.log("Claude 进程已终止")
                except:
                    pass

    async def _output_loop(self, instance: AgentInstance):
        """转发输出"""
        while instance.status == "online" and instance.master_fd:
            try:
                ready, _, _ = select.select([instance.master_fd], [], [], 0.1)
                if ready:
                    data = os.read(instance.master_fd, 4096)
                    if data and instance.websocket:
                        await instance.websocket.send(json.dumps({
                            "type": "output",
                            "data": data.decode('utf-8', errors='replace')
                        }))
            except Exception as e:
                self.log(f"输出转发错误: {e}", "ERROR")
                break
            await asyncio.sleep(0.05)

    async def _receive_loop(self, instance: AgentInstance):
        """接收消息"""
        while instance.status == "online" and instance.websocket:
            try:
                data = await instance.websocket.recv()
                msg = json.loads(data)
                if msg["type"] == "input" and instance.master_fd:
                    os.write(instance.master_fd, msg["data"].encode('utf-8'))
            except Exception as e:
                self.log(f"接收错误: {e}", "ERROR")
                break

    async def _heartbeat_loop(self, instance: AgentInstance):
        """心跳"""
        while instance.status == "online" and instance.websocket:
            try:
                await instance.websocket.send(json.dumps({"type": "heartbeat"}))
                await asyncio.sleep(30)
            except:
                break

    async def stop_instance(self, instance: AgentInstance):
        """停止实例"""
        self.log(f"停止代理 {instance.name}...")
        instance.status = "disconnected"

        if instance.websocket:
            try:
                await instance.websocket.close()
            except:
                pass

        if instance.process:
            try:
                instance.process.terminate()
                await asyncio.wait_for(instance.process.wait(), timeout=5)
            except:
                instance.process.kill()

        if instance.master_fd:
            try:
                os.close(instance.master_fd)
            except:
                pass
        if instance.slave_fd:
            try:
                os.close(instance.slave_fd)
            except:
                pass

        self.log(f"代理 {instance.name} 已停止")


# ============================================================================
# GUI Application
# ============================================================================

class AgentGUI:
    """GUI 主窗口"""

    # 米色主题配色
    COLORS = {
        'bg': '#FDF5E6',           # 主背景 - 米色
        'bg_light': '#FFFAF0',     # 浅背景 - 花白色
        'bg_dark': '#F5DEB3',      # 深背景 - 小麦色
        'accent': '#D2691E',       # 强调色 - 巧克力色
        'accent_light': '#DEB887', # 浅强调 - 实木色
        'accent_hover': '#CD853F', # 悬停色 - 秘鲁色
        'text': '#4A4A4A',         # 主文字 - 深灰
        'text_light': '#8B7355',   # 浅文字 - 褐色
        'success': '#6B8E23',      # 成功 - 橄榄绿
        'error': '#CD5C5C',        # 错误 - 印度红
        'border': '#D2B48C',       # 边框 - 棕褐色
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Claude Code Remote")
        self.root.geometry("900x680")
        self.root.minsize(750, 550)

        # 设置窗口背景色
        self.root.configure(bg=self.COLORS['bg'])

        # 加载配置
        self.config = AppConfig.load()

        self.manager = AgentManager(log_callback=self.log)
        self.async_loop: Optional[asyncio.AbstractEventLoop] = None
        self.async_thread: Optional[threading.Thread] = None

        self._setup_styles()
        self._setup_ui()
        self._start_async_loop()
        self._load_saved_agents()

    def _setup_styles(self):
        """设置自定义样式"""
        style = ttk.Style()

        # 使用 clam 主题作为基础
        style.theme_use('clam')

        # 主框架样式
        style.configure('TFrame', background=self.COLORS['bg'])
        style.configure('TLabelframe', background=self.COLORS['bg'])
        style.configure('TLabelframe.Label',
                        background=self.COLORS['bg'],
                        foreground=self.COLORS['accent'],
                        font=('', 12, 'bold'))

        # 标签样式
        style.configure('TLabel',
                        background=self.COLORS['bg'],
                        foreground=self.COLORS['text'],
                        font=('', 11))

        # 输入框样式
        style.configure('TEntry',
                        fieldbackground=self.COLORS['bg_light'],
                        foreground=self.COLORS['text'],
                        borderwidth=2,
                        relief='solid')

        # 普通按钮样式
        style.configure('TButton',
                        background=self.COLORS['accent_light'],
                        foreground=self.COLORS['text'],
                        borderwidth=0,
                        focuscolor='none',
                        font=('', 10),
                        padding=(15, 8))
        style.map('TButton',
                  background=[('active', self.COLORS['accent_hover']),
                              ('pressed', self.COLORS['accent'])],
                  foreground=[('active', 'white')])

        # 强调按钮样式 (一键连接)
        style.configure('Accent.TButton',
                        background=self.COLORS['accent'],
                        foreground='white',
                        borderwidth=0,
                        focuscolor='none',
                        font=('', 12, 'bold'),
                        padding=(20, 10))
        style.map('Accent.TButton',
                  background=[('active', self.COLORS['accent_hover']),
                              ('pressed', '#A0522D')])

        # 小按钮样式
        style.configure('Small.TButton',
                        background=self.COLORS['bg_dark'],
                        foreground=self.COLORS['text'],
                        font=('', 9),
                        padding=(8, 4))

        # Treeview 样式
        style.configure('Treeview',
                        background=self.COLORS['bg_light'],
                        foreground=self.COLORS['text'],
                        fieldbackground=self.COLORS['bg_light'],
                        borderwidth=1,
                        relief='solid',
                        font=('', 10),
                        rowheight=28)
        style.configure('Treeview.Heading',
                        background=self.COLORS['bg_dark'],
                        foreground=self.COLORS['text'],
                        font=('', 10, 'bold'),
                        borderwidth=1,
                        relief='raised')
        style.map('Treeview',
                  background=[('selected', self.COLORS['accent_light'])],
                  foreground=[('selected', self.COLORS['text'])])

    def _setup_ui(self):
        """设置界面"""
        # 主框架
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # === 标题栏 ===
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 15))

        title_label = tk.Label(header_frame,
                               text="Claude Code Remote",
                               font=('', 24, 'bold'),
                               fg=self.COLORS['accent'],
                               bg=self.COLORS['bg'])
        title_label.pack(side=tk.LEFT)

        # 右侧按钮
        btn_frame = ttk.Frame(header_frame)
        btn_frame.pack(side=tk.RIGHT)

        ttk.Button(btn_frame, text="设置", command=self._show_settings, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="代理管理", command=self._show_agent_manager, style='Small.TButton').pack(side=tk.LEFT, padx=5)

        # === 快速连接区 ===
        quick_frame = ttk.LabelFrame(main_frame, text=" 快速连接 ", padding="15")
        quick_frame.pack(fill=tk.X, pady=(0, 12))

        # 变量初始化
        self.server_var = tk.StringVar(value=self.config.server_url)
        self.name_var = tk.StringVar(value=self.config.default_name)
        self.workspace_var = tk.StringVar(value=self.config.default_workspace)
        self.claude_path_var = tk.StringVar(value=self.config.claude_path)

        # 一行显示
        row = ttk.Frame(quick_frame)
        row.pack(fill=tk.X)

        # 一键连接按钮（左侧大按钮）
        connect_btn = ttk.Button(row, text="一键连接", command=self._quick_connect, style='Accent.TButton')
        connect_btn.pack(side=tk.LEFT, padx=(0, 15))

        # 工作目录
        ttk.Label(row, text="工作目录:").pack(side=tk.LEFT)
        workspace_entry = ttk.Entry(row, textvariable=self.workspace_var, width=40, font=('', 11))
        workspace_entry.pack(side=tk.LEFT, padx=(5, 5), fill=tk.X, expand=True)
        ttk.Button(row, text="...", width=3, command=self._browse_workspace, style='Small.TButton').pack(side=tk.LEFT)

        # 提示文本
        hint_frame = ttk.Frame(quick_frame)
        hint_frame.pack(fill=tk.X, pady=(10, 0))
        hint_label = tk.Label(hint_frame,
                              text=f"服务器: {self.config.server_url}  |  代理名称: {self.config.default_name}  |  点击「设置」修改配置",
                              font=('', 10),
                              fg=self.COLORS['text_light'],
                              bg=self.COLORS['bg'])
        hint_label.pack(side=tk.LEFT)
        self.hint_label = hint_label

        # === 日志区域（主要区域） ===
        log_frame = ttk.LabelFrame(main_frame, text=" 运行日志 ", padding="15")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=20,
            state=tk.DISABLED,
            font=("Menlo", 11),
            bg=self.COLORS['bg_light'],
            fg=self.COLORS['text'],
            relief='solid',
            borderwidth=1,
            insertbackground=self.COLORS['text']
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 日志底部操作栏
        log_actions = ttk.Frame(log_frame)
        log_actions.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(log_actions, text="清空日志", command=self._clear_log, style='Small.TButton').pack(side=tk.LEFT)

        # 配置日志文本标签颜色
        self.log_text.tag_configure('info', foreground=self.COLORS['text'])
        self.log_text.tag_configure('success', foreground=self.COLORS['success'])
        self.log_text.tag_configure('error', foreground=self.COLORS['error'])
        self.log_text.tag_configure('highlight', foreground=self.COLORS['accent'], font=('Menlo', 11, 'bold'))

        # 初始化隐藏的 tree（用于兼容现有代码）
        self._init_hidden_tree()

    def _init_hidden_tree(self):
        """初始化隐藏的代理列表（用于状态管理）"""
        # 创建隐藏的 Treeview 用于兼容现有代码
        self._hidden_frame = ttk.Frame(self.root)
        columns = ("name", "status", "agent_id", "url")
        self.tree = ttk.Treeview(self._hidden_frame, columns=columns, show="headings", height=1)
        for col in columns:
            self.tree.heading(col, text=col)

    def _clear_log(self):
        """清空日志"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.log("日志已清空")

    def _show_settings(self):
        """显示设置窗口"""
        settings_win = tk.Toplevel(self.root)
        settings_win.title("设置")
        settings_win.geometry("550x400")
        settings_win.configure(bg=self.COLORS['bg'])
        settings_win.transient(self.root)
        settings_win.grab_set()

        frame = ttk.Frame(settings_win, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        # 服务器设置
        ttk.Label(frame, text="服务器地址:", font=('', 11, 'bold')).grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        server_entry = ttk.Entry(frame, textvariable=self.server_var, width=50, font=('', 11))
        server_entry.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 15))

        # 代理名称
        ttk.Label(frame, text="默认代理名称:", font=('', 11, 'bold')).grid(row=2, column=0, sticky=tk.W, pady=(0, 5))
        name_entry = ttk.Entry(frame, textvariable=self.name_var, width=50, font=('', 11))
        name_entry.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 15))

        # 工作目录
        ttk.Label(frame, text="默认工作目录:", font=('', 11, 'bold')).grid(row=4, column=0, sticky=tk.W, pady=(0, 5))
        ws_frame = ttk.Frame(frame)
        ws_frame.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 15))
        workspace_entry = ttk.Entry(ws_frame, textvariable=self.workspace_var, width=45, font=('', 11))
        workspace_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(ws_frame, text="浏览", command=self._browse_workspace, style='Small.TButton').pack(side=tk.LEFT, padx=(5, 0))

        # Claude CLI 路径
        ttk.Label(frame, text="Claude CLI 路径:", font=('', 11, 'bold')).grid(row=6, column=0, sticky=tk.W, pady=(0, 5))
        self.claude_path_var = tk.StringVar(value=self.config.claude_path)
        claude_frame = ttk.Frame(frame)
        claude_frame.grid(row=7, column=0, columnspan=2, sticky=tk.EW, pady=(0, 5))
        claude_entry = ttk.Entry(claude_frame, textvariable=self.claude_path_var, width=45, font=('', 11))
        claude_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(claude_frame, text="自动检测", command=self._detect_claude, style='Small.TButton').pack(side=tk.LEFT, padx=(5, 0))

        hint = tk.Label(frame, text="留空则自动检测。如自动检测失败，请手动指定路径。",
                       font=('', 9), fg=self.COLORS['text_light'], bg=self.COLORS['bg'])
        hint.grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=(0, 20))

        # 按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=9, column=0, columnspan=2, pady=(10, 0))

        ttk.Button(btn_frame, text="测试连接", command=self._test_connection, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="保存", command=lambda: self._save_settings(settings_win), style='Accent.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=settings_win.destroy, style='Small.TButton').pack(side=tk.LEFT, padx=5)

        frame.columnconfigure(0, weight=1)

    def _detect_claude(self):
        """自动检测 Claude CLI"""
        path = find_claude_command()
        if path:
            self.claude_path_var.set(path)
            self.log(f"检测到 Claude CLI: {path}")
            messagebox.showinfo("成功", f"检测到 Claude CLI:\n{path}")
        else:
            messagebox.showwarning("未找到", "未能自动检测到 Claude CLI。\n\n请先安装:\nnpm install -g @anthropic-ai/claude-code\n\n或手动指定路径。")

    def _save_settings(self, window):
        """保存设置"""
        self.config.server_url = self.server_var.get().strip()
        self.config.default_name = self.name_var.get().strip()
        self.config.default_workspace = self.workspace_var.get().strip()
        self.config.claude_path = self.claude_path_var.get().strip()
        self.config.save()

        # 更新提示文本
        self.hint_label.config(text=f"服务器: {self.config.server_url}  |  代理名称: {self.config.default_name}  |  点击「设置」修改配置")

        self.log("配置已保存")
        window.destroy()

    def _show_agent_manager(self):
        """显示代理管理窗口"""
        agent_win = tk.Toplevel(self.root)
        agent_win.title("代理管理")
        agent_win.geometry("700x400")
        agent_win.configure(bg=self.COLORS['bg'])

        frame = ttk.Frame(agent_win, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="已注册的代理", font=('', 14, 'bold')).pack(anchor=tk.W, pady=(0, 10))

        # 表格
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "status", "agent_id", "url")
        agent_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        agent_tree.heading("name", text="名称")
        agent_tree.heading("status", text="状态")
        agent_tree.heading("agent_id", text="Agent ID")
        agent_tree.heading("url", text="访问地址")

        agent_tree.column("name", width=100, minwidth=80)
        agent_tree.column("status", width=70, minwidth=50)
        agent_tree.column("agent_id", width=100, minwidth=80)
        agent_tree.column("url", width=350, minwidth=200)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=agent_tree.yview)
        agent_tree.configure(yscrollcommand=scrollbar.set)

        agent_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 填充数据
        for agent_id, instance in self.manager.instances.items():
            agent_tree.insert("", tk.END, iid=agent_id, values=(
                instance.name,
                instance.status,
                instance.agent_id,
                instance.terminal_url
            ))

        # 操作按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(15, 0))

        def get_selected():
            selection = agent_tree.selection()
            if not selection:
                messagebox.showinfo("提示", "请先选择一个代理")
                return None
            return self.manager.instances.get(selection[0])

        def start_agent():
            instance = get_selected()
            if instance and instance.status != "online":
                self._run_async(self.manager.start_instance(instance))
                agent_tree.set(instance.agent_id, "status", "connecting")

        def stop_agent():
            instance = get_selected()
            if instance and instance.status == "online":
                self._run_async(self.manager.stop_instance(instance))
                agent_tree.set(instance.agent_id, "status", "stopping")

        def copy_url():
            instance = get_selected()
            if instance:
                self.root.clipboard_clear()
                self.root.clipboard_append(instance.terminal_url)
                self.log(f"已复制: {instance.terminal_url}")
                messagebox.showinfo("已复制", f"地址已复制到剪贴板:\n{instance.terminal_url}")

        def delete_agent():
            instance = get_selected()
            if instance:
                if messagebox.askyesno("确认", f"确定要删除代理 {instance.name} 吗?"):
                    if instance.status == "online":
                        self._run_async(self.manager.stop_instance(instance))
                    agent_tree.delete(instance.agent_id)
                    self.tree.delete(instance.agent_id)
                    del self.manager.instances[instance.agent_id]
                    self._save_agents()
                    self.log(f"已删除代理: {instance.name}")

        ttk.Button(btn_frame, text="启动", command=start_agent, style='Small.TButton').pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="停止", command=stop_agent, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="复制地址", command=copy_url, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="删除", command=delete_agent, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="刷新", command=lambda: self._refresh_agent_tree(agent_tree), style='Small.TButton').pack(side=tk.LEFT, padx=5)

        # 定时刷新状态
        def update_status():
            if agent_win.winfo_exists():
                for agent_id, instance in self.manager.instances.items():
                    try:
                        agent_tree.set(agent_id, "status", instance.status)
                    except:
                        pass
                agent_win.after(1000, update_status)
        update_status()

    def _refresh_agent_tree(self, tree):
        """刷新代理列表"""
        for item in tree.get_children():
            tree.delete(item)
        for agent_id, instance in self.manager.instances.items():
            tree.insert("", tk.END, iid=agent_id, values=(
                instance.name,
                instance.status,
                instance.agent_id,
                instance.terminal_url
            ))

    def _browse_workspace(self):
        """浏览工作目录"""
        from tkinter import filedialog
        path = filedialog.askdirectory(initialdir=self.workspace_var.get())
        if path:
            self.workspace_var.set(path)

    def _save_server_config(self):
        """保存服务器配置"""
        self.config.server_url = self.server_var.get().strip()
        self.config.default_name = self.name_var.get().strip()
        self.config.default_workspace = self.workspace_var.get().strip()
        if hasattr(self, 'claude_path_var'):
            self.config.claude_path = self.claude_path_var.get().strip()
        self.config.save()
        # 更新提示文本
        if hasattr(self, 'hint_label'):
            self.hint_label.config(text=f"服务器: {self.config.server_url}  |  代理名称: {self.config.default_name}  |  点击「设置」修改配置")
        self.log("配置已保存")

    def _test_connection(self):
        """测试服务器连接"""
        server = self.server_var.get().strip()
        if not server:
            messagebox.showerror("错误", "请输入服务器地址")
            return

        self.log(f"测试连接: {server}")

        def test():
            try:
                url = f"{server.rstrip('/')}/health"
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    self.log(f"连接成功! 服务器状态: {data.get('status', 'unknown')}")
                    self.log(f"  - 在线代理: {data.get('agents_online', 0)}")
                    self.log(f"  - 已连接客户端: {data.get('clients_connected', 0)}")
                    self.root.after(0, lambda: messagebox.showinfo("成功", "服务器连接正常!"))
            except urllib.error.URLError as e:
                self.log(f"连接失败: {e.reason}", "ERROR")
                self.root.after(0, lambda: messagebox.showerror("失败", f"无法连接服务器\n{e.reason}"))
            except Exception as e:
                self.log(f"连接失败: {e}", "ERROR")
                self.root.after(0, lambda: messagebox.showerror("失败", f"连接错误\n{e}"))

        threading.Thread(target=test, daemon=True).start()

    def _quick_connect(self):
        """快速连接 - 自动注册并启动"""
        server = self.server_var.get().strip()
        name = self.name_var.get().strip()
        workspace = self.workspace_var.get().strip()

        if not server:
            messagebox.showerror("错误", "请输入服务器地址")
            return
        if not name:
            messagebox.showerror("错误", "请输入代理名称")
            return
        if not workspace:
            messagebox.showerror("错误", "请输入工作目录")
            return

        # 保存配置
        self._save_server_config()

        self.log(f"正在自动注册并连接...")
        self._create_agent()

    def _load_saved_agents(self):
        """加载保存的代理"""
        for agent_data in self.config.saved_agents:
            try:
                instance = AgentInstance(**agent_data)
                instance.status = "disconnected"
                self.manager.instances[instance.agent_id] = instance
                self._add_instance_to_tree(instance)
            except Exception as e:
                self.log(f"加载代理失败: {e}", "ERROR")

    def _save_agents(self):
        """保存代理列表"""
        self.config.saved_agents = [
            {
                "agent_id": i.agent_id,
                "agent_key": i.agent_key,
                "name": i.name,
                "workspace": i.workspace,
                "server_url": i.server_url,
                "terminal_url": i.terminal_url,
            }
            for i in self.manager.instances.values()
        ]
        self.config.save()

    def _start_async_loop(self):
        """启动异步事件循环"""
        def run_loop():
            self.async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.async_loop)
            self.async_loop.run_forever()

        self.async_thread = threading.Thread(target=run_loop, daemon=True)
        self.async_thread.start()

    def _run_async(self, coro):
        """在异步循环中运行协程"""
        if self.async_loop:
            return asyncio.run_coroutine_threadsafe(coro, self.async_loop)

    def log(self, message: str, level: str = "info"):
        """添加日志"""
        def _log():
            self.log_text.configure(state=tk.NORMAL)

            # 根据内容自动判断标签
            tag = 'info'
            if 'ERROR' in message or '失败' in message or '错误' in message:
                tag = 'error'
            elif '成功' in message or '已连接' in message or '已启动' in message:
                tag = 'success'
            elif '====' in message or '地址' in message and 'http' in message:
                tag = 'highlight'

            self.log_text.insert(tk.END, message + "\n", tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.root.after(0, _log)

    def _create_agent(self):
        """创建新代理"""
        server = self.server_var.get().strip()
        name = self.name_var.get().strip()
        workspace = self.workspace_var.get().strip()

        if not server:
            messagebox.showerror("错误", "请输入服务器地址")
            return
        if not name:
            messagebox.showerror("错误", "请输入代理名称")
            return
        if not workspace:
            messagebox.showerror("错误", "请输入工作目录")
            return

        # 在后台线程中注册
        def register_and_start():
            instance = self.manager.register_agent(server, name, workspace)
            if instance:
                # 更新界面
                def update_ui():
                    self._add_instance_to_tree(instance)
                    self._save_agents()
                    # 显示访问信息
                    self.log(f"")
                    self.log(f"{'='*50}")
                    self.log(f"代理已创建! 手机访问地址:")
                    self.log(f"  {instance.terminal_url}")
                    self.log(f"{'='*50}")
                    self.log(f"")

                self.root.after(0, update_ui)
                # 启动实例
                self._run_async(self.manager.start_instance(instance))

        threading.Thread(target=register_and_start, daemon=True).start()

    def _add_instance_to_tree(self, instance: AgentInstance):
        """添加实例到列表"""
        self.tree.insert("", tk.END, iid=instance.agent_id, values=(
            instance.name,
            instance.status,
            instance.agent_id,
            instance.terminal_url
        ))
        self._schedule_status_update()

    def _schedule_status_update(self):
        """定时更新状态"""
        def update():
            for agent_id, instance in self.manager.instances.items():
                try:
                    self.tree.set(agent_id, "status", instance.status)
                except:
                    pass
            self.root.after(1000, update)
        self.root.after(1000, update)

    def _get_selected_instance(self) -> Optional[AgentInstance]:
        """获取选中的实例"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个代理")
            return None
        agent_id = selection[0]
        return self.manager.instances.get(agent_id)

    def _start_selected(self):
        """启动选中的实例"""
        instance = self._get_selected_instance()
        if instance and instance.status != "online":
            self._run_async(self.manager.start_instance(instance))

    def _stop_selected(self):
        """停止选中的实例"""
        instance = self._get_selected_instance()
        if instance and instance.status == "online":
            self._run_async(self.manager.stop_instance(instance))

    def _copy_url(self):
        """复制访问地址"""
        instance = self._get_selected_instance()
        if instance:
            self.root.clipboard_clear()
            self.root.clipboard_append(instance.terminal_url)
            self.log(f"已复制: {instance.terminal_url}")

    def _delete_selected(self):
        """删除选中的实例"""
        instance = self._get_selected_instance()
        if instance:
            if instance.status == "online":
                self._run_async(self.manager.stop_instance(instance))

            self.tree.delete(instance.agent_id)
            del self.manager.instances[instance.agent_id]
            self._save_agents()
            self.log(f"已删除代理: {instance.name}")

    def run(self):
        """运行应用"""
        self.log("Claude Code Remote GUI 已启动")
        self.log(f"配置文件: {get_config_file()}")
        self.log("")
        self.log("使用步骤:")
        self.log("  1. 输入中继服务器地址")
        self.log("  2. 点击「测试连接」确认服务器正常")
        self.log("  3. 点击「一键连接」自动注册并启动")
        self.log("  4. 用手机扫描二维码或访问显示的地址")
        self.log("")

        def on_closing():
            # 保存配置
            self._save_agents()
            # 停止所有实例
            for instance in list(self.manager.instances.values()):
                if instance.status == "online":
                    self._run_async(self.manager.stop_instance(instance))
            self.root.destroy()

        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        self.root.mainloop()


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    app = AgentGUI()
    app.run()
