#!/bin/bash
# scripts/backup.sh — SentinelMind 数据备份

set -e

# 配置（可通过环境变量覆盖）
INSTALL_DIR="${SENTINELMIND_DIR:-/opt/sentinelmind}"
BACKUP_ROOT="${BACKUP_DIR:-$INSTALL_DIR/backups}"
DATA_DIR="$INSTALL_DIR/data"
CONFIG_DIR="$INSTALL_DIR/configs"
DB_FILE="$DATA_DIR/sentinelmind.db"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_ROOT/$DATE"

echo "=== SentinelMind 备份 $DATE ==="

# 创建备份目录
mkdir -p "$BACKUP_DIR"

# 1. SQLite 原子备份
if [ -f "$DB_FILE" ]; then
    sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/sentinelmind.db'"
    DB_SIZE=$(du -sh "$BACKUP_DIR/sentinelmind.db" | cut -f1)
    echo "✅ 数据库备份完成 ($DB_SIZE)"
else
    echo "⚠️  数据库文件不存在，跳过"
fi

# 2. 截图备份（保留目录结构）
if [ -d "$DATA_DIR/snapshots" ] && [ -n "$(ls -A "$DATA_DIR/snapshots" 2>/dev/null)" ]; then
    tar -czf "$BACKUP_DIR/snapshots.tar.gz" -C "$DATA_DIR" snapshots/
    SNAP_SIZE=$(du -sh "$BACKUP_DIR/snapshots.tar.gz" | cut -f1)
    SNAP_COUNT=$(find "$DATA_DIR/snapshots" -name "*.jpg" | wc -l)
    echo "✅ 截图备份完成 ($SNAP_COUNT 张, $SNAP_SIZE)"
else
    echo "ℹ️  无截图，跳过"
fi

# 3. 视频片段备份
if [ -d "$DATA_DIR/clips" ] && [ -n "$(ls -A "$DATA_DIR/clips" 2>/dev/null)" ]; then
    tar -czf "$BACKUP_DIR/clips.tar.gz" -C "$DATA_DIR" clips/
    CLIP_SIZE=$(du -sh "$BACKUP_DIR/clips.tar.gz" | cut -f1)
    CLIP_COUNT=$(find "$DATA_DIR/clips" -name "*.mp4" | wc -l)
    echo "✅ 视频备份完成 ($CLIP_COUNT 个, $CLIP_SIZE)"
else
    echo "ℹ️  无视频片段，跳过"
fi

# 4. 配置文件备份
cp -r "$CONFIG_DIR" "$BACKUP_DIR/configs"
echo "✅ 配置备份完成"

# 5. 备份元数据
DB_SIZE=$(stat -c%s "$DB_FILE" 2>/dev/null || echo 0)
SNAP_COUNT=$(find "$DATA_DIR/snapshots" -name "*.jpg" 2>/dev/null | wc -l)
CLIP_COUNT=$(find "$DATA_DIR/clips" -name "*.mp4" 2>/dev/null | wc -l)
HOSTNAME=$(hostname | sed 's/"/\\"/g')
VERSION=$(cat "$INSTALL_DIR/src/sentinelmind/__init__.py" 2>/dev/null | grep __version__ | cut -d'"' -f2 || echo 'unknown')

cat > "$BACKUP_DIR/metadata.json" << EOF
{
    "date": "$DATE",
    "db_size_bytes": $DB_SIZE,
    "snapshot_count": $SNAP_COUNT,
    "clip_count": $CLIP_COUNT,
    "hostname": "$HOSTNAME",
    "sentinelmind_version": "$VERSION"
}
EOF

# 计算备份总大小
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo ""
echo "✅ 备份完成: $BACKUP_DIR ($BACKUP_SIZE)"
