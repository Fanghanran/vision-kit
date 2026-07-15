#!/bin/bash
# deploy/install.sh — SentinelMind 一键安装

set -e

INSTALL_DIR="/opt/sentinelmind"
SERVICE_FILE="deploy/sentinelmind.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== SentinelMind 安装 ==="

# 1. 检查 root 权限
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 sudo 运行此脚本"
    exit 1
fi

# 2. 检查是否已在目标目录
CURRENT_DIR=$(realpath .)
if [ "$CURRENT_DIR" = "$INSTALL_DIR" ]; then
    echo "❌ 请不要在 $INSTALL_DIR 目录内运行此脚本"
    exit 1
fi

# 3. 创建系统用户
if ! id "sentinelmind" &>/dev/null; then
    useradd -r -s /bin/false sentinelmind
    echo "✅ 创建系统用户 sentinelmind"
else
    echo "ℹ️  用户 sentinelmind 已存在"
fi

# 4. 创建安装目录并复制文件
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
# 代码文件由 root 拥有（只读保护）
chown -R root:root "$INSTALL_DIR"
echo "✅ 文件已复制到 $INSTALL_DIR"

# 5. 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "ℹ️  Python 版本: $PYTHON_VERSION"

# 6. 创建数据目录并设置权限
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/configs"
chown sentinelmind:sentinelmind "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/configs"
chmod 755 "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/configs"

# 创建 Ultralytics 配置目录（系统用户需要）
mkdir -p /home/sentinelmind/.config/Ultralytics
chown -R sentinelmind:sentinelmind /home/sentinelmind
echo "✅ 数据目录已创建"

# 7. 创建虚拟环境（root 用户创建，sentinelmind 用户使用）
cd "$INSTALL_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    chown -R sentinelmind:sentinelmind venv
    echo "✅ 虚拟环境已创建"
fi

# 8. 安装依赖（root 用户安装到 venv）
./venv/bin/pip install -r requirements.txt
# 安装 sentinelmind 包本身（editable 模式）
./venv/bin/pip install -e .
echo "✅ 依赖已安装"

# 9. 复制配置文件（如果不存在）
if [ ! -f "$INSTALL_DIR/configs/settings.yaml" ]; then
    if [ -f "$INSTALL_DIR/configs/settings.yaml.example" ]; then
        cp "$INSTALL_DIR/configs/settings.yaml.example" "$INSTALL_DIR/configs/settings.yaml"
        chown sentinelmind:sentinelmind "$INSTALL_DIR/configs/settings.yaml"
        echo "⚠️  请编辑 $INSTALL_DIR/configs/settings.yaml"
    fi
fi

# 10. 安装 systemd 服务
cp "$SERVICE_FILE" "$SYSTEMD_DIR/"
systemctl daemon-reload
systemctl enable sentinelmind
echo "✅ systemd 服务已安装并启用"

# 11. 完成
echo ""
echo "=== 安装完成 ==="
echo ""
echo "启动：  sudo systemctl start sentinelmind"
echo "状态：  sudo systemctl status sentinelmind"
echo "日志：  journalctl -u sentinelmind -f"
echo "配置：  $INSTALL_DIR/configs/settings.yaml"
echo "数据：  $INSTALL_DIR/data/"
echo ""
