#!/bin/bash
# scripts/cleanup_backups.sh — SentinelMind 备份清理

set -e

INSTALL_DIR="${SENTINELMIND_DIR:-/opt/sentinelmind}"
BACKUP_ROOT="${BACKUP_DIR:-$INSTALL_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

echo "=== SentinelMind 备份清理 ==="
echo "保留最近 $KEEP_DAYS 天的备份"

# 1. 统计当前备份
TOTAL=$(find "$BACKUP_ROOT" -maxdepth 1 -type d | wc -l)
TOTAL=$((TOTAL - 1))  # 排除 BACKUP_ROOT 自身

if [ "$TOTAL" -eq 0 ]; then
    echo "ℹ️  无备份"
    exit 0
fi

echo "当前备份数: $TOTAL"

# 2. 删除过期备份
DELETED=0
for dir in "$BACKUP_ROOT"/*/; do
    [ -d "$dir" ] || continue
    dir_name=$(basename "$dir")

    # 解析日期（格式：YYYYMMDD_HHMMSS）
    dir_date=$(echo "$dir_name" | cut -d_ -f1)

    # 计算天数差
    if [[ "$dir_date" =~ ^[0-9]{8}$ ]]; then
        dir_epoch=$(date -d "$dir_date" +%s 2>/dev/null || echo 0)
        now_epoch=$(date +%s)
        days_old=$(( (now_epoch - dir_epoch) / 86400 ))

        if [ "$days_old" -gt "$KEEP_DAYS" ]; then
            rm -rf "$dir"
            echo "🗑️  删除: $dir_name ($days_old 天前)"
            DELETED=$((DELETED + 1))
        fi
    fi
done

# 3. 汇总
REMAINING=$((TOTAL - DELETED))
echo ""
echo "✅ 清理完成: 删除 $DELETED 个, 保留 $REMAINING 个"
