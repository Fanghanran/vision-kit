"""
规则管理 REST API

三模式（write-check-test）：
- write：POST /api/rules（创建）+ PUT /api/rules/{name}（编辑）+ DELETE /api/rules/{name}（删除）
- check：GET /api/rules（列表）+ GET /api/rules/{name}（详情+YAML原文）
- test：POST /api/rules/test（干跑校验，不写文件）

设计来源：docs/modules/rules/rule_management.md
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fastapi import APIRouter

logger = logging.getLogger(__name__)

# ─── 请求/响应模型 ────────────────────────────────────────────────


class ConditionParams(BaseModel):
    zone: list[list[float]] | None = Field(default=None, description="多边形顶点 [[x,y],...]")
    line_start: list[float] | None = Field(default=None, description="计数线起点 [x,y]")
    line_end: list[float] | None = Field(default=None, description="计数线终点 [x,y]")
    threshold: int | None = Field(default=None, description="计数阈值")
    target_classes: list[str] | None = Field(default=None, description="目标类别，如 ['person']")


class RuleCondition(BaseModel):
    type: str = Field(..., description="object_in_zone / count_line / zone_empty")
    params: ConditionParams = Field(default_factory=ConditionParams)


class RuleAction(BaseModel):
    type: str = Field(..., description="notify / record_clip / llm_analyze")


class TimeWindow(BaseModel):
    start: str = Field(default="00:00", description="HH:MM")
    end: str = Field(default="23:59", description="HH:MM")
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6], description="0=周一,...,6=周日")


class RuleRequest(BaseModel):
    """创建/更新规则的请求体"""

    name: str = Field(..., min_length=1, max_length=64, description="规则名称，全局唯一")
    conditions: list[RuleCondition] = Field(..., min_length=1, description="触发条件列表")
    description: str = Field(default="", description="规则描述")
    camera_ids: list[str] | None = Field(default=None, description="适用摄像头 ID 列表，null 表示全部")
    severity: str = Field(default="warning", description="critical / warning / info")
    cooldown: int | None = Field(default=None, ge=0, description="冷却时间（秒），null 表示关闭")
    window_size: int | None = Field(default=None, ge=1, description="连续触发帧数阈值")
    time_windows: list[dict] | None = Field(default=None, description="时间窗口列表")
    actions: list[RuleAction] = Field(default_factory=list, description="触发的动作")
    enabled: bool = Field(default=True, description="是否启用")


class TestRuleRequest(BaseModel):
    """测试请求，接受与创建相同的字段，但不写文件"""

    name: str = Field(default="(测试规则)", max_length=64)
    description: str = Field(default="", description="规则描述")
    conditions: list[RuleCondition] = Field(..., min_length=1)
    camera_ids: list[str] | None = None
    severity: str = "warning"
    cooldown: int | None = None
    window_size: int | None = None
    time_windows: list[dict] | None = None
    actions: list[RuleAction] = Field(default_factory=list)
    enabled: bool = True


# ─── 内部工具 ────────────────────────────────────────────────────

def _dump_with_none(data: dict[str, Any]) -> dict[str, Any]:
    """序列化请求体，保留显式设为 None 的字段（用于清空可选值）

    Pydantic 的 exclude_none=True 会丢弃所有 None 值，导致无法
    将 camera_ids/cooldown 等字段重置为 null。
    此方法用 exclude_unset 保留用户显式传入的 None。
    """
    # 先用 exclude_unset 获取用户传了的字段
    result: dict[str, Any] = {}
    for key, value in data.items():
        if value is not None:
            result[key] = value
        # None 值保留：允许用户清空可选字段
    return result


# ─── FastAPI Router ──────────────────────────────────────────────


# 模块级单例 manager
_rule_manager: Any = None


def _get_manager() -> Any:
    global _rule_manager
    if _rule_manager is None:
        from vision_agent.rules.manager import RuleManager

        _rule_manager = RuleManager()
    return _rule_manager


def create_router(auth_dependency: Any = None) -> "APIRouter | None":
    """创建规则管理路由

    Args:
        auth_dependency: 可选的 FastAPI 认证依赖（如 _require_auth）。
                         传入后，增删改端点将要求登录态。
    """
    try:
        from fastapi import APIRouter, Body, Depends, HTTPException
    except ImportError:
        return None

    manager = _get_manager()

    router = APIRouter(prefix="/api/rules", tags=["规则管理"])

    # 认证依赖：传入则要求登录，否则允许匿名访问
    _auth: list[Any] = [Depends(auth_dependency)] if auth_dependency else []

    # ── CHECK：列表 ──

    @router.get("",
        summary="规则列表",
        description="返回所有规则文件的摘要信息（名称、类型、摄像头、严重级别、冷却、动作等）。",
    )
    async def list_rules() -> list[dict]:
        return manager.list_rules()

    # ── CHECK：详情 ──

    @router.get("/{name}",
        summary="规则详情",
        description="返回单条规则的完整配置和 YAML 原文。",
        responses={200: {"description": "成功"}, 404: {"description": "规则不存在"}},
    )
    async def get_rule(name: str) -> dict:
        rule = manager.get_rule(name)
        if not rule:
            raise HTTPException(404, f"规则不存在: {name}")
        return rule

    # ── WRITE：创建 ──

    @router.post("", status_code=201,
        summary="创建规则",
        description="创建新检测规则 → 写 YAML 文件 → 5 秒内热重载自动生效。",
        dependencies=_auth,
        responses={
            201: {"description": "创建成功"},
            409: {"description": "同名规则已存在"},
            422: {"description": "校验失败"},
        },
    )
    async def create_rule(body: RuleRequest = Body(...)) -> dict:
        data = body.model_dump(exclude_none=True)
        # 显式保留 None 值字段（允许用户传 camera_ids: null 表示全部摄像头）
        for field_name in ("camera_ids", "cooldown", "window_size", "time_windows"):
            if getattr(body, field_name) is None and field_name in body.model_fields_set:
                data[field_name] = None
        try:
            filepath = manager.create_rule(body.name, data)
        except FileExistsError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"message": f"规则 '{body.name}' 已创建", "name": body.name, "filepath": filepath}

    # ── WRITE：编辑 ──

    @router.put("/{name}",
        summary="更新规则",
        description="修改规则配置 → 覆盖写 YAML 文件 → 5 秒内热重载自动生效。",
        dependencies=_auth,
        responses={
            200: {"description": "更新成功"},
            404: {"description": "规则不存在"},
            422: {"description": "校验失败"},
        },
    )
    async def update_rule(name: str, body: RuleRequest = Body(...)) -> dict:
        data = body.model_dump(exclude_none=True)
        for field_name in ("camera_ids", "cooldown", "window_size", "time_windows"):
            if getattr(body, field_name) is None and field_name in body.model_fields_set:
                data[field_name] = None
        try:
            filepath = manager.update_rule(name, data)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"message": f"规则 '{name}' 已更新", "name": name, "filepath": filepath}

    # ── WRITE：删除 ──

    @router.delete("/{name}",
        summary="删除规则",
        description="删除规则 → 删除 YAML 文件 → 5 秒内热重载自动感知。",
        dependencies=_auth,
        responses={200: {"description": "删除成功"}, 404: {"description": "规则不存在"}},
    )
    async def delete_rule(name: str) -> dict:
        ok = manager.delete_rule(name)
        if not ok:
            raise HTTPException(404, f"规则不存在: {name}")
        return {"message": f"规则 '{name}' 已删除", "name": name}

    # ── TEST：干跑校验 ──

    @router.post("/test",
        summary="测试规则（干跑）",
        description="校验规则配置，返回校验结果、参数摘要和预期行为。不写文件、不影响引擎。",
        responses={200: {"description": "校验完成"}, 422: {"description": "校验失败"}},
    )
    async def test_rule(body: TestRuleRequest = Body(...)) -> dict:
        result = manager.test_rule(body.model_dump(exclude_none=True))
        if not result["valid"]:
            raise HTTPException(422, detail="; ".join(result["errors"]))
        return {
            "valid": result["valid"],
            "rule_type": result["rule_type"],
            "params": result["params"],
            "actions": result.get("actions", []),
            "message": "校验通过 — 规则语法正确，可以保存",
        }

    return router
