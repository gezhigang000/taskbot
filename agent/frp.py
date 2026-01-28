#!/usr/bin/env python3
"""
FRP 客户端管理
自动下载、配置和启动 frpc，建立到公网服务器的隧道
"""

import hashlib
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("claude-remote")

# FRP 版本
FRP_VERSION = "0.61.1"

# 下载地址模板
FRP_DOWNLOAD_URL = (
    "https://github.com/fatedier/frp/releases/download/v{version}/"
    "frp_{version}_{os}_{arch}.tar.gz"
)


def get_frp_dir() -> Path:
    """获取 FRP 安装目录"""
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    d = base / "ClaudeCodeRemote" / "frp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_frpc_path() -> Optional[str]:
    """获取 frpc 可执行文件路径
    
    查找顺序：
    1. 应用内置资源（打包后）
    2. 开发目录资源
    3. 用户数据目录（下载缓存）
    4. 系统 PATH
    """
    # 1. 检查应用内置资源（PyInstaller 打包后）
    if getattr(sys, 'frozen', False):
        # 打包后的应用
        bundle_dir = Path(sys._MEIPASS) / "resources"
        frpc = bundle_dir / "frpc"
        if frpc.exists() and os.access(frpc, os.X_OK):
            return str(frpc)
    
    # 2. 检查开发目录资源
    dev_resources = Path(__file__).parent / "resources" / "frpc"
    if dev_resources.exists() and os.access(dev_resources, os.X_OK):
        return str(dev_resources)
    
    # 3. 检查用户数据目录（下载缓存）
    local_path = get_frp_dir() / "frpc"
    if local_path.exists() and os.access(local_path, os.X_OK):
        return str(local_path)
    
    # 4. 检查系统 PATH
    found = shutil.which("frpc")
    if found:
        return found

    return None


def _get_platform_info():
    """获取当前平台信息（用于下载）"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    os_name = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(
        system, system
    )

    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)

    return os_name, arch


def download_frpc(progress_callback=None) -> Optional[str]:
    """下载 frpc 到本地（如果尚未安装）"""
    # 先检查是否已有 frpc
    existing = get_frpc_path()
    if existing:
        logger.debug(f"frpc 已存在: {existing}")
        return existing

    os_name, arch = _get_platform_info()
    url = FRP_DOWNLOAD_URL.format(version=FRP_VERSION, os=os_name, arch=arch)

    # 决定保存位置：开发环境保存到 agent/resources，否则保存到用户目录
    if not getattr(sys, 'frozen', False):
        # 开发环境：保存到项目资源目录
        frp_dir = Path(__file__).parent / "resources"
        frp_dir.mkdir(parents=True, exist_ok=True)
    else:
        # 打包环境：保存到用户目录
        frp_dir = get_frp_dir()
    
    frpc_path = frp_dir / "frpc"

    logger.info(f"首次运行，正在下载 frpc v{FRP_VERSION}...")
    logger.info(f"下载地址: {url}")
    logger.info(f"保存位置: {frpc_path}")
    if progress_callback:
        progress_callback(f"首次运行，正在下载 frpc...")

    try:
        # 下载到临时文件
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name
            urllib.request.urlretrieve(url, tmp_path)

        # 解压
        import tarfile
        with tarfile.open(tmp_path, "r:gz") as tar:
            # 查找 frpc 文件（在子目录中）
            for member in tar.getmembers():
                if member.name.endswith("/frpc") or member.name == "frpc":
                    # 提取文件内容
                    f = tar.extractfile(member)
                    if f:
                        frpc_path.write_bytes(f.read())
                        break

        # 删除临时文件
        os.unlink(tmp_path)

        # 设置可执行权限
        os.chmod(frpc_path, 0o755)
        logger.info(f"frpc 已安装: {frpc_path}")

        if progress_callback:
            progress_callback("frpc 下载完成")

        return str(frpc_path)
        
    except Exception as e:
        logger.error(f"下载 frpc 失败: {e}")
        if progress_callback:
            progress_callback(f"下载失败: {e}")
        return None


class FRPClient:
    """FRP 客户端管理器"""

    def __init__(
        self,
        server_addr: str,
        server_port: int = 7000,
        auth_token: str = "",
        agent_id: str = "",
        local_port: int = 8080,
    ):
        self.server_addr = server_addr
        self.server_port = server_port
        self.auth_token = auth_token
        self.agent_id = agent_id or self._gen_agent_id()
        self.local_port = local_port
        self.process: Optional[subprocess.Popen] = None
        self._config_path: Optional[str] = None

    @staticmethod
    def _gen_agent_id() -> str:
        """生成唯一 agent ID"""
        hostname = platform.node().lower().replace(".", "-")
        # 取机器信息的哈希前6位
        uid = hashlib.md5(
            f"{hostname}-{os.getuid()}".encode()
        ).hexdigest()[:6]
        return f"{hostname}-{uid}"

    @property
    def public_url(self) -> str:
        """获取公网访问地址"""
        subdomain = getattr(self, '_subdomain', self.agent_id)
        return f"http://{subdomain}.{self.server_addr}"

    def _write_config(self) -> str:
        """生成 frpc 配置文件"""
        # 使用时间戳确保代理名称和子域名唯一，避免 "proxy already exists" 和 "router config conflict" 错误
        import time
        proxy_suffix = hex(int(time.time()) & 0xFFFF)[2:]  # 取时间戳后4位十六进制
        proxy_name = f"claude-{self.agent_id}-{proxy_suffix}"
        # 子域名也需要唯一，否则会出现 router config conflict
        self._subdomain = f"{self.agent_id}-{proxy_suffix}"
        
        config = f"""serverAddr = "{self.server_addr}"
serverPort = {self.server_port}
"""
        if self.auth_token:
            config += f"""
auth.method = "token"
auth.token = "{self.auth_token}"
"""

        config += f"""
[[proxies]]
name = "{proxy_name}"
type = "http"
localPort = {self.local_port}
customDomains = ["{self._subdomain}.{self.server_addr}"]
"""

        config_dir = get_frp_dir()
        config_path = config_dir / "frpc.toml"
        config_path.write_text(config, encoding="utf-8")
        self._config_path = str(config_path)
        logger.info(f"FRP 配置: {config_path}")
        logger.info(f"代理名称: {proxy_name}")
        logger.info(f"子域名: {self._subdomain}.{self.server_addr}")
        return self._config_path

    def start(self, frpc_path: Optional[str] = None, timeout: float = 10.0) -> bool:
        """启动 FRP 客户端
        
        Args:
            frpc_path: frpc 可执行文件路径
            timeout: 等待连接成功的超时时间（秒）
        """
        frpc = frpc_path or get_frpc_path()
        if not frpc:
            logger.error("未找到 frpc，请先下载")
            return False

        config_path = self._write_config()
        logger.info(f"正在启动 frpc: {frpc}")
        logger.info(f"配置文件: {config_path}")
        logger.info(f"目标服务器: {self.server_addr}:{self.server_port}")
        logger.info(f"本地端口: {self.local_port}")
        logger.info(f"Agent ID: {self.agent_id}")

        try:
            self.process = subprocess.Popen(
                [frpc, "-c", config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            logger.info(f"frpc 进程已启动 (PID: {self.process.pid})")
            
            # 等待并检查启动状态
            import time
            start_time = time.time()
            connected = False
            error_msg = None
            
            while time.time() - start_time < timeout:
                # 检查进程是否异常退出
                if self.process.poll() is not None:
                    # 进程已退出，读取所有输出
                    remaining = self.process.stdout.read() if self.process.stdout else ""
                    logger.error(f"frpc 进程异常退出，退出码: {self.process.returncode}")
                    if remaining:
                        for line in remaining.strip().split("\n"):
                            logger.error(f"  frpc: {line}")
                    return False
                
                # 尝试读取输出
                line = self._read_line_nonblock()
                if line:
                    line = line.strip()
                    if line:
                        # 记录 frpc 输出
                        if "error" in line.lower() or "failed" in line.lower():
                            logger.warning(f"frpc: {line}")
                            error_msg = line
                        elif "start proxy success" in line.lower():
                            logger.info(f"frpc: {line}")
                            connected = True
                            break
                        elif "login to server success" in line.lower():
                            logger.info(f"frpc: {line}")
                        else:
                            logger.debug(f"frpc: {line}")
                else:
                    time.sleep(0.1)
            
            if connected:
                logger.info(f"FRP 隧道连接成功: {self.public_url}")
                return True
            elif error_msg:
                logger.error(f"FRP 隧道连接失败: {error_msg}")
                return False
            else:
                # 超时但进程还在运行，可能是网络慢
                if self.is_running():
                    logger.warning(f"FRP 隧道启动超时({timeout}秒)，但进程仍在运行")
                    logger.warning("可能是网络连接较慢，隧道可能稍后建立成功")
                    return True
                else:
                    logger.error("FRP 隧道启动失败，进程已退出")
                    return False
                    
        except Exception as e:
            logger.error(f"启动 frpc 失败: {e}")
            return False

    def _read_line_nonblock(self) -> Optional[str]:
        """非阻塞读取一行输出"""
        if self.process and self.process.stdout:
            try:
                import select as sel
                ready, _, _ = sel.select([self.process.stdout], [], [], 0)
                if ready:
                    return self.process.stdout.readline()
            except (OSError, ValueError):
                pass
        return None

    def stop(self):
        """停止 FRP 客户端"""
        if self.process:
            try:
                self.process.send_signal(signal.SIGTERM)
                self.process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self.process.kill()
            self.process = None
            logger.info("frpc 已停止")

    def is_running(self) -> bool:
        """检查 FRP 是否在运行"""
        return self.process is not None and self.process.poll() is None

    def read_output(self) -> Optional[str]:
        """读取 frpc 输出（非阻塞）"""
        if self.process and self.process.stdout:
            try:
                import select as sel
                ready, _, _ = sel.select([self.process.stdout], [], [], 0)
                if ready:
                    return self.process.stdout.readline()
            except (OSError, ValueError):
                pass
        return None
