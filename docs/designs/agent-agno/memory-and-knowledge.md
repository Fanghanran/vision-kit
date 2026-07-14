# 记忆与知识库（Agno 版）

## LangChain vs Agno 记忆对比

| | LangChain | Agno |
|---|---|---|
| 短期记忆 | `ConversationBufferWindowMemory(k=20)` | `add_history_to_context=True` |
| 中期记忆 | `ConversationSummaryMemory(max_token_limit=300)` | Agno 内部自动处理 |
| 长期记忆 | `SqliteSaver` + `UserPreferenceStore` 手动实现 | `AgentMemory(db=..., create_user_memories=True)` |
| 用户偏好 | 手动建表 `user_prefs` + CRUD | `create_user_memories=True` 自动提取 |
| 知识库 RAG | 手动搭 `VectorStoreRetriever` + chain | `KnowledgeBase(vector_db=ChromaDb(...))` |

**Agno 把四层压缩成了一行配置。**

## AgentMemory — 开箱即用

```python
# agent_agno/memory/__init__.py
from agno.memory import AgentMemory
from agno.storage.sqlite import SqliteStorage

def create_agent_memory(db_path: str = "data/agent_memory.db") -> AgentMemory:
    """
    一个对象，覆盖所有记忆需求。

    框架内部自动做的事：
    - 对话历史追加（短期）
    - 超长对话自动摘要（中期）
    - 会话状态持久化到 SQLite（长期）
    - 用户偏好自动提取："用户总是关注 cam_02"（长期）

    不需要手动组合 Buffer + Summary + Checkpoint + UserPrefs。
    """
    return AgentMemory(
        db=SqliteStorage(
            table_name="agent_memory",
            db_file=db_path,
        ),

        # 自动提取用户偏好，跨 session 记住
        create_user_memories=True,
        # 每次对话结束后更新偏好
        update_user_memories_after_run=True,

        # 记忆摘要配置
        # Agno 内部会在对话超过阈值时自动压缩
    )
```

## 构建 Agent 时挂载记忆

```python
from agno import Agent

agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[...],
    system_prompt="你是 SentinelMind 运维专家。",

    # ─── 记忆：一行 ───
    memory=create_agent_memory(),

    # ─── 历史对话自动注入 ───
    add_history_to_context=True,  # 最近对话自动加入 prompt
    num_history_runs=10,          # 保留最近 10 轮

    # ─── 会话持久化 ───
    # Agno 通过 session_id 自动区分不同对话
    # 同一个 session_id 的对话自动串联
)
```

## 知识库 — RAG 开箱自带

```python
# agent_agno/memory/knowledge.py
from agno.knowledge import KnowledgeBase
from agno.vectordb.chroma import ChromaDb

def create_knowledge_base() -> KnowledgeBase:
    """
    Agno 的知识库自动：
    - 加载文档目录（Markdown/PDF/文本）
    - 分块（chunk）
    - 向量化（embedding）
    - 检索（retrieve）
    - 注入 Agent 上下文

    在 Agent 中配置后，Agent 会自动在需要时检索相关知识。
    """
    return KnowledgeBase(
        vector_db=ChromaDb(
            collection="sentinelmind_knowledge",
            path="data/vector_db",
        ),
        # 可选：预加载文档目录
        # path="data/knowledge/",
    )

# 挂载到 Agent
agent = Agent(
    ...,
    knowledge=create_knowledge_base(),
    # Agent 自动搜索知识库，把相关文档注入对话上下文
    # 用户问"怎么配置 RTSP 摄像头"，Agent 自动从文档中查
    search_knowledge=True,  # 每次对话自动检索
)
```

## 用户偏好 — 自动提取

Agno 的 `create_user_memories=True` 会自动从对话中提取用户偏好：

```
用户多次问 cam_02 的状态
    ↓
Agno 自动记住：
    "用户 fanghr 频繁关注 cam_02（仓库摄像头）的状态，
     偏好钉钉接收告警通知，习惯在下午巡检时段查看系统"
    ↓
下次对话时自动注入到 prompt 中
```

不需要像 LangChain 版那样手动建 `user_prefs` 表、手动写 CRUD。

## 对话示例

```
=== 第一次对话 ===
用户：帮我看看 cam_02 的状态
Agent：cam_02 当前在线，25fps，今日无告警。

用户：如果它掉线了，帮我在钉钉上通知我
Agent：好的，我会在 cam_02 掉线时通过钉钉通知您。

=== 第二次对话（两天后） ===
用户：巡检一下
Agent：执行系统巡检...

      🟢 cam_01 (正门): online
      🔴 cam_02 (仓库): offline —— ⚠️ 已自动向您的钉钉推送通知
      🟢 cam_03 (后门): online
      ...

      （Agno 自动记住了用户偏好：关注 cam_02，钉钉通知）
```
