#!/usr/bin/env python3
"""
Claude Code Remote Access - Relay Server
部署在有公网 IP 的服务器上，作为中继转发消息

架构：
  手机/浏览器 ←──WebSocket──→ Relay Server ←──WebSocket──→ Local Agent
                (客户端)          (公网)         (本地 Claude Code)
"""

import asyncio
import json
import logging
import secrets
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Set
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import hashlib
import socket


# ============================================================================
# Admin Configuration
# ============================================================================

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "taskbot2024"
ADMIN_SESSIONS = set()  # 存储有效的 session token


# ============================================================================
# Logging Configuration
# ============================================================================

def setup_logging():
    """配置日志系统"""
    # 创建日志格式
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)

    # 设置第三方库日志级别
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    return logging.getLogger("relay")

logger = setup_logging()


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Agent:
    """本地代理（运行 Claude Code 的机器）"""
    agent_id: str
    agent_key: str  # 认证密钥
    websocket: Optional[WebSocket] = None
    connected_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    name: str = "unnamed"
    is_online: bool = False


@dataclass
class Client:
    """客户端（手机/浏览器）"""
    client_id: str
    websocket: WebSocket
    connected_agent: Optional[str] = None  # 连接到哪个 agent
    connected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Session:
    """会话：客户端和代理之间的连接"""
    session_id: str
    client_id: str
    agent_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================================
# Relay Server
# ============================================================================

class RelayServer:
    """中继服务器核心逻辑"""

    def __init__(self):
        # 已注册的代理（本地端）
        self.agents: Dict[str, Agent] = {}
        # 已连接的客户端（手机/浏览器）
        self.clients: Dict[str, Client] = {}
        # 活跃会话
        self.sessions: Dict[str, Session] = {}
        # Agent ID -> Client IDs 的映射
        self.agent_clients: Dict[str, Set[str]] = {}

    def register_agent(self, name: str = "default") -> Agent:
        """注册一个新的代理"""
        agent_id = secrets.token_urlsafe(8)
        agent_key = secrets.token_urlsafe(32)

        agent = Agent(
            agent_id=agent_id,
            agent_key=agent_key,
            name=name,
        )
        self.agents[agent_id] = agent
        self.agent_clients[agent_id] = set()

        logger.info(f"Agent registered: id={agent_id}, name={name}")
        return agent

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """获取代理"""
        return self.agents.get(agent_id)

    def verify_agent(self, agent_id: str, agent_key: str) -> Optional[Agent]:
        """验证代理凭证"""
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning(f"Agent verification failed: agent_id={agent_id} not found")
            return None
        if agent.agent_key != agent_key:
            logger.warning(f"Agent verification failed: agent_id={agent_id} invalid key")
            return None
        logger.debug(f"Agent verified: agent_id={agent_id}")
        return agent

    async def connect_agent(self, agent: Agent, websocket: WebSocket):
        """代理连接"""
        agent.websocket = websocket
        agent.connected_at = datetime.utcnow()
        agent.last_heartbeat = datetime.utcnow()
        agent.is_online = True

        logger.info(f"Agent connected: id={agent.agent_id}, name={agent.name}")

        # 通知所有等待的客户端
        client_count = len(self.agent_clients.get(agent.agent_id, []))
        if client_count > 0:
            logger.info(f"Notifying {client_count} waiting clients about agent {agent.agent_id}")
        for client_id in self.agent_clients.get(agent.agent_id, []):
            client = self.clients.get(client_id)
            if client and client.websocket:
                await self.send_to_client(client, {
                    "type": "agent_online",
                    "agent_id": agent.agent_id,
                })

    def disconnect_agent(self, agent: Agent):
        """代理断开"""
        agent.websocket = None
        agent.is_online = False
        logger.info(f"Agent disconnected: id={agent.agent_id}, name={agent.name}")

    async def connect_client(self, client_id: str, websocket: WebSocket) -> Client:
        """客户端连接"""
        client = Client(
            client_id=client_id,
            websocket=websocket,
        )
        self.clients[client_id] = client
        logger.debug(f"Client created: id={client_id}")
        return client

    def disconnect_client(self, client: Client):
        """客户端断开"""
        if client.connected_agent:
            self.agent_clients[client.connected_agent].discard(client.client_id)
        if client.client_id in self.clients:
            del self.clients[client.client_id]
        logger.info(f"Client disconnected: id={client.client_id}, agent={client.connected_agent}")

    async def bind_client_to_agent(self, client: Client, agent_id: str) -> bool:
        """将客户端绑定到代理"""
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning(f"Client {client.client_id} tried to bind to non-existent agent {agent_id}")
            return False

        client.connected_agent = agent_id
        self.agent_clients[agent_id].add(client.client_id)
        logger.info(f"Client {client.client_id} bound to agent {agent_id}")
        return True

    async def forward_to_agent(self, agent: Agent, message: dict) -> bool:
        """转发消息到代理"""
        if not agent.websocket or not agent.is_online:
            return False

        try:
            await agent.websocket.send_text(json.dumps(message))
            return True
        except Exception as e:
            print(f"Error forwarding to agent {agent.agent_id}: {e}")
            return False

    async def send_to_client(self, client: Client, message: dict) -> bool:
        """发送消息到客户端"""
        try:
            await client.websocket.send_text(json.dumps(message))
            return True
        except Exception as e:
            print(f"Error sending to client {client.client_id}: {e}")
            return False

    async def broadcast_to_clients(self, agent_id: str, message: dict):
        """广播消息到所有连接到指定代理的客户端"""
        for client_id in self.agent_clients.get(agent_id, []):
            client = self.clients.get(client_id)
            if client:
                await self.send_to_client(client, message)

    def list_agents(self) -> list:
        """列出所有代理"""
        return [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "is_online": a.is_online,
                "connected_at": a.connected_at.isoformat() if a.connected_at else None,
                "client_count": len(self.agent_clients.get(a.agent_id, [])),
            }
            for a in self.agents.values()
        ]


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(title="Claude Code Relay Server")
relay = RelayServer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Agent Management API
# ============================================================================

@app.post("/api/agents")
async def create_agent(request: Request, name: str = "default"):
    """创建新的代理（在本地机器上运行）"""
    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"API: Create agent request from {client_ip}, name={name}")

    try:
        agent = relay.register_agent(name)
        logger.info(f"API: Agent created successfully: id={agent.agent_id}")
        return {
            "agent_id": agent.agent_id,
            "agent_key": agent.agent_key,
            "name": agent.name,
            "message": "Save the agent_key securely! It won't be shown again.",
        }
    except Exception as e:
        logger.error(f"API: Failed to create agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@app.get("/api/agents")
async def list_agents():
    """列出所有代理"""
    return {"agents": relay.list_agents()}


@app.get("/api/agents/{agent_id}")
async def get_agent_status(agent_id: str):
    """获取代理状态"""
    agent = relay.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "is_online": agent.is_online,
        "connected_at": agent.connected_at.isoformat() if agent.connected_at else None,
        "client_count": len(relay.agent_clients.get(agent_id, [])),
    }


# ============================================================================
# Agent WebSocket (本地代理连接)
# ============================================================================

@app.websocket("/ws/agent/{agent_id}")
async def agent_websocket(
    websocket: WebSocket,
    agent_id: str,
    key: str = Query(...),
):
    """代理 WebSocket 连接点"""
    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.info(f"WS Agent: Connection attempt from {client_ip}, agent_id={agent_id}")

    # 验证代理
    agent = relay.verify_agent(agent_id, key)
    if not agent:
        logger.warning(f"WS Agent: Rejected connection from {client_ip}, invalid credentials for agent_id={agent_id}")
        await websocket.close(code=4001, reason="Invalid agent credentials")
        return

    await websocket.accept()
    logger.info(f"WS Agent: Connection accepted for agent_id={agent_id} from {client_ip}")
    await relay.connect_agent(agent, websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError as e:
                logger.error(f"WS Agent: Invalid JSON from agent {agent_id}: {e}")
                continue

            msg_type = message.get("type", "unknown")
            logger.debug(f"WS Agent: Received message type={msg_type} from agent {agent_id}")

            if msg_type == "heartbeat":
                agent.last_heartbeat = datetime.utcnow()
                await websocket.send_text(json.dumps({"type": "heartbeat_ack"}))

            elif msg_type == "output":
                # 转发 Claude Code 输出到所有客户端
                await relay.broadcast_to_clients(agent_id, message)

            elif msg_type == "error":
                logger.warning(f"WS Agent: Error from agent {agent_id}: {message.get('message', 'unknown')}")
                await relay.broadcast_to_clients(agent_id, message)

            elif msg_type == "status":
                await relay.broadcast_to_clients(agent_id, message)

    except WebSocketDisconnect:
        logger.info(f"WS Agent: Disconnected agent_id={agent_id}")
        relay.disconnect_agent(agent)

        # 通知客户端代理离线
        await relay.broadcast_to_clients(agent_id, {
            "type": "agent_offline",
            "agent_id": agent_id,
        })
    except Exception as e:
        logger.error(f"WS Agent: Error with agent {agent_id}: {e}", exc_info=True)
        relay.disconnect_agent(agent)


# ============================================================================
# Client WebSocket (手机/浏览器连接)
# ============================================================================

@app.websocket("/ws/client/{agent_id}")
async def client_websocket(
    websocket: WebSocket,
    agent_id: str,
):
    """客户端 WebSocket 连接点"""
    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.info(f"WS Client: Connection attempt from {client_ip} for agent_id={agent_id}")

    # 检查代理是否存在
    agent = relay.get_agent(agent_id)
    if not agent:
        logger.warning(f"WS Client: Rejected connection from {client_ip}, agent_id={agent_id} not found")
        await websocket.close(code=4004, reason="Agent not found")
        return

    await websocket.accept()

    # 生成客户端 ID
    client_id = secrets.token_urlsafe(8)
    client = await relay.connect_client(client_id, websocket)
    await relay.bind_client_to_agent(client, agent_id)

    logger.info(f"WS Client: {client_id} connected to agent {agent_id}, agent_online={agent.is_online}")

    # 发送连接状态
    await relay.send_to_client(client, {
        "type": "connected",
        "client_id": client_id,
        "agent_id": agent_id,
        "agent_online": agent.is_online,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError as e:
                logger.error(f"WS Client: Invalid JSON from {client_id}: {e}")
                continue

            msg_type = message.get("type", "unknown")

            if msg_type == "input":
                # 转发输入到代理
                if agent.is_online:
                    message["client_id"] = client_id
                    await relay.forward_to_agent(agent, message)
                else:
                    logger.debug(f"WS Client: Input from {client_id} dropped, agent {agent_id} offline")
                    await relay.send_to_client(client, {
                        "type": "error",
                        "message": "Agent is offline",
                    })

            elif msg_type == "ping":
                await relay.send_to_client(client, {"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WS Client: {client_id} disconnected from agent {agent_id}")
        relay.disconnect_client(client)
    except Exception as e:
        logger.error(f"WS Client: Error with client {client_id}: {e}", exc_info=True)
        relay.disconnect_client(client)


# ============================================================================
# Web Interface
# ============================================================================

@app.get("/")
async def get_index():
    """主页"""
    return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claude Code Relay</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 40px 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { text-align: center; margin-bottom: 40px; color: #0f9d58; }
        .card {
            background: #16213e;
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 20px;
        }
        .card h2 { margin-bottom: 16px; color: #4a9eff; }
        .card p { color: #aaa; line-height: 1.6; }
        code {
            background: #0f3460;
            padding: 2px 8px;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
        }
        pre {
            background: #0f3460;
            padding: 16px;
            border-radius: 4px;
            overflow-x: auto;
            margin: 12px 0;
        }
        .btn {
            display: inline-block;
            padding: 12px 24px;
            background: #4a9eff;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            margin-top: 16px;
        }
        .btn:hover { background: #3a8eef; }
        .agents { margin-top: 20px; }
        .agent-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px;
            background: #0f3460;
            border-radius: 4px;
            margin-bottom: 8px;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 8px;
        }
        .status-dot.online { background: #0f9d58; }
        .status-dot.offline { background: #dc3545; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Claude Code Relay Server</h1>

        <div class="card">
            <h2>How it works</h2>
            <p>This relay server connects your local Claude Code instance with remote clients (phone, browser).</p>
            <pre>
Phone/Browser  ←→  Relay Server (Public IP)  ←→  Local Agent (Claude Code)
            </pre>
        </div>

        <div class="card">
            <h2>1. Create an Agent</h2>
            <p>First, register your local machine as an agent:</p>
            <pre>curl -X POST "http://YOUR_SERVER/api/agents?name=my-laptop"</pre>
            <p>Save the <code>agent_id</code> and <code>agent_key</code> returned.</p>
        </div>

        <div class="card">
            <h2>2. Run Local Agent</h2>
            <p>On your local machine with Claude Code installed:</p>
            <pre>python agent.py --server ws://YOUR_SERVER --id AGENT_ID --key AGENT_KEY</pre>
        </div>

        <div class="card">
            <h2>3. Connect from Phone</h2>
            <p>Open the terminal interface in your browser:</p>
            <pre>http://YOUR_SERVER/terminal/AGENT_ID</pre>
            <a href="/api/agents" class="btn">View Registered Agents</a>
        </div>

        <div class="card">
            <h2>Registered Agents</h2>
            <div class="agents" id="agentList">Loading...</div>
        </div>
    </div>

    <script>
        async function loadAgents() {
            try {
                const resp = await fetch('/api/agents');
                const data = await resp.json();
                const container = document.getElementById('agentList');

                if (data.agents.length === 0) {
                    container.innerHTML = '<p style="color:#888">No agents registered yet.</p>';
                    return;
                }

                container.innerHTML = data.agents.map(agent => `
                    <div class="agent-item">
                        <div>
                            <span class="status-dot ${agent.is_online ? 'online' : 'offline'}"></span>
                            <strong>${agent.name}</strong>
                            <span style="color:#888">(${agent.agent_id})</span>
                        </div>
                        <div>
                            ${agent.is_online ?
                                `<a href="/terminal/${agent.agent_id}" class="btn" style="padding:8px 16px;font-size:14px">Connect</a>` :
                                '<span style="color:#888">Offline</span>'
                            }
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                document.getElementById('agentList').innerHTML = '<p style="color:#dc3545">Error loading agents</p>';
            }
        }

        loadAgents();
        setInterval(loadAgents, 5000);
    </script>
</body>
</html>
    """)


@app.get("/terminal/{agent_id}")
async def get_terminal(agent_id: str):
    """终端界面"""
    agent = relay.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <title>Claude Code - {agent.name}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; background: #1e1e1e; color: #d4d4d4; font-family: sans-serif; }}
        .app {{ display: flex; flex-direction: column; height: 100dvh; }}
        .header {{
            background: #2d2d30;
            padding: 8px 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #3e3e42;
        }}
        .status {{ display: flex; align-items: center; gap: 8px; }}
        .status-dot {{
            width: 8px; height: 8px;
            border-radius: 50%;
            background: #dc3545;
        }}
        .status-dot.online {{ background: #28a745; box-shadow: 0 0 8px #28a745; }}
        .terminal-container {{ flex: 1; overflow: hidden; }}
        #terminal {{ width: 100%; height: 100%; }}
        .fn-keys {{
            display: none;
            background: #2d2d30;
            padding: 6px;
            gap: 6px;
            overflow-x: auto;
            border-top: 1px solid #3e3e42;
        }}
        @media (max-width: 768px) {{ .fn-keys {{ display: flex; }} }}
        .fn-key {{
            padding: 10px 14px;
            background: #3e3e42;
            border: none;
            border-radius: 4px;
            color: #d4d4d4;
            font-family: monospace;
            cursor: pointer;
            flex-shrink: 0;
        }}
        .fn-key:active {{ background: #0e639c; }}
    </style>
</head>
<body>
    <div class="app">
        <header class="header">
            <div><strong>{agent.name}</strong></div>
            <div class="status">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">Connecting...</span>
            </div>
        </header>
        <div class="terminal-container">
            <div id="terminal"></div>
        </div>
        <div class="fn-keys">
            <button class="fn-key" data-key="\\x03">Ctrl+C</button>
            <button class="fn-key" data-key="\\x04">Ctrl+D</button>
            <button class="fn-key" data-key="\\t">Tab</button>
            <button class="fn-key" data-key="\\x1b">Esc</button>
            <button class="fn-key" data-key="\\x1b[A">↑</button>
            <button class="fn-key" data-key="\\x1b[B">↓</button>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
    <script>
        const AGENT_ID = "{agent_id}";
        let ws = null;
        let term = null;
        let currentLine = '';

        function initTerminal() {{
            term = new Terminal({{
                cursorBlink: true,
                fontSize: 14,
                fontFamily: 'Menlo, Monaco, monospace',
                theme: {{ background: '#1e1e1e', foreground: '#d4d4d4' }},
                scrollback: 10000,
            }});

            const fitAddon = new FitAddon.FitAddon();
            term.loadAddon(fitAddon);
            term.open(document.getElementById('terminal'));
            fitAddon.fit();
            window.addEventListener('resize', () => fitAddon.fit());

            term.onData(data => {{
                if (!ws || ws.readyState !== WebSocket.OPEN) return;

                if (data === '\\r') {{
                    ws.send(JSON.stringify({{ type: 'input', data: currentLine + '\\n' }}));
                    currentLine = '';
                }} else if (data === '\\u007F') {{
                    if (currentLine.length > 0) {{
                        currentLine = currentLine.slice(0, -1);
                        term.write('\\b \\b');
                    }}
                }} else if (data === '\\u0003') {{
                    ws.send(JSON.stringify({{ type: 'input', data: '\\u0003' }}));
                    currentLine = '';
                }} else if (data.startsWith('\\x1b')) {{
                    ws.send(JSON.stringify({{ type: 'input', data }}));
                }} else {{
                    currentLine += data;
                    term.write(data);
                }}
            }});

            term.writeln('Connecting to Claude Code...');
        }}

        function connect() {{
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${{protocol}}//${{location.host}}/ws/client/${{AGENT_ID}}`);

            ws.onopen = () => term.writeln('Connected to relay server.');

            ws.onmessage = (e) => {{
                const msg = JSON.parse(e.data);
                switch (msg.type) {{
                    case 'connected':
                        updateStatus(msg.agent_online);
                        if (msg.agent_online) {{
                            term.writeln('\\x1b[32m✓ Agent is online\\x1b[0m\\n');
                        }} else {{
                            term.writeln('\\x1b[33m⚠ Agent is offline, waiting...\\x1b[0m');
                        }}
                        break;
                    case 'agent_online':
                        updateStatus(true);
                        term.writeln('\\x1b[32m✓ Agent came online\\x1b[0m\\n');
                        break;
                    case 'agent_offline':
                        updateStatus(false);
                        term.writeln('\\x1b[31m✗ Agent went offline\\x1b[0m');
                        break;
                    case 'output':
                        term.write(msg.data);
                        break;
                    case 'error':
                        term.writeln(`\\x1b[31mError: ${{msg.message}}\\x1b[0m`);
                        break;
                }}
            }};

            ws.onclose = () => {{
                updateStatus(false);
                term.writeln('\\x1b[31mDisconnected. Reconnecting...\\x1b[0m');
                setTimeout(connect, 3000);
            }};
        }}

        function updateStatus(online) {{
            const dot = document.getElementById('statusDot');
            const text = document.getElementById('statusText');
            if (online) {{
                dot.classList.add('online');
                text.textContent = 'Online';
            }} else {{
                dot.classList.remove('online');
                text.textContent = 'Offline';
            }}
        }}

        document.querySelectorAll('.fn-key').forEach(btn => {{
            btn.addEventListener('click', () => {{
                if (ws && ws.readyState === WebSocket.OPEN) {{
                    ws.send(JSON.stringify({{ type: 'input', data: btn.dataset.key }}));
                }}
                term.focus();
            }});
        }});

        initTerminal();
        connect();
    </script>
</body>
</html>
    """)


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "agents_total": len(relay.agents),
        "agents_online": sum(1 for a in relay.agents.values() if a.is_online),
        "clients_connected": len(relay.clients),
    }


# ============================================================================
# Admin Dashboard
# ============================================================================

def verify_admin_session(session_token: str) -> bool:
    """验证管理员会话"""
    return session_token in ADMIN_SESSIONS


def get_server_info() -> dict:
    """获取服务器信息"""
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "127.0.0.1"

    return {
        "hostname": hostname,
        "local_ip": local_ip,
        "port": 8080,
        "python_version": sys.version.split()[0],
    }


@app.get("/admin")
async def admin_page(session: str = Cookie(default=None)):
    """管理后台"""
    if not session or not verify_admin_session(session):
        return RedirectResponse(url="/admin/login", status_code=302)

    server_info = get_server_info()
    agents_list = relay.list_agents()
    uptime = datetime.utcnow().isoformat()

    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>管理后台 - Claude Code Relay</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #FDF5E6 0%, #F5DEB3 100%);
            min-height: 100vh;
            color: #4A4A4A;
        }}
        .header {{
            background: linear-gradient(135deg, #D2691E 0%, #CD853F 100%);
            color: white;
            padding: 20px 40px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .header h1 {{ font-size: 24px; font-weight: 600; }}
        .header-content {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .logout-btn {{
            background: rgba(255,255,255,0.2);
            border: 1px solid rgba(255,255,255,0.3);
            color: white;
            padding: 8px 20px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 14px;
            text-decoration: none;
        }}
        .logout-btn:hover {{ background: rgba(255,255,255,0.3); }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 30px 40px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: white;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            border-left: 4px solid #D2691E;
        }}
        .stat-card h3 {{
            color: #8B7355;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }}
        .stat-card .value {{
            font-size: 32px;
            font-weight: 700;
            color: #D2691E;
        }}
        .stat-card .sub {{ color: #aaa; font-size: 12px; margin-top: 4px; }}
        .card {{
            background: white;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .card h2 {{
            color: #D2691E;
            font-size: 18px;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #F5DEB3;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            text-align: left;
            padding: 12px 16px;
            border-bottom: 1px solid #F5DEB3;
        }}
        th {{
            background: #FFFAF0;
            color: #8B7355;
            font-weight: 600;
            font-size: 13px;
            text-transform: uppercase;
        }}
        tr:hover {{ background: #FFFAF0; }}
        .status-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-online {{
            background: #d4edda;
            color: #155724;
        }}
        .status-offline {{
            background: #f8d7da;
            color: #721c24;
        }}
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }}
        .info-item {{
            display: flex;
            justify-content: space-between;
            padding: 12px;
            background: #FFFAF0;
            border-radius: 8px;
        }}
        .info-label {{ color: #8B7355; }}
        .info-value {{ font-weight: 600; color: #4A4A4A; }}
        .terminal-link {{
            color: #D2691E;
            text-decoration: none;
        }}
        .terminal-link:hover {{ text-decoration: underline; }}
        .refresh-btn {{
            background: #D2691E;
            color: white;
            border: none;
            padding: 10px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
        }}
        .refresh-btn:hover {{ background: #CD853F; }}
        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #8B7355;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <h1>Claude Code Relay 管理后台</h1>
            <a href="/admin/logout" class="logout-btn">退出登录</a>
        </div>
    </div>

    <div class="container">
        <div class="stats">
            <div class="stat-card">
                <h3>注册代理</h3>
                <div class="value">{len(relay.agents)}</div>
                <div class="sub">Total Agents</div>
            </div>
            <div class="stat-card">
                <h3>在线代理</h3>
                <div class="value">{sum(1 for a in relay.agents.values() if a.is_online)}</div>
                <div class="sub">Online Now</div>
            </div>
            <div class="stat-card">
                <h3>连接客户端</h3>
                <div class="value">{len(relay.clients)}</div>
                <div class="sub">Active Connections</div>
            </div>
            <div class="stat-card">
                <h3>服务状态</h3>
                <div class="value" style="color: #6B8E23;">正常</div>
                <div class="sub">Healthy</div>
            </div>
        </div>

        <div class="card">
            <h2>服务器信息</h2>
            <div class="info-grid">
                <div class="info-item">
                    <span class="info-label">主机名</span>
                    <span class="info-value">{server_info['hostname']}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">IP 地址</span>
                    <span class="info-value">{server_info['local_ip']}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">监听端口</span>
                    <span class="info-value">{server_info['port']}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Python 版本</span>
                    <span class="info-value">{server_info['python_version']}</span>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>已注册代理 <button class="refresh-btn" onclick="location.reload()">刷新</button></h2>
            {"<table><thead><tr><th>名称</th><th>Agent ID</th><th>状态</th><th>客户端数</th><th>连接时间</th><th>操作</th></tr></thead><tbody>" +
            "".join([f'''
                <tr>
                    <td><strong>{a['name']}</strong></td>
                    <td><code>{a['agent_id']}</code></td>
                    <td><span class="status-badge {'status-online' if a['is_online'] else 'status-offline'}">{'在线' if a['is_online'] else '离线'}</span></td>
                    <td>{a['client_count']}</td>
                    <td>{a['connected_at'][:19] if a['connected_at'] else '-'}</td>
                    <td><a href="/terminal/{a['agent_id']}" class="terminal-link" target="_blank">打开终端</a></td>
                </tr>
            ''' for a in agents_list]) +
            "</tbody></table>" if agents_list else '<div class="empty-state">暂无注册代理</div>'}
        </div>
    </div>

    <script>
        // 每 10 秒自动刷新数据
        setTimeout(() => location.reload(), 30000);
    </script>
</body>
</html>
    """)


@app.get("/admin/login")
async def admin_login_page(error: str = None):
    """登录页面"""
    error_msg = '<p class="error">用户名或密码错误</p>' if error else ''

    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>登录 - Claude Code Relay</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #FDF5E6 0%, #F5DEB3 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .login-box {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .logo h1 {{
            color: #D2691E;
            font-size: 24px;
            margin-bottom: 8px;
        }}
        .logo p {{
            color: #8B7355;
            font-size: 14px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            color: #8B7355;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        input {{
            width: 100%;
            padding: 14px 16px;
            border: 2px solid #F5DEB3;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
            background: #FFFAF0;
        }}
        input:focus {{
            outline: none;
            border-color: #D2691E;
            background: white;
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #D2691E 0%, #CD853F 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(210, 105, 30, 0.4);
        }}
        .error {{
            background: #f8d7da;
            color: #721c24;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
            font-size: 14px;
        }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            color: #aaa;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="login-box">
        <div class="logo">
            <h1>Claude Code Relay</h1>
            <p>管理后台登录</p>
        </div>

        {error_msg}

        <form action="/admin/login" method="post">
            <div class="form-group">
                <label>用户名</label>
                <input type="text" name="username" required autofocus placeholder="请输入用户名">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" name="password" required placeholder="请输入密码">
            </div>
            <button type="submit">登 录</button>
        </form>

        <div class="footer">
            Claude Code Remote Access System
        </div>
    </div>
</body>
</html>
    """)


@app.post("/admin/login")
async def admin_login_submit(request: Request):
    """处理登录"""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    logger.info(f"Admin login attempt: username={username}")

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        # 生成 session token
        session_token = secrets.token_urlsafe(32)
        ADMIN_SESSIONS.add(session_token)
        logger.info(f"Admin login successful: username={username}")

        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie(key="session", value=session_token, httponly=True, max_age=86400)
        return response
    else:
        logger.warning(f"Admin login failed: username={username}")
        return RedirectResponse(url="/admin/login?error=1", status_code=302)


@app.get("/admin/logout")
async def admin_logout(session: str = Cookie(default=None)):
    """退出登录"""
    if session and session in ADMIN_SESSIONS:
        ADMIN_SESSIONS.discard(session)
        logger.info("Admin logged out")

    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(key="session")
    return response


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║           Claude Code Relay Server                            ║
╚═══════════════════════════════════════════════════════════════╝

This server acts as a relay between:
  - Local agents (machines running Claude Code)
  - Remote clients (phones, browsers)

Endpoints:
  /                         - Web interface
  /api/agents               - Agent management
  /ws/agent/{id}?key=xxx    - Agent WebSocket
  /ws/client/{agent_id}     - Client WebSocket
  /terminal/{agent_id}      - Terminal UI

    """)

    uvicorn.run(app, host="0.0.0.0", port=8080)
