# 内置规则设计 — 设计书

## 1. 模块职责

内置规则模块提供 Vision Agent 第一版的 5 个核心规则实现，覆盖视频监控中最常见的异常检测场景。每个规则实现 RuleProtocol 接口，作为 YAML 声明式规则的底层评估器。

模块职责包括：

1. **区域闯入检测**：目标进入禁止多边形区域并持续一段时间。
2. **离岗检测**：指定区域在工作时间内无人值守。
3. **人员聚集检测**：区域内的人员数量超过设定阈值。
4. **遗留物检测**：某物体在区域内静止停留超过设定时长。
5. **人数统计**：经过指定计数线的人员数量统计。

每个规则都遵循统一的接口规范（RuleProtocol），与三层防线机制完全兼容。规则内部维护各自的评估状态（如停留计时器、历史计数），通过 `reset()` 方法支持状态重置。

---

## 2. 对外接口

### 2.1 公共接口（所有内置规则共享）

所有内置规则均实现 RuleProtocol：

| 成员 | 类型 | 说明 |
|------|------|------|
| name | 属性 (str) | 规则名称，取自 YAML 配置的 name 字段 |
| camera_ids | 属性 (list[str] 或 None) | 适用摄像头列表，取自 YAML 配置 |
| evaluate | 方法 | (tracks: list[Track], frame: numpy.ndarray, context: dict) -> Event 或 None |
| reset | 方法 | () -> None，清理内部状态缓存 |

### 2.2 各规则类注册名

| 注册名 | 类名 | 评估器类型 |
|--------|------|-----------|
| intrusion | IntrusionRule | object_in_zone |
| absence | AbsenceRule | zone_empty |
| crowd | CrowdRule | crowd_threshold |
| abandoned_object | AbandonedObjectRule | object_stationary |
| counting | CountingRule | count_line |

---

## 3. 内部逻辑

### 3.1 规则一：区域闯入（IntrusionRule）

**规则描述**：检测是否有指定类别的目标进入预定义的多边形禁区区域，并持续停留超过设定时长。

**YAML 配置格式**：

```
name: str          规则名称
description: str   规则描述
camera_ids: list   适用摄像头列表
severity: str      严重级别（默认 critical）

conditions:
  - type: object_in_zone
    classes: list[str]          目标类别（如 ["person"]）
    zone: list[list[int]]       多边形顶点坐标 [[x1,y1], [x2,y2], ...]
    persist: str                持续时长（如 "10s"、"30s"、"1m"）
    confidence: float           最低置信度阈值（默认 0.5）

actions:
  - type: notify                通知动作
  - type: record_clip           录制片段
  - type: llm_analyze           LLM 分析
```

**evaluate() 判定逻辑**：

1. 遍历 tracks 列表，筛选 class_name 在配置的 classes 中的 Track。
2. 对每个符合条件的 Track，取其当前帧的 bbox 中心点坐标。
3. 使用射线法（ray casting algorithm）判断中心点是否在配置的多边形 zone 内部。
4. 若目标在区域内：
   - 查询内部状态缓存，获取该 track_id 首次进入区域的时间。
   - 若为首次进入，记录进入时间到缓存（key: `intrusion:{camera_id}:track:{track_id}`）。
   - 计算当前时间与首次进入时间的差值，若 >= persist 配置的秒数，判定为触发。
5. 若目标离开区域，清除该 track_id 的缓存记录。
6. 触发时返回 Event 对象，event_type 为 "intrusion"，metadata 包含进入的目标 track_id 列表、进入时间、停留时长。

**需要的上下文数据**：
- context["camera_id"]：摄像头标识
- context["camera_name"]：摄像头名称
- context["timestamp"]：当前帧时间戳

**典型使用场景**：
- 仓库禁区检测：有人进入存放贵重物品的区域。
- 周界安防：翻越围栏进入限制区域。
- 机房入口监控：非授权时段有人进入机房。

---

### 3.2 规则二：离岗检测（AbsenceRule）

**规则描述**：检测指定区域在工作时间内是否有人值守。如果在配置的时间窗口内（如工作时间），区域内持续无人超过设定时长，触发告警。

**YAML 配置格式**：

```
name: str          规则名称
description: str   规则描述
camera_ids: list   适用摄像头列表
severity: str      严重级别（默认 warning）

conditions:
  - type: zone_empty
    classes: list[str]          目标类别（如 ["person"]）
    zone: list[list[int]]       多边形顶点坐标
    min_absent: str             最短空岗时长（如 "5m"、"10m"）

time_windows:
  - start: "08:00"
    end: "18:00"
    days: [0, 1, 2, 3, 4]      周一到周五

actions:
  - type: notify
```

**evaluate() 判定逻辑**：

1. 遍历 tracks 列表，统计在配置的 zone 区域内、class_name 在 classes 中的目标数量。
2. 统计方法：取每个 Track 的 bbox 中心点，用射线法判断是否在 zone 内。
3. 若区域内目标数量 > 0：
   - 清除空岗状态缓存（key: `absence:{camera_id}:empty_since`）。
   - 返回 None（有人值守，不触发）。
4. 若区域内目标数量 == 0：
   - 查询缓存中首次检测到空岗的时间。
   - 若为首次空岗，记录当前时间戳到缓存。
   - 计算空岗持续时长，若 >= min_absent 配置的秒数，判定为触发。
5. 触发时返回 Event 对象，event_type 为 "absence"，metadata 包含空岗开始时间、持续时长。

**需要的上下文数据**：
- context["camera_id"]、context["camera_name"]、context["timestamp"]

**典型使用场景**：
- 前台/门卫岗位监控：工作时间有人离开岗位超过 5 分钟。
- 收银台监控：营业时间无人值守。
- 值班室监控：夜班时段无人在岗。

---

### 3.3 规则三：人员聚集（CrowdRule）

**规则描述**：检测指定区域内的人员数量是否超过设定阈值。

**YAML 配置格式**：

```
name: str          规则名称
description: str   规则描述
camera_ids: list   适用摄像头列表
severity: str      严重级别（默认 warning）

conditions:
  - type: crowd_threshold
    classes: list[str]          目标类别（如 ["person"]）
    zone: list[list[int]]       多边形顶点坐标（可选，缺省为全画面）
    threshold: int              人数阈值
    confidence: float           最低置信度阈值（默认 0.5）

actions:
  - type: notify
  - type: llm_analyze
```

**evaluate() 判定逻辑**：

1. 遍历 tracks 列表，筛选 class_name 在 classes 中且 confidence >= 阈值的 Track。
2. 若配置了 zone，用射线法判断每个 Track 的 bbox 中心点是否在 zone 内，只统计区域内的目标。
3. 若未配置 zone，统计全画面的符合条件的目标数量。
4. 统计去重：同一 track_id 只计数一次（追踪器保证同一目标跨帧 track_id 不变）。
5. 若人数 >= threshold，判定为触发。
6. 触发时返回 Event 对象，event_type 为 "crowd"，metadata 包含当前人数、阈值、区域内目标的 track_id 列表。

**需要的上下文数据**：
- context["camera_id"]、context["camera_name"]、context["timestamp"]

**典型使用场景**：
- 工厂门口人群聚集：可能有劳资纠纷或安全事件。
- 施工区域超员：超过安全容纳人数。
- 出入口拥堵：人流密度过高，需要分流。

---

### 3.4 规则四：遗留物检测（AbandonedObjectRule）

**规则描述**：检测区域内是否有物体在原地停留超过设定时长，且周围一定范围内无人。典型目标：行李、包裹、箱子。

**YAML 配置格式**：

```
name: str          规则名称
description: str   规则描述
camera_ids: list   适用摄像头列表
severity: str      严重级别（默认 critical）

conditions:
  - type: object_stationary
    classes: list[str]          目标类别（如 ["backpack", "suitcase", "box"]）
    zone: list[list[int]]       多边形顶点坐标（可选）
    duration: str               停留时长阈值（如 "5m"、"10m"）
    max_velocity: float         视为静止的最大速度（像素/秒，默认 5.0）
    proximity_radius: int       周围无人的判定半径（像素，默认 100）
    confidence: float           最低置信度阈值（默认 0.5）

actions:
  - type: notify
  - type: record_clip
  - type: llm_analyze
```

**evaluate() 判定逻辑**：

1. 遍历 tracks 列表，筛选 class_name 在 classes 中的 Track。
2. 若配置了 zone，过滤只保留在区域内的目标。
3. 对每个候选目标，计算其移动速度（通过 trajectory 中最近 N 个点的位移 / 时间差）。
4. 若速度 <= max_velocity，认定为静止目标：
   - 查询缓存中该 track_id 的首次静止时间（key: `abandoned:{camera_id}:track:{track_id}:stationary_since`）。
   - 若为首次检测到静止，记录当前时间。
   - 计算静止持续时长，若 >= duration 配置的秒数，进入"周围无人"检查。
5. 周围无人检查：计算该目标 bbox 中心点到所有 class_name 为 "person" 的 Track 中心点的距离。若所有人的距离 > proximity_radius，确认为遗留物。
6. 若周围有人，不触发（可能有人在旁边看着，属于正常场景），但不清除静止计时。
7. 触发时返回 Event 对象，event_type 为 "abandoned_object"，metadata 包含物体 track_id、类别、位置、停留时长。

**需要的上下文数据**：
- context["camera_id"]、context["camera_name"]、context["timestamp"]

**典型使用场景**：
- 车站/机场行李监控：旅客遗留行李后离开。
- 公共场所可疑包裹：包裹长时间无人认领。
- 商场/超市物品遗留：顾客遗忘手提袋。

---

### 3.5 规则五：人数统计（CountingRule）

**规则描述**：统计经过指定计数线的人员数量。支持双向计数（进入/离开），可在累计达到阈值时触发告警。

**YAML 配置格式**：

```
name: str          规则名称
description: str   规则描述
camera_ids: list   适用摄像头列表
severity: str      严重级别（默认 info）

conditions:
  - type: count_line
    classes: list[str]          目标类别（如 ["person"]）
    line: list[list[int]]       计数线的两个端点 [[x1,y1], [x2,y2]]
    direction: str              计数方向（"in" / "out" / "both"，默认 "both"）
    alert_threshold: int        累计达到此数量触发告警（可选，0 或不配置则每过一人触发一次事件）
    reset_interval: str         计数重置间隔（如 "1h"、"1d"，可选）
    confidence: float           最低置信度阈值（默认 0.5）

actions:
  - type: notify
  - type: log                   仅记录日志
```

**evaluate() 判定逻辑**：

1. 遍历 tracks 列表，筛选 class_name 在 classes 中、trajectory 至少有 2 个点的 Track。
2. 对每个符合条件的 Track，检查其轨迹最近两个点（前一帧位置 P1 和当前帧位置 P2）是否穿越了计数线。
3. 穿越判定：计算线段 P1-P2 与计数线的交点。若存在交点，判定为穿越。
4. 方向判定：
   - 计算穿越方向向量（P2 - P1）与计数线法向量的点积。
   - 点积 > 0 为正方向（如 "进入"），点积 < 0 为负方向（如 "离开"）。
   - 根据 direction 配置决定是否计数。
5. 去重：已计数的 track_id 记录在缓存中（key: `counting:{camera_id}:counted_tracks`），同一目标在同一次穿越中不重复计数。当 track_id 离开计数线附近区域后，从已计数集合中移除，允许下次再计。
6. 更新缓存中的累计计数（正方向和负方向分别计数）。
7. 事件触发逻辑：
   - 若配置了 alert_threshold 且累计计数达到阈值，触发事件，重置计数器。
   - 若未配置 alert_threshold，每检测到一次有效穿越就触发一次事件。
8. 若配置了 reset_interval，检查是否到了重置时间，到了则重置计数器。
9. 返回 Event 对象，event_type 为 "counting"，metadata 包含当前累计计数（in_count、out_count）、触发的 track_id。

**需要的上下文数据**：
- context["camera_id"]、context["camera_name"]、context["timestamp"]

**典型使用场景**：
- 出入口客流统计：商场入口每小时进入人数。
- 区域人数控制：某区域进入人数达到上限时告警。
- 人流趋势分析：采集数据用于后期分析。

---

## 4. 依赖关系

| 依赖模块 | 依赖方向 | 说明 |
|----------|----------|------|
| rules/engine | 内置规则 ← 规则引擎 | 内置规则由引擎注册和调用 |
| cache | 内置规则 → 缓存层 | 遗留物的停留计时、闯入的首次进入时间、计数器的状态均依赖缓存 |
| core/types | 内置规则 → 数据类型 | 使用 Track、Event、BoundingBox 等数据模型 |
| config | 内置规则 → 配置管理 | 读取规则 YAML 配置 |

内置规则之间无互相依赖，各自独立评估。

---

## 5. 配置项

### 5.1 各规则的配置参数汇总

| 规则 | 参数 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| intrusion | classes | list[str] | ["person"] | 检测目标类别 |
| intrusion | zone | list[list[int]] | 必填 | 多边形禁区坐标 |
| intrusion | persist | str | "5s" | 最短停留时长 |
| intrusion | confidence | float | 0.5 | 最低置信度 |
| absence | classes | list[str] | ["person"] | 检测目标类别 |
| absence | zone | list[list[int]] | 必填 | 岗位区域坐标 |
| absence | min_absent | str | "5m" | 最短空岗时长 |
| crowd | classes | list[str] | ["person"] | 检测目标类别 |
| crowd | zone | list[list[int]] | None | 区域坐标，None=全画面 |
| crowd | threshold | int | 必填 | 人数阈值 |
| crowd | confidence | float | 0.5 | 最低置信度 |
| abandoned_object | classes | list[str] | ["backpack", "suitcase"] | 检测目标类别 |
| abandoned_object | zone | list[list[int]] | None | 区域坐标，None=全画面 |
| abandoned_object | duration | str | "5m" | 停留时长阈值 |
| abandoned_object | max_velocity | float | 5.0 | 视为静止的最大速度（像素/秒） |
| abandoned_object | proximity_radius | int | 100 | 周围无人的判定半径（像素） |
| abandoned_object | confidence | float | 0.5 | 最低置信度 |
| counting | classes | list[str] | ["person"] | 检测目标类别 |
| counting | line | list[list[int]] | 必填 | 计数线两端点坐标 |
| counting | direction | str | "both" | 计数方向 |
| counting | alert_threshold | int | 0 | 累计阈值，0=每次穿越触发 |
| counting | reset_interval | str | None | 计数重置间隔 |
| counting | confidence | float | 0.5 | 最低置信度 |

### 5.2 时长字符串解析

`persist`、`duration`、`min_absent`、`reset_interval` 等时长参数支持以下格式：

- `Ns`：N 秒（如 "10s"）
- `Nm`：N 分钟（如 "5m"）
- `Nh`：N 小时（如 "1h"）
- `Nd`：N 天（如 "1d"）

内部统一转换为秒数（int）存储。

---

## 6. 错误处理

### 6.1 配置校验错误

| 规则 | 错误场景 | 处理方式 |
|------|----------|----------|
| intrusion | zone 坐标点数 < 3 | 记录 ERROR，规则不加载 |
| intrusion | persist 格式错误 | 记录 ERROR，使用默认值 5s |
| absence | zone 未配置 | 记录 ERROR，规则不加载 |
| crowd | threshold 未配置或 <= 0 | 记录 ERROR，规则不加载 |
| abandoned_object | duration 格式错误 | 记录 ERROR，使用默认值 5m |
| counting | line 未配置或不是两个点 | 记录 ERROR，规则不加载 |
| counting | direction 值非法 | 记录 ERROR，使用默认值 "both" |

### 6.2 运行时错误

| 错误场景 | 处理方式 |
|----------|----------|
| Track 数据异常（bbox 坐标越界） | 跳过该 Track，记录 WARNING |
| 射线法计算异常 | 跳过该目标，记录 WARNING |
| 缓存读写失败 | 该次评估结果为"未触发"（宁可漏报不误报），记录 WARNING |
| 轨迹点不足（counting 需要至少 2 个点） | 跳过该 Track，不参与计数 |

### 6.3 边界条件

- **零目标帧**：所有规则都应能正常处理 tracks 为空的情况，返回 None。
- **极高帧率**：滑动窗口帧数阈值在高帧率下可能导致触发过于敏感，建议用户根据实际帧率调整 window_size。
- **跨午夜时间窗口**：离岗检测的 time_windows 支持跨午夜配置（如 22:00-06:00），规则引擎负责正确处理。
- **同一目标同时触发多条规则**：各规则独立评估，互不影响，同一目标可以同时触发闯入和聚集两条规则。

---

## 7. 设计决策

### 7.1 为什么遗留物检测需要"周围无人"条件

仅凭"物体静止"来判定遗留物会产生大量误报：固定的垃圾桶、消防栓、停放的自行车等都会被误判。增加"周围无人"条件可以区分"被人遗弃的物品"和"固定设施"：如果一个物体旁边一直有人，大概率不是遗留物；如果物体静止且周围长时间无人，才是真正的遗留场景。

### 7.2 为什么闯入检测需要"持续 N 秒"而不立即触发

单帧检测可能因为目标边缘经过禁区边界线而产生误报（目标实际在禁区外但 bbox 边缘触及禁区）。持续 N 秒（对应 N 帧）的确认机制确保目标是真正进入并停留在禁区内，而不是路过或误检。

### 7.3 为什么计数规则使用轨迹穿越而非区域计数

区域计数（统计区域内人数）无法区分"进入"和"离开"，且在人员密集时计数不准确（遮挡导致漏检）。基于轨迹穿越的方法：每个目标有唯一 track_id，通过追踪轨迹与计数线的交叉关系来精确计数，避免重复计数和遮挡问题。

### 7.4 为什么离岗检测用区域人数而非特定目标追踪

追踪特定目标（如"张三"）需要人脸识别，侵入性强且技术复杂。使用"区域内有无人"的简单判断更通用：不关心是谁在值守，只关心是否有人。大多数岗位监控场景只需要知道"有没有人"就够了。

### 7.5 为什么使用射线法判断点在多边形内

射线法（ray casting）是计算几何中判断点与多边形关系的标准算法，优点：实现简单、支持凹多边形、时间复杂度 O(n)（n 为顶点数，通常很少）。相比其他方法（如角度求和法），射线法的数值稳定性更好，对浮点精度不敏感。

### 7.6 规则间状态隔离

每个规则实例维护自己独立的内部状态（通过缓存 key 前缀隔离，如 `intrusion:` vs `absence:`）。即使同一摄像头上同时运行多条规则，状态也不会互相干扰。reset() 方法只清除当前规则的状态，不影响其他规则。
