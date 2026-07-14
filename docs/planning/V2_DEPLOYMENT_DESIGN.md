# SentinelMind V2 — 运维部署详细设计书

> 状态：设计中 | 版本：v1 | 日期：2026-07-14

---

## 一、总览

| 批次 | 内容 | 优先级 | 预估 |
|------|------|--------|------|
| **第一批** | systemd + 备份 + Docker | 高 | 3 天 |
| **第二批** | HTTPS + RAG | 中 | 4 天 |
| **第三批** | PostgreSQL + 端-云 | 低 | 7 天 |

---

## 第一批 — 部署就绪（3 天）

---

### V2-1 systemd 部署

#### 1.1 目标

生产环境一键部署，支持：
- 开机自启
- 自动重启（崩溃后 5 秒）
- 日志管理（journald）
- 优雅关闭（SIGTERM）

#### 1.2 文件清单

```
deploy/
├── sentinelmind.service      # systemd 服务文件
├── install.sh                # 一键安装脚本
└── uninstall.sh              # 卸载脚本
```

#### 1.3 systemd 服务文件

```ini
# deploy/sentinelmind.service
[Unit]
Description=SentinelMind 多路视频智能分析系统
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=sentinelmind
Group=sentinelmind
WorkingDirectory=/opt/sentinelmind
ExecStart=/opt/sentinelmind/venv/bin/python -m sentinelmind --config /opt/sentinelmind/configs
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=5
TimeoutStopSec=30

# 日志
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sentinelmind

# 环境变量
EnvironmentFile=-/opt/sentinelmind/.env

# 安全加固
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/sentinelmind/data /opt/sentinelmind/logs /opt/sentinelmind/configs

[Install]
WantedBy=multi-user.target
```

#### 1.4 安装脚本

```bash
#!/bin/bash
# deploy/install.sh — SentinelMind 一键安装

set -e

INSTALL_DIR="/opt/sentinelmind"
SERVICE_FILE="deploy/sentinelmind.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== SentinelMind 安装 ==="

# 1. 创建系统用户
if ! id "sentinelmind" &>/dev/null; then
    sudo useradd -r -s /bin/false sentinelmind
    echo "✅ 创建系统用户 sentinelmind"
fi

# 2. 创建安装目录
sudo mkdir -p $INSTALL_DIR
sudo cp -r . $INSTALL_DIR/
sudo chown -R sentinelmind:sentinelmind $INSTALL_DIR

# 3. 创建 Python 虚拟环境
cd $INSTALL_DIR
sudo -u sentinelmind python3 -m venv venv
sudo -u sentinelmind ./venv/bin/pip install -r requirements.txt

# 4. 复制配置文件（如果不存在）
if [ ! -f "$INSTALL_DIR/configs/settings.yaml" ]; then
    sudo -u sentinelmind cp configs/settings.yaml.example configs/settings.yaml
    echo "⚠️  请编辑 $INSTALL_DIR/configs/settings.yaml"
fi

# 5. 安装 systemd 服务
sudo cp $SERVICE_FILE $SYSTEMD_DIR/
sudo systemctl daemon-reload
sudo systemctl enable sentinelmind

echo "✅ 安装完成"
echo ""
echo "启动：sudo systemctl start sentinelmind"
echo "状态：sudo systemctl status sentinelmind"
echo "日志：journalctl -u sentinelmind -f"
echo "配置：$INSTALL_DIR/configs/settings.yaml"
```

#### 1.5 卸载脚本

```bash
#!/bin/bash
# deploy/uninstall.sh — SentinelMind 卸载

set -e

echo "=== SentinelMind 卸载 ==="

# 停止服务
sudo systemctl stop sentinelmind 2>/dev/null || true
sudo systemctl disable sentinelmind 2>/dev/null || true

# 删除服务文件
sudo rm -f /etc/systemd/system/sentinelmind.service
sudo systemctl daemon-reload

# 询问是否删除数据
read -p "是否删除安装目录 /opt/sentinelmind？(y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo rm -rf /opt/sentinelmind
    echo "✅ 已删除安装目录"
fi

# 询问是否删除用户
read -p "是否删除系统用户 sentinelmind？(y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo userdel sentinelmind 2>/dev/null || true
    echo "✅ 已删除用户"
fi

echo "✅ 卸载完成"
```

---

### V2-2 数据备份

#### 2.1 目标

自动备份关键数据，支持手动触发和定时执行。

#### 2.2 备份策略

| 数据 | 备份方式 | 保留策略 | 说明 |
|------|---------|---------|------|
| SQLite 数据库 | `sqlite3 .backup` 原子备份 | 30 天 | 避免文件锁问题 |
| 截图 | 按日期压缩归档 | 90 天 | tar.gz 打包 |
| 视频片段 | 按日期压缩归档 | 30 天 | tar.gz 打包 |
| 配置文件 | 复制到 backup/ | 10 份 | 保留最近修改 |

#### 2.3 文件清单

```
scripts/
├── backup.sh              # 备份脚本
├── restore.sh             # 恢复脚本
└── cleanup_backups.sh     # 清理过期备份
```

#### 2.4 备份脚本

```bash
#!/bin/bash
# scripts/backup.sh — SentinelMind 数据备份

set -e

# 配置
BACKUP_ROOT="/opt/sentinelmind/backups"
DATA_DIR="/opt/sentinelmind/data"
CONFIG_DIR="/opt/sentinelmind/configs"
DB_FILE="$DATA_DIR/sentinelmind.db"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_ROOT/$DATE"

echo "=== SentinelMind 备份 $DATE ==="
mkdir -p "$BACKUP_DIR"

# 1. SQLite 原子备份
if [ -f "$DB_FILE" ]; then
    sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/sentinelmind.db'"
    echo "✅ 数据库备份完成"
else
    echo "⚠️  数据库文件不存在，跳过"
fi

# 2. 截图备份（保留目录结构）
if [ -d "$DATA_DIR/snapshots" ]; then
    tar -czf "$BACKUP_DIR/snapshots.tar.gz" -C "$DATA_DIR" snapshots/
    echo "✅ 截图备份完成"
fi

# 3. 视频片段备份
if [ -d "$DATA_DIR/clips" ]; then
    tar -czf "$BACKUP_DIR/clips.tar.gz" -C "$DATA_DIR" clips/
    echo "✅ 视频备份完成"
fi

# 4. 配置文件备份
cp -r "$CONFIG_DIR" "$BACKUP_DIR/configs"
echo "✅ 配置备份完成"

# 5. 备份元数据
cat > "$BACKUP_DIR/metadata.json" << EOF
{
    "date": "$DATE",
    "db_size": $(stat -c%s "$DB_FILE" 2>/dev/null || echo 0),
    "snapshot_count": $(find "$DATA_DIR/snapshots" -name "*.jpg" 2>/dev/null | wc -l),
    "clip_count": $(find "$DATA_DIR/clips" -name "*.mp4" 2>/dev/null | wc -l)
}
EOF

# 计算备份大小
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo ""
echo "✅ 备份完成: $BACKUP_DIR ($BACKUP_SIZE)"
```

#### 2.5 恢复脚本

```bash
#!/bin/bash
# scripts/restore.sh — SentinelMind 数据恢复

set -e

BACKUP_ROOT="/opt/sentinelmind/backups"
DATA_DIR="/opt/sentinelmind/data"
CONFIG_DIR="/opt/sentinelmind/configs"

# 列出可用备份
echo "=== 可用备份 ==="
ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | while read dir; do
    basename "$dir"
done

echo ""
read -p "输入要恢复的备份日期 (如 20260714_120000): " BACKUP_DATE
BACKUP_DIR="$BACKUP_ROOT/$BACKUP_DATE"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "❌ 备份目录不存在: $BACKUP_DIR"
    exit 1
fi

echo ""
echo "⚠️  警告：恢复将覆盖当前数据！"
read -p "确认恢复？(y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# 停止服务
echo "停止 SentinelMind..."
sudo systemctl stop sentinelmind 2>/dev/null || true

# 1. 恢复数据库
if [ -f "$BACKUP_DIR/sentinelmind.db" ]; then
    cp "$BACKUP_DIR/sentinelmind.db" "$DATA_DIR/sentinelmind.db"
    echo "✅ 数据库已恢复"
fi

# 2. 恢复截图
if [ -f "$BACKUP_DIR/snapshots.tar.gz" ]; then
    rm -rf "$DATA_DIR/snapshots"
    tar -xzf "$BACKUP_DIR/snapshots.tar.gz" -C "$DATA_DIR/"
    echo "✅ 截图已恢复"
fi

# 3. 恢复视频
if [ -f "$BACKUP_DIR/clips.tar.gz" ]; then
    rm -rf "$DATA_DIR/clips"
    tar -xzf "$BACKUP_DIR/clips.tar.gz" -C "$DATA_DIR/"
    echo "✅ 视频已恢复"
fi

# 4. 恢复配置
if [ -d "$BACKUP_DIR/configs" ]; then
    cp -r "$BACKUP_DIR/configs/"* "$CONFIG_DIR/"
    echo "✅ 配置已恢复"
fi

# 重启服务
echo "启动 SentinelMind..."
sudo systemctl start sentinelmind 2>/dev/null || true

echo "✅ 恢复完成"
```

#### 2.6 定时备份配置

通过 systemd timer 或 cron 实现每日凌晨 2:00 自动备份：

```bash
# /etc/cron.d/sentinelmind-backup
0 2 * * * root /opt/sentinelmind/scripts/backup.sh >> /var/log/sentinelmind-backup.log 2>&1
```

---

### V2-3 Docker 容器化

#### 3.1 目标

一键部署，跨平台运行，支持 GPU 直通。

#### 3.2 文件清单

```
Dockerfile                   # 镜像构建
docker-compose.yml           # 服务编排
.dockerignore                # 排除文件
deploy/docker/               # Docker 专用配置
├── nginx.conf               # Nginx 配置（可选）
└── supervisord.conf         # 进程管理（可选）
```

#### 3.3 Dockerfile

```dockerfile
# Dockerfile — SentinelMind 多阶段构建

# ─── 构建阶段 ───────────────────────────────────
FROM python:3.12-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── 运行阶段 ───────────────────────────────────
FROM python:3.12-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# 复制 Python 依赖
COPY --from=builder /install /usr/local

# 创建用户
RUN useradd -m -s /bin/bash sentinelmind

WORKDIR /app

# 复制代码
COPY src/ src/
COPY configs/ configs/
COPY frontend/dist/ frontend/dist/

# 创建数据目录
RUN mkdir -p data/logs data/snapshots data/clips \
    && chown -R sentinelmind:sentinelmind /app

# 切换用户
USER sentinelmind

# 暴露端口
EXPOSE 8080

# 启动
CMD ["python", "-m", "sentinelmind", "--config", "configs"]
```

#### 3.4 docker-compose.yml

```yaml
# docker-compose.yml

version: "3.8"

services:
  # ─── SentinelMind 主服务 ─────────────────────
  app:
    build: .
    container_name: sentinelmind
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./configs:/app/configs
      - ./data:/app/data
      - ./models:/app/models
    env_file:
      - .env
    # GPU 直通（可选）
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # ─── Redis（可选）───────────────────────────
  redis:
    image: redis:7-alpine
    container_name: sentinelmind-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    profiles:
      - with-redis

volumes:
  redis-data:
```

#### 3.5 .dockerignore

```
.git
.github
__pycache__
*.pyc
*.pyo
*.egg-info
dist
build
node_modules
data/
logs/
*.db
*.log
.env
.vscode
.idea
tests/
docs/
```

#### 3.6 使用方式

```bash
# 基础启动（无 Redis）
docker compose up -d

# 带 Redis 启动
docker compose --profile with-redis up -d

# 查看日志
docker compose logs -f app

# 停止
docker compose down
```

---

## 第二批 — 安全 + 智能增强（4 天）

---

### V2-4 HTTPS 反代

#### 4.1 目标

Nginx 反向代理 + Let's Encrypt 自动证书。

#### 4.2 文件清单

```
deploy/nginx/
├── sentinelmind.conf         # Nginx 配置
└── ssl-params.conf           # SSL 安全参数
scripts/
└── setup-ssl.sh              # SSL 证书申请脚本
```

#### 4.3 Nginx 配置

```nginx
# deploy/nginx/sentinelmind.conf

upstream sentinelmind {
    server 127.0.0.1:8080;
}

server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    # SSL 证书
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    include /etc/nginx/snippets/ssl-params.conf;

    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # 静态文件
    location /static/ {
        alias /opt/sentinelmind/frontend/dist/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # API 代理
    location /api/ {
        proxy_pass http://sentinelmind;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket 代理
    location /ws {
        proxy_pass http://sentinelmind;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # 健康检查
    location /health {
        proxy_pass http://sentinelmind;
    }

    # 默认
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

#### 4.4 SSL 证书申请

```bash
#!/bin/bash
# scripts/setup-ssl.sh — Let's Encrypt 证书申请

DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "用法: ./setup-ssl.sh <域名> <邮箱>"
    exit 1
fi

# 安装 certbot
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-nginx

# 申请证书
sudo certbot certonly --nginx -d "$DOMAIN" --email "$EMAIL" --agree-tos --non-interactive

# 配置自动续期
echo "0 0,12 * * * root certbot renew --quiet" | sudo tee /etc/cron.d/certbot-renew

# 复制 Nginx 配置
sudo cp deploy/nginx/sentinelmind.conf /etc/nginx/sites-available/
sudo sed -i "s/\${DOMAIN}/$DOMAIN/g" /etc/nginx/sites-available/sentinelmind.conf
sudo ln -sf /etc/nginx/sites-available/sentinelmind.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

echo "✅ SSL 证书申请完成: $DOMAIN"
```

---

### V2-6 RAG 知识检索

#### 6.1 目标

让 LLM 分析参考历史案例和 SOP，提升分析准确性。

#### 6.2 架构

```
用户查询 / 告警事件
        ↓
  拼接检索 query
        ↓
  ChromaDB 向量检索 top-5
        ↓
  注入 LLM prompt
        ↓
  LLM 分析（参考历史案例）
```

#### 6.3 文件清单

```
src/sentinelmind/storage/
└── vector_store.py        # ChromaDB 封装
src/sentinelmind/llm/
└── rag.py                 # RAG 检索逻辑
scripts/
└── ingest_knowledge.py    # 知识库导入脚本
```

#### 6.4 配置

```yaml
# configs/settings.yaml
rag:
  enabled: true
  vector_store: chromadb
  persist_dir: data/vector_db
  embedding_model: text-embedding-3-small
  top_k: 5
  knowledge_dir: data/knowledge
```

#### 6.5 知识库内容

| 类型 | 来源 | 价值 |
|------|------|------|
| 历史告警及处理 | 系统自动积累 | "上次同类事件是这么处理的" |
| 安全管理规章 | 手动导入 | "根据规定第X条，应采取XX措施" |
| 标准处置流程 | 手动导入 | "可疑物品处置流程：先疏散，再通知安保" |
| 区域特殊说明 | 手动导入 | "该区域为化学品仓库，需穿戴防护装备" |

---

## 第三批 — 视需求再定（7 天）

---

### V2-5 PostgreSQL 迁移

SQLite → PostgreSQL，支持多用户并发。提供迁移脚本和双后端适配。

### V2-7 端-云架构

边缘设备做推理，服务器做规则+LLM+通知。新增 `RemoteDetector` 实现。

---

## 实施计划

| 阶段 | 内容 | 预估 |
|------|------|------|
| **第一批** | systemd + 备份 + Docker | 3 天 |
| **第二批** | HTTPS + RAG | 4 天 |
| **第三批** | PostgreSQL + 端-云 | 7 天 |

---

| | |
|---|---|
| **文档版本** | v1 |
| **作者** | Fang |
| **最后更新** | 2026-07-14 |
