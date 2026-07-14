# 主动巡检调度器

## 设计原则

**调度器管"什么时候执行"，LangGraph 管"单次执行的流程"。** 两者职责分离：

- APScheduler 负责定时触发（cron / interval）
- LangGraph 负责执行巡检流程（调工具 → 分析 → 输出）
- 巡检结果通过现有 Notifier 推送给人
- 人通过 Web Chat 或钉钉回复来决策

## 巡检频率

| 任务 | 频率 | 内容 |
|---|---|---|
| 健康巡检 | 每 5 分钟 | 摄像头在线状态、GPU、磁盘、队列深度 |
| 告警趋势分析 | 每小时 | 告警密度变化、异常激增检测 |
| 日报 | 每天 9:00 | 前一日汇总 + 推送 |

## 实现

```python
# agent/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from langchain_core.messages import HumanMessage
from datetime import datetime
import json

class PatrolScheduler:
    """
    主动巡检调度器。

    生命周期：
    - init: 创建调度器 + 注册任务
    - start: 启动所有定时任务（在 VisionAgent 启动后调用）
    - stop: 优雅关闭（在 VisionAgent shut down 时调用）
    """

    def __init__(self, patrol_skill, report_skill, notifier):
        self.scheduler = AsyncIOScheduler()
        self.patrol_skill = patrol_skill    # 巡检 skill 子图
        self.report_skill = report_skill    # 日报 skill 子图
        self.notifier = notifier            # WebhookNotifier / EmailNotifier

    def start(self):
        # 每 5 分钟：健康巡检
        self.scheduler.add_job(
            self._run_health_patrol,
            IntervalTrigger(minutes=5),
            id="health_patrol",
            replace_existing=True,
        )

        # 每小时：告警趋势分析
        self.scheduler.add_job(
            self._run_alert_trend,
            IntervalTrigger(hours=1),
            id="alert_trend",
            replace_existing=True,
        )

        # 每天 9:00：日报
        self.scheduler.add_job(
            self._run_daily_report,
            CronTrigger(hour=9, minute=0),
            id="daily_report",
            replace_existing=True,
        )

        self.scheduler.start()

    def stop(self):
        self.scheduler.shutdown(wait=False)

    # ─── 巡检任务 ───

    async def _run_health_patrol(self):
        """健康巡检：发现问题 → 推送通知"""
        thread_id = f"patrol_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        try:
            result = await self.patrol_skill.ainvoke(
                {"messages": [HumanMessage(content="执行健康巡检")]},
                config={"configurable": {"thread_id": thread_id}},
            )

            # 从巡检结果中提取异常
            issues = self._extract_issues(result)

            if issues:
                await self._notify_issues(issues, "health_patrol")
            else:
                # 全部正常，只打日志，不打扰人
                print(f"[Patrol] {datetime.now():%H:%M:%S} 系统正常")

        except Exception as e:
            print(f"[Patrol] 巡检异常: {e}")
            # 巡检本身出问题也推送
            await self.notifier.send_system_alert(
                title="⚠️ 巡检系统异常",
                content=f"巡检线程执行失败：{str(e)}",
            )

    async def _run_alert_trend(self):
        """告警趋势分析：异常激增 → 推送"""
        thread_id = f"trend_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        try:
            result = await self.report_skill.ainvoke(
                {"messages": [HumanMessage(content="分析过去 1 小时告警趋势，检测异常激增")]},
                config={"configurable": {"thread_id": thread_id}},
            )

            spikes = self._extract_spikes(result)
            if spikes:
                await self._notify_issues(spikes, "alert_spike")

        except Exception as e:
            print(f"[Trend] 趋势分析异常: {e}")

    async def _run_daily_report(self):
        """日报：每天 9:00 生成并推送"""
        thread_id = f"daily_{datetime.now().strftime('%Y%m%d')}"

        try:
            result = await self.report_skill.ainvoke(
                {"messages": [HumanMessage(content="生成今日日报")]},
                config={"configurable": {"thread_id": thread_id}},
            )

            # 日报总是推送（即使正常）
            report_text = result["messages"][-1].content
            await self.notifier.send_daily_report(report_text)

        except Exception as e:
            print(f"[Daily] 日报生成异常: {e}")

    # ─── 通知 + 决策 ───

    def _extract_issues(self, result: dict) -> list[dict]:
        """从 Patrol Skill 结果中提取结构化问题列表"""
        last_message = result["messages"][-1].content
        # 解析 Markdown 格式的巡检报告
        # 提取"异常汇总"段落的每一项
        ...
        return issues

    def _extract_spikes(self, result: dict) -> list[dict]:
        """从趋势分析结果中提取异常激增"""
        ...

    async def _notify_issues(self, issues: list[dict], issue_type: str):
        """
        推送巡检异常通知。

        消息格式示例：
        ┌─────────────────────────────────┐
        │ 🔔 巡检发现 2 个异常    14:35    │
        │                                 │
        │ 🔴 cam_02 离线 12 分钟           │
        │    建议：检查网络连接和 RTSP 源   │
        │                                 │
        │ 🟡 cam_03 队列堆积（8/200）      │
        │    建议：检查推理是否阻塞         │
        │                                 │
        │ 回复 [确认] 开始排查              │
        │ 回复 [忽略] 暂不处理              │
        └─────────────────────────────────┘
        """
        for issue in issues:
            issue["decision_token"] = f"patrol_{issue_type}_{uuid4().hex[:8]}"
            # 写入待决策队列
            self._enqueue_decision(issue)

        await self.notifier.send_patrol_alert(issues)

    def _enqueue_decision(self, issue: dict):
        """将问题写入待决策队列，等待人回复"""
        ...

    async def handle_decision(self, decision_token: str, decision: str, reply: str):
        """
        人回复后回调此方法。
        decision: "confirm" | "reject" | "modify"
        reply: 人的附加说明
        """
        issue = self._dequeue_decision(decision_token)
        if not issue:
            return "该决策已过期或不存在"

        if decision == "confirm":
            # 执行建议操作（重启摄像头/调整配置等）
            await self._execute_remediation(issue)
            return f"✅ 已按建议处理：{issue['suggestion']}"

        elif decision == "reject":
            return f"已忽略：{issue['detail']}"

        elif decision == "modify":
            # 按人指定的方式处理
            await self._execute_custom_action(issue, reply)
            return f"✅ 已按指示处理"
```

## 决策流程

```
调度器触发巡检
    ↓
patrol_skill 子图执行
    ↓
发现 issues? ──No──→ 只记录日志，不推送
    │
   Yes
    ↓
创建 DecisionToken → 写入待决策队列
    ↓
通过 Notifier 推送消息到钉钉/企微
    │  "巡检发现 cam_02 离线 12 分钟"
    │  "建议：检查网络连接"
    │  "回复 [确认] 开始排查  [忽略] 暂不处理"
    ↓
等待人回复...
    ↓
人回复 "确认"
    ↓
Agent 收到回调 → 标记已处理
    ↓
如果建议操作可自动执行（如重启摄像头），Agent 执行
如果需要人工操作（如检查物理网络），Agent 记录待办
```

## Agent 应用生命周期

Agent 独立管理自己的生命周期，不耦合 SentinelMind 的 pipeline 回调：

```python
# agent/app.py（Agent 独立入口，不依赖 SentinelMind 的 assemble_components）

class AgentApp:
    """Agent 应用 — 独立于 SentinelMind 运行"""

    def __init__(self, config: dict):
        self.config = config
        self.llm = self._create_llm(config)

        # Agent 自己的存储
        self.checkpointer = create_checkpointer(config["memory"]["db_path"])
        self.user_prefs = UserPreferenceStore(config["memory"]["db_path"])

        # 构建 Agent graph
        self.graph = build_dual_mode_graph(
            llm=self.llm,
            router_llm=ChatOpenAI(model="gpt-4o-mini"),
            professional_tools=load_professional_tools(),
            general_tools=await load_mcp_general_tools(config["mcp_servers"]),
            checkpointer=self.checkpointer,
        )

        # 调度器（可选启动）
        self.scheduler = PatrolScheduler(
            patrol_skill=create_patrol_skill(self.llm),
            report_skill=create_daily_report_skill(self.llm),
            notifier=self._create_notifier(config.get("notification")),
        )

    async def start(self):
        self.scheduler.start()

    async def stop(self):
        self.scheduler.stop()

    def _create_llm(self, config: dict):
        """Agent 自己的 LLM 配置，不依赖 SentinelMind 的 llm/provider.py"""
        provider = config.get("llm", {})
        return ChatOpenAI(
            model=provider.get("model", "gpt-4o"),
            api_key=provider.get("api_key"),
            base_url=provider.get("base_url"),
        )

    def _create_notifier(self, notif_config: dict | None):
        """Agent 自己的通知配置"""
        if not notif_config:
            return None
        # 可对接相同的钉钉/企微 webhook，但配置在 Agent 自己的 config 中
        from agent.adapters.notification import WebhookNotifier
        return WebhookNotifier(notif_config)
```
