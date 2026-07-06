#!/usr/bin/env python3
"""
Task Manager — 多 Agent 协作任务管理器

用途：管理多个 Agent 的协作开发任务，实时显示进度面板。
适用：任何需要"写代码 + Review + 测试"协作流程的项目。

用法：
    # 作为库使用
    from tools.task_manager import TaskManager

    tm = TaskManager()
    tm.add_module("types.py", depends_on=[])
    tm.add_module("camera.py", depends_on=["types.py"])
    tm.add_agent("写代码", color="green")
    tm.add_agent("Review", color="yellow")
    tm.add_agent("测试", color="cyan")

    tm.assign("写代码", "types.py")
    tm.update("写代码", "types.py", "in_progress", detail="编写 BoundingBox...")
    tm.update("写代码", "types.py", "done")

    tm.assign("Review", "types.py")
    tm.update("Review", "types.py", "done", detail="0 问题")

    tm.display()  # 打印状态面板

    # CLI 用法
    python tools/task_manager.py status
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ─── 状态枚举 ───────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


# ─── 数据模型 ───────────────────────────────────────────────

@dataclass
class AgentTask:
    """一个 Agent 对一个模块的任务"""
    agent_name: str
    module_name: str
    status: TaskStatus = TaskStatus.PENDING
    detail: str = ""           # 当前进度描述
    start_time: float = 0.0
    end_time: float = 0.0
    output: str = ""           # 最终输出（review 报告 / 测试结果）


@dataclass
class Module:
    """一个待开发的模块"""
    name: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    line_count: int = 0        # 代码行数


@dataclass
class Agent:
    """一个参与协作的 Agent"""
    name: str
    color: str = "white"       # 终端显示颜色
    current_task: Optional[str] = None  # 当前正在处理的模块名


# ─── Task Manager ───────────────────────────────────────────

class TaskManager:
    """多 Agent 协作任务管理器"""

    def __init__(self, project_name: str = "Vision Agent"):
        self.project_name = project_name
        self.modules: dict[str, Module] = {}
        self.agents: dict[str, Agent] = {}
        self.tasks: list[AgentTask] = []
        self.start_time = time.time()

    # ─── 配置 ────────────────────────────────────────────────

    def add_module(self, name: str, depends_on: list[str] | None = None) -> None:
        """注册一个待开发的模块"""
        self.modules[name] = Module(
            name=name,
            depends_on=depends_on or []
        )

    def add_agent(self, name: str, color: str = "white") -> None:
        """注册一个参与协作的 Agent"""
        self.agents[name] = Agent(name=name, color=color)

    # ─── 任务操作 ─────────────────────────────────────────────

    def assign(self, agent_name: str, module_name: str) -> AgentTask:
        """分配任务：让某个 Agent 处理某个模块"""
        self._validate(agent_name, module_name)

        task = AgentTask(
            agent_name=agent_name,
            module_name=module_name,
            status=TaskStatus.IN_PROGRESS,
            start_time=time.time()
        )
        self.tasks.append(task)
        self.agents[agent_name].current_task = module_name
        self.modules[module_name].status = TaskStatus.IN_PROGRESS
        return task

    def update(self, agent_name: str, module_name: str,
               status: str | TaskStatus, detail: str = "", output: str = "") -> None:
        """更新任务状态"""
        task = self._find_task(agent_name, module_name)
        if task is None:
            return

        task.status = TaskStatus(status)
        if detail:
            task.detail = detail
        if output:
            task.output = output

        if task.status in (TaskStatus.DONE, TaskStatus.FAILED):
            task.end_time = time.time()
            self.agents[agent_name].current_task = None

        # 更新模块状态
        module = self.modules[module_name]
        if task.status == TaskStatus.DONE:
            # 检查是否所有 agent 对该模块的任务都完成了
            all_done = all(
                t.status == TaskStatus.DONE
                for t in self.tasks
                if t.module_name == module_name
            )
            if all_done:
                module.status = TaskStatus.DONE
        elif task.status == TaskStatus.FAILED:
            module.status = TaskStatus.FAILED

    def set_line_count(self, module_name: str, count: int) -> None:
        """设置模块的代码行数"""
        if module_name in self.modules:
            self.modules[module_name].line_count = count

    # ─── 查询 ────────────────────────────────────────────────

    def get_progress(self) -> tuple[int, int]:
        """返回 (已完成模块数, 总模块数)"""
        done = sum(1 for m in self.modules.values() if m.status == TaskStatus.DONE)
        return done, len(self.modules)

    def get_agent_status(self, agent_name: str) -> str:
        """获取 Agent 当前状态描述"""
        agent = self.agents.get(agent_name)
        if agent is None:
            return "未知"

        if agent.current_task:
            task = self._find_task(agent_name, agent.current_task)
            if task and task.detail:
                return f"{agent.current_task} — {task.detail}"
            return f"{agent.current_task} 处理中..."

        # 查找最近完成的任务
        recent = [t for t in self.tasks if t.agent_name == agent_name and t.status == TaskStatus.DONE]
        if recent:
            last = recent[-1]
            return f"✅ {last.module_name} 完成"

        return "等待中..."

    def get_total_lines(self) -> int:
        """获取总代码行数"""
        return sum(m.line_count for m in self.modules.values())

    def get_elapsed(self) -> str:
        """获取已用时间"""
        elapsed = int(time.time() - self.start_time)
        if elapsed < 60:
            return f"{elapsed}s"
        elif elapsed < 3600:
            return f"{elapsed // 60}m {elapsed % 60}s"
        else:
            return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"

    def get_pending_modules(self) -> list[str]:
        """获取可以开始的模块（依赖已完成）"""
        pending = []
        for module in self.modules.values():
            if module.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                self.modules.get(d) and self.modules[d].status == TaskStatus.DONE
                for d in module.depends_on
            )
            if deps_met:
                pending.append(module.name)
        return pending

    def get_blocked_modules(self) -> list[str]:
        """获取被阻塞的模块（依赖未完成）"""
        blocked = []
        for module in self.modules.values():
            if module.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                self.modules.get(d) and self.modules[d].status == TaskStatus.DONE
                for d in module.depends_on
            )
            if not deps_met:
                blocked.append(module.name)
        return blocked

    # ─── 显示 ────────────────────────────────────────────────

    def display(self) -> str:
        """生成状态面板文本"""
        done, total = self.get_progress()
        elapsed = self.get_elapsed()
        lines = self.get_total_lines()
        progress_pct = int(done / total * 100) if total > 0 else 0
        bar = self._progress_bar(done, total, 20)

        output = []
        output.append("")
        output.append(f"┌{'─' * 56}┐")
        output.append(f"│  🖥️  {self.project_name} Task Manager{' ' * (44 - len(self.project_name))}│")
        output.append(f"├{'─' * 56}│")

        # Agent 状态
        for agent in self.agents.values():
            status = self.get_agent_status(agent.name)
            icon = self._agent_icon(agent)
            line = f"│  {icon} {agent.name:<12} {status}"
            padding = 56 - len(line.encode('utf-8')) // 2 + len(line) - len(line)
            output.append(f"{line:<55}│")

        output.append(f"├{'─' * 56}│")

        # 进度
        output.append(f"│  进度：{bar} {done}/{total} ({progress_pct}%){' ' * max(0, 20 - len(f'{done}/{total} ({progress_pct}%)'))}│")

        # 统计
        stats = f"耗时 {elapsed} | 代码 {lines} 行"
        output.append(f"│  {stats:<54}│")

        # 下一步
        pending = self.get_pending_modules()
        if pending:
            next_up = f"下一步：{', '.join(pending[:3])}"
            if len(pending) > 3:
                next_up += f" (+{len(pending)-3})"
            output.append(f"│  {next_up:<54}│")

        output.append(f"└{'─' * 56}┘")
        output.append("")

        return "\n".join(output)

    def display_compact(self) -> str:
        """生成单行紧凑状态"""
        done, total = self.get_progress()
        elapsed = self.get_elapsed()
        agents = []
        for agent in self.agents.values():
            if agent.current_task:
                agents.append(f"⏳{agent.name}→{agent.current_task}")
            else:
                agents.append(f"✅{agent.name}")
        return f"[{done}/{total}] {' | '.join(agents)} | {elapsed}"

    # ─── 报告 ────────────────────────────────────────────────

    def review_report(self, module_name: str, issues: list[dict]) -> str:
        """生成 Review 报告"""
        output = []
        output.append("")
        output.append(f"📋 Review 报告 — {module_name}")
        output.append(f"{'━' * 40}")

        if not issues:
            output.append("✅ 无问题")
        else:
            for issue in issues:
                severity = issue.get("severity", "info")
                icon = {"error": "❌", "warning": "⚠️", "info": "💡"}.get(severity, "•")
                output.append(f"{icon} [{severity}] {issue.get('message', '')}")
                if issue.get("file"):
                    output.append(f"   位置：{issue['file']}:{issue.get('line', '?')}")

        error_count = sum(1 for i in issues if i.get("severity") == "error")
        warn_count = sum(1 for i in issues if i.get("severity") == "warning")
        output.append(f"\n结论：{error_count} 个错误，{warn_count} 个警告")
        output.append("")
        return "\n".join(output)

    def test_report(self, module_name: str, results: list[dict]) -> str:
        """生成测试报告"""
        output = []
        output.append("")
        output.append(f"🧪 测试报告 — {module_name}")
        output.append(f"{'━' * 40}")

        passed = 0
        failed = 0
        for r in results:
            name = r.get("name", "unknown")
            status = r.get("status", "unknown")
            if status == "passed":
                output.append(f"  ✅ {name}")
                passed += 1
            else:
                output.append(f"  ❌ {name} — {r.get('error', '')}")
                failed += 1

        total_time = sum(r.get("duration", 0) for r in results)
        output.append(f"\n结果：{passed} passed, {failed} failed | 耗时 {total_time:.2f}s")
        output.append("")
        return "\n".join(output)

    # ─── 持久化 ───────────────────────────────────────────────

    def save(self, path: str = "data/task_manager_state.json") -> None:
        """保存状态到文件"""
        state = {
            "project_name": self.project_name,
            "start_time": self.start_time,
            "modules": {
                name: {
                    "name": m.name,
                    "depends_on": m.depends_on,
                    "status": m.status.value,
                    "line_count": m.line_count
                }
                for name, m in self.modules.items()
            },
            "agents": {
                name: {
                    "name": a.name,
                    "color": a.color,
                    "current_task": a.current_task
                }
                for name, a in self.agents.items()
            },
            "tasks": [
                {
                    "agent_name": t.agent_name,
                    "module_name": t.module_name,
                    "status": t.status.value,
                    "detail": t.detail,
                    "start_time": t.start_time,
                    "end_time": t.end_time,
                    "output": t.output
                }
                for t in self.tasks
            ]
        }

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def load(self, path: str = "data/task_manager_state.json") -> None:
        """从文件加载状态"""
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)

        self.project_name = state.get("project_name", self.project_name)
        self.start_time = state.get("start_time", time.time())

        for name, m in state.get("modules", {}).items():
            self.modules[name] = Module(
                name=m["name"],
                depends_on=m.get("depends_on", []),
                status=TaskStatus(m.get("status", "pending")),
                line_count=m.get("line_count", 0)
            )

        for name, a in state.get("agents", {}).items():
            self.agents[name] = Agent(
                name=a["name"],
                color=a.get("color", "white"),
                current_task=a.get("current_task")
            )

        for t in state.get("tasks", []):
            self.tasks.append(AgentTask(
                agent_name=t["agent_name"],
                module_name=t["module_name"],
                status=TaskStatus(t.get("status", "pending")),
                detail=t.get("detail", ""),
                start_time=t.get("start_time", 0),
                end_time=t.get("end_time", 0),
                output=t.get("output", "")
            ))

    # ─── 内部方法 ─────────────────────────────────────────────

    def _validate(self, agent_name: str, module_name: str) -> None:
        if agent_name not in self.agents:
            raise ValueError(f"未知 Agent: {agent_name}")
        if module_name not in self.modules:
            raise ValueError(f"未知模块: {module_name}")

        # 检查依赖是否满足
        module = self.modules[module_name]
        for dep in module.depends_on:
            if dep in self.modules and self.modules[dep].status != TaskStatus.DONE:
                raise ValueError(f"模块 {module_name} 依赖 {dep}，但 {dep} 未完成")

    def _find_task(self, agent_name: str, module_name: str) -> Optional[AgentTask]:
        for task in reversed(self.tasks):
            if task.agent_name == agent_name and task.module_name == module_name:
                return task
        return None

    def _agent_icon(self, agent: Agent) -> str:
        if agent.current_task:
            return "⏳"
        recent = [t for t in self.tasks if t.agent_name == agent.name and t.status == TaskStatus.DONE]
        if recent:
            return "✅"
        return "⏸️"

    @staticmethod
    def _progress_bar(current: int, total: int, width: int = 20) -> str:
        if total == 0:
            return "░" * width
        filled = int(width * current / total)
        return "█" * filled + "░" * (width - filled)


# ─── 便捷工厂函数 ────────────────────────────────────────────

def create_vision_agent_manager() -> TaskManager:
    """创建 Vision Agent 项目的 Task Manager（预配置）"""
    tm = TaskManager(project_name="Vision Agent")

    # 注册模块（按依赖顺序）
    tm.add_module("core/types.py", depends_on=[])
    tm.add_module("core/camera.py", depends_on=["core/types.py"])
    tm.add_module("core/detector.py", depends_on=["core/types.py"])
    tm.add_module("core/tracker.py", depends_on=["core/types.py"])
    tm.add_module("core/recorder.py", depends_on=["core/types.py"])
    tm.add_module("core/pipeline.py", depends_on=[
        "core/camera.py", "core/detector.py", "core/tracker.py", "core/recorder.py"
    ])
    tm.add_module("config/settings.py", depends_on=[])
    tm.add_module("rules/engine.py", depends_on=["core/types.py"])
    tm.add_module("rules/builtin/*.py", depends_on=["rules/engine.py"])
    tm.add_module("storage/database.py", depends_on=["core/types.py"])
    tm.add_module("llm/analyzer.py", depends_on=["core/types.py"])
    tm.add_module("llm/provider.py", depends_on=[])
    tm.add_module("actions/notifier.py", depends_on=["core/types.py"])
    tm.add_module("web/api/*.py", depends_on=["core/types.py", "storage/database.py"])
    tm.add_module("__main__.py", depends_on=[
        "core/pipeline.py", "config/settings.py", "web/api/*.py"
    ])

    # 注册 Agent
    tm.add_agent("写代码", color="green")
    tm.add_agent("Review", color="yellow")
    tm.add_agent("测试", color="cyan")

    return tm


# ─── CLI ────────────────────────────────────────────────────

def main():
    import sys
    import io

    # Windows 终端 UTF-8 支持
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    tm = create_vision_agent_manager()
    state_path = "data/task_manager_state.json"

    if Path(state_path).exists():
        tm.load(state_path)

    if len(sys.argv) < 2:
        print(tm.display())
        return

    cmd = sys.argv[1]

    if cmd == "status":
        print(tm.display())

    elif cmd == "compact":
        print(tm.display_compact())

    elif cmd == "pending":
        pending = tm.get_pending_modules()
        if pending:
            print("可开始的模块：")
            for m in pending:
                print(f"  • {m}")
        else:
            print("没有可开始的模块（可能有依赖未满足）")

    elif cmd == "assign" and len(sys.argv) >= 4:
        agent_name = sys.argv[2]
        module_name = sys.argv[3]
        tm.assign(agent_name, module_name)
        tm.save(state_path)
        print(f"已分配：{agent_name} → {module_name}")

    elif cmd == "done" and len(sys.argv) >= 4:
        agent_name = sys.argv[2]
        module_name = sys.argv[3]
        detail = sys.argv[4] if len(sys.argv) > 4 else ""
        tm.update(agent_name, module_name, "done", detail=detail)
        tm.save(state_path)
        print(f"已完成：{agent_name} → {module_name}")

    elif cmd == "fail" and len(sys.argv) >= 4:
        agent_name = sys.argv[2]
        module_name = sys.argv[3]
        detail = sys.argv[4] if len(sys.argv) > 4 else ""
        tm.update(agent_name, module_name, "failed", detail=detail)
        tm.save(state_path)
        print(f"失败：{agent_name} → {module_name}")

    else:
        print("用法：")
        print("  python tools/task_manager.py status      # 显示状态面板")
        print("  python tools/task_manager.py compact      # 单行状态")
        print("  python tools/task_manager.py pending      # 可开始的模块")
        print("  python tools/task_manager.py assign <agent> <module>")
        print("  python tools/task_manager.py done <agent> <module> [detail]")
        print("  python tools/task_manager.py fail <agent> <module> [detail]")


if __name__ == "__main__":
    main()
