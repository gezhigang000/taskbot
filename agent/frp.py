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
    """获取 frpc 可执行文件路径"""
    # 先检查系统 PATH
    found = shutil.which("frpc")
    if found:
        return found

    # 检查本地安装
    local_path = get_frp_dir() / "frpc"
    if local_path.exists() and os.access(local_path, os.X_OK):
        return str(local_path)

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


def download_frpc(progress_callback=None) -> str:
    """下载 frpc 到本地"""
    os_name, arch = _get_platform_info()
    url = FRP_DOWNLOAD_URL.format(version=FRP_VERSION, os=os_name, arch=arch)

    frp_dir = get_frp_dir()
    frpc_path = frp_dir / "frpc"

    if frpc_path.exists():
        logger.info(f"frpc 已存在: {frpc_path}")
        return str(frpc_path)

    logger.info(f"下载 frpc: {url}")
    if progress_callback:
        progress_callback(f"正在下载 frpc v{FRP_VERSION}...")

    # 下载到临时文件
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name
        urllib.request.urlretrieve(url, tmp_path)

    try:
        # 解压
        import tarfile

        with tarfile.open(tmp_path, "r:gz") as tar:
            # 查找 frpc 文件
            for member in tar.getmembers():
                if member.name.endswith("/frpc") or member.name == "frpc":
                    member.name = "frpc"
                    tar.extract(member, path=str(frp_dir))
                    break

        # 设置可执行权限
        os.chmod(frpc_path, 0o755)
        logger.info(f"frpc 已安装: {frpc_path}")

        if progress_callback:
            progress_callback("frpc 下载完成")

        return str(frpc_path)
    finally:
        os.unlink(tmp_path)


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
        return f"http://{self.agent_id}.{self.server_addr}"

    def _write_config(self) -> str:
        """生成 frpc 配置文件"""
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
name = "claude-{self.agent_id}"
type = "http"
localPort = {self.local_port}
customDomains = ["{self.agent_id}.{self.server_addr}"]
"""

        config_dir = get_frp_dir()
        config_path = config_dir / "frpc.toml"
        config_path.write_text(config, encoding="utf-8")
        self._config_path = str(config_path)
        logger.info(f"FRP 配置: {config_path}")
        return self._config_path

    def start(self, frpc_path: Optional[str] = None) -> bool:
        """启动 FRP 客户端"""
        frpc = frpc_path or get_frpc_path()
        if not frpc:
            logger.error("未找到 frpc，请先下载")
            return False

        config_path = self._write_config()

        try:
            self.process = subprocess.Popen(
                [frpc, "-c", config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            logger.info(f"frpc 已启动 (PID: {self.process.pid})")
            return True
        except Exception as e:
            logger.error(f"启动 frpc 失败: {e}")
            return False

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
