"""规则管理 REST API 测试 — FastAPI TestClient"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from vision_agent.rules.manager import RuleManager
from vision_agent.web.api.rules import create_router


# ─── 辅助函数 ──────────────────────────────────────────────

def _valid_body(name: str, **overrides) -> dict:
    """构造合法的规则请求体（与 RuleRequest 模型对应）"""
    body = {
        "name": name,
        "conditions": [
            {
                "type": "object_in_zone",
                "params": {
                    "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    "target_classes": ["person"],
                },
            }
        ],
        "severity": "warning",
        "cooldown": 300,
        "window_size": 5,
        "camera_ids": ["cam-1"],
        "actions": [{"type": "notify"}],
        "enabled": True,
        "description": "API 测试规则",
    }
    body.update(overrides)
    return body


# ─── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """创建 TestClient，规则写入临时目录，无认证要求"""
    rules_dir = tmp_path / "test_rules"
    rules_dir.mkdir()
    mgr = RuleManager(rules_file=str(rules_dir / "rules.yaml"))

    # Patch 模块级单例 _rule_manager，使 create_router 使用 temp dir 的 manager
    import vision_agent.web.api.rules as rules_mod

    with patch.object(rules_mod, "_rule_manager", mgr):
        router = create_router(auth_dependency=None)
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
    return TestClient(app)


# ─── 列表 ──────────────────────────────────────────────────

class TestListRules:
    def test_list_rules_empty(self, client):
        """GET /api/rules 空目录返回空列表"""
        resp = client.get("/api/rules")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_rules_with_entries(self, client):
        """GET /api/rules 有规则时返回列表"""
        client.post("/api/rules", json=_valid_body("rule_1"))
        client.post("/api/rules", json=_valid_body("rule_2"))
        resp = client.get("/api/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {r["name"] for r in data}
        assert "rule_1" in names
        assert "rule_2" in names


# ─── 详情 ──────────────────────────────────────────────────

class TestGetRule:
    def test_get_rule_returns_detail(self, client):
        """GET /api/rules/{name} 返回完整配置 + YAML 原文"""
        client.post("/api/rules", json=_valid_body("detail_rule"))
        resp = client.get("/api/rules/detail_rule")
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["name"] == "detail_rule"
        assert "yaml_raw" in data
        assert "filepath" in data
        assert "filename" in data

    def test_get_rule_not_found(self, client):
        """GET /api/rules/{name} 不存在返回 404"""
        resp = client.get("/api/rules/nonexistent")
        assert resp.status_code == 404


# ─── 创建 ──────────────────────────────────────────────────

class TestCreateRule:
    def test_create_rule_success(self, client):
        """POST /api/rules 创建成功返回 201"""
        resp = client.post("/api/rules", json=_valid_body("new_rule"))
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new_rule"
        assert "filepath" in data

    def test_create_rule_persisted(self, client):
        """POST 创建后 GET 能查到"""
        client.post("/api/rules", json=_valid_body("persist_me"))
        resp = client.get("/api/rules/persist_me")
        assert resp.status_code == 200
        assert resp.json()["config"]["name"] == "persist_me"


class TestCreateDuplicate:
    def test_create_duplicate_returns_409(self, client):
        """POST 重名规则返回 409 Conflict"""
        body = _valid_body("dup_rule")
        resp1 = client.post("/api/rules", json=body)
        assert resp1.status_code == 201

        resp2 = client.post("/api/rules", json=body)
        assert resp2.status_code == 409

    def test_create_invalid_returns_422(self, client):
        """POST 非法配置返回 422"""
        resp = client.post("/api/rules", json={
            "name": "bad",
            "conditions": [{"type": "invalid_type", "params": {}}],
        })
        assert resp.status_code == 422


# ─── 更新 ──────────────────────────────────────────────────

class TestUpdateRule:
    def test_update_rule_success(self, client):
        """PUT /api/rules/{name} 更新成功"""
        client.post("/api/rules", json=_valid_body("edit_me", severity="warning"))
        resp = client.put("/api/rules/edit_me", json=_valid_body("edit_me", severity="critical"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "edit_me"

        # 验证更新已持久化
        detail = client.get("/api/rules/edit_me")
        assert detail.json()["config"]["severity"] == "critical"

    def test_update_not_found(self, client):
        """PUT 不存在的规则返回 404"""
        resp = client.put("/api/rules/ghost", json=_valid_body("ghost"))
        assert resp.status_code == 404


# ─── 删除 ──────────────────────────────────────────────────

class TestDeleteRule:
    def test_delete_rule_success(self, client):
        """DELETE /api/rules/{name} 删除成功返回 200"""
        client.post("/api/rules", json=_valid_body("del_me"))
        resp = client.delete("/api/rules/del_me")
        assert resp.status_code == 200
        assert "已删除" in resp.json()["message"]

        # 确认已删除
        get_resp = client.get("/api/rules/del_me")
        assert get_resp.status_code == 404

    def test_delete_not_found(self, client):
        """DELETE 不存在的规则返回 404"""
        resp = client.delete("/api/rules/no_such_rule")
        assert resp.status_code == 404


# ─── 干跑测试 ──────────────────────────────────────────────

class TestTestValid:
    def test_test_rule_valid(self, client):
        """POST /api/rules/test 校验通过返回 200"""
        resp = client.post("/api/rules/test", json=_valid_body("(test)"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["rule_type"] == "object_in_zone"
        assert data["params"]["zone_vertices"] == 4

    def test_test_rule_count_line_valid(self, client):
        """POST /api/rules/test 计数线校验通过"""
        resp = client.post("/api/rules/test", json={
            "name": "(test_line)",
            "conditions": [{
                "type": "count_line",
                "params": {
                    "line_start": [0, 0],
                    "line_end": [100, 0],
                    "threshold": 5,
                },
            }],
            "severity": "info",
            "camera_ids": None,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["rule_type"] == "count_line"

    def test_test_rule_no_side_effects(self, client):
        """POST /api/rules/test 不写入文件，不创建规则"""
        resp = client.post("/api/rules/test", json=_valid_body("(dry_only)"))
        assert resp.status_code == 200

        # 确认规则未被创建
        get_resp = client.get("/api/rules/(dry_only)")
        # 可能 404 也可能空列表，取决于 RuleManager.name_to_path 的查找逻辑
        # 规范要求：test 不写文件，所以不应返回该规则
        list_resp = client.get("/api/rules")
        created_names = {r["name"] for r in list_resp.json()}
        assert "(dry_only)" not in created_names


class TestTestInvalid:
    def test_test_rule_invalid_returns_422(self, client):
        """POST /api/rules/test 校验失败返回 422"""
        resp = client.post("/api/rules/test", json={
            "name": "(bad_test)",
            "conditions": [{"type": "nonexistent_type", "params": {}}],
        })
        assert resp.status_code == 422

    def test_test_rule_missing_conditions_returns_422(self, client):
        """POST /api/rules/test 缺少 conditions 返回 422"""
        resp = client.post("/api/rules/test", json={
            "name": "(no_cond)",
            "conditions": [],
        })
        # 缺少 conditions 校验失败 → 422
        assert resp.status_code == 422

    def test_test_rule_invalid_severity_returns_422(self, client):
        """POST /api/rules/test 非法严重级别返回 422"""
        resp = client.post("/api/rules/test", json={
            "name": "(bad_sev)",
            "conditions": [{
                "type": "object_in_zone",
                "params": {"zone": [[0, 0], [10, 0], [10, 10], [0, 10]]},
            }],
            "severity": "fatal",
        })
        assert resp.status_code == 422
