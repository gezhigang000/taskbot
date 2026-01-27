#!/usr/bin/env python3
"""
Claude Code Remote - 本地代理
自动注册并显示二维码，方便手机扫描访问

用法:
  python agent.py --server http://relay.example.com:8080 --name 我的电脑
"""

import argparse
import asyncio
import json
import os
import pty
import select
import signal
import sys
import urllib.request
import urllib.parse
from typing import Optional

try:
    import websockets
except ImportError:
    print("正在安装 websockets...")
    os.system(f"{sys.executable} -m pip install -q websockets")
    import websockets


# ============================================================================
# 终端二维码生成（纯 ASCII，无需额外依赖）
# ============================================================================

def generate_qr_ascii(data: str) -> str:
    """
    生成简单的 ASCII 二维码
    使用在线 API 或本地生成
    """
    try:
        # 尝试使用 qrcode 库
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(data)
        qr.make(fit=True)

        # 转换为 ASCII
        lines = []
        matrix = qr.get_matrix()
        for row in matrix:
            line = ""
            for cell in row:
                line += "██" if cell else "  "
            lines.append(line)
        return "\n".join(lines)

    except ImportError:
        # 没有 qrcode 库，使用简化版本
        return _generate_simple_qr(data)


def _generate_simple_qr(data: str) -> str:
    """简单的二维码替代方案（显示链接即可）"""
    return f"""
┌────────────────────────────────────────────┐
│                                            │
│   请安装 qrcode 库以显示二维码:            │
│   pip install qrcode                       │
│                                            │
│   或直接在手机浏览器输入以下地址:          │
│                                            │
└────────────────────────────────────────────┘
"""


# ============================================================================
# Claude Code 进程管理
# ============================================================================

class ClaudeCodeProcess:
    """管理 Claude Code 进程"""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.process: Optional[asyncio.subprocess.Process] = None
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.running = False

    async def start(self):
        """启动 Claude Code"""
        print(f"[Claude] 启动中，工作目录: {self.workspace_dir}")

        os.makedirs(self.workspace_dir, exist_ok=True)
        self.master_fd, self.slave_fd = pty.openpty()

        self.process = await asyncio.create_subprocess_exec(
            'claude',
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            cwd=self.workspace_dir,
            env={**os.environ, 'TERM': 'xterm-256color'}
        )

        self.running = True
        print(f"[Claude] 已启动 (PID: {self.process.pid})")

    async def read_output(self) -> Optional[str]:
        """读取输出"""
        if not self.master_fd:
            return None
        try:
            ready, _, _ = select.select([self.master_fd], [], [], 0.1)
            if ready:
                data = os.read(self.master_fd, 4096)
                if data:
                    return data.decode('utf-8', errors='replace')
        except:
            pass
        return None

    async def write_input(self, text: str):
        """写入输入"""
        if self.master_fd and self.running:
            try:
                os.write(self.master_fd, text.encode('utf-8'))
            except:
                pass

    async def stop(self):
        """停止进程"""
        print("[Claude] 停止中...")
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except:
                self.process.kill()
        if self.master_fd:
            os.close(self.master_fd)
        if self.slave_fd:
            os.close(self.slave_fd)
        print("[Claude] 已停止")


# ============================================================================
# 本地代理
# ============================================================================

class LocalAgent:
    """本地代理"""

    def __init__(self, server_url: str, name: str, workspace: str):
        self.server_url = server_url.rstrip('/')
        self.name = name
        self.workspace = workspace
        self.agent_id: Optional[str] = None
        self.agent_key: Optional[str] = None
        self.ws = None
        self.claude: Optional[ClaudeCodeProcess] = None
        self.running = False

    def register(self) -> bool:
        """向中继服务器注册"""
        print(f"\n[Agent] 正在注册到 {self.server_url}...")

        try:
            url = f"{self.server_url}/api/agents?name={urllib.parse.quote(self.name)}"
            req = urllib.request.Request(url, method='POST')
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                self.agent_id = data['agent_id']
                self.agent_key = data['agent_key']
                print(f"[Agent] 注册成功!")
                print(f"        Agent ID: {self.agent_id}")
                return True
        except Exception as e:
            print(f"[Agent] 注册失败: {e}")
            return False

    def show_access_info(self):
        """显示访问信息和二维码"""
        # 构建访问 URL
        base_url = self.server_url.replace('ws://', 'http://').replace('wss://', 'https://')
        terminal_url = f"{base_url}/terminal/{self.agent_id}"

        print("\n")
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║                    手机访问地址                              ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        print(f"\n  {terminal_url}\n")

        # 显示二维码
        qr = generate_qr_ascii(terminal_url)
        print(qr)

        print("\n════════════════════════════════════════════════════════════════")
        print("  用手机扫描上方二维码，或在浏览器中输入上方地址")
        print("════════════════════════════════════════════════════════════════\n")

    async def connect(self) -> bool:
        """连接到中继服务器"""
        ws_url = self.server_url.replace('http://', 'ws://').replace('https://', 'wss://')
        url = f"{ws_url}/ws/agent/{self.agent_id}?key={self.agent_key}"

        try:
            self.ws = await websockets.connect(url)
            print("[Agent] 已连接到中继服务器")
            return True
        except Exception as e:
            print(f"[Agent] 连接失败: {e}")
            return False

    async def start_claude(self):
        """启动 Claude Code"""
        self.claude = ClaudeCodeProcess(self.workspace)
        await self.claude.start()

    async def run(self):
        """主循环"""
        self.running = True
        reconnect_delay = 5

        while self.running:
            if not await self.connect():
                print(f"[Agent] {reconnect_delay}秒后重连...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue

            reconnect_delay = 5

            if not self.claude or not self.claude.running:
                await self.start_claude()

            # 启动任务
            tasks = [
                asyncio.create_task(self._output_loop()),
                asyncio.create_task(self._heartbeat_loop()),
                asyncio.create_task(self._receive_loop()),
            ]

            try:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            except Exception as e:
                print(f"[Agent] 错误: {e}")

            print(f"[Agent] 连接断开，{reconnect_delay}秒后重连...")
            await asyncio.sleep(reconnect_delay)

    async def _output_loop(self):
        """转发 Claude 输出"""
        while self.running and self.ws and self.claude:
            try:
                output = await self.claude.read_output()
                if output:
                    await self.ws.send(json.dumps({"type": "output", "data": output}))
            except websockets.exceptions.ConnectionClosed:
                break
            except:
                break
            await asyncio.sleep(0.05)

    async def _heartbeat_loop(self):
        """心跳"""
        while self.running and self.ws:
            try:
                await self.ws.send(json.dumps({"type": "heartbeat"}))
                await asyncio.sleep(30)
            except:
                break

    async def _receive_loop(self):
        """接收消息"""
        while self.running and self.ws:
            try:
                data = await self.ws.recv()
                msg = json.loads(data)
                if msg["type"] == "input" and self.claude:
                    await self.claude.write_input(msg["data"])
            except:
                break

    async def shutdown(self):
        """关闭"""
        print("\n[Agent] 正在关闭...")
        self.running = False
        if self.claude:
            await self.claude.stop()
        if self.ws:
            await self.ws.close()
        print("[Agent] 已关闭")


# ============================================================================
# 主函数
# ============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description='Claude Code 本地代理',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python agent.py -s http://relay.example.com:8080
  python agent.py -s http://relay.example.com:8080 -n "我的MacBook" -w ~/projects
        """
    )
    parser.add_argument(
        '--server', '-s',
        required=True,
        help='中继服务器地址 (例如: http://relay.example.com:8080)'
    )
    parser.add_argument(
        '--name', '-n',
        default=os.uname().nodename,
        help='代理名称 (默认: 计算机名)'
    )
    parser.add_argument(
        '--workspace', '-w',
        default=os.getcwd(),
        help='工作目录 (默认: 当前目录)'
    )

    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════════════════════╗
║            Claude Code Remote - 本地代理                     ║
╚══════════════════════════════════════════════════════════════╝
    """)

    agent = LocalAgent(
        server_url=args.server,
        name=args.name,
        workspace=args.workspace,
    )

    # 注册
    if not agent.register():
        print("\n注册失败，请检查服务器地址是否正确")
        sys.exit(1)

    # 显示访问信息
    agent.show_access_info()

    # 信号处理
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(agent.shutdown()))

    try:
        await agent.run()
    except KeyboardInterrupt:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
