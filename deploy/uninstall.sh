#!/bin/bash
# deploy/uninstall.sh — SentinelMind 卸载

set -e

INSTALL_DIR="/opt/sentinelmind"

echo "=== SentinelMind 卸载 ==="

# 1. 检查 root 权限
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 sudo 运行此脚本"
    exit 1
fi

# 2. 停止并禁用服务
if systemctl is-active --quiet sentinelmind 2>/dev/null; then
    systemctl stop sentinelmind
    echo "✅ 服务已停止"
fi

if systemctl is-enabled --quiet sentinelmind 2>/dev/null; then
    systemctl disable sentinelmind
    echo "✅ 服务已禁用"
fi

# 3. 删除服务文件
rm -f /etc/systemd/system/sentinelmind.service
systemctl daemon-reload
echo "✅ systemd 服务已删除"

# 4. 询问是否删除安装目录
read -p "是否删除安装目录 $INSTALL_DIR？(y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo "✅ 安装目录已删除"
else
    echo "ℹ️  保留安装目录 $INSTALL_DIR"
fi

# 5. 询问是否删除用户
read -p "是否删除系统用户 sentinelmind？(y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    userdel sentinelmind 2>/dev/null || true
    echo "✅ 用户已删除"
else
    echo "ℹ️  保留用户 sentinelmind"
fi

echo ""
echo "=== 卸载完成 ==="
