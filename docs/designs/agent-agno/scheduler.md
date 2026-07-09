# 主动巡检调度器（Agno 版）

## 说明

调度器逻辑 LangChain 版和 Agno 版**基本一样**——都使用 APScheduler 定时触发，区别只在于执行单元：

| | LangChain | Agno |
|---|---|---|
| 执行单元 | `patrol_skill.ainvoke(...)` — 调 Skill 子图 | `PatrolWorkflow().run(...)` — 调 Workflow |
| 分析单元 | Skill 内部的 Agent LLM | 一个独立的分析 Agent |

## 巡检频率

| 任务 | 频率 | 内容 |
|---|---|---|
| 健康巡检 | 每 5 分钟 | 摄像头在线状态、GPU、磁盘、队列深度 |
| 告警趋势分析 | 每小时 | 告警密度变化、异常激增检测 |
| 日报 | 每天 9:00 | 前一日汇总 + 推送 |

## 实现

```python
# agent_agno/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from uuid import uuid4
from agno import Agent

class PatrolScheduler:
    """主动巡检调度器（Agno 版）"""

    def __init__(self, patrol_workflow, report_workflow, analysis_agent, notifier):
        self.scheduler = AsyncIOScheduler()
        self.patrol_workflow = patrol_workflow    # PatrolWorkflow 实例
        self.report_workflow = report_workflow    # DailyReportWorkflow 实例
        self.analysis_agent = analysis_agent      # Agent 实例（用于分析巡检结果）
        self.notifier = notifier

    def start(self):
        self.scheduler.add_job(self._run_health_patrol, IntervalTrigger(minutes=5),
                               id="health_patrol", replace_existing=True)
        self.scheduler.add_job(self._run_alert_trend, IntervalTrigger(hours=1),
                               id="alert_trend", replace_existing=True)
        self.scheduler.add_job(self._run_daily_report, CronTrigger(hour=9, minute=0),
                               id="daily_report", replace_existing=True)
        self.scheduler.start()

    def stop(self):
        self.scheduler.shutdown(wait=False)

    async def _run_health_patrol(self):
        """健康巡检"""
        try:
            # 1. Workflow 采集数据
            data = await self.patrol_workflow.run(token=self._get_system_token())

            # 2. Agent 分析数据
            result = await self.analysis_agent.arun(
                f"""请分析以下巡检数据，只报告异常：

{data}

格式要求：
- 🟢 全部正常 → 只输出 "系统正常"
- 🔴 有异常 → 列出每个异常：问题描述 + 严重程度 + 建议操作
""",
            )

            # 3. 有异常 → 推送
            if "异常" in result.content or "🔴" in result.content:
                await self._notify(result.content, "health_patrol")
            else:
                print(f"[Patrol] {datetime.now():%H:%M:%S} 系统正常")

        except Exception as e:
            print(f"[Patrol] 巡检异常: {e}")

    async def _run_alert_trend(self):
        """告警趋势分析"""
        try:
            data = await self.report_workflow.run(token=self._get_system_token())

            result = await self.analysis_agent.arun(
                f"""分析过去 1 小时的告警趋势，检测异常激增：

{data}

判断标准：单路摄像头告警数超过日均值 3 倍 = 异常激增。
只报告有异常的情况，全部正常则输出 "告警趋势正常"。
""",
            )

            if "正常" not in result.content:
                await self._notify(result.content, "alert_spike")

        except Exception as e:
            print(f"[Trend] 趋势分析异常: {e}")

    async def _run_daily_report(self):
        """日报"""
        try:
            data = await self.report_workflow.run(token=self._get_system_token())

            result = await self.analysis_agent.arun(
                f"""生成每日运维报告（Markdown 格式）：

{data}

包括：总告警数、已处理/待处理/误报、Top3 告警摄像头、系统状态、关注事项。
""",
            )

            await self.notifier.send_daily_report(result.content)

        except Exception as e:
            print(f"[Daily] 日报生成异常: {e}")

    def _get_system_token(self) -> str:
        """获取系统级 token（用于定时任务）"""
        ...
```

## 关键差异

LangChain 版巡检用 Skill 内部的 Agent LLM 做分析，Agno 版用了一个独立的 `analysis_agent`：

```python
# LangChain：Skill 子图自带 LLM + ToolNode
result = await patrol_skill.ainvoke({"messages": [...]})

# Agno：Workflow 只采集数据，分析交给 analysis_agent
data = await patrol_workflow.run(token=token)
result = await analysis_agent.arun(f"分析巡检数据：{data}")
```

Agno 的方式更解耦——Workflow 只管"跑流程、采集数据"，Agent 只管"分析数据"。同一个 Agent 既可以用于巡检分析，也可以用于排障、日报。

## 决策流程（与 LangChain 版一致）

```
调度器触发
    ↓
Workflow 采集数据（调 REST API）
    ↓
Agent 分析数据
    ↓
有异常？
  Yes → 推送通知 + 等待人决策
  No  → 只记日志
```
