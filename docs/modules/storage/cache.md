# 缓存层（Cache） — 设计书

## 1. 模块职责

缓存层为 SentinelMind 提供高性能的键值对缓存服务，是规则引擎三层防线、摄像头状态管理、LLM 响应缓存等场景的基础设施。

模块职责包括：

1. **缓存接口抽象**：定义统一的 CacheProtocol 接口，屏蔽底层实现差异。
2. **内存缓存实现**：基于 Python dict + TTL 的内存缓存，作为默认实现和 Redis 不可用时的降级方案。
3. **Redis 缓存实现**：可选的 Redis 缓存实现，支持分布式部署和跨进程共享。
4. **自动降级**：Redis 不可用时自动切换到内存缓存，保证系统持续运行。
5. **TTL 管理**：支持缓存条目的过期自动清理，避免内存无限增长。
6. **用途支撑**：为规则引擎的滑动窗口计数、冷却时间记录、摄像头运行状态、LLM 响应缓存等场景提供统一的缓存访问接口。

设计原则：Redis 是可选增强，不是硬依赖。没有 Redis，系统照样运行，只是缓存不支持跨进程共享。

---

## 2. 对外接口

### 2.1 CacheProtocol（缓存协议接口）

所有缓存实现必须实现此 Protocol。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| get | (key: str) -> Any 或 None | 缓存值或 None | 获取缓存值，不存在或已过期返回 None |
| set | (key: str, value: Any, ttl: int 或 None = None) -> None | None | 设置缓存值，ttl 为过期秒数（None 表示永不过期） |
| delete | (key: str) -> bool | 是否存在并删除 | 删除缓存条目 |
| exists | (key: str) -> bool | 是否存在 | 检查缓存条目是否存在（未过期） |
| increment | (key: str, amount: int = 1) -> int | 递增后的值 | 原子递增操作（用于计数器） |
| clear | (prefix: str 或 None = None) -> int | 清除的条目数 | 清除指定前缀或全部缓存条目 |
| keys | (pattern: str) -> list[str] | 匹配的 key 列表 | 按模式匹配查询 key（调试用） |
| size | () -> int | 条目数量 | 返回当前缓存中的有效条目数量 |

### 2.2 MemoryCache 类

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| __init__ | (max_size: int = 10000, cleanup_interval: int = 60) -> None | — | 初始化内存缓存，设置最大条目数和清理间隔 |
| get | (key: str) -> Any 或 None | 缓存值 | 获取值，过期条目在访问时惰性删除 |
| set | (key: str, value: Any, ttl: int 或 None = None) -> None | None | 存储值到内存 dict |
| delete | (key: str) -> bool | 是否存在并删除 | 从内存 dict 删除 |
| exists | (key: str) -> bool | 是否存在 | 检查 key 存在且未过期 |
| increment | (key: str, amount: int = 1) -> int | 递增后的值 | 原子递增（单线程 GIL 保证） |
| clear | (prefix: str 或 None = None) -> int | 清除数量 | 按前缀或全部清除 |
| keys | (pattern: str) -> list[str] | 匹配的 key 列表 | 通配符匹配查询 |
| size | () -> int | 有效条目数 | 返回未过期的条目数 |
| _cleanup_expired | () -> int | 清理数量 | 后台清理过期条目（内部方法） |

### 2.3 RedisCache 类

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| __init__ | (config: dict) -> None | — | 初始化 Redis 连接，传入 redis 配置段 |
| get | (key: str) -> Any 或 None | 缓存值 | 从 Redis GET，自动反序列化 |
| set | (key: str, value: Any, ttl: int 或 None = None) -> None | None | SET 到 Redis，自动序列化，支持 EX 参数 |
| delete | (key: str) -> bool | 是否存在 | DEL 操作 |
| exists | (key: str) -> bool | 是否存在 | EXISTS 操作 |
| increment | (key: str, amount: int = 1) -> int | 递增后的值 | INCRBY 操作 |
| clear | (prefix: str 或 None = None) -> int | 清除数量 | SCAN + DEL 按前缀删除 |
| keys | (pattern: str) -> list[str] | 匹配列表 | SCAN 按模式查询 |
| size | () -> int | 条目数 | DBSIZE 操作 |
| ping | () -> bool | 是否可达 | 连接健康检查 |
| close | () -> None | None | 关闭 Redis 连接 |

### 2.4 CacheFactory 工厂函数

| 函数 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| create_cache | (config: dict) -> CacheProtocol | 缓存实例 | 根据配置创建缓存实例，Redis 不可用时自动降级为内存缓存 |

---

## 3. 内部逻辑

### 3.1 缓存实例创建流程（create_cache）

1. 读取配置中的 redis.enabled 字段。
2. 若 enabled 为 false：
   - 直接创建 MemoryCache 实例并返回。
   - 记录 INFO 日志："缓存模式：内存缓存（Redis 未启用）"。
3. 若 enabled 为 true：
   - 尝试创建 RedisCache 实例。
   - 执行 ping() 测试连接。
   - 若连接成功：返回 RedisCache 实例。记录 INFO 日志。
   - 若连接失败：记录 WARNING 日志，自动降级为 MemoryCache。
   - 启动后台重连线程，每 30 秒尝试重连 Redis。
   - 重连成功后：不自动切换回 Redis（运行时切换可能导致缓存状态不一致），记录 INFO 日志提示重启进程可启用 Redis。
4. 返回最终的缓存实例。

### 3.2 MemoryCache 内部实现

**数据结构**：

使用 Python dict 存储缓存数据，每个条目为一个元组 (value, expire_at)：
- value：任意可序列化的 Python 对象。
- expire_at：过期时间戳（Unix 秒），None 表示永不过期。

**惰性过期清理**：

- get() 和 exists() 方法在访问条目时检查 expire_at。
- 若当前时间 > expire_at，删除该条目，返回 None/False。
- 不会在 get 时批量扫描所有条目，开销极小。

**定期过期清理**：

- 启动一个后台清理线程（或使用定时器），每隔 cleanup_interval 秒（默认 60 秒）执行一次全量扫描。
- 遍历所有条目，删除已过期的条目。
- 记录清理的条目数量（用于监控）。

**最大容量限制**：

- 设置 max_size（默认 10000），当有效条目数超过此限制时触发清理。
- 清理策略：先删除所有已过期条目。若仍超限，按 LRU（最近最少使用）策略删除最旧的条目。
- LRU 实现：维护一个 OrderedDict，每次 get/set 操作将条目移到末尾，淘汰时从头部删除。

**线程安全**：

- Python GIL 保证了 dict 操作的原子性，单线程内的 get/set 操作不会出现竞态。
- increment() 操作的读取-递增-写入序列在 GIL 保护下是原子的。
- 后台清理线程的全量扫描与主操作之间可能存在竞态，但由于清理只是删除已过期条目，最坏情况是多访问一次已过期的数据（返回 None），不影响正确性。

### 3.3 RedisCache 内部实现

**连接管理**：

- 使用 `redis-py` 库的 `Redis` 或 `ConnectionPool`。
- 配置连接池参数：max_connections（默认 20）、socket_timeout（默认 5 秒）、socket_connect_timeout（默认 5 秒）、retry_on_timeout（默认 true）。
- 启用 TCP keepalive，检测连接断开。

**序列化**：

- set() 时将 value 序列化为 JSON 字符串后存储。
- get() 时将 JSON 字符串反序列化为 Python 对象。
- 使用标准库 json 模块，不引入额外的序列化库。
- 对于简单的字符串和数字值，跳过 JSON 序列化，直接存储。

**Key 命名规范**：

- 所有 key 统一添加前缀 `va:`（sentinelmind 缩写），避免与其他 Redis 使用者冲突。
- 示例：`va:rule:intrusion:camera:cam_01:sliding_window`

**错误处理**：

- 所有 Redis 操作包装在 try-except 中。
- 捕获 `redis.ConnectionError`、`redis.TimeoutError`、`redis.RedisError`。
- 操作失败时记录 WARNING 日志，返回 None/False（与 key 不存在的行为一致）。
- 连续失败计数器：超过 3 次连续失败，触发降级通知。

**健康检查**：

- 提供 ping() 方法，执行 Redis PING 命令。
- 调用方可通过 ping() 检查连接状态。

### 3.4 自动降级机制

1. 系统启动时，CacheFactory 尝试连接 Redis。
2. 若连接失败，自动创建 MemoryCache 实例。
3. 系统运行过程中，Redis 连接断开：
   - RedisCache 的每次操作失败时记录错误。
   - 上层模块感知到 get 返回 None 频率异常升高。
   - 系统不主动切换到 MemoryCache（运行时切换会丢失 Redis 中的缓存状态，导致滑动窗口和冷却计时重置，可能产生误报）。
4. 后台重连线程持续尝试恢复 Redis 连接。
5. 日志中记录降级事件，通知运维关注。

### 3.5 缓存用途说明

| 用途 | Key 格式 | TTL | 说明 |
|------|----------|-----|------|
| 滑动窗口计数 | `rule:{name}:camera:{id}:window` | 无（随规则 reset 清除） | 记录连续触发帧数 |
| 冷却时间 | `rule:{name}:camera:{id}:cooldown` | 等于冷却配置值 | 记录上次放行时间戳 |
| 闯入首次进入时间 | `intrusion:{camera_id}:track:{track_id}` | 等于 persist 配置值 | 记录首次进入区域时间 |
| 遗留物静止时间 | `abandoned:{camera_id}:track:{track_id}:stationary_since` | 等于 duration 配置值 | 记录首次静止时间 |
| 计数器累计 | `counting:{camera_id}:counter:{direction}` | 按 reset_interval | 正/负方向累计计数 |
| 已计数 Track 集合 | `counting:{camera_id}:counted_tracks` | 60 秒 | 防止重复计数 |
| 摄像头状态 | `camera:{id}:state` | 无 | JSON 序列化的 CameraState |
| LLM 响应缓存 | `llm:response:{hash}` | 3600 秒（1 小时） | 相同输入的 LLM 响应缓存 |
| 规则文件 mtime | `config:rules:mtime:{filename}` | 无 | 用于热重载判断 |

---

## 4. 依赖关系

| 依赖模块 | 依赖方向 | 说明 |
|----------|----------|------|
| config | 缓存 → 配置管理 | 读取 redis 配置段（enabled、host、port、password） |
| redis（第三方库） | 缓存 → 第三方库 | Redis 驱动（可选依赖，redis-enabled 时需要） |
| rules/engine | 规则引擎 → 缓存 | 三层防线的状态存储 |
| rules/builtin | 内置规则 → 缓存 | 闯入、遗留物等规则的内部状态 |
| core/pipeline | Pipeline → 缓存 | 摄像头状态缓存 |
| llm/analyzer | LLM → 缓存 | LLM 响应缓存 |

缓存层不依赖任何业务模块，是纯基础设施层。

---

## 5. 配置项

### 5.1 redis 配置段（settings.yaml）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| enabled | bool | false | 是否启用 Redis 缓存 |
| host | str | "localhost" | Redis 主机地址 |
| port | int | 6379 | Redis 端口 |
| password | str | None | Redis 密码（${REDIS_PASS}），可选 |
| db | int | 0 | Redis 数据库编号 |
| key_prefix | str | "va:" | 所有 key 的统一前缀 |
| socket_timeout | int | 5 | Socket 超时秒数 |
| max_connections | int | 20 | 连接池最大连接数 |

### 5.2 内存缓存配置（隐式，由系统内部管理）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| max_size | 10000 | 最大缓存条目数 |
| cleanup_interval | 60 | 过期清理间隔（秒） |

内存缓存的参数不暴露在配置文件中，由系统内部固定。原因：内存缓存是降级方案，用户不需要关心其参数；且在典型使用场景下（5-10 路摄像头），缓存条目数远小于 10000。

---

## 6. 错误处理

### 6.1 连接阶段错误

| 错误场景 | 处理方式 | 是否阻断启动 |
|----------|----------|-------------|
| Redis 连接超时 | 记录 WARNING，降级为内存缓存 | 否 |
| Redis 认证失败 | 记录 ERROR，降级为内存缓存 | 否 |
| Redis 服务不可用 | 记录 WARNING，降级为内存缓存 | 否 |
| redis-py 库未安装 | 记录 WARNING，降级为内存缓存 | 否 |

### 6.2 运行阶段错误

| 错误场景 | 处理方式 |
|----------|----------|
| Redis 操作超时 | 记录 WARNING，返回 None/False |
| Redis 连接断开 | 自动重连（redis-py 内置重连机制） |
| JSON 序列化失败 | 记录 ERROR，返回 None |
| JSON 反序列化失败 | 记录 WARNING，删除该条目，返回 None |
| 内存缓存超过 max_size | 触发 LRU 淘汰，记录 INFO |
| 内存缓存清理线程异常退出 | 记录 ERROR，尝试重启清理线程 |

### 6.3 数据一致性

- 缓存是易失性存储，系统重启后缓存丢失是预期行为。
- 规则引擎的滑动窗口计数器在重启后重新从 0 开始，需要重新积累帧数才能触发告警（最多延迟 5 帧，约 1 秒）。
- 冷却计时器在重启后丢失，可能导致重启后短时间内重复告警（一次），可接受。
- LLM 响应缓存在重启后丢失，会导致重启后的首次 LLM 调用无法命中缓存，不影响正确性。

---

## 7. 设计决策

### 7.1 为什么 Redis 是可选的而非必选的

SentinelMind 的典型部署场景是单机运行（1-10 路摄像头）。在这种场景下，内存缓存完全够用，引入 Redis 增加了部署复杂度和外部依赖。Redis 的价值在于：多进程部署时共享缓存状态（如多 Worker 的 FastAPI）、缓存数据持久化（重启不丢失）、更丰富的数据结构。这些在第一版都不是刚需。让 Redis 可选，保证了最小化部署（pip install 即可跑）的体验。

### 7.2 为什么用惰性过期 + 定期清理双策略

惰性过期（访问时检查）保证了 get 操作不会返回已过期的数据，且开销极小（只在访问时检查一次）。但仅靠惰性过期，从未被访问的过期条目会一直占用内存。定期清理（每 60 秒全量扫描）回收这些"死条目"，防止内存无限增长。两种策略互补，兼顾了实时性和内存效率。

### 7.3 为什么运行时不自动切换回 Redis

运行时从内存缓存切换到 Redis 存在严重问题：内存缓存中积累了滑动窗口计数、冷却时间戳等状态数据，切换到 Redis 后这些状态丢失，会导致：滑动窗口计数器归零（可能产生误报）、冷却计时器归零（可能重复告警）。更安全的做法是：降级后保持内存缓存运行，Redis 恢复后提示重启进程以启用 Redis。

### 7.4 为什么缓存的 value 类型不做强制约束

不同用途的缓存值类型不同：滑动窗口计数器存整数、冷却时间戳存浮点数、摄像头状态存字典、LLM 响应缓存存字符串。如果强制统一类型，每个 set 操作都需要类型转换，增加不必要的开销。CacheProtocol 接口接受 Any 类型，由调用方自行保证类型正确。RedisCache 内部通过 JSON 序列化/反序列化处理类型转换。

### 7.5 为什么 Key 命名使用冒号分隔

冒号分隔是 Redis 社区的通用惯例（如 `user:1001:name`），优点：可读性好、支持按前缀批量操作（`SCAN MATCH va:rule:*`）、与 Redis 的 key 空间分析工具兼容。即使使用内存缓存，相同的 key 命名规范也方便在日志中排查问题。

### 7.6 为什么 LRU 淘汰策略用 OrderedDict 而非第三方库

Python 标准库的 `collections.OrderedDict` 提供了 O(1) 的 move_to_end() 和 popitem(last=False) 操作，正好满足 LRU 的需求。不需要引入第三方 LRU 库，减少依赖。对于 10000 条目的缓存规模，OrderedDict 的性能完全足够。
