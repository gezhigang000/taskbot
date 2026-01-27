# Claude Code Remote

[English](#english) | [中文](#中文)

---

## English

Access Claude Code CLI remotely from your phone.

### Architecture

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│   Phone     │◄──HTTP──►│   FRP Server    │◄──TCP──►│   Client    │
│  (Browser)  │          │ (Public Server) │         │(Local Mac)  │
└─────────────┘          └─────────────────┘         └─────────────┘
   xterm.js                  FRP Tunnel               HTTP/SSE + PTY
```

**Features:**
- No relay server needed - direct P2P via FRP tunneling
- HTTP/SSE protocol - firewall friendly
- GUI client - one-click start
- Phone uses browser only - no app installation

### Quick Start

#### 1. Server Setup (One-time)

Run on a server with public IP:

```bash
ssh root@your-server 'bash -s' < server/install.sh
```

Save the **auth token** displayed after installation.

Configure DNS wildcard:
- Type: A
- Host: *
- Value: Server IP

#### 2. Client Usage

**macOS:**

Download `dist/Claude Code Remote.app` and run.

Or run from source:
```bash
pip install -r requirements.txt
python agent/gui.py
```

**Configuration:**

1. Click "Settings"
2. Enter FRP server address and token
3. Select workspace directory
4. Click "Start Service"

#### 3. Phone Connection

1. Click "Copy URL"
2. Open the URL in phone browser
3. Start using Claude Code

### File Structure

```
claude-code-remote/
├── agent/
│   ├── gui.py          # GUI client
│   ├── server.py       # HTTP/SSE server
│   ├── frp.py          # FRP client management
│   ├── cli.py          # CLI entry point
│   └── terminal.html   # Mobile terminal page
├── server/
│   ├── install.sh      # Server one-click install
│   ├── frps.toml       # FRP server config
│   └── nginx.conf      # Nginx config (optional)
├── build.py            # Build script
└── requirements.txt    # Python dependencies
```

### Building

```bash
pip install pyinstaller
python build.py
```

Output: `dist/Claude Code Remote.app`

### Server Management

```bash
# Check status
systemctl status frps

# View logs
journalctl -u frps -f

# Restart service
systemctl restart frps

# View config
cat /etc/frp/frps.toml
```

### Ports

| Port | Purpose |
|------|---------|
| 7000 | FRP client connection |
| 8080 | HTTP proxy (phone access) |
| 7500 | FRP dashboard (local) |

### Troubleshooting

**Q: Phone shows "page not found"?**

A: FRP client not connected. Check:
1. Server frps running: `systemctl status frps`
2. Client FRP token is correct
3. Client log shows "FRP tunnel established"

**Q: FRP service won't start?**

A: Check logs: `journalctl -u frps -n 30`
- Port in use: Change port in `/etc/frp/frps.toml`
- Config error: Check toml syntax

**Q: Connection timeout?**

A: Check server firewall:
```bash
ufw allow 7000/tcp
ufw allow 8080/tcp
```

### Requirements

- Python 3.8+
- Claude Code CLI
- FRP server (public IP)

---

## 中文

从手机远程访问本地 Claude Code CLI。

### 架构

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│   手机      │◄──HTTP──►│  FRP 服务器     │◄──TCP──►│   客户端    │
│  (浏览器)   │          │ (公网服务器)    │         │ (本地电脑)  │
└─────────────┘          └─────────────────┘         └─────────────┘
   xterm.js                   FRP 隧道                HTTP/SSE + PTY
```

**特点：**
- 无需中继服务器，通过 FRP 隧道直连
- HTTP/SSE 协议，防火墙友好
- GUI 客户端，一键启动
- 手机端纯浏览器，无需安装 App

### 快速开始

#### 1. 服务端部署（一次性）

在有公网 IP 的服务器上运行：

```bash
ssh root@your-server 'bash -s' < server/install.sh
```

安装完成后记录显示的 **认证令牌**。

配置 DNS 泛域名解析：
- 类型：A
- 主机记录：*
- 记录值：服务器 IP

#### 2. 客户端使用

**macOS：**

下载 `dist/Claude Code Remote.app`，双击运行。

或从源码运行：
```bash
pip install -r requirements.txt
python agent/gui.py
```

**配置：**

1. 点击「设置」
2. 填写 FRP 服务器地址和令牌
3. 选择工作目录
4. 点击「启动服务」

#### 3. 手机连接

1. 点击「复制访问地址」
2. 手机浏览器打开该地址
3. 开始使用 Claude Code

### 文件结构

```
claude-code-remote/
├── agent/
│   ├── gui.py          # GUI 客户端
│   ├── server.py       # HTTP/SSE 服务器
│   ├── frp.py          # FRP 客户端管理
│   ├── cli.py          # 命令行入口
│   └── terminal.html   # 手机端终端页面
├── server/
│   ├── install.sh      # 服务端一键安装
│   ├── frps.toml       # FRP 服务端配置
│   └── nginx.conf      # Nginx 配置（可选）
├── build.py            # 打包脚本
└── requirements.txt    # Python 依赖
```

### 打包

```bash
pip install pyinstaller
python build.py
```

输出：`dist/Claude Code Remote.app`

### 服务端管理

```bash
# 查看状态
systemctl status frps

# 查看日志
journalctl -u frps -f

# 重启服务
systemctl restart frps

# 查看配置
cat /etc/frp/frps.toml
```

### 端口说明

| 端口 | 用途 |
|------|------|
| 7000 | FRP 客户端连接 |
| 8080 | HTTP 代理（手机访问） |
| 7500 | FRP 管理面板（本地） |

### 常见问题

**Q: 手机显示 "page not found"？**

A: FRP 客户端未连接。检查：
1. 服务端 frps 是否运行：`systemctl status frps`
2. 客户端 FRP 令牌是否正确
3. 客户端日志是否显示「FRP 隧道已建立」

**Q: FRP 服务启动失败？**

A: 查看日志：`journalctl -u frps -n 30`
- 端口被占用：修改 `/etc/frp/frps.toml` 中的端口
- 配置错误：检查 toml 语法

**Q: 连接超时？**

A: 检查服务器防火墙：
```bash
ufw allow 7000/tcp
ufw allow 8080/tcp
```

**Q: 如何更换端口？**

A: 编辑 `/etc/frp/frps.toml`：
```toml
bindPort = 7000        # FRP 连接端口
vhostHTTPPort = 8080   # HTTP 代理端口
```

### 依赖

- Python 3.8+
- Claude Code CLI
- FRP 服务器（公网）

---

## License

MIT
