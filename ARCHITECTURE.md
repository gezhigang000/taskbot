# Architecture | 架构设计

[English](#english) | [中文](#中文)

---

## English

### Overview

Claude Code Remote uses a relay architecture to enable remote access to Claude Code CLI from any device. The system consists of three main components:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ARCHITECTURE                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐      ┌─────────────────┐      ┌─────────────────┐        │
│   │   Client    │      │  Relay Server   │      │   Local Agent   │        │
│   │  (Mobile)   │◄────►│   (Public IP)   │◄────►│  (Your PC)      │        │
│   └─────────────┘      └─────────────────┘      └─────────────────┘        │
│         │                      │                        │                   │
│         │                      │                        │                   │
│   ┌─────▼─────┐         ┌──────▼──────┐         ┌──────▼──────┐            │
│   │ xterm.js  │         │  FastAPI    │         │  PTY        │            │
│   │ Terminal  │         │  WebSocket  │         │  Process    │            │
│   │ UI        │         │  Hub        │         │  Manager    │            │
│   └───────────┘         └─────────────┘         └──────┬──────┘            │
│                                                        │                    │
│                                                 ┌──────▼──────┐            │
│                                                 │ Claude Code │            │
│                                                 │    CLI      │            │
│                                                 └─────────────┘            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Components

#### 1. Relay Server (`relay/server.py`)

The relay server is deployed on a machine with a public IP address. It acts as a message broker between clients and agents.

**Responsibilities:**
- Agent registration and authentication
- Client connection management
- Message routing between clients and agents
- Web interface serving
- Terminal UI serving

**Key Classes:**

```python
@dataclass
class Agent:
    """Represents a local machine running Claude Code"""
    agent_id: str           # Unique identifier
    agent_key: str          # Authentication key
    websocket: WebSocket    # Active connection
    name: str               # Human-readable name
    is_online: bool         # Connection status

@dataclass
class Client:
    """Represents a browser/mobile connecting to an agent"""
    client_id: str
    websocket: WebSocket
    connected_agent: str    # Which agent this client is viewing

class RelayServer:
    """Core relay logic"""
    agents: Dict[str, Agent]
    clients: Dict[str, Client]
    agent_clients: Dict[str, Set[str]]  # Agent -> Clients mapping
```

**WebSocket Endpoints:**

| Endpoint | Purpose |
|----------|---------|
| `/ws/agent/{id}?key=xxx` | Agent connects here with auth key |
| `/ws/client/{agent_id}` | Client connects to view specific agent |

**Message Flow:**

```
Client Input → Relay Server → Agent → Claude Code
                    ↓
Client Display ← Relay Server ← Agent ← Claude Code Output
```

#### 2. Local Agent (`agent/agent.py`)

The agent runs on your local machine where Claude Code is installed.

**Responsibilities:**
- Register with relay server
- Manage Claude Code PTY process
- Forward input from relay to Claude Code
- Forward output from Claude Code to relay
- Display QR code for easy mobile access

**Key Classes:**

```python
class ClaudeCodeProcess:
    """Manages the Claude Code CLI process"""
    def __init__(self, workspace_dir: str)
    async def start()           # Start Claude Code with PTY
    async def read_output()     # Read from PTY
    async def write_input()     # Write to PTY
    async def stop()            # Terminate process

class LocalAgent:
    """Main agent class"""
    def register()              # Register with relay
    def show_access_info()      # Display URL and QR code
    async def connect()         # WebSocket to relay
    async def run()             # Main event loop
```

**PTY (Pseudo-Terminal):**

The agent uses Python's `pty` module to create a pseudo-terminal for Claude Code:

```python
master_fd, slave_fd = pty.openpty()
process = await asyncio.create_subprocess_exec(
    'claude',
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=slave_fd,
    env={'TERM': 'xterm-256color'}
)
```

This allows:
- Full terminal emulation
- ANSI color support
- Interactive input handling

#### 3. Client (Browser)

The client is a web-based terminal using xterm.js.

**Features:**
- Full terminal emulation in browser
- Mobile-optimized UI
- Virtual function keys (Ctrl+C, Tab, etc.)
- Auto-reconnection
- PWA support

### Message Protocol

All messages are JSON over WebSocket.

#### Agent → Relay Messages

```json
// Heartbeat
{"type": "heartbeat"}

// Terminal output
{"type": "output", "data": "Hello World\n"}

// Status update
{"type": "status", "message": "Ready"}

// Error
{"type": "error", "message": "Process crashed"}
```

#### Relay → Agent Messages

```json
// Heartbeat acknowledgment
{"type": "heartbeat_ack"}

// User input
{"type": "input", "data": "ls -la\n", "client_id": "xxx"}
```

#### Client → Relay Messages

```json
// User input
{"type": "input", "data": "hello"}

// Ping
{"type": "ping"}
```

#### Relay → Client Messages

```json
// Connection status
{"type": "connected", "client_id": "xxx", "agent_id": "yyy", "agent_online": true}

// Agent status change
{"type": "agent_online", "agent_id": "xxx"}
{"type": "agent_offline", "agent_id": "xxx"}

// Terminal output
{"type": "output", "data": "..."}

// Error
{"type": "error", "message": "Agent is offline"}

// Pong
{"type": "pong"}
```

### Security

#### Current Implementation

1. **Agent Key Authentication**
   - Each agent gets a unique `agent_key` on registration
   - Agent must provide key when connecting via WebSocket
   - Key is verified before accepting connection

2. **Random IDs**
   - Agent IDs and Client IDs use `secrets.token_urlsafe()`
   - Unpredictable, collision-resistant

#### Recommendations for Production

1. **Use HTTPS/WSS**
   - Deploy behind nginx with SSL certificate
   - Encrypt all traffic

2. **Network Isolation**
   - Consider Tailscale or similar VPN
   - Restrict relay server access to known networks

3. **Rate Limiting**
   - Limit registration requests
   - Limit connection attempts

4. **Authentication Enhancement**
   - Add user accounts
   - Implement session tokens
   - Add PIN/password for terminal access

### Data Flow Diagram

```
                    Registration Flow
                    ─────────────────
┌────────────┐                           ┌────────────────┐
│   Agent    │  POST /api/agents         │  Relay Server  │
│            │ ────────────────────────► │                │
│            │                           │  Generate:     │
│            │  {agent_id, agent_key}    │  - agent_id    │
│            │ ◄──────────────────────── │  - agent_key   │
└────────────┘                           └────────────────┘


                    Connection Flow
                    ───────────────
┌────────────┐  WS /ws/agent/{id}?key=   ┌────────────────┐
│   Agent    │ ────────────────────────► │  Relay Server  │
│            │  Verify key               │                │
│            │ ◄──────────────────────── │                │
│            │  Connected                │                │
└────────────┘                           └────────────────┘

┌────────────┐  WS /ws/client/{agent_id} ┌────────────────┐
│   Client   │ ────────────────────────► │  Relay Server  │
│            │                           │                │
│            │  {type: "connected", ...} │                │
│            │ ◄──────────────────────── │                │
└────────────┘                           └────────────────┘


                    Data Flow
                    ─────────
┌────────────┐   input    ┌────────────┐   input    ┌────────────┐
│   Client   │ ─────────► │   Relay    │ ─────────► │   Agent    │
│            │            │   Server   │            │            │
│            │   output   │            │   output   │            │
│            │ ◄───────── │            │ ◄───────── │            │
└────────────┘            └────────────┘            └────────────┘
                                                          │
                                                          ▼
                                                   ┌────────────┐
                                                   │Claude Code │
                                                   │    CLI     │
                                                   └────────────┘
```

### Scalability

#### Current Limitations

- Single-process relay server
- In-memory state (lost on restart)
- No horizontal scaling

#### Future Improvements

1. **Persistence**
   - Redis for session state
   - PostgreSQL for agent registry

2. **Horizontal Scaling**
   - Redis Pub/Sub for cross-instance messaging
   - Sticky sessions or shared state

3. **Output Buffering**
   - Cache terminal output for reconnection
   - Configurable buffer size

---

## 中文

### 概述

Claude Code Remote 使用中继架构，实现从任何设备远程访问 Claude Code CLI。系统由三个主要组件组成：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              系统架构                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐      ┌─────────────────┐      ┌─────────────────┐        │
│   │   客户端     │      │   中继服务器     │      │    本地代理      │        │
│   │  (手机)     │◄────►│   (公网IP)      │◄────►│   (你的电脑)     │        │
│   └─────────────┘      └─────────────────┘      └─────────────────┘        │
│         │                      │                        │                   │
│         │                      │                        │                   │
│   ┌─────▼─────┐         ┌──────▼──────┐         ┌──────▼──────┐            │
│   │ xterm.js  │         │  FastAPI    │         │  PTY        │            │
│   │ 终端UI    │         │  WebSocket  │         │  进程管理    │            │
│   │           │         │  消息中枢    │         │             │            │
│   └───────────┘         └─────────────┘         └──────┬──────┘            │
│                                                        │                    │
│                                                 ┌──────▼──────┐            │
│                                                 │ Claude Code │            │
│                                                 │    CLI      │            │
│                                                 └─────────────┘            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 组件详解

#### 1. 中继服务器 (`relay/server.py`)

中继服务器部署在有公网 IP 的机器上，作为客户端和代理之间的消息中转站。

**职责：**
- 代理注册和认证
- 客户端连接管理
- 在客户端和代理之间路由消息
- 提供 Web 管理界面
- 提供终端 UI

**核心类：**

```python
@dataclass
class Agent:
    """代表运行 Claude Code 的本地机器"""
    agent_id: str           # 唯一标识符
    agent_key: str          # 认证密钥
    websocket: WebSocket    # 活跃连接
    name: str               # 可读名称
    is_online: bool         # 连接状态

@dataclass
class Client:
    """代表连接到代理的浏览器/手机"""
    client_id: str
    websocket: WebSocket
    connected_agent: str    # 连接的代理ID

class RelayServer:
    """核心中继逻辑"""
    agents: Dict[str, Agent]
    clients: Dict[str, Client]
    agent_clients: Dict[str, Set[str]]  # 代理 -> 客户端映射
```

**WebSocket 端点：**

| 端点 | 用途 |
|------|------|
| `/ws/agent/{id}?key=xxx` | 代理通过认证密钥连接 |
| `/ws/client/{agent_id}` | 客户端连接到特定代理 |

**消息流向：**

```
客户端输入 → 中继服务器 → 代理 → Claude Code
                ↓
客户端显示 ← 中继服务器 ← 代理 ← Claude Code 输出
```

#### 2. 本地代理 (`agent/agent.py`)

代理运行在安装了 Claude Code 的本地机器上。

**职责：**
- 向中继服务器注册
- 管理 Claude Code PTY 进程
- 将输入从中继转发到 Claude Code
- 将 Claude Code 输出转发到中继
- 显示二维码方便手机访问

**核心类：**

```python
class ClaudeCodeProcess:
    """管理 Claude Code CLI 进程"""
    def __init__(self, workspace_dir: str)
    async def start()           # 通过 PTY 启动 Claude Code
    async def read_output()     # 从 PTY 读取
    async def write_input()     # 写入 PTY
    async def stop()            # 终止进程

class LocalAgent:
    """主代理类"""
    def register()              # 向中继注册
    def show_access_info()      # 显示 URL 和二维码
    async def connect()         # WebSocket 连接到中继
    async def run()             # 主事件循环
```

**PTY（伪终端）：**

代理使用 Python 的 `pty` 模块为 Claude Code 创建伪终端：

```python
master_fd, slave_fd = pty.openpty()
process = await asyncio.create_subprocess_exec(
    'claude',
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=slave_fd,
    env={'TERM': 'xterm-256color'}
)
```

这使得：
- 完整的终端模拟
- ANSI 颜色支持
- 交互式输入处理

#### 3. 客户端（浏览器）

客户端是基于 xterm.js 的 Web 终端。

**特性：**
- 浏览器中完整的终端模拟
- 移动端优化的 UI
- 虚拟功能键（Ctrl+C、Tab 等）
- 自动重连
- PWA 支持

### 消息协议

所有消息都是通过 WebSocket 传输的 JSON。

#### 代理 → 中继 消息

```json
// 心跳
{"type": "heartbeat"}

// 终端输出
{"type": "output", "data": "Hello World\n"}

// 状态更新
{"type": "status", "message": "Ready"}

// 错误
{"type": "error", "message": "Process crashed"}
```

#### 中继 → 代理 消息

```json
// 心跳确认
{"type": "heartbeat_ack"}

// 用户输入
{"type": "input", "data": "ls -la\n", "client_id": "xxx"}
```

#### 客户端 → 中继 消息

```json
// 用户输入
{"type": "input", "data": "hello"}

// Ping
{"type": "ping"}
```

#### 中继 → 客户端 消息

```json
// 连接状态
{"type": "connected", "client_id": "xxx", "agent_id": "yyy", "agent_online": true}

// 代理状态变化
{"type": "agent_online", "agent_id": "xxx"}
{"type": "agent_offline", "agent_id": "xxx"}

// 终端输出
{"type": "output", "data": "..."}

// 错误
{"type": "error", "message": "Agent is offline"}

// Pong
{"type": "pong"}
```

### 安全性

#### 当前实现

1. **Agent Key 认证**
   - 每个代理注册时获得唯一的 `agent_key`
   - 代理通过 WebSocket 连接时必须提供密钥
   - 连接前验证密钥

2. **随机 ID**
   - Agent ID 和 Client ID 使用 `secrets.token_urlsafe()`
   - 不可预测，抗碰撞

#### 生产环境建议

1. **使用 HTTPS/WSS**
   - 通过 nginx 部署，配置 SSL 证书
   - 加密所有流量

2. **网络隔离**
   - 考虑使用 Tailscale 或类似 VPN
   - 限制中继服务器访问到已知网络

3. **速率限制**
   - 限制注册请求
   - 限制连接尝试

4. **增强认证**
   - 添加用户账户系统
   - 实现会话令牌
   - 为终端访问添加 PIN/密码

### 数据流图

```
                    注册流程
                    ────────
┌────────────┐                           ┌────────────────┐
│   代理     │  POST /api/agents         │   中继服务器    │
│            │ ────────────────────────► │                │
│            │                           │  生成:         │
│            │  {agent_id, agent_key}    │  - agent_id    │
│            │ ◄──────────────────────── │  - agent_key   │
└────────────┘                           └────────────────┘


                    连接流程
                    ────────
┌────────────┐  WS /ws/agent/{id}?key=   ┌────────────────┐
│   代理     │ ────────────────────────► │   中继服务器    │
│            │  验证密钥                  │                │
│            │ ◄──────────────────────── │                │
│            │  已连接                    │                │
└────────────┘                           └────────────────┘

┌────────────┐  WS /ws/client/{agent_id} ┌────────────────┐
│   客户端   │ ────────────────────────► │   中继服务器    │
│            │                           │                │
│            │  {type: "connected", ...} │                │
│            │ ◄──────────────────────── │                │
└────────────┘                           └────────────────┘


                    数据流
                    ──────
┌────────────┐   输入    ┌────────────┐   输入    ┌────────────┐
│   客户端   │ ─────────► │   中继     │ ─────────► │   代理    │
│            │            │   服务器   │            │           │
│            │   输出    │            │   输出    │           │
│            │ ◄───────── │            │ ◄───────── │           │
└────────────┘            └────────────┘            └───────────┘
                                                          │
                                                          ▼
                                                   ┌────────────┐
                                                   │Claude Code │
                                                   │    CLI     │
                                                   └────────────┘
```

### 可扩展性

#### 当前限制

- 单进程中继服务器
- 内存状态（重启后丢失）
- 无水平扩展

#### 未来改进

1. **持久化**
   - Redis 存储会话状态
   - PostgreSQL 存储代理注册信息

2. **水平扩展**
   - Redis Pub/Sub 实现跨实例消息
   - 粘性会话或共享状态

3. **输出缓冲**
   - 缓存终端输出以便重连
   - 可配置缓冲区大小
