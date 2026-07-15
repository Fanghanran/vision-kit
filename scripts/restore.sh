#!/bin/bash
# scripts/restore.sh — SentinelMind 数据恢复

set -e

INSTALL_DIR="${SENTINELMIND_DIR:-/opt/sentinelmind}"
BACKUP_ROOT="${BACKUP_DIR:-$INSTALL_DIR/backups}"
DATA_DIR="$INSTALL_DIR/data"
CONFIG_DIR="$INSTALL_DIR/configs"

echo "=== SentinelMind 数据恢复 ==="

# 1. 列出可用备份
echo ""
echo "可用备份："
if [ ! -d "$BACKUP_ROOT" ] || [ -z "$(ls -A $BACKUP_ROOT 2>/dev/null)" ]; then
    echo "❌ 无可用备份"
    exit 1
fi

ls -1d "$BACKUP_ROOT"/*/ | while read dir; do
    backup_name=$(basename "$dir")
    backup_size=$(du -sh "$dir" | cut -f1)
    echo "  $backup_name ($backup_size)"
done

# 2. 选择备份
echo ""
read -p "输入要恢复的备份日期 (如 20260714_120000): " BACKUP_DATE

# 路径遍历防护：只允许 YYYYMMDD_HHMMSS 格式
if [[ ! "$BACKUP_DATE" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
    echo "❌ 格式错误，请输入 YYYYMMDD_HHMMSS 格式"
    exit 1
fi

BACKUP_DIR="$BACKUP_ROOT/$BACKUP_DATE"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "❌ 备份目录不存在: $BACKUP_DIR"
    exit 1
fi

# 3. 显示备份信息
echo ""
echo "备份信息："
if [ -f "$BACKUP_DIR/metadata.json" ]; then
    cat "$BACKUP_DIR/metadata.json"
fi

# 4. 确认恢复
echo ""
echo "⚠️  警告：恢复将覆盖当前数据！"
read -p "确认恢复？(y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# 5. 停止服务
if systemctl is-active --quiet sentinelmind 2>/dev/null; then
    echo "停止 SentinelMind..."
    systemctl stop sentinelmind
    sleep 2
fi

# 6. 恢复数据库
if [ -f "$BACKUP_DIR/sentinelmind.db" ]; then
    cp "$BACKUP_DIR/sentinelmind.db" "$DATA_DIR/sentinelmind.db"
    chown sentinelmind:sentinelmind "$DATA_DIR/sentinelmind.db" 2>/dev/null || true
    echo "✅ 数据库已恢复"
fi

# 7. 恢复截图
if [ -f "$BACKUP_DIR/snapshots.tar.gz" ]; then
    rm -rf "$DATA_DIR/snapshots"
    tar -xzf "$BACKUP_DIR/snapshots.tar.gz" -C "$DATA_DIR/"
    chown -R sentinelmind:sentinelmind "$DATA_DIR/snapshots" 2>/dev/null || true
    echo "✅ 截图已恢复"
fi

# 8. 恢复视频
if [ -f "$BACKUP_DIR/clips.tar.gz" ]; then
    rm -rf "$DATA_DIR/clips"
    tar -xzf "$BACKUP_DIR/clips.tar.gz" -C "$DATA_DIR/"
    chown -R sentinelmind:sentinelmind "$DATA_DIR/clips" 2>/dev/null || true
    echo "✅ 视频已恢复"
fi

# 9. 恢复配置
if [ -d "$BACKUP_DIR/configs" ]; then
    cp -r "$BACKUP_DIR/configs/"* "$CONFIG_DIR/"
    echo "✅ 配置已恢复"
fi

# 10. 重启服务
if systemctl is-enabled --quiet sentinelmind 2>/dev/null; then
    echo "启动 SentinelMind..."
    systemctl start sentinelmind
fi

echo ""
echo "✅ 恢复完成"
