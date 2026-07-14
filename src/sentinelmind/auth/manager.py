"""认证管理器 — 用户管理、密码验证、Token 签发/校验（SQLite 持久化）"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from sentinelmind.auth.models import PERMISSIONS, Role, User, UserStatus

logger = logging.getLogger(__name__)

_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    email       TEXT    DEFAULT '',
    password    TEXT    NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'viewer',
    status      INTEGER NOT NULL DEFAULT 0,
    avatar_bg   TEXT    DEFAULT '#1890ff',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
    "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
    "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)",
]

_CREATE_LOGIN_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS login_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    ip         TEXT DEFAULT '',
    success    INTEGER NOT NULL DEFAULT 1,
    reason     TEXT DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
)
"""

_CREATE_HISTORY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_login_history_username ON login_history(username)",
    "CREATE INDEX IF NOT EXISTS idx_login_history_created ON login_history(created_at)",
]

_CREATE_PREFERENCES_TABLE = """
CREATE TABLE IF NOT EXISTS user_preferences (
    username               TEXT PRIMARY KEY,
    notify_alert_enabled   INTEGER NOT NULL DEFAULT 1,
    notify_alert_channels  TEXT NOT NULL DEFAULT '["webhook"]',
    notify_system_enabled  INTEGER NOT NULL DEFAULT 1,
    notify_system_channels TEXT NOT NULL DEFAULT '["webhook"]',
    notify_daily_enabled   INTEGER NOT NULL DEFAULT 0,
    notify_daily_channels  TEXT NOT NULL DEFAULT '["webhook"]',
    updated_at             REAL NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
)
"""

_CREATE_ACTIVE_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS active_tokens (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    ip          TEXT DEFAULT '',
    expires_at  REAL NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
)
"""

_CREATE_ACTIVE_TOKENS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_active_tokens_username ON active_tokens(username)",
    "CREATE INDEX IF NOT EXISTS idx_active_tokens_expires ON active_tokens(expires_at)",
]

_CREATE_REFRESH_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    ip          TEXT DEFAULT '',
    expires_at  REAL NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
)
"""

_CREATE_REFRESH_TOKENS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_username ON refresh_tokens(username)",
    "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires ON refresh_tokens(expires_at)",
]


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    """让 SQLite 返回字典而非元组"""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


class AuthManager:
    """认证管理器（SQLite 持久化）

    功能：
    - 用户 CRUD（SQLite 持久化）
    - 密码 PBKDF2-HMAC-SHA256 哈希
    - Token 生成 / 校验（SQLite 持久化，支持多设备同时登录）
    - 登录限流 + 权限检查
    - 默认管理员自动创建
    """

    DEFAULT_ADMIN = "admin"
    DEFAULT_PASSWORD = "admin123"
    TOKEN_EXPIRY = 86400          # 24 小时
    REFRESH_TOKEN_EXPIRY = 604800  # 7 天
    MAX_FAILED = 5
    LOCKOUT_DURATION = 300

    def __init__(self, db_path: str | Path = "data/auth.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._failed_attempts: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

        # 连接池（每线程一个连接，SQLite 限制）
        self._local = threading.local()

        self._init_db()
        self._init_default_admin()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = _dict_factory
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        """初始化数据库表"""
        conn = self._get_conn()
        conn.execute(_CREATE_USERS_TABLE)
        for idx in _CREATE_INDEXES:
            conn.execute(idx)
        conn.execute(_CREATE_LOGIN_HISTORY_TABLE)
        for idx in _CREATE_HISTORY_INDEXES:
            conn.execute(idx)
        conn.execute(_CREATE_PREFERENCES_TABLE)
        conn.execute(_CREATE_ACTIVE_TOKENS_TABLE)
        for idx in _CREATE_ACTIVE_TOKENS_INDEXES:
            conn.execute(idx)
        conn.execute(_CREATE_REFRESH_TOKENS_TABLE)
        for idx in _CREATE_REFRESH_TOKENS_INDEXES:
            conn.execute(idx)
        conn.commit()
        logger.info("auth_db_initialized path=%s", self._db_path)

    # ─── 用户管理 ──────────────────────────────────────────────

    def _init_default_admin(self) -> None:
        """初始化默认管理员账户"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (self.DEFAULT_ADMIN,)
        ).fetchone()
        if not row:
            now = time.time()
            conn.execute(
                """INSERT INTO users (username, password, role, status, must_change_password, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.DEFAULT_ADMIN,
                    self._hash_password(self.DEFAULT_PASSWORD),
                    Role.ADMIN.value,
                    UserStatus.ACTIVE.value,
                    1,  # 必须修改默认密码
                    now,
                    now,
                ),
            )
            conn.commit()
            logger.info("default_admin_created username=%s must_change_password=true", self.DEFAULT_ADMIN)

    def create_user(
        self,
        username: str,
        password: str,
        role: Role | str = Role.VIEWER,
        email: str = "",
    ) -> dict[str, Any]:
        """创建用户，返回 dict"""
        if isinstance(role, str):
            role = Role(role)
        now = time.time()
        conn = self._get_conn()
        # 邮箱唯一性检查（空邮箱不校验）
        if email and email.strip():
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ? AND email != ''", (email.strip(),)
            ).fetchone()
            if existing:
                raise ValueError(f"邮箱 {email} 已被其他用户使用")
        try:
            conn.execute(
                """INSERT INTO users (username, email, password, role, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (username, email.strip(), self._hash_password(password), role.value, UserStatus.ACTIVE.value, now, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"用户 {username} 已存在")

        logger.info("user_created username=%s role=%s", username, role.value)
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row).to_dict()

    def update_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        role: str | None = None,
        status: int | None = None,
        avatar_bg: str | None = None,
        must_change_password: bool | None = None,
    ) -> dict[str, Any]:
        """更新用户信息，只更新传入的非 None 字段"""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise ValueError(f"用户 {username} 不存在")

        updates: list[str] = []
        params: list[Any] = []

        if email is not None:
            if email and email.strip():
                existing = conn.execute(
                    "SELECT id FROM users WHERE email = ? AND username != ? AND email != ''",
                    (email.strip(), username),
                ).fetchone()
                if existing:
                    raise ValueError(f"邮箱 {email} 已被其他用户使用")
            updates.append("email = ?")
            params.append(email.strip())
        if password is not None:
            updates.append("password = ?")
            params.append(self._hash_password(password))
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if avatar_bg is not None:
            updates.append("avatar_bg = ?")
            params.append(avatar_bg)
        if must_change_password is not None:
            updates.append("must_change_password = ?")
            params.append(1 if must_change_password else 0)

        if not updates:
            return self._row_to_user(row).to_dict()

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(username)

        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params)
        conn.commit()
        logger.info("user_updated username=%s fields=%s", username, [u.split(" = ")[0] for u in updates])
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row).to_dict()

    def delete_user(self, username: str) -> None:
        """删除用户（原子事务：同时清理 token）"""
        if username == self.DEFAULT_ADMIN:
            raise ValueError("不能删除默认管理员")
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise ValueError(f"用户 {username} 不存在")
            conn.execute("DELETE FROM active_tokens WHERE username = ?", (username,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.info("user_deleted username=%s", username)

    def get_user(self, username: str) -> User | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: int) -> User | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._row_to_user(r).to_dict() for r in rows]

    @staticmethod
    def _row_to_user(row: dict[str, Any]) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            email=row.get("email", ""),
            password_hash=row["password"],
            role=Role(_safe_str(row, "role", "viewer")),
            status=UserStatus(row.get("status", 0)),
            avatar_bg=row.get("avatar_bg", "#1890ff"),
            must_change_password=bool(row.get("must_change_password", 0)),
            created_at=row.get("created_at", 0.0),
            updated_at=row.get("updated_at", 0.0),
        )

    # ─── 密码 ──────────────────────────────────────────────────

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        return salt.hex() + ":" + key.hex()

    def verify_password(self, password: str, password_hash: str) -> bool:
        try:
            salt_hex, key_hex = password_hash.split(":")
            salt = bytes.fromhex(salt_hex)
            expected_key = bytes.fromhex(key_hex)
            key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
            return secrets.compare_digest(key, expected_key)
        except (ValueError, AttributeError):
            return False

    # ─── Token ─────────────────────────────────────────────────

    def login(self, username: str, password: str, ip: str = "") -> str | None:
        """验证密码并签发 Token（含登录限流 + 状态检查 + 登录历史）"""
        # 限流检查（含自动清理过期记录）
        now = time.time()
        expired = [u for u, (_, t) in self._failed_attempts.items() if now - t >= self.LOCKOUT_DURATION]
        for u in expired:
            del self._failed_attempts[u]

        if username in self._failed_attempts:
            count, first_time = self._failed_attempts[username]
            if count >= self.MAX_FAILED and now - first_time < self.LOCKOUT_DURATION:
                remaining = int(self.LOCKOUT_DURATION - (now - first_time))
                logger.warning("login_locked username=%s remaining=%ds", username, remaining)
                return None

        user = self.get_user(username)
        if not user or not self.verify_password(password, user.password_hash):
            count, _ = self._failed_attempts.get(username, (0, time.time()))
            self._failed_attempts[username] = (count + 1, time.time())
            self.record_login(username, ip, success=False, reason="密码错误")
            return None

        # 状态检查
        if not user.is_active:
            logger.warning("login_disabled username=%s", username)
            self.record_login(username, ip, success=False, reason="账户已禁用")
            return None

        self._failed_attempts.pop(username, None)
        token = secrets.token_urlsafe(32)
        expiry = time.time() + self.TOKEN_EXPIRY
        now = time.time()

        # 持久化到 SQLite（支持多设备同时登录）
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO active_tokens (token, username, ip, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, username, ip, expiry, now),
        )
        conn.commit()

        # 概率触发过期 token 清理（约 1% 概率）
        if secrets.randbelow(100) < 1:
            self._cleanup_expired_tokens()

        self.record_login(username, ip, success=True, reason="")
        logger.info("user_logged_in username=%s ip=%s", username, ip)
        return token

    def verify_token(self, token: str) -> User | None:
        """验证 Token 并返回用户（SQLite 持久化查询）"""
        now = time.time()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT username, expires_at FROM active_tokens WHERE token = ?", (token,)
        ).fetchone()
        if row:
            if row["expires_at"] > now:
                user = self.get_user(row["username"])
                if user and user.is_active:
                    return user
            else:
                # 惰性清理过期 token
                conn.execute("DELETE FROM active_tokens WHERE token = ?", (token,))
                conn.commit()
            return None
        return None

    def logout_by_token(self, token: str) -> None:
        """单设备登出：删除指定 token"""
        conn = self._get_conn()
        conn.execute("DELETE FROM active_tokens WHERE token = ?", (token,))
        conn.commit()

    def logout(self, username: str) -> None:
        """按用户名登出（强制下线场景：删除该用户全部 token）"""
        conn = self._get_conn()
        conn.execute("DELETE FROM active_tokens WHERE username = ?", (username,))
        conn.commit()

    def _cleanup_expired_tokens(self) -> int:
        """清理过期 token，返回删除数量"""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM active_tokens WHERE expires_at < ?", (time.time(),))
        conn.commit()
        count = cur.rowcount
        if count > 0:
            logger.debug("expired_tokens_cleaned count=%d", count)
        return count

    # ─── Refresh Token ─────────────────────────────────────────

    def create_refresh_token(self, username: str, ip: str = "") -> str:
        """生成 refresh token 并持久化"""
        token = secrets.token_urlsafe(48)  # 更长的 token 更安全
        expiry = time.time() + self.REFRESH_TOKEN_EXPIRY
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO refresh_tokens (token, username, ip, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, username, ip, expiry, time.time()),
        )
        conn.commit()
        return token

    def verify_refresh_token(self, token: str) -> User | None:
        """验证 refresh token，一次性使用（验证后删除）"""
        now = time.time()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT username, expires_at FROM refresh_tokens WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        # 一次性使用：验证后立即删除
        conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))
        conn.commit()
        if row["expires_at"] <= now:
            return None
        user = self.get_user(row["username"])
        if user and user.is_active:
            return user
        return None

    def login_with_refresh(self, username: str, password: str, ip: str = "") -> dict[str, str] | None:
        """登录并返回 access_token + refresh_token"""
        access_token = self.login(username, password, ip)
        if not access_token:
            return None
        refresh_token = self.create_refresh_token(username, ip)
        return {
            "token": access_token,
            "refresh_token": refresh_token,
            "expires_in": self.TOKEN_EXPIRY,
        }

    def refresh_access_token(self, refresh_token: str, ip: str = "") -> dict[str, str] | None:
        """用 refresh token 换取新的 access token（原子事务 + rotate）"""
        now = time.time()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            # 1. 验证旧 refresh token（在事务内）
            row = conn.execute(
                "SELECT username, expires_at FROM refresh_tokens WHERE token = ?", (refresh_token,)
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None
            if row["expires_at"] <= now:
                conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (refresh_token,))
                conn.execute("COMMIT")
                return None
            username = row["username"]
            # 检查用户有效
            user = self.get_user(username)
            if not user or not user.is_active:
                conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (refresh_token,))
                conn.execute("COMMIT")
                return None
            # 2. 删除旧 refresh token
            conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (refresh_token,))
            # 3. 签发新 access token
            new_access = secrets.token_urlsafe(32)
            access_expiry = now + self.TOKEN_EXPIRY
            conn.execute(
                "INSERT INTO active_tokens (token, username, ip, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_access, username, ip, access_expiry, now),
            )
            # 4. 签发新 refresh token（rotate）
            new_refresh = secrets.token_urlsafe(48)
            refresh_expiry = now + self.REFRESH_TOKEN_EXPIRY
            conn.execute(
                "INSERT INTO refresh_tokens (token, username, ip, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_refresh, username, ip, refresh_expiry, now),
            )
            conn.execute("COMMIT")
            return {
                "token": new_access,
                "refresh_token": new_refresh,
                "expires_in": self.TOKEN_EXPIRY,
            }
        except Exception:
            conn.execute("ROLLBACK")
            logger.error("refresh_access_token_failed", exc_info=True)
            return None

    # ─── 权限 ──────────────────────────────────────────────────

    def has_permission(self, user: User, permission: str) -> bool:
        return permission in PERMISSIONS.get(user.role, set())

    def require_role(self, user: User, role: Role) -> bool:
        role_order = {Role.ADMIN: 3, Role.OPERATOR: 2, Role.VIEWER: 1}
        return role_order.get(user.role, 0) >= role_order.get(role, 0)

    # ─── 登录历史 ──────────────────────────────────────────────

    def record_login(self, username: str, ip: str, success: bool, reason: str = "") -> None:
        """写入登录历史记录（写入失败不影响主流程）"""
        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO login_history (username, ip, success, reason, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (username, ip, 1 if success else 0, reason, time.time()),
            )
            conn.commit()
        except Exception as e:
            logger.warning("login_history_write_failed username=%s error=%s", username, e)

    def get_login_history(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        """查询某用户的登录历史（最近 N 条）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM login_history WHERE username = ? ORDER BY created_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "ip": r["ip"],
                "success": bool(r["success"]),
                "reason": r["reason"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ─── 会话管理 ──────────────────────────────────────────────

    def list_active_sessions(self) -> list[dict[str, Any]]:
        """列出所有活跃 Token 会话（从持久化表查询）"""
        now = time.time()
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT token, username, ip, expires_at FROM active_tokens WHERE expires_at > ?",
            (now,),
        ).fetchall()
        return [
            {
                "token": r["token"][:8] + "...",
                "username": r["username"],
                "ip": r["ip"],
                "expires_at": r["expires_at"],
                "remaining_seconds": int(r["expires_at"] - now),
            }
            for r in rows
        ]

    def revoke_sessions(self, username: str) -> bool:
        """撤销某用户的所有 Token（强制下线）"""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM active_tokens WHERE username = ?", (username,))
        conn.commit()
        if cur.rowcount > 0:
            logger.info("sessions_revoked username=%s count=%d", username, cur.rowcount)
        return cur.rowcount > 0

    # ─── 统计 ──────────────────────────────────────────────────

    def get_user_stats(self) -> dict[str, Any]:
        """用户统计：总数、角色分布、启用/禁用数"""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()["cnt"]
        by_role = {}
        for row in conn.execute(
            "SELECT role, COUNT(*) as cnt FROM users GROUP BY role"
        ).fetchall():
            by_role[row["role"]] = row["cnt"]
        active = conn.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE status = ?", (UserStatus.ACTIVE.value,)
        ).fetchone()["cnt"]
        # 在线数从持久化 token 表查
        now = time.time()
        online = conn.execute(
            "SELECT COUNT(DISTINCT username) AS cnt FROM active_tokens WHERE expires_at > ?",
            (now,),
        ).fetchone()["cnt"]
        return {
            "total_users": total,
            "by_role": by_role,
            "active_count": active,
            "disabled_count": total - active,
            "online_count": online,
        }

    # ─── 通知偏好 ──────────────────────────────────────────────

    def _init_preferences(self, username: str) -> None:
        """为新用户创建默认偏好设置"""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR IGNORE INTO user_preferences (username, updated_at)
               VALUES (?, ?)""",
            (username, time.time()),
        )
        conn.commit()

    def get_preferences(self, username: str) -> dict[str, Any]:
        """获取用户通知偏好"""
        self._init_preferences(username)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM user_preferences WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            return self._default_preferences()
        import json as _json
        return {
            "notify_alert": {
                "enabled": bool(row["notify_alert_enabled"]),
                "channels": _json.loads(row["notify_alert_channels"]),
            },
            "notify_system": {
                "enabled": bool(row["notify_system_enabled"]),
                "channels": _json.loads(row["notify_system_channels"]),
            },
            "notify_daily": {
                "enabled": bool(row["notify_daily_enabled"]),
                "channels": _json.loads(row["notify_daily_channels"]),
            },
        }

    def update_preferences(self, username: str, prefs: dict[str, Any]) -> dict[str, Any]:
        """更新用户通知偏好"""
        import json as _json
        conn = self._get_conn()
        self._init_preferences(username)

        sets: list[str] = []
        params: list[Any] = []

        for key, col in [
            ("notify_alert", "notify_alert"),
            ("notify_system", "notify_system"),
            ("notify_daily", "notify_daily"),
        ]:
            entry = prefs.get(key)
            if isinstance(entry, dict):
                if "enabled" in entry:
                    sets.append(f"{col}_enabled = ?")
                    params.append(1 if entry["enabled"] else 0)
                if "channels" in entry:
                    sets.append(f"{col}_channels = ?")
                    params.append(_json.dumps(entry["channels"], ensure_ascii=False))

        if sets:
            sets.append("updated_at = ?")
            params.append(time.time())
            params.append(username)
            conn.execute(
                f"UPDATE user_preferences SET {', '.join(sets)} WHERE username = ?",
                params,
            )
            conn.commit()

        return self.get_preferences(username)

    @staticmethod
    def _default_preferences() -> dict[str, Any]:
        return {
            "notify_alert": {"enabled": True, "channels": ["webhook"]},
            "notify_system": {"enabled": True, "channels": ["webhook"]},
            "notify_daily": {"enabled": False, "channels": ["webhook"]},
        }

    # ─── 用户详情（含统计）──────────────────────────────────

    def get_user_detail(self, username: str) -> dict[str, Any] | None:
        """获取用户完整信息：基本信息 + 统计 + 偏好 + 安全"""
        user = self.get_user(username)
        if not user:
            return None

        history = self.get_login_history(username, limit=1)
        last_login = history[0] if history else None

        sessions = self.list_active_sessions()
        user_sessions = [s for s in sessions if s["username"] == username]

        prefs = self.get_preferences(username)

        return {
            **user.to_dict(),
            "last_login": {
                "ip": last_login["ip"] if last_login else "",
                "time": last_login["created_at"] if last_login else 0,
                "success": last_login["success"] if last_login else False,
            } if last_login else None,
            "active_sessions": len(user_sessions),
            "preferences": prefs,
        }


def _safe_str(row: dict, key: str, default: str) -> str:
    val = row.get(key, default)
    return val if isinstance(val, str) else default


# 全局单例
_auth_manager: AuthManager | None = None
_auth_lock = threading.Lock()


def get_auth_manager(db_path: str = "data/auth.db") -> AuthManager:
    global _auth_manager
    if _auth_manager is None:
        with _auth_lock:
            if _auth_manager is None:
                _auth_manager = AuthManager(db_path)
    return _auth_manager
