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
import secrets
import time
from datetime import datetime
from typing import Dict, Optional, Set
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


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

        return agent

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """获取代理"""
        return self.agents.get(agent_id)

    def verify_agent(self, agent_id: str, agent_key: str) -> Optional[Agent]:
        """验证代理凭证"""
        agent = self.agents.get(agent_id)
        if agent and agent.agent_key == agent_key:
            return agent
        return None

    async def connect_agent(self, agent: Agent, websocket: WebSocket):
        """代理连接"""
        agent.websocket = websocket
        agent.connected_at = datetime.utcnow()
        agent.last_heartbeat = datetime.utcnow()
        agent.is_online = True

        # 通知所有等待的客户端
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

    async def connect_client(self, client_id: str, websocket: WebSocket) -> Client:
        """客户端连接"""
        client = Client(
            client_id=client_id,
            websocket=websocket,
        )
        self.clients[client_id] = client
        return client

    def disconnect_client(self, client: Client):
        """客户端断开"""
        if client.connected_agent:
            self.agent_clients[client.connected_agent].discard(client.client_id)
        if client.client_id in self.clients:
            del self.clients[client.client_id]

    async def bind_client_to_agent(self, client: Client, agent_id: str) -> bool:
        """将客户端绑定到代理"""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        client.connected_agent = agent_id
        self.agent_clients[agent_id].add(client.client_id)
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
async def create_agent(name: str = "default"):
    """创建新的代理（在本地机器上运行）"""
    agent = relay.register_agent(name)
    return {
        "agent_id": agent.agent_id,
        "agent_key": agent.agent_key,
        "name": agent.name,
        "message": "Save the agent_key securely! It won't be shown again.",
    }


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

    # 验证代理
    agent = relay.verify_agent(agent_id, key)
    if not agent:
        await websocket.close(code=4001, reason="Invalid agent credentials")
        return

    await websocket.accept()
    await relay.connect_agent(agent, websocket)
    print(f"[Agent] {agent_id} connected")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message["type"] == "heartbeat":
                agent.last_heartbeat = datetime.utcnow()
                await websocket.send_text(json.dumps({"type": "heartbeat_ack"}))

            elif message["type"] == "output":
                # 转发 Claude Code 输出到所有客户端
                await relay.broadcast_to_clients(agent_id, message)

            elif message["type"] == "error":
                await relay.broadcast_to_clients(agent_id, message)

            elif message["type"] == "status":
                await relay.broadcast_to_clients(agent_id, message)

    except WebSocketDisconnect:
        print(f"[Agent] {agent_id} disconnected")
        relay.disconnect_agent(agent)

        # 通知客户端代理离线
        await relay.broadcast_to_clients(agent_id, {
            "type": "agent_offline",
            "agent_id": agent_id,
        })


# ============================================================================
# Client WebSocket (手机/浏览器连接)
# ============================================================================

@app.websocket("/ws/client/{agent_id}")
async def client_websocket(
    websocket: WebSocket,
    agent_id: str,
):
    """客户端 WebSocket 连接点"""

    # 检查代理是否存在
    agent = relay.get_agent(agent_id)
    if not agent:
        await websocket.close(code=4004, reason="Agent not found")
        return

    await websocket.accept()

    # 生成客户端 ID
    client_id = secrets.token_urlsafe(8)
    client = await relay.connect_client(client_id, websocket)
    await relay.bind_client_to_agent(client, agent_id)

    print(f"[Client] {client_id} connected to agent {agent_id}")

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
            message = json.loads(data)

            if message["type"] == "input":
                # 转发输入到代理
                if agent.is_online:
                    message["client_id"] = client_id
                    await relay.forward_to_agent(agent, message)
                else:
                    await relay.send_to_client(client, {
                        "type": "error",
                        "message": "Agent is offline",
                    })

            elif message["type"] == "ping":
                await relay.send_to_client(client, {"type": "pong"})

    except WebSocketDisconnect:
        print(f"[Client] {client_id} disconnected")
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
