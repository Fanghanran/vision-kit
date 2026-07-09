"""用户、角色数据模型"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class UserStatus(int, Enum):
    ACTIVE = 0
    DISABLED = 1


# 权限矩阵
PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {"view:alerts", "manage:alerts", "manage:config", "manage:users", "view:cameras", "control:cameras"},
    Role.OPERATOR: {"view:alerts", "manage:alerts", "view:cameras", "control:cameras"},
    Role.VIEWER: {"view:alerts", "view:cameras"},
}


@dataclass
class User:
    """系统用户"""

    username: str
    password_hash: str
    role: Role = Role.VIEWER
    email: str = ""
    status: UserStatus = UserStatus.ACTIVE
    avatar_bg: str = "#1890ff"
    id: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role.value,
            "status": self.status.value,
            "avatar_bg": self.avatar_bg,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
