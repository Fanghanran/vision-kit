"""
数据库存储模块 — 告警数据持久化

设计来源：docs/modules/storage/database.md

职责：
- SQLite/PostgreSQL 双后端适配（第一版仅 SQLite）
- alerts + events 表管理（自动建表、索引）
- 告警 CRUD + 分页查询 + 统计
- JSON 字段序列化/反序列化
- 软删除（is_archived）

设计决策：
- 第一版用 SQLite（零部署），后期通过配置切换 PostgreSQL
- 接口层抽象与实现层分离
- UUID 主键，全局唯一
- WAL 模式支持读写并发
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sentinelmind.core.exceptions import DatabaseError
from sentinelmind.core.types import Alert, AlertStatus, Event, LLMAnalysis, Severity

logger = logging.getLogger(__name__)


# ─── SQL 建表语句 ────────────────────────────────────────────

_CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    camera_name TEXT DEFAULT '',
    rule_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    snapshot_path TEXT DEFAULT '',
    video_clip_path TEXT DEFAULT '',
    llm_description TEXT DEFAULT '',
    llm_risk_level TEXT DEFAULT '',
    llm_suggestion TEXT DEFAULT '',
    llm_raw_response TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    detections_snapshot TEXT DEFAULT '[]',
    notified_channels TEXT DEFAULT '[]',
    created_at REAL NOT NULL,
    acknowledged_at REAL DEFAULT 0,
    acknowledged_by TEXT DEFAULT '',
    rejected_at REAL DEFAULT 0,
    resolved_at REAL DEFAULT 0,
    is_archived INTEGER DEFAULT 0
)
"""

_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    camera_name TEXT DEFAULT '',
    rule_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    detections_snapshot TEXT DEFAULT '[]',
    created_at REAL NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_camera_status ON alerts(camera_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_event_type ON alerts(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity)",
    "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id)",
]

_CREATE_ALERT_ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    TEXT NOT NULL,
    action_type TEXT NOT NULL,
    actor       TEXT NOT NULL,
    actor_role  TEXT DEFAULT '',
    details     TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
)
"""

_CREATE_ALERT_ACTIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_alert_actions_alert_id ON alert_actions(alert_id)",
    "CREATE INDEX IF NOT EXISTS idx_alert_actions_created ON alert_actions(created_at)",
]

_CREATE_AUDIT_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,
    role        TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource    TEXT DEFAULT '',
    details     TEXT DEFAULT '',
    ip          TEXT DEFAULT '',
    created_at  REAL NOT NULL
)
"""

_CREATE_AUDIT_LOGS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_username ON audit_logs(username)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at)",
]

_CREATE_SYSTEM_CONTROLS_TABLE = """
CREATE TABLE IF NOT EXISTS system_controls (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_by  TEXT DEFAULT '',
    updated_at  REAL NOT NULL
)
"""

# 控制项白名单 + 默认值
SYSTEM_CONTROLS_DEFAULTS = {
    "llm.enabled": True,
    "llm.cache_enabled": True,
    "notification.webhook.enabled": False,
    "notification.email.enabled": False,
    "recording.enabled": True,
    "rules.hot_reload": True,
    "cameras.hot_reload": True,
    "camera.auto_reconnect": True,
    "websocket.enabled": True,
    "audit.enabled": True,
}

# 控制项类型定义（True=布尔型，False=字符串型）
SYSTEM_CONTROLS_TYPES = {k: True for k in SYSTEM_CONTROLS_DEFAULTS}


# ─── 工具函数 ────────────────────────────────────────────────


def _safe_json_dumps(obj: Any) -> str:
    """安全 JSON 序列化"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _safe_json_loads(s: str | None, default: Any = None) -> Any:
    """安全 JSON 反序列化"""
    if not s:
        return default if default is not None else {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        logger.warning("json_parse_failed value=%s", s[:100] if s else "")
        return default if default is not None else {}


# ─── DatabaseManager ─────────────────────────────────────────


class DatabaseManager:
    """数据库管理器（database.md 2.1 节）

    第一版实现 SQLite，后期可扩展 PostgreSQL。
    接口不变，切换只需修改 storage.type 配置。
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化数据库管理器

        Args:
            config: storage 配置段（可选，有默认值）
        """
        self._config = config or {}
        self._db_type = self._config.get("type", "sqlite")
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

        # SQLite 配置
        sqlite_config = self._config.get("sqlite", {})
        self._db_path = sqlite_config.get("path", "data/sentinelmind.db")

    # ─── 连接管理 ──────────────────────────────────────────────

    def connect(self) -> None:
        """建立数据库连接，创建表结构"""
        if self._db_type == "sqlite":
            self._connect_sqlite()
        else:
            raise DatabaseError(f"不支持的数据库类型: {self._db_type}")

        self.init_tables()
        logger.info("database_connected type=%s path=%s", self._db_type, self._db_path)

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                logger.error("db_close_error error=%s", str(e))
            finally:
                self._conn = None
        logger.info("database_closed")

    def _connect_sqlite(self) -> None:
        """SQLite 连接"""
        db_path = Path(self._db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                str(db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            # WAL 模式支持读写并发 + 启用外键约束（CASCADE 生效）
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            raise DatabaseError(
                f"SQLite 连接失败: {e}",
                context={"path": str(db_path)},
            ) from e

    def init_tables(self) -> None:
        """创建或升级表结构（幂等操作）"""
        if not self._conn:
            raise DatabaseError("数据库未连接")
        try:
            cursor = self._conn.cursor()
            cursor.execute(_CREATE_ALERTS_TABLE)
            cursor.execute(_CREATE_EVENTS_TABLE)
            cursor.execute(_CREATE_ALERT_ACTIONS_TABLE)
            cursor.execute(_CREATE_AUDIT_LOGS_TABLE)
            cursor.execute(_CREATE_SYSTEM_CONTROLS_TABLE)
            for idx_sql in _CREATE_INDEXES + _CREATE_ALERT_ACTIONS_INDEXES + _CREATE_AUDIT_LOGS_INDEXES:
                cursor.execute(idx_sql)
            self._conn.commit()
            # 初始化默认控制项
            self._init_system_controls()
            logger.info("database_tables_initialized")
        except sqlite3.Error as e:
            raise DatabaseError(f"建表失败: {e}") from e

    # ─── 告警 CRUD ────────────────────────────────────────────

    def save_alert(self, event: Event, **kwargs: Any) -> str:
        """保存告警记录（database.md 3.3 节）

        兼容 DatabaseProtocol 签名：接受 Event，内部构造 Alert。

        Args:
            event: Event 对象
            **kwargs: 额外参数（llm_analysis 等）

        Returns:
            alert_id
        """
        if not self._conn:
            raise DatabaseError("数据库未连接")

        created_at = kwargs.get("created_at", time.time())
        alert = Alert(
            alert_id=kwargs.get("alert_id", str(uuid.uuid4())),
            event=event,
            llm_analysis=kwargs.get("llm_analysis"),
            created_at=created_at,
        )

        # 保存关联的 Event（INSERT OR IGNORE，幂等）
        self.save_event(alert.event)

        data = self._alert_to_row(alert)
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))

        sql = f"INSERT INTO alerts ({columns}) VALUES ({placeholders})"  # noqa: S608
        try:
            with self._lock:
                self._conn.execute(sql, list(data.values()))
                self._conn.commit()
            return alert.alert_id
        except sqlite3.IntegrityError:
            logger.warning("alert_duplicate id=%s", alert.alert_id)
            return alert.alert_id
        except sqlite3.Error as e:
            raise DatabaseError(
                f"保存告警失败: {e}", context={"alert_id": alert.alert_id}
            ) from e

    def get_alert(self, alert_id: str) -> Alert | None:
        """按 ID 获取单条告警"""
        if not self._conn:
            raise DatabaseError("数据库未连接")

        sql = "SELECT * FROM alerts WHERE id = ? AND is_archived = 0"
        try:
            with self._lock:
                cursor = self._conn.execute(sql, (alert_id,))
                row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_alert(dict(row))
        except sqlite3.Error as e:
            raise DatabaseError(f"查询告警失败: {e}") from e

    def list_alerts(
        self,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[Alert], int]:
        """分页查询告警列表（database.md 3.4 节）

        Args:
            filters: 筛选条件（status/camera_id/event_type/severity/start_time/end_time）
            page: 页码（从 1 开始）
            page_size: 每页条数（最大 100）
            sort_by: 排序字段
            sort_order: 排序方向（asc/desc）

        Returns:
            (告警列表, 总数)
        """
        if not self._conn:
            raise DatabaseError("数据库未连接")

        filters = filters or {}
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        if sort_order.lower() not in ("asc", "desc"):
            sort_order = "desc"

        # 构造 WHERE 子句
        where_clauses = ["is_archived = 0"]
        params: list[Any] = []

        if filters.get("status"):
            where_clauses.append("status = ?")
            params.append(filters["status"])
        if filters.get("camera_id"):
            where_clauses.append("camera_id = ?")
            params.append(filters["camera_id"])
        if filters.get("event_type"):
            where_clauses.append("event_type = ?")
            params.append(filters["event_type"])
        if filters.get("severity"):
            where_clauses.append("severity = ?")
            params.append(filters["severity"])
        if filters.get("start_time"):
            where_clauses.append("created_at >= ?")
            params.append(filters["start_time"])
        if filters.get("end_time"):
            where_clauses.append("created_at <= ?")
            params.append(filters["end_time"])

        where_str = " AND ".join(where_clauses)

        # 白名单校验排序字段，防止 SQL 注入
        allowed_sort_fields = {
            "created_at",
            "severity",
            "status",
            "camera_id",
            "event_type",
        }
        if sort_by not in allowed_sort_fields:
            sort_by = "created_at"

        try:
            with self._lock:
                # COUNT
                count_sql = f"SELECT COUNT(*) FROM alerts WHERE {where_str}"  # noqa: S608
                total = self._conn.execute(count_sql, params).fetchone()[0]

                # SELECT
                offset = (page - 1) * page_size
                query_sql = (
                    f"SELECT * FROM alerts WHERE {where_str} "  # noqa: S608
                    f"ORDER BY {sort_by} {sort_order} "
                    f"LIMIT ? OFFSET ?"
                )
                cursor = self._conn.execute(query_sql, params + [page_size, offset])
                rows = cursor.fetchall()

            alerts = [self._row_to_alert(dict(row)) for row in rows]
            return alerts, total

        except sqlite3.Error as e:
            raise DatabaseError(f"查询告警列表失败: {e}") from e

    def update_alert(self, alert_id: str, updates: dict[str, Any]) -> bool:
        """更新告警字段（如状态变更）

        Args:
            alert_id: 告警 ID
            updates: 要更新的字段字典

        Returns:
            是否成功
        """
        if not self._conn:
            raise DatabaseError("数据库未连接")

        # 白名单：只允许更新的字段
        allowed_fields = {
            "status",
            "acknowledged_at",
            "acknowledged_by",
            "resolved_at",
            "llm_description",
            "llm_risk_level",
            "llm_suggestion",
            "llm_raw_response",
            "video_clip_path",
            "notified_channels",
            "is_archived",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered:
            return False

        # JSON 字段序列化
        json_fields = {"notified_channels"}
        for k in json_fields:
            if k in filtered and not isinstance(filtered[k], str):
                filtered[k] = _safe_json_dumps(filtered[k])

        set_clauses = [f"{k} = ?" for k in filtered]
        sql = f"UPDATE alerts SET {', '.join(set_clauses)} WHERE id = ?"  # noqa: S608
        params = list(filtered.values()) + [alert_id]

        try:
            with self._lock:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise DatabaseError(
                f"更新告警失败: {e}", context={"alert_id": alert_id}
            ) from e

    def delete_alert(self, alert_id: str) -> bool:
        """软删除告警（标记为 archived）"""
        return self.update_alert(alert_id, {"is_archived": 1})

    # ─── 事件存储 ──────────────────────────────────────────────

    def save_event(self, event: Event) -> str:
        """保存事件记录"""
        if not self._conn:
            raise DatabaseError("数据库未连接")

        data = {
            "id": event.event_id,
            "event_type": event.event_type,
            "camera_id": event.camera_id,
            "camera_name": event.camera_name,
            "rule_name": event.rule_name,
            "severity": event.severity.value,
            "metadata": _safe_json_dumps(event.metadata),
            "detections_snapshot": _safe_json_dumps(
                [d.to_dict() for d in event.detections]
            ),
            "created_at": event.timestamp,
        }

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT OR IGNORE INTO events ({columns}) VALUES ({placeholders})"  # noqa: S608

        try:
            with self._lock:
                self._conn.execute(sql, list(data.values()))
                self._conn.commit()
            return event.event_id
        except sqlite3.Error as e:
            raise DatabaseError(f"保存事件失败: {e}") from e

    def get_event(self, event_id: str) -> Event | None:
        """按 ID 获取事件"""
        if not self._conn:
            raise DatabaseError("数据库未连接")

        sql = "SELECT * FROM events WHERE id = ?"
        try:
            with self._lock:
                cursor = self._conn.execute(sql, (event_id,))
                row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_event(dict(row))
        except sqlite3.Error as e:
            raise DatabaseError(f"查询事件失败: {e}") from e

    # ─── 统计查询 ──────────────────────────────────────────────

    def get_stats(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """告警统计查询（database.md 3.5 节）

        Args:
            filters: 筛选条件（start_time/end_time/camera_id/group_by）

        Returns:
            统计结果字典
        """
        if not self._conn:
            raise DatabaseError("数据库未连接")

        filters = filters or {}
        where_clauses = ["is_archived = 0"]
        params: list[Any] = []

        if filters.get("start_time"):
            where_clauses.append("created_at >= ?")
            params.append(filters["start_time"])
        if filters.get("end_time"):
            where_clauses.append("created_at <= ?")
            params.append(filters["end_time"])
        if filters.get("camera_id"):
            where_clauses.append("camera_id = ?")
            params.append(filters["camera_id"])

        where_str = " AND ".join(where_clauses)

        try:
            with self._lock:
                # 总数
                total_sql = f"SELECT COUNT(*) FROM alerts WHERE {where_str}"  # noqa: S608
                total_count = self._conn.execute(total_sql, params).fetchone()[0]

                # 按状态统计
                status_sql = f"SELECT status, COUNT(*) FROM alerts WHERE {where_str} GROUP BY status"  # noqa: S608
                by_status = dict(self._conn.execute(status_sql, params).fetchall())

                # 按严重级别统计
                severity_sql = f"SELECT severity, COUNT(*) FROM alerts WHERE {where_str} GROUP BY severity"  # noqa: S608
                by_severity = dict(self._conn.execute(severity_sql, params).fetchall())

                # 按 group_by 聚合
                group_by = filters.get("group_by", "")
                groups = []
                if group_by in ("camera", "event_type", "severity"):
                    col = "camera_id" if group_by == "camera" else group_by
                    group_sql = (
                        f"SELECT {col}, COUNT(*) FROM alerts WHERE {where_str} "  # noqa: S608
                        f"GROUP BY {col} ORDER BY COUNT(*) DESC"
                    )
                    groups = [
                        {"group_key": row[0], "count": row[1]}
                        for row in self._conn.execute(group_sql, params).fetchall()
                    ]

            return {
                "total_count": total_count,
                "groups": groups,
                "by_status": by_status,
                "by_severity": by_severity,
            }

        except sqlite3.Error as e:
            raise DatabaseError(f"统计查询失败: {e}") from e

    def count_alerts_today(self) -> int:
        """统计今日告警数（pipeline 健康检查用）"""
        if not self._conn:
            return 0

        today_start = (
            datetime.now()
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )

        sql = "SELECT COUNT(*) FROM alerts WHERE created_at >= ? AND is_archived = 0"
        try:
            with self._lock:
                count = self._conn.execute(sql, (today_start,)).fetchone()[0]
            return count
        except sqlite3.Error:
            return 0

    # ─── 序列化/反序列化 ──────────────────────────────────────

    @staticmethod
    def _alert_to_row(alert: Alert) -> dict[str, Any]:
        """Alert → 数据库行"""
        event = alert.event
        llm = alert.llm_analysis
        return {
            "id": alert.alert_id,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "camera_id": event.camera_id,
            "camera_name": event.camera_name,
            "rule_name": event.rule_name,
            "severity": event.severity.value,
            "status": alert.status.value,
            "snapshot_path": event.snapshot_path,
            "video_clip_path": alert.video_clip_path,
            "llm_description": llm.description if llm else "",
            "llm_risk_level": llm.risk_level if llm else "",
            "llm_suggestion": llm.suggestion if llm else "",
            "llm_raw_response": llm.raw_response if llm else "",
            "metadata": _safe_json_dumps(event.metadata),
            "detections_snapshot": _safe_json_dumps(
                [d.to_dict() for d in event.detections]
            ),
            "notified_channels": _safe_json_dumps(alert.notified_channels),
            "created_at": alert.created_at,
            "acknowledged_at": alert.acknowledged_at,
            "acknowledged_by": alert.acknowledged_by,
            "rejected_at": alert.rejected_at,
            "resolved_at": 0.0,
            "is_archived": 0,
        }

    @staticmethod
    def _row_to_alert(row: dict[str, Any]) -> Alert:
        """数据库行 → Alert"""
        llm_data = None
        if row.get("llm_description") or row.get("llm_risk_level"):
            llm_data = LLMAnalysis(
                description=row.get("llm_description", ""),
                risk_level=row.get("llm_risk_level", ""),
                suggestion=row.get("llm_suggestion", ""),
                raw_response=row.get("llm_raw_response", ""),
            )

        event = Event(
            event_id=row.get("event_id", ""),
            event_type=row.get("event_type", ""),
            camera_id=row.get("camera_id", ""),
            camera_name=row.get("camera_name", ""),
            rule_name=row.get("rule_name", ""),
            severity=_safe_enum(
                Severity, row.get("severity", "warning"), Severity.WARNING
            ),
            snapshot_path=row.get("snapshot_path", ""),
            metadata=_safe_json_loads(row.get("metadata"), {}),
            timestamp=row.get("created_at", 0.0),
        )

        return Alert(
            alert_id=row.get("id", ""),
            event=event,
            llm_analysis=llm_data,
            video_clip_path=row.get("video_clip_path", ""),
            status=_safe_enum(
                AlertStatus, row.get("status", "pending"), AlertStatus.PENDING
            ),
            notified_channels=_safe_json_loads(row.get("notified_channels"), []),
            created_at=row.get("created_at", 0.0),
            acknowledged_at=row.get("acknowledged_at", 0.0),
            acknowledged_by=row.get("acknowledged_by", ""),
            rejected_at=row.get("rejected_at", 0.0),
        )

    @staticmethod
    def _row_to_event(row: dict[str, Any]) -> Event:
        """数据库行 → Event"""
        return Event(
            event_id=row.get("id", ""),
            event_type=row.get("event_type", ""),
            camera_id=row.get("camera_id", ""),
            camera_name=row.get("camera_name", ""),
            rule_name=row.get("rule_name", ""),
            severity=_safe_enum(
                Severity, row.get("severity", "warning"), Severity.WARNING
            ),
            metadata=_safe_json_loads(row.get("metadata"), {}),
            timestamp=row.get("created_at", 0.0),
        )

    # ─── 操作历史 ──────────────────────────────────────────────

    def save_alert_action(
        self,
        alert_id: str,
        action_type: str,
        actor: str,
        actor_role: str = "",
        details: str = "",
    ) -> None:
        """保存告警操作记录（降级模式：DB 不可用时 log warning 而非 raise）"""
        if not self._conn:
            logger.warning("save_alert_action_skipped alert_id=%s reason=no_connection", alert_id)
            return
        sql = (
            "INSERT INTO alert_actions (alert_id, action_type, actor, actor_role, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        try:
            with self._lock:
                self._conn.execute(
                    sql, (alert_id, action_type, actor, actor_role, details, time.time())
                )
                self._conn.commit()
        except sqlite3.Error as e:
            logger.warning("save_alert_action_failed alert_id=%s error=%s", alert_id, e)

    def get_alert_actions(self, alert_id: str) -> list[dict[str, Any]]:
        """查询告警的操作历史"""
        if not self._conn:
            return []
        sql = (
            "SELECT id, alert_id, action_type, actor, actor_role, details, created_at "
            "FROM alert_actions WHERE alert_id = ? ORDER BY created_at ASC"
        )
        try:
            with self._lock:
                rows = self._conn.execute(sql, (alert_id,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning("get_alert_actions_failed alert_id=%s error=%s", alert_id, e)
            return []

    # ─── 审计日志 ──────────────────────────────────────────────

    def save_audit_log(
        self,
        username: str,
        role: str,
        action: str,
        resource: str = "",
        details: str = "",
        ip: str = "",
    ) -> None:
        """保存审计日志（降级模式：DB 不可用时 log warning 而非 raise）"""
        if not self._conn:
            logger.warning("save_audit_log_skipped username=%s action=%s reason=no_connection", username, action)
            return
        # 检查控制面板开关
        if not self.get_control_value("audit.enabled"):
            return
        sql = (
            "INSERT INTO audit_logs (username, role, action, resource, details, ip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            with self._lock:
                self._conn.execute(
                    sql, (username, role, action, resource, details, ip, time.time())
                )
                self._conn.commit()
        except sqlite3.Error as e:
            logger.warning("save_audit_log_failed username=%s action=%s error=%s", username, action, e)

    def list_audit_logs(
        self,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页查询审计日志"""
        if not self._conn:
            return [], 0
        filters = filters or {}
        page = max(1, page)
        page_size = min(100, max(1, page_size))

        where_clauses: list[str] = []
        params: list[Any] = []
        if filters.get("username"):
            where_clauses.append("username = ?")
            params.append(filters["username"])
        if filters.get("action"):
            where_clauses.append("action = ?")
            params.append(filters["action"])
        if filters.get("start_time"):
            where_clauses.append("created_at >= ?")
            params.append(filters["start_time"])
        if filters.get("end_time"):
            where_clauses.append("created_at <= ?")
            params.append(filters["end_time"])

        where_str = " AND ".join(where_clauses) if where_clauses else "1=1"

        try:
            with self._lock:
                count_sql = f"SELECT COUNT(*) FROM audit_logs WHERE {where_str}"  # noqa: S608
                total = self._conn.execute(count_sql, params).fetchone()[0]

                offset = (page - 1) * page_size
                query_sql = (
                    f"SELECT * FROM audit_logs WHERE {where_str} "  # noqa: S608
                    f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
                )
                rows = self._conn.execute(query_sql, params + [page_size, offset]).fetchall()
            return [dict(r) for r in rows], total
        except sqlite3.Error as e:
            logger.warning("list_audit_logs_failed error=%s", e)
            return [], 0

    # ─── 系统控制项 ────────────────────────────────────────────

    def _init_system_controls(self) -> None:
        """初始化默认控制项（仅插入不存在的 key）"""
        if not self._conn:
            return
        now = time.time()
        for key, default_val in SYSTEM_CONTROLS_DEFAULTS.items():
            self._conn.execute(
                "INSERT OR IGNORE INTO system_controls (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)",
                (key, str(default_val).lower(), "", now),
            )
        self._conn.commit()

    def get_controls(self) -> dict[str, dict[str, Any]]:
        """获取所有控制项"""
        if not self._conn:
            return {}
        rows = self._conn.execute("SELECT * FROM system_controls").fetchall()
        result = {}
        for r in rows:
            key = r["key"]
            val_str = r["value"]
            # 根据类型定义转换值
            if key in SYSTEM_CONTROLS_TYPES and SYSTEM_CONTROLS_TYPES[key]:
                val = val_str.lower() in ("true", "1", "yes")
            else:
                val = val_str
            result[key] = {
                "value": val,
                "updated_by": r["updated_by"],
                "updated_at": r["updated_at"],
            }
        return result

    def get_control(self, key: str) -> dict[str, Any] | None:
        """获取单个控制项"""
        controls = self.get_controls()
        return controls.get(key)

    def get_control_value(self, key: str) -> Any:
        """获取控制项的值（带默认值回退）"""
        controls = self.get_controls()
        if key in controls:
            return controls[key]["value"]
        return SYSTEM_CONTROLS_DEFAULTS.get(key)

    def update_control(self, key: str, value: Any, updated_by: str = "") -> bool:
        """更新单个控制项"""
        if key not in SYSTEM_CONTROLS_DEFAULTS:
            return False
        if not self._conn:
            return False
        # 类型校验
        if key in SYSTEM_CONTROLS_TYPES and SYSTEM_CONTROLS_TYPES[key]:
            if not isinstance(value, bool):
                return False
            val_str = str(value).lower()
        else:
            val_str = str(value)
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO system_controls (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)",
                    (key, val_str, updated_by, time.time()),
                )
                self._conn.commit()
            return True
        except sqlite3.Error as e:
            logger.warning("update_control_failed key=%s error=%s", key, e)
            return False

    def update_controls(self, controls: dict[str, Any], updated_by: str = "") -> int:
        """批量更新控制项（单一事务），返回成功更新的数量"""
        if not self._conn:
            return 0
        count = 0
        now = time.time()
        try:
            with self._lock:
                for key, value in controls.items():
                    if key not in SYSTEM_CONTROLS_DEFAULTS:
                        continue
                    if key in SYSTEM_CONTROLS_TYPES and SYSTEM_CONTROLS_TYPES[key]:
                        if not isinstance(value, bool):
                            continue
                        val_str = str(value).lower()
                    else:
                        val_str = str(value)
                    self._conn.execute(
                        "INSERT OR REPLACE INTO system_controls (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)",
                        (key, val_str, updated_by, now),
                    )
                    count += 1
                self._conn.commit()
        except sqlite3.Error as e:
            logger.warning("update_controls_failed error=%s", e)
        return count


# ─── 辅助函数 ────────────────────────────────────────────────


def _safe_enum(enum_cls: type, value: str, default: Any) -> Any:
    """安全枚举反序列化"""
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return default
