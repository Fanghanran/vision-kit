# State 与记忆设计

## AgentState

LangGraph 的 State 是所有记忆持久化的核心载体。每次对话的完整状态通过 SqliteSaver 自动写入 SQLite。

```python
# agent/core/state.py
from typing import TypedDict, Annotated, Sequence
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """Agent 状态 — 每个 thread 独立持久化"""

    # ─── LangGraph 托管 ───
    # add_messages 注解：自动追加消息 + 去重（按 message ID）
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ─── 模式信息 ───
    mode: str                        # "general" | "professional"
    mode_locked: bool                # 用户手动 /general 或 /professional 后锁定

    # ─── 系统上下文（专业模式自动注入）───
    system_snapshot: dict | None     # 摄像头列表、告警数、GPU 状态
    last_snapshot_time: float | None # 快照刷新时间，避免每轮都查 API

    # ─── Skill 激活 ───
    active_skill: str | None         # 当前激活的 skill 标识
    skill_params: dict | None        # skill 参数

    # ─── 巡检状态 ───
    patrol_issues: list[dict]        # 巡检发现的问题列表
    patrol_awaiting_decision: bool   # 是否在等待人决策

    # ─── 元信息 ───
    user_id: str | None
    user_role: str | None            # admin / operator / viewer
    started_at: float | None         # session 开始时间戳
```

## 四层记忆体系

```
第1层 ─ 上下文窗口（模型自带）
  messages 列表，受模型 context window 限制。
  LangGraph 自动管理消息追加和裁剪。

第2层 ─ Buffer Window（短期）
  保留最近 K 轮完整对话。超出的交给第 3 层。
  ── ConversationBufferWindowMemory(k=20)

第3层 ─ Summary（中期压缩）
  超过 Buffer 的旧对话 → LLM 自动压缩为 300 token 摘要
  → 注入新对话开头作为"前情提要"
  ── ConversationSummaryMemory(llm, max_token_limit=300)

第4层 ─ SqliteSaver Checkpoint（长期持久化）
  每个 thread_id 的完整 AgentState 写 SQLite。
  包括：所有历史消息、系统快照、用户偏好、巡检结论
  ── LangGraph 内置，配置即用
```

## 各层实现

### 第 1 层：上下文窗口

LangGraph 自动管理，无需额外代码。Agent 节点的 `messages` 列表自动维护在 State 中。

### 第 2 层：Buffer Window

```python
# agent/memory/buffer.py
from langchain.memory import ConversationBufferWindowMemory

def create_buffer_memory() -> ConversationBufferWindowMemory:
    return ConversationBufferWindowMemory(
        k=20,                     # 保留最近 20 轮（一轮 = human + AI + tool_calls）
        return_messages=True,     # 返回 Message 对象，不是字符串
        memory_key="chat_history",# 注入 prompt 时使用的变量名
        input_key="input",
        output_key="output",
    )
```

### 第 3 层：Summary

```python
# agent/memory/summary.py
from langchain.memory import ConversationSummaryMemory

def create_summary_memory(llm) -> ConversationSummaryMemory:
    """
    用便宜的模型做摘要。超过 300 token 的旧对话自动压缩。
    摘要注入到新对话开头，让模型知道"之前发生了什么"。
    """
    return ConversationSummaryMemory(
        llm=ChatOpenAI(model="gpt-4o-mini"),  # 摘要不需要高智能
        max_token_limit=300,                   # 触发摘要的阈值
        memory_key="conversation_summary",
    )
```

### 第 4 层：SqliteSaver Checkpoint

```python
# agent/memory/checkpoint.py
from langgraph.checkpoint.sqlite import SqliteSaver

def create_checkpointer(db_path: str = "data/agent_memory.db") -> SqliteSaver:
    """
    LangGraph 的 checkpointer 自动在每次 super-step 后
    将 AgentState 序列化到 SQLite。

    特性：
    - 每个 thread_id 独立存储
    - 对话中断后恢复，无缝衔接
    - 支持回溯到历史任一步骤
    - 一个 SQLite 文件存所有 thread，无需额外管理
    """
    return SqliteSaver.from_conn_string(db_path)
```

## 三层组合：CompositeMemory

LangChain 没有内置的 CompositeMemory，需要自己组合：

```python
# agent/memory/__init__.py
from langchain.memory import ConversationBufferWindowMemory, ConversationSummaryMemory

class CompositeMemory:
    """
    组合记忆策略：
    1. Summary → 最近 300 token 摘要（覆盖 Buffer 之外的旧对话）
    2. Buffer → 最近 20 轮完整对话
    3. 两者同时注入 prompt 构建

    这样模型既能"看到最近的细节"（Buffer），
    也能"了解更早的背景"（Summary）。
    """

    def __init__(self, llm):
        self.summary = ConversationSummaryMemory(
            llm=ChatOpenAI(model="gpt-4o-mini"),
            max_token_limit=300,
            memory_key="conversation_summary",
        )
        self.buffer = ConversationBufferWindowMemory(
            k=20,
            return_messages=True,
            memory_key="chat_history",
        )

    async def load(self, inputs: dict) -> dict:
        buf = await self.buffer.aload_memory_variables(inputs)
        smr = await self.summary.aload_memory_variables(inputs)
        return {
            "history": buf.get("chat_history", []),
            "summary": smr.get("conversation_summary", ""),
        }

    async def save(self, inputs: dict, outputs: dict):
        await self.buffer.asave_context(inputs, outputs)
        await self.summary.asave_context(inputs, outputs)

    def clear(self):
        self.buffer.clear()
        self.summary.clear()
```

## 长期记忆：用户偏好

```python
# agent/memory/long_term.py
import sqlite3
import json
import time

class UserPreferenceStore:
    """
    跨 session 的用户偏好存储。
    独立于对话 Cycle 的 Buffer/Summary/Checkpoint。

    用途：
    - 用户关注的摄像头列表
    - 偏好通知方式（钉钉/企微/邮件）
    - 常用巡检参数
    """

    def __init__(self, db_path: str = "data/agent_memory.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, key)
            )
        """)
        self.conn.commit()

    def get(self, user_id: str, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM user_prefs WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        return row[0] if row else None

    def set(self, user_id: str, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO user_prefs (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, key, value, time.time()),
        )
        self.conn.commit()

    def get_all(self, user_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT key, value FROM user_prefs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def delete(self, user_id: str, key: str):
        self.conn.execute(
            "DELETE FROM user_prefs WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        self.conn.commit()
```

## 长期记忆：历史告警规律

```python
# agent/memory/long_term.py
import httpx

class AlertHistoryPattern:
    """
    通过 REST API 查询 Vision Agent 的历史告警，在 Agent 本地做聚合分析。

    不做额外存储——每次查询实时计算。结果可缓存到 Agent 自己的 memory 中。

    典型查询：
    - 某摄像头 30 天内告警高峰时段
    - 常见误报类型（被标记为 rejected 的规律）
    - 告警密度趋势（用于日报的同比/环比）
    """

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url

    async def get_hotspot(self, token: str, camera_id: str, days: int = 30) -> dict:
        """通过 API 查询，在本地按小时/类型聚合"""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.get("/api/alerts", headers=headers, params={
                "camera_id": camera_id,
                "limit": 1000,
            })
            alerts = resp.json().get("items", [])

        # Agent 本地聚合分析
        by_hour = {}
        by_type = {}
        for a in alerts:
            hour = a["created_at"][:13]  # 按小时分组
            by_hour[hour] = by_hour.get(hour, 0) + 1
            t = a.get("event_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total": len(alerts),
            "by_hour": by_hour,
            "by_type": by_type,
        }

    async def get_daily_pattern(self, token: str, camera_id: str) -> dict:
        """获取该摄像头的日常告警基线（用于判断异常激增）"""
        ...
```

## Prompt 注入策略

```
最终发送给 LLM 的消息顺序：

[System Prompt（通用/专业模式）]
[System Snapshot（仅专业模式）]
    "当前系统状态：
     - 摄像头：cam_01 在线, cam_02 离线 12 分钟, cam_03 在线
     - 今日告警：7 条（2 条待处理）
     - GPU 利用率：34%"
[Conversation Summary]          ← 摘要记忆
    "之前的对话摘要：用户询问了 cam_02 的告警问题..."
[Chat History (最近 20 轮)]     ← Buffer 记忆
    Human: 帮我看看 cam_02
    AI: cam_02 当前离线...
[用户最新消息]                   ← 本次输入
```
