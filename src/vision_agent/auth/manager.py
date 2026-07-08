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

from vision_agent.auth.models import PERMISSIONS, Role, User, UserStatus

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
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
    "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
    "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)",
]


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    """让 SQLite 返回字典而非元组"""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


class AuthManager:
    """认证管理器（SQLite 持久化）

    功能：
    - 用户 CRUD（SQLite 持久化）
    - 密码 PBKDF2-HMAC-SHA256 哈希
    - Token 生成 / 校验（内存）
    - 登录限流 + 权限检查
    - 默认管理员自动创建
    """

    DEFAULT_ADMIN = "admin"
    DEFAULT_PASSWORD = "admin123"
    TOKEN_EXPIRY = 86400
    MAX_FAILED = 5
    LOCKOUT_DURATION = 300

    def __init__(self, db_path: str | Path = "data/auth.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._tokens: dict[str, tuple[str, float]] = {}
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
                """INSERT INTO users (username, password, role, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self.DEFAULT_ADMIN,
                    self._hash_password(self.DEFAULT_PASSWORD),
                    Role.ADMIN.value,
                    UserStatus.ACTIVE.value,
                    now,
                    now,
                ),
            )
            conn.commit()
            logger.info("default_admin_created username=%s", self.DEFAULT_ADMIN)

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
        try:
            conn.execute(
                """INSERT INTO users (username, email, password, role, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (username, email, self._hash_password(password), role.value, UserStatus.ACTIVE.value, now, now),
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
    ) -> dict[str, Any]:
        """更新用户信息，只更新传入的非 None 字段"""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise ValueError(f"用户 {username} 不存在")

        updates: list[str] = []
        params: list[Any] = []

        if email is not None:
            updates.append("email = ?")
            params.append(email)
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
        """删除用户"""
        if username == self.DEFAULT_ADMIN:
            raise ValueError("不能删除默认管理员")
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"用户 {username} 不存在")
        with self._lock:
            self._tokens.pop(username, None)
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

    def login(self, username: str, password: str) -> str | None:
        """验证密码并签发 Token（含登录限流 + 状态检查）"""
        # 限流检查
        if username in self._failed_attempts:
            count, first_time = self._failed_attempts[username]
            if count >= self.MAX_FAILED and time.time() - first_time < self.LOCKOUT_DURATION:
                remaining = int(self.LOCKOUT_DURATION - (time.time() - first_time))
                logger.warning("login_locked username=%s remaining=%ds", username, remaining)
                return None
            if time.time() - first_time >= self.LOCKOUT_DURATION:
                del self._failed_attempts[username]

        user = self.get_user(username)
        if not user or not self.verify_password(password, user.password_hash):
            count, _ = self._failed_attempts.get(username, (0, time.time()))
            self._failed_attempts[username] = (count + 1, time.time())
            return None

        # 状态检查
        if not user.is_active:
            logger.warning("login_disabled username=%s", username)
            return None

        self._failed_attempts.pop(username, None)
        token = secrets.token_urlsafe(32)
        expiry = time.time() + self.TOKEN_EXPIRY
        with self._lock:
            self._tokens[username] = (token, expiry)
        logger.info("user_logged_in username=%s", username)
        return token

    def verify_token(self, token: str) -> User | None:
        """验证 Token 并返回用户（常数时间比较 + 状态检查）"""
        now = time.time()
        with self._lock:
            for username, (stored_token, expiry) in list(self._tokens.items()):
                if expiry < now:
                    self._tokens.pop(username, None)
                    continue
                if secrets.compare_digest(stored_token, token):
                    user = self.get_user(username)
                    if user and user.is_active:
                        return user
                    return None
        return None

    def logout(self, username: str) -> None:
        with self._lock:
            self._tokens.pop(username, None)

    # ─── 权限 ──────────────────────────────────────────────────

    def has_permission(self, user: User, permission: str) -> bool:
        return permission in PERMISSIONS.get(user.role, set())

    def require_role(self, user: User, role: Role) -> bool:
        role_order = {Role.ADMIN: 3, Role.OPERATOR: 2, Role.VIEWER: 1}
        return role_order.get(user.role, 0) >= role_order.get(role, 0)


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
