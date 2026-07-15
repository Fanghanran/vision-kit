# Dockerfile — SentinelMind 多阶段构建

# ─── 构建阶段 ──────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    && pip install --no-cache-dir --prefix=/install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com numpy \
    && pip install --no-cache-dir --prefix=/install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt \
    && pip install --no-cache-dir --prefix=/install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com "fastapi" "uvicorn[standard]" \
    && pip install --no-cache-dir --prefix=/install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com ultralytics \
    && pip install --no-cache-dir --prefix=/install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com torch torchvision

# ─── 运行阶段 ──────────────────────────────────────
FROM python:3.12-slim

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制 Python 依赖
COPY --from=builder /install /usr/local

# 创建用户
RUN useradd -m -s /bin/bash sentinelmind

WORKDIR /app

# 复制代码（保持 src/sentinelmind/ 结构）
COPY src/sentinelmind/ sentinelmind/
COPY configs/ configs/
COPY frontend/dist/ frontend/dist/

# 创建数据目录
RUN mkdir -p data/snapshots data/clips logs \
    && chown -R sentinelmind:sentinelmind /app

# 切换用户
USER sentinelmind

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# 启动
CMD ["python", "-m", "sentinelmind", "--config", "configs"]
