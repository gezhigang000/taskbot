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


@dataclass
class AppConfig:
    """应用配置"""
    server_url: str = "http://taskbot.com.cn"
    default_name: str = ""
    default_workspace: str = ""
    saved_agents: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.default_name:
            self.default_name = platform.node()
        if not self.default_workspace:
            self.default_workspace = str(Path.home())

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

        # 启动 Claude Code 进程
        try:
            os.makedirs(instance.workspace, exist_ok=True)
            instance.master_fd, instance.slave_fd = pty.openpty()

            instance.process = await asyncio.create_subprocess_exec(
                'claude',
                stdin=instance.slave_fd,
                stdout=instance.slave_fd,
                stderr=instance.slave_fd,
                cwd=instance.workspace,
                env={**os.environ, 'TERM': 'xterm-256color'}
            )
            self.log(f"Claude Code 已启动 (PID: {instance.process.pid})")
        except FileNotFoundError:
            self.log("错误: 未找到 claude 命令，请确保已安装 Claude Code CLI", "ERROR")
            instance.status = "error"
            return
        except Exception as e:
            self.log(f"启动 Claude Code 失败: {e}", "ERROR")
            instance.status = "error"
            return

        # 连接到中继服务器
        ws_url = instance.server_url.replace('http://', 'ws://').replace('https://', 'wss://')
        url = f"{ws_url}/ws/agent/{instance.agent_id}?key={instance.agent_key}"

        try:
            instance.websocket = await websockets.connect(url)
            instance.status = "online"
            self.log(f"代理 {instance.name} 已连接到中继服务器")

            # 启动转发任务
            await asyncio.gather(
                self._output_loop(instance),
                self._receive_loop(instance),
                self._heartbeat_loop(instance),
            )

        except Exception as e:
            self.log(f"连接失败: {e}", "ERROR")
            instance.status = "error"

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

        # === 标题区域 ===
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 15))

        title_label = tk.Label(header_frame,
                               text="Claude Code Remote",
                               font=('', 24, 'bold'),
                               fg=self.COLORS['accent'],
                               bg=self.COLORS['bg'])
        title_label.pack(side=tk.LEFT)

        subtitle_label = tk.Label(header_frame,
                                  text="远程访问您的 Claude Code 终端",
                                  font=('', 11),
                                  fg=self.COLORS['text_light'],
                                  bg=self.COLORS['bg'])
        subtitle_label.pack(side=tk.LEFT, padx=(15, 0), pady=(8, 0))

        # === 服务器配置 ===
        server_frame = ttk.LabelFrame(main_frame, text=" 服务器设置 ", padding="15")
        server_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(server_frame, text="服务器地址:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.server_var = tk.StringVar(value=self.config.server_url)
        server_entry = ttk.Entry(server_frame, textvariable=self.server_var, width=45, font=('', 11))
        server_entry.grid(row=0, column=1, sticky=tk.EW, pady=5, padx=(10, 10))

        btn_frame1 = ttk.Frame(server_frame)
        btn_frame1.grid(row=0, column=2, pady=5)
        ttk.Button(btn_frame1, text="保存", command=self._save_server_config, style='Small.TButton').pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame1, text="测试连接", command=self._test_connection, style='Small.TButton').pack(side=tk.LEFT, padx=2)

        server_frame.columnconfigure(1, weight=1)

        # === 快速连接 ===
        quick_frame = ttk.LabelFrame(main_frame, text=" 快速连接 ", padding="15")
        quick_frame.pack(fill=tk.X, pady=(0, 12))

        # 第一行：代理名称
        row1 = ttk.Frame(quick_frame)
        row1.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(row1, text="代理名称:").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value=self.config.default_name)
        name_entry = ttk.Entry(row1, textvariable=self.name_var, width=30, font=('', 11))
        name_entry.pack(side=tk.LEFT, padx=(10, 20))

        ttk.Label(row1, text="工作目录:").pack(side=tk.LEFT)
        self.workspace_var = tk.StringVar(value=self.config.default_workspace)
        workspace_entry = ttk.Entry(row1, textvariable=self.workspace_var, width=40, font=('', 11))
        workspace_entry.pack(side=tk.LEFT, padx=(10, 5), fill=tk.X, expand=True)
        ttk.Button(row1, text="...", width=3, command=self._browse_workspace, style='Small.TButton').pack(side=tk.LEFT)

        # 第二行：一键连接按钮
        row2 = ttk.Frame(quick_frame)
        row2.pack(fill=tk.X, pady=(8, 0))

        connect_btn = ttk.Button(row2, text="一键连接", command=self._quick_connect, style='Accent.TButton')
        connect_btn.pack(side=tk.LEFT)

        hint_label = tk.Label(row2,
                              text="点击自动注册并连接到服务器，生成手机访问地址",
                              font=('', 10),
                              fg=self.COLORS['text_light'],
                              bg=self.COLORS['bg'])
        hint_label.pack(side=tk.LEFT, padx=(15, 0))

        # === 实例列表 ===
        list_frame = ttk.LabelFrame(main_frame, text=" 已连接的代理 ", padding="15")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        # 表格容器
        tree_container = ttk.Frame(list_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)

        # 表格
        columns = ("name", "status", "agent_id", "url")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=5)
        self.tree.heading("name", text="名称")
        self.tree.heading("status", text="状态")
        self.tree.heading("agent_id", text="Agent ID")
        self.tree.heading("url", text="访问地址")

        self.tree.column("name", width=120, minwidth=80)
        self.tree.column("status", width=80, minwidth=60)
        self.tree.column("agent_id", width=120, minwidth=100)
        self.tree.column("url", width=350, minwidth=200)

        scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 操作按钮
        action_frame = ttk.Frame(list_frame)
        action_frame.pack(fill=tk.X, pady=(12, 0))

        ttk.Button(action_frame, text="启动", command=self._start_selected, style='Small.TButton').pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(action_frame, text="停止", command=self._stop_selected, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="复制地址", command=self._copy_url, style='Small.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="删除", command=self._delete_selected, style='Small.TButton').pack(side=tk.LEFT, padx=5)

        # === 日志区域 ===
        log_frame = ttk.LabelFrame(main_frame, text=" 运行日志 ", padding="15")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=8,
            state=tk.DISABLED,
            font=("Menlo", 10),
            bg=self.COLORS['bg_light'],
            fg=self.COLORS['text'],
            relief='solid',
            borderwidth=1,
            insertbackground=self.COLORS['text']
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 配置日志文本标签颜色
        self.log_text.tag_configure('info', foreground=self.COLORS['text'])
        self.log_text.tag_configure('success', foreground=self.COLORS['success'])
        self.log_text.tag_configure('error', foreground=self.COLORS['error'])
        self.log_text.tag_configure('highlight', foreground=self.COLORS['accent'], font=('Menlo', 10, 'bold'))

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
        self.config.save()
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
