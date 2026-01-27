# Claude Code Remote

[English](#english) | [中文](#中文)

---

## English

**Access Claude Code from anywhere - phone, tablet, or any browser.**

```
Mobile/Browser  <-->  Relay Server (Public IP)  <-->  Your Computer (Claude Code)
```

### Features

- **Remote Access** - Use Claude Code from your phone or any device
- **QR Code Login** - Scan to connect instantly
- **Auto Reconnection** - Handles network interruptions gracefully
- **Mobile Optimized** - Touch-friendly terminal with virtual keys
- **Secure Relay** - Agent key authentication

### Architecture

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   Mobile/Web    │◄──────►│  Relay Server   │◄──────►│  Local Agent    │
│    Browser      │  WSS   │  (Public IP)    │   WS   │  (Claude Code)  │
└─────────────────┘         └─────────────────┘         └─────────────────┘
     xterm.js                   FastAPI                     PTY Process
```

### Quick Start

#### 1. Install

```bash
git clone https://github.com/your-repo/claude-code-remote.git
cd claude-code-remote
bash install.sh
```

#### 2. Start Relay Server

On a server with public IP:

```bash
./start-relay.sh
```

The server will run on `http://YOUR_SERVER_IP:8080`

#### 3. Start Local Agent

On your computer with Claude Code installed:

```bash
./start-agent.sh -s http://YOUR_SERVER_IP:8080
```

The agent will:
- Auto-register with the relay server
- Display access URL
- Show QR code for mobile scanning

#### 4. Connect from Mobile

Scan the QR code shown in terminal, or visit the URL directly.

### Usage

```bash
# Basic usage
./start-agent.sh -s http://relay.example.com:8080

# Custom agent name
./start-agent.sh -s http://relay.example.com:8080 -n "MacBook Pro"

# Custom workspace directory
./start-agent.sh -s http://relay.example.com:8080 -w ~/projects
```

### API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web dashboard |
| `/api/agents` | GET | List all agents |
| `/api/agents` | POST | Register new agent |
| `/api/agents/{id}` | GET | Get agent status |
| `/ws/agent/{id}?key=xxx` | WS | Agent WebSocket |
| `/ws/client/{agent_id}` | WS | Client WebSocket |
| `/terminal/{agent_id}` | GET | Terminal UI |
| `/health` | GET | Health check |

### Deployment

#### Using nginx (recommended for HTTPS)

```nginx
server {
    listen 443 ssl;
    server_name relay.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

#### Using Docker

```bash
docker build -t claude-code-remote .
docker run -p 8080:8080 claude-code-remote
```

### Requirements

- Python 3.8+
- Claude Code CLI installed on local machine
- Server with public IP for relay

### Troubleshooting

**Q: Mobile shows "Agent offline"?**
A: Make sure the local agent is running and connected.

**Q: Cannot connect to server?**
A: Check if port 8080 is open in firewall.

**Q: QR code not showing?**
A: Run `pip install qrcode` to install the QR code library.

---

## 中文

**从任何设备远程访问 Claude Code - 手机、平板或浏览器**

```
手机/浏览器  <-->  中继服务器(公网IP)  <-->  你的电脑(Claude Code)
```

### 特性

- **远程访问** - 从手机或任何设备使用 Claude Code
- **二维码登录** - 扫码即连，方便快捷
- **自动重连** - 优雅处理网络中断
- **移动端优化** - 触控友好的终端界面，带虚拟按键
- **安全中继** - Agent 密钥认证

### 架构

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   手机/浏览器    │◄──────►│   中继服务器     │◄──────►│    本地代理      │
│    Browser      │  WSS   │  (公网IP)       │   WS   │  (Claude Code)  │
└─────────────────┘         └─────────────────┘         └─────────────────┘
     xterm.js                   FastAPI                     PTY 进程
```

### 快速开始

#### 1. 安装

```bash
git clone https://github.com/your-repo/claude-code-remote.git
cd claude-code-remote
bash install.sh
```

#### 2. 启动中继服务器

在有公网 IP 的服务器上：

```bash
./start-relay.sh
```

服务器会运行在 `http://服务器IP:8080`

#### 3. 启动本地代理

在安装了 Claude Code 的电脑上：

```bash
./start-agent.sh -s http://服务器IP:8080
```

代理会：
- 自动向中继服务器注册
- 显示访问地址
- 显示二维码供手机扫描

#### 4. 手机连接

扫描终端显示的二维码，或直接访问显示的网址。

### 使用方法

```bash
# 基本用法
./start-agent.sh -s http://relay.example.com:8080

# 自定义代理名称
./start-agent.sh -s http://relay.example.com:8080 -n "我的MacBook"

# 自定义工作目录
./start-agent.sh -s http://relay.example.com:8080 -w ~/projects
```

### API 接口

| 端点 | 方法 | 描述 |
|------|------|------|
| `/` | GET | Web 管理界面 |
| `/api/agents` | GET | 列出所有代理 |
| `/api/agents` | POST | 注册新代理 |
| `/api/agents/{id}` | GET | 获取代理状态 |
| `/ws/agent/{id}?key=xxx` | WS | 代理 WebSocket |
| `/ws/client/{agent_id}` | WS | 客户端 WebSocket |
| `/terminal/{agent_id}` | GET | 终端界面 |
| `/health` | GET | 健康检查 |

### 部署

#### 使用 nginx（推荐，支持 HTTPS）

```nginx
server {
    listen 443 ssl;
    server_name relay.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

#### 使用 Docker

```bash
docker build -t claude-code-remote .
docker run -p 8080:8080 claude-code-remote
```

### 环境要求

- Python 3.8+
- 本地电脑需安装 Claude Code CLI
- 中继服务器需要公网 IP

### 常见问题

**Q: 手机显示 "Agent offline"？**
A: 确保本地代理正在运行并已连接。

**Q: 无法连接服务器？**
A: 检查防火墙是否开放了 8080 端口。

**Q: 二维码不显示？**
A: 运行 `pip install qrcode` 安装二维码库。

---

## Project Structure

```
claude-code-remote/
├── relay/
│   └── server.py      # Relay server (FastAPI + WebSocket)
├── agent/
│   └── agent.py       # Local agent (PTY + WebSocket client)
├── install.sh         # One-click installation
├── start-relay.sh     # Start relay server
├── start-agent.sh     # Start local agent
├── requirements.txt   # Python dependencies
└── README.md          # This file
```

## License

MIT License

## Contributing

Issues and Pull Requests are welcome!
