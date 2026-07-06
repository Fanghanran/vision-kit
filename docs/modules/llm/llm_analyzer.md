# LLM 分析器 — 设计书

## 1. 模块职责

LLM 分析器（LLMAnalyzer）负责在告警事件生成后，将事件上下文和现场截图发送给大语言模型进行智能分析，输出结构化的分析报告（LLMAnalysis）。它是规则引擎与通知层之间的增强环节：规则引擎产出告警事件后，LLMAnalyzer 对事件进行语义理解，生成人类可读的情况描述、风险评估和处理建议，使通知内容从简单的"检测到异常"升级为"发生了什么、风险多大、该怎么处理"。

核心定位：
- 作为 ActionProtocol 的实现，可被规则引擎的 actions 配置灵活调用
- LLM 是增强层而非必要层，分析失败不影响告警通知的正常发送
- 预留 RAG（检索增强生成）扩展点，第二版接入向量知识库后可参考历史案例和 SOP

## 2. 对外接口

### 类：LLMAnalyzer

LLMAnalyzer 实现 ActionProtocol 接口，同时提供独立的 analyze 方法供直接调用。

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `provider: LLMProviderProtocol`, `config: LLMConfig`, `prompt_template: str = None` | None | 构造函数，注入 LLM 提供者和配置 |
| `execute` | `alert: Alert` | `bool` | ActionProtocol 接口，对告警执行 LLM 分析，将分析结果写入 alert.llm_analysis，成功返回 True |
| `analyze` | `event: Event`, `snapshot: numpy.ndarray`, `rag_context: str = None` | `LLMAnalysis 或 None` | 核心分析方法，接收事件+截图帧+可选 RAG 上下文，返回结构化分析结果，失败返回 None |
| `name` | （属性） | `str` | 返回 `"llm_analyze"`，ActionProtocol 要求 |

### 辅助函数

| 函数 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `_build_prompt` | `event: Event`, `rag_context: str = None` | `str` | 根据事件信息和可选 RAG 上下文构造分析 prompt |
| `_encode_snapshot` | `frame: numpy.ndarray` | `str` | 将 numpy BGR 帧编码为 base64 JPEG 字符串 |
| `_parse_response` | `raw: str` | `LLMAnalysis` | 将 LLM 返回的文本解析为 LLMAnalysis 结构化对象 |
| `_build_fallback_analysis` | `event: Event` | `LLMAnalysis` | LLM 不可用时，基于规则引擎原始信息构造降级分析结果 |

### 数据结构：LLMConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | True | 是否启用 LLM 分析 |
| provider_type | str | "openai_compatible" | LLM 提供者类型 |
| model | str | （必填） | 模型名称 |
| timeout | int | 30 | 单次调用超时秒数 |
| max_retries | int | 2 | 最大重试次数 |
| monthly_budget | float | 100.0 | 月度预算上限（美元） |
| budget_alert_threshold | float | 0.8 | 预算告警阈值（0-1） |

## 3. 内部逻辑

### 3.1 核心分析流程

当 `analyze()` 方法被调用时，执行以下流程：

1. **前置检查**：检查 `config.enabled` 是否为 True，若关闭则返回 None。检查 provider 是否为 None（可能因初始化失败导致），若为 None 则返回 None。

2. **截图编码**：调用 `_encode_snapshot()` 将 numpy BGR 帧压缩编码为 JPEG 格式，再转为 base64 字符串。JPEG 质量默认 85，长边最大 1280 像素（等比缩放），控制 token 消耗。

3. **Prompt 构造**：调用 `_build_prompt()` 拼装完整的分析 prompt。Prompt 由三部分组成：
   - 系统角色设定：定义 LLM 为视频监控分析专家，要求输出 JSON 格式
   - 事件上下文：包括事件类型、摄像头信息、触发规则、检测目标数量、严重级别、时间戳
   - RAG 参考资料（可选）：当 `rag_context` 不为 None 且非空时，拼接在事件上下文之后，标注为"参考资料"

4. **LLM 调用**：调用 `provider.chat_with_image()`，传入 prompt 文本和 base64 截图。内部由 provider 处理重试、断路器、超时和预算控制。

5. **响应解析**：调用 `_parse_response()` 解析 LLM 返回的文本。解析策略：
   - 首选：尝试从返回文本中提取 JSON 块（匹配 ```json ``` 标记或直接 JSON 解析）
   - 次选：若 JSON 解析失败，用正则表达式从自然语言文本中提取 description、risk_level、suggestion 关键字段
   - 兜底：若所有解析均失败，将整个返回文本作为 description，risk_level 设为 "未知"，suggestion 设为 "LLM 分析结果无法结构化，请查看原始返回"

6. **构建 LLMAnalysis**：将解析结果填充到 LLMAnalysis 对象中，同时保留 raw_response 字段存储 LLM 原始返回文本。

7. **返回结果**：返回填充完毕的 LLMAnalysis 对象。

### 3.2 execute 方法流程

`execute(alert)` 是 ActionProtocol 要求的接口方法：

1. 检查 alert.event 是否存在，不存在则返回 False。
2. 检查 alert.event.snapshot_path 是否存在且文件可读，若截图文件不可用，用纯文本模式分析（不传图片）。
3. 读取截图文件为 numpy 数组（cv2.imread），若读取失败则降级为纯文本模式。
4. 调用 `analyze(event=alert.event, snapshot=snapshot, rag_context=None)`。
5. 若 analyze 返回 None（LLM 不可用），调用 `_build_fallback_analysis()` 构造降级结果。
6. 将分析结果赋值给 `alert.llm_analysis`。
7. 记录日志：分析耗时、风险等级、是否使用降级结果。
8. 返回 True（execute 始终返回 True，即使降级也不算失败，因为降级是有意设计）。

### 3.3 Prompt 构造逻辑

`_build_prompt()` 的 prompt 模板分为三层：

**系统指令层**（固定）：
定义 LLM 的角色为视频监控智能分析专家，要求输出 JSON 格式，包含 description（情况描述）、risk_level（风险等级，枚举值：低/中/高/紧急）、suggestion（建议措施）、context（补充说明）四个字段。

**事件上下文层**（动态）：
从 Event 对象提取以下信息拼接：
- 事件类型（event_type）的中文映射名
- 摄像头名称（camera_name）和 ID（camera_id）
- 触发规则名称（rule_name）
- 检测到的目标数量和类型（从 detections 提取 class_name 去重计数）
- 严重级别（severity）
- 事件发生时间（timestamp 格式化为可读时间）
- 规则附带的 metadata（若有）

**RAG 参考资料层**（可选）：
当 `rag_context` 参数非空时，以"以下为相关参考资料，请结合分析"为前缀拼接。rag_context 的内容由上游 RAG 检索模块提供，LLMAnalyzer 不关心其内部实现。

### 3.4 响应解析逻辑

`_parse_response()` 的解析策略按优先级：

1. **JSON 提取**：用正则匹配文本中的 JSON 块（支持 ```json ``` 包裹和裸 JSON），然后 json.loads 解析。提取 description、risk_level、suggestion、context 四个字段，缺失字段填默认值。

2. **字段校验**：对 risk_level 做枚举校验，只允许"低/中/高/紧急"四个值，不匹配则归类为"中"。对 description 和 suggestion 做长度检查，超过 2000 字符截断。

3. **正则回退**：若 JSON 解析完全失败，用正则匹配"风险等级：(.+)"、"建议：(.+)" 等模式提取字段。

4. **文本兜底**：若正则也失败，整个返回文本存入 description，其余字段用默认值。

### 3.5 降级策略

当 LLM 不可用（provider 返回 None、超时、断路器打开等）时，`_build_fallback_analysis()` 构造降级分析结果：

- description：从 Event 中提取信息，格式为"[event_type中文名]：[camera_name]检测到[count]个[class_name]"
- risk_level：映射 Event.severity（critical→紧急，warning→中，info→低）
- suggestion："LLM 分析不可用，请人工查看截图确认情况"
- context："此为规则引擎原始结果，未经 LLM 分析"
- raw_response：空字符串

## 4. 依赖关系

| 依赖项 | 类型 | 说明 |
|--------|------|------|
| LLMProviderProtocol | 运行时依赖 | 通过构造函数注入，实际为 OpenAICompatibleProvider 或其他实现 |
| core/types | 模块依赖 | 使用 Event、Alert、LLMAnalysis、Detection 数据模型 |
| config | 模块依赖 | 读取 llm 配置段 |
| numpy | 运行时依赖 | 截图帧为 numpy 数组 |
| opencv-python (cv2) | 运行时依赖 | 读取截图文件、图像压缩编码 |
| logging | 标准库 | 结构化日志记录 |
| json | 标准库 | 解析 LLM 返回的 JSON |
| re | 标准库 | 正则提取 LLM 返回中的字段 |
| base64 | 标准库 | 截图 base64 编码 |
| storage/vector_store | 预留依赖 | RAG 知识检索，第二版实现，通过 rag_context 参数注入 |

### 依赖方向

LLMAnalyzer 依赖 LLMProviderProtocol，不依赖具体实现。通过构造函数注入 provider 实例，符合依赖倒置原则。pipeline 模块负责组装：创建 provider 后注入 analyzer，再将 analyzer 注册为规则引擎的 action。

## 5. 配置项

配置来自 `configs/settings.yaml` 的 `llm` 段：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| llm.enabled | bool | True | 是否启用 LLM 分析 |
| llm.provider | str | "openai_compatible" | 提供者类型 |
| llm.model | str | （必填） | 模型标识，如 "gpt-4o-mini" |
| llm.api_base | str | （必填） | API 基础地址 |
| llm.api_key | str | （必填，环境变量） | API 密钥，必须用 `${LLM_API_KEY}` 引用 |
| llm.timeout | int | 30 | 单次调用超时秒数 |
| llm.max_retries | int | 2 | 最大重试次数 |
| llm.monthly_budget | float | 100.0 | 月度预算上限（美元） |
| llm.budget_alert_threshold | float | 0.8 | 预算告警阈值比例 |
| llm.system_prompt | str | 内置默认值 | 自定义系统 prompt，覆盖默认角色设定 |
| llm.snapshot_quality | int | 85 | 截图 JPEG 压缩质量 |
| llm.snapshot_max_size | int | 1280 | 截图长边最大像素 |
| rag.enabled | bool | false | RAG 开关（预留） |
| rag.top_k | int | 5 | 检索返回条数（预留） |

## 6. 错误处理

### 6.1 错误分类与处理策略

| 错误场景 | 处理方式 | 对上游影响 |
|----------|----------|-----------|
| LLM 分析整体关闭（enabled=false） | analyze() 直接返回 None，execute() 调用降级分析 | 告警正常发送，内容为规则引擎原始结果 |
| Provider 未初始化（为 None） | analyze() 直接返回 None | 同上 |
| 截图文件不存在或损坏 | 降级为纯文本模式，不传图片给 LLM | 分析质量降低但不中断 |
| 截图 base64 编码失败 | 记录 warning 日志，降级为纯文本模式 | 分析质量降低但不中断 |
| LLM API 调用失败（已在 provider 层重试和断路器） | provider 返回 None，analyze() 返回 None | 告警正常发送，内容降级 |
| LLM 返回内容无法解析 | 使用文本兜底策略，全文存入 description | 分析结果结构化程度降低 |
| LLM 返回内容为空 | 构造默认的 LLMAnalysis（description="LLM 返回为空"） | 分析结果质量降低 |
| prompt 构造时 Event 字段缺失 | 用默认值填充缺失字段，记录 warning 日志 | prompt 信息不完整 |

### 6.2 降级层级

LLMAnalyzer 设计了三级降级：

1. **正常模式**：截图 + 事件上下文 + RAG 参考资料 → LLM 分析 → 结构化结果
2. **纯文本模式**：截图不可用时，仅用事件上下文 → LLM 分析 → 结构化结果
3. **完全降级**：LLM 不可用时，基于规则引擎原始信息构造分析结果，不调用 LLM

任何情况下 execute() 方法都返回 True，确保 pipeline 不会因为 LLM 分析失败而中断告警流程。

### 6.3 日志记录

- 分析开始：记录 event_id、event_type、camera_id
- 分析完成：记录耗时（ms）、risk_level、是否降级
- 分析失败：记录错误类型和错误消息
- 所有日志不包含截图内容（base64 数据量大且无日志价值）

## 7. 设计决策

### 7.1 LLM 作为增强层而非必要层

决策：LLM 分析失败时自动降级为规则引擎原始结果，告警通知照常发送。

理由：系统核心价值是"检测异常并通知"，LLM 分析是锦上添花。如果 LLM 不可用导致整个告警中断，系统的可靠性反而下降。降级策略确保 LLM 故障的爆炸半径仅限于"通知内容不够详细"，不会导致"漏报告警"。

### 7.2 JSON 结构化输出 + 多级解析

决策：要求 LLM 输出 JSON 格式，但解析时不完全信任 LLM 输出，设计了 JSON 解析→正则提取→文本兜底三级策略。

理由：LLM 输出格式不可控，即使 prompt 明确要求 JSON，实际输出可能包含额外文本、格式错误或思考过程。三级解析策略确保在最差情况下也能得到一个有意义的 LLMAnalysis 对象，不会因为解析失败导致整个分析链路中断。

### 7.3 截图压缩策略

决策：截图编码为 JPEG 质量 85，长边最大 1280 像素。

理由：原始监控帧通常是 1920x1080 或更高分辨率，直接编码 base64 会导致 token 消耗过大（一张 1080p 图片约 500-800 token）。压缩到 1280 长边后约 200-300 token，在保持足够分析细节的同时显著降低成本。质量 85 在视觉上几乎无损，但文件大小减少约 60%。

### 7.4 RAG 预留设计

决策：analyze() 方法接受可选的 rag_context 字符串参数，enabled=false 时不注入。

理由：RAG 是纯增量功能，通过参数注入而非硬编码，实现零影响的预留。第二版接入 RAG 时，只需在调用 analyze() 之前增加一步知识库检索，将检索结果作为 rag_context 传入即可，不需要修改 LLMAnalyzer 的任何内部逻辑。rag_context 设计为字符串而非结构化数据，给上游更大的灵活性。

### 7.5 execute 始终返回 True

决策：execute() 方法无论 LLM 分析是否成功都返回 True。

理由：ActionProtocol 中 execute 返回 bool 表示行动是否成功执行。对通知类 action，发送失败应返回 False 以便重试。但 LLM 分析的"失败"是有意设计的降级行为，不应触发上游的失败重试逻辑。LLM 不可用时构造降级分析是正常行为，不是错误。
