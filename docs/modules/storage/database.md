# 数据库存储（Database） — 设计书

## 1. 模块职责

数据库存储模块负责 SentinelMind 的告警数据持久化，包括告警记录的增删改查、事件数据的关联存储，以及告警统计查询。

模块职责包括：

1. **数据库连接管理**：统一管理数据库连接的创建、初始化和关闭。
2. **表结构管理**：启动时自动创建所需的数据库表（若不存在），保证表结构与代码版本一致。
3. **告警 CRUD**：提供告警记录的创建、读取、更新、删除接口。
4. **事件存储**：将规则引擎产出的 Event 数据持久化，与告警记录关联。
5. **分页查询**：支持告警列表的分页、排序和条件筛选。
6. **告警统计**：支持按时间、类型、摄像头维度的聚合统计。
7. **多数据库适配**：第一版使用 SQLite（零部署），后期通过配置切换到 PostgreSQL，接口不变。

设计目标：接口层抽象与实现层分离，SQLite 和 PostgreSQL 共享同一套接口，切换只需修改配置文件中的 storage.type 字段。

---

## 2. 对外接口

### 2.1 DatabaseManager 类

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| __init__ | (config: dict) -> None | — | 初始化，传入 storage 配置段 |
| connect | () -> None | None | 建立数据库连接，创建表结构 |
| close | () -> None | None | 关闭数据库连接 |
| init_tables | () -> None | None | 创建或升级表结构（幂等操作） |
| save_alert | (alert: Alert) -> str | alert_id | 保存告警记录，返回 alert_id |
| get_alert | (alert_id: str) -> Alert 或 None | 告警对象 | 按 ID 获取单条告警 |
| list_alerts | (filters: dict, page: int, page_size: int, sort_by: str, sort_order: str) -> tuple[list[Alert], int] | (告警列表, 总数) | 分页查询告警列表 |
| update_alert | (alert_id: str, updates: dict) -> bool | 是否成功 | 更新告警字段（如状态变更） |
| delete_alert | (alert_id: str) -> bool | 是否成功 | 删除告警记录（软删除） |
| save_event | (event: Event) -> str | event_id | 保存事件记录 |
| get_event | (event_id: str) -> Event 或 None | 事件对象 | 按 ID 获取事件 |
| get_stats | (filters: dict) -> dict | 统计结果字典 | 告警统计查询 |

### 2.2 get_alerts 分页查询参数

| 参数 | 类型 | 说明 |
|------|------|------|
| filters.status | str 或 None | 按状态筛选（pending/acknowledged/rejected/resolved） |
| filters.camera_id | str 或 None | 按摄像头筛选 |
| filters.event_type | str 或 None | 按事件类型筛选 |
| filters.severity | str 或 None | 按严重级别筛选 |
| filters.start_time | float 或 None | 起始时间戳 |
| filters.end_time | float 或 None | 结束时间戳 |
| page | int | 页码（从 1 开始，默认 1） |
| page_size | int | 每页条数（默认 20，最大 100） |
| sort_by | str | 排序字段（默认 "created_at"） |
| sort_order | str | 排序方向（"asc" 或 "desc"，默认 "desc"） |

### 2.3 get_stats 统计查询参数和返回值

| 参数 | 类型 | 说明 |
|------|------|------|
| filters.start_time | float | 起始时间戳（必填） |
| filters.end_time | float | 结束时间戳（必填） |
| filters.camera_id | str 或 None | 按摄像头筛选（可选） |
| filters.group_by | str | 聚合维度（"hour"/"day"/"camera"/"event_type"/"severity"） |

返回值 dict 结构：
- total_count：总告警数
- groups：聚合后的分组数据列表，每项含 group_key 和 count
- by_status：按状态分组的数量统计
- by_severity：按严重级别分组的数量统计

---

## 3. 内部逻辑

### 3.1 数据库连接管理

1. 读取 storage 配置中的 type 字段，确定数据库类型（sqlite 或 postgres）。
2. 根据类型实例化对应的数据库适配器：
   - SQLite：使用 Python 标准库 `sqlite3`，连接到配置的文件路径。
   - PostgreSQL：使用 `psycopg2` 或 `asyncpg`，连接到配置的主机/端口/数据库。
3. 连接参数：
   - SQLite：`check_same_thread=False`（多线程访问），`journal_mode=WAL`（写并发支持）。
   - PostgreSQL：连接池大小 5-20，连接超时 10 秒。
4. 连接失败的处理：
   - SQLite：文件路径目录不存在则自动创建。文件权限不足则报错退出。
   - PostgreSQL：连接失败重试 3 次，间隔 5 秒。仍失败则报错退出。
5. 连接建立后立即执行 `init_tables()`。

### 3.2 表结构设计

#### alerts 表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | TEXT / VARCHAR(36) | PRIMARY KEY | 告警 ID（UUID） |
| event_id | TEXT / VARCHAR(36) | NOT NULL, INDEX | 关联的事件 ID |
| event_type | TEXT / VARCHAR(50) | NOT NULL, INDEX | 事件类型 |
| camera_id | TEXT / VARCHAR(50) | NOT NULL, INDEX | 摄像头 ID |
| camera_name | TEXT / VARCHAR(100) | | 摄像头名称 |
| rule_name | TEXT / VARCHAR(100) | NOT NULL | 触发的规则名称 |
| severity | TEXT / VARCHAR(20) | NOT NULL, INDEX | 严重级别 |
| status | TEXT / VARCHAR(20) | NOT NULL, DEFAULT 'pending', INDEX | 告警状态 |
| snapshot_path | TEXT / VARCHAR(500) | | 截图文件路径 |
| video_clip_path | TEXT / VARCHAR(500) | | 视频片段路径 |
| llm_description | TEXT | | LLM 分析描述 |
| llm_risk_level | TEXT / VARCHAR(20) | | LLM 风险等级 |
| llm_suggestion | TEXT | | LLM 建议措施 |
| llm_raw_response | TEXT | | LLM 原始返回 |
| metadata | TEXT (JSON) | | 规则附带的额外信息（JSON 序列化） |
| detections_snapshot | TEXT (JSON) | | 触发时的检测结果快照（JSON 序列化） |
| notified_channels | TEXT (JSON) | | 已通知的渠道列表（JSON 序列化） |
| created_at | REAL / TIMESTAMP | NOT NULL, INDEX | 创建时间（Unix 秒） |
| acknowledged_at | REAL / TIMESTAMP | | 确认时间 |
| acknowledged_by | TEXT / VARCHAR(100) | | 确认人 |
| resolved_at | REAL / TIMESTAMP | | 解决时间 |
| is_archived | INTEGER / BOOLEAN | DEFAULT 0 | 是否已归档 |

#### events 表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | TEXT / VARCHAR(36) | PRIMARY KEY | 事件 ID（UUID） |
| event_type | TEXT / VARCHAR(50) | NOT NULL, INDEX | 事件类型 |
| camera_id | TEXT / VARCHAR(50) | NOT NULL, INDEX | 摄像头 ID |
| camera_name | TEXT / VARCHAR(100) | | 摄像头名称 |
| rule_name | TEXT / VARCHAR(100) | NOT NULL | 规则名称 |
| severity | TEXT / VARCHAR(20) | NOT NULL | 严重级别 |
| metadata | TEXT (JSON) | | 额外信息 |
| detections_snapshot | TEXT (JSON) | | 检测结果快照 |
| created_at | REAL / TIMESTAMP | NOT NULL, INDEX | 创建时间 |

#### 表关系

events 表和 alerts 表通过 event_id 字段关联。一个 Event 可能对应 0 个或多个 Alert（被过滤的 Event 没有 Alert，多渠道通知可能产生多个 Alert）。

#### 索引设计

| 表 | 索引 | 字段 | 说明 |
|------|------|------|------|
| alerts | idx_alerts_created_at | created_at | 按时间排序查询 |
| alerts | idx_alerts_camera_status | camera_id, status | 按摄像头和状态筛选 |
| alerts | idx_alerts_event_type | event_type | 按事件类型筛选 |
| alerts | idx_alerts_severity | severity | 按严重级别筛选 |
| events | idx_events_created_at | created_at | 按时间排序查询 |
| events | idx_events_camera | camera_id | 按摄像头筛选 |

### 3.3 告警保存流程

1. 接收 Alert 对象。
2. 将 Alert 序列化为字典，列表和字典类型的字段序列化为 JSON 字符串。
3. 构造 INSERT SQL 语句。
4. 同时将关联的 Event 数据写入 events 表（若尚未存在）。
5. 执行 INSERT，返回 alert_id。
6. 若主键冲突（理论上 UUID 不会冲突），记录 WARNING 并跳过。

### 3.4 分页查询流程

1. 构造 WHERE 子句，根据 filters 参数动态拼接条件。
2. 执行 COUNT 查询获取满足条件的总记录数。
3. 计算 offset = (page - 1) * page_size。
4. 执行 SELECT 查询，按 sort_by 和 sort_order 排序，LIMIT page_size OFFSET offset。
5. 将查询结果逐行转换为 Alert 对象（JSON 字段反序列化）。
6. 返回 (alerts_list, total_count) 元组。
7. 参数校验：page_size 超过 100 自动截断为 100；page < 1 自动修正为 1。

### 3.5 告警统计查询流程

1. 根据 filters.group_by 参数确定聚合方式：
   - "hour"：按小时聚合，返回 24 个时段的告警数量。
   - "day"：按天聚合，返回指定日期范围每天的告警数量。
   - "camera"：按摄像头聚合，返回每个摄像头的告警数量。
   - "event_type"：按事件类型聚合，返回每种类型的告警数量。
   - "severity"：按严重级别聚合，返回每个级别的告警数量。
2. 构造 GROUP BY SQL 查询。
3. 同时执行按 status 和 severity 的分组统计。
4. 返回聚合结果字典。

### 3.6 SQLite 与 PostgreSQL 适配

使用抽象基类（DatabaseBackend）定义接口，SQLite 和 PostgreSQL 分别实现：

| 操作 | SQLite 实现 | PostgreSQL 实现 |
|------|------------|----------------|
| 连接 | sqlite3.connect() | psycopg2.connect() |
| 自增 ID | TEXT PRIMARY KEY（UUID） | VARCHAR(36) PRIMARY KEY |
| JSON 存储 | TEXT 字段 + 序列化 | JSONB 字段（原生支持） |
| 分页 | LIMIT + OFFSET | LIMIT + OFFSET |
| 事务 | 自动提交模式 | 显式 BEGIN/COMMIT |
| 并发 | WAL 模式支持读写并发 | 原生支持 |
| UPSERT | INSERT OR REPLACE | INSERT ... ON CONFLICT DO UPDATE |

切换方式：配置 storage.type 从 "sqlite" 改为 "postgres"，补充 postgres 连接参数，重启进程即可。

---

## 4. 依赖关系

| 依赖模块 | 依赖方向 | 说明 |
|----------|----------|------|
| config | 数据库 → 配置管理 | 读取 storage 配置段（类型、路径、连接参数） |
| core/types | 数据库 → 数据类型 | 使用 Alert、Event 等数据模型的 to_dict/from_dict |
| sqlite3（标准库） | 数据库 → 系统库 | SQLite 驱动（第一版） |
| psycopg2（第三方） | 数据库 → 第三方库 | PostgreSQL 驱动（后期，可选依赖） |
| web/api | Web → 数据库 | Web 层通过 DatabaseManager 查询告警数据 |
| core/pipeline | Pipeline → 数据库 | Pipeline 通过 DatabaseManager 保存告警 |

---

## 5. 配置项

### 5.1 storage 配置段

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| type | str | "sqlite" | 数据库类型，"sqlite" 或 "postgres" |
| sqlite.path | str | "data/sentinelmind.db" | SQLite 文件路径 |
| postgres.host | str | "localhost" | PostgreSQL 主机地址 |
| postgres.port | int | 5432 | PostgreSQL 端口 |
| postgres.database | str | "sentinelmind" | 数据库名 |
| postgres.username | str | "postgres" | 用户名 |
| postgres.password | str | ${DB_PASS} | 密码（环境变量） |
| postgres.pool_min | int | 5 | 连接池最小连接数 |
| postgres.pool_max | int | 20 | 连接池最大连接数 |
| postgres.timeout | int | 10 | 连接超时秒数 |

### 5.2 数据清理配置（system 配置段中）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| cleanup.alert_archive_days | int | 90 | 超过此天数的告警标记为 archived |
| cleanup.snapshot_retain_days | int | 30 | 截图保留天数 |
| cleanup.clip_retain_days | int | 7 | 视频片段保留天数 |
| cleanup.interval_hours | int | 1 | 清理任务执行间隔（小时） |

---

## 6. 错误处理

### 6.1 连接阶段错误

| 错误场景 | 处理方式 | 是否阻断启动 |
|----------|----------|-------------|
| SQLite 文件路径目录不存在 | 自动创建目录 | 否 |
| SQLite 文件权限不足 | 报错退出 | 是 |
| PostgreSQL 连接失败 | 重试 3 次，间隔 5 秒 | 是（重试后仍失败） |
| PostgreSQL 数据库不存在 | 报错退出，提示创建数据库 | 是 |
| 认证失败 | 报错退出 | 是 |

### 6.2 运行阶段错误

| 错误场景 | 处理方式 |
|----------|----------|
| SQL 执行异常 | 捕获异常，记录 ERROR 日志，向上层返回 None 或 False |
| 数据库连接断开 | 自动重连（SQLite 文件不会断开，PostgreSQL 连接池自动管理） |
| 事务冲突 | SQLite WAL 模式自动重试，PostgreSQL 回滚并重试 |
| 磁盘空间不足 | 记录 CRITICAL 日志，告警通知管理员 |
| 数据格式异常（JSON 反序列化失败） | 记录 WARNING，返回部分数据（该字段设为 None） |

### 6.3 数据完整性保障

- 告警 ID 使用 UUID4，全局唯一，理论上不会冲突。
- 保存告警时使用事务，确保 alerts 表和 events 表的数据一致性。
- SQLite 使用 WAL 模式，支持读写并发，避免写操作阻塞读操作。
- 软删除（is_archived 标记）而非硬删除，保留历史数据。

---

## 7. 设计决策

### 7.1 为什么第一版用 SQLite 而非直接用 PostgreSQL

SQLite 的核心优势是零部署成本：不需要安装数据库服务、不需要配置用户权限、不需要管理连接池。一个文件就是一个数据库，`pip install` 后即可运行。对于第一版（单机部署、10 路摄像头以内），SQLite 的性能完全足够（WAL 模式下读写并发无问题）。降低部署门槛是让项目快速落地的关键。

### 7.2 为什么用接口抽象而非直接写 SQLite 代码

如果第一版直接写死 SQLite 的 SQL 和 API，后期切 PostgreSQL 时需要大量重写。通过 DatabaseBackend 抽象接口，第一版实现 SQLite，后期加一个 PostgreSQL 实现类，只改配置不改业务代码。前期多花一点抽象成本，后期省大量迁移成本。

### 7.3 为什么用软删除而非硬删除

告警数据是安全事件的证据链，直接删除会导致：无法统计误报率（删除后无法回溯）、无法进行事后审计、无法用历史数据优化规则阈值。软删除（is_archived 标记）保留了所有数据，只是在查询时默认过滤已归档数据。需要查看历史数据时，可以查询归档记录。

### 7.4 为什么 JSON 字段用 TEXT 存储而非 JSONB

SQLite 不支持 JSONB 类型，用 TEXT 存储 JSON 字符串是 SQLite 的标准做法。PostgreSQL 支持 JSONB，切换后自动获得 JSONB 的查询优势（索引、路径查询）。在接口层统一序列化/反序列化，对业务代码透明。

### 7.5 为什么告警和事件分两张表

一个 Event 可能不产生 Alert（被三层防线过滤），一个 Event 也可能产生多个 Alert（通知到不同渠道）。如果合并为一张表，无法准确表达这种一对多关系。分表后：events 表记录"发生了什么"（规则引擎的原始产出），alerts 表记录"需要处理什么"（经过过滤后的实际告警）。统计分析时可以分别查询原始事件数和实际告警数，计算过滤率。

### 7.6 为什么统计查询用 GROUP BY 而非物化视图

告警数据的写入频率不高（每天几十到几百条），统计查询的性能压力很小，直接 GROUP BY 足够。物化视图增加了维护成本（需要刷新策略），对于当前数据量没有收益。当告警数据增长到百万级别时（第三版），再考虑物化视图或专用的统计表。
