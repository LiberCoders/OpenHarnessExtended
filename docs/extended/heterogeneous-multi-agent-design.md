# 异构多Agent并行协作框架设计文档

## 1. 需求分析

### 1.1 场景描述

用户下发一个复杂任务后，主对话（Master Agent）需要：
1. **任务理解与分解**：分析任务结构，识别可并行执行的子任务
2. **异构子任务分发**：
   - **CLI任务**：可在本地直接通过命令行完成
   - **电脑GUI任务**：需要截图→推理→操作→再截图的循环
   - **手机GUI任务**：需要在移动设备上执行类似的操作循环
3. **并行执行**：对于无依赖关系的子任务，同时启动多个Agent执行
4. **结果汇总**：所有子任务完成后，整合结果返回给用户
5. **双向通信**：
   - 主对话能主动查看子Agent的执行过程
   - 子Agent能主动上报进度/状态/问题给主对话

### 1.2 核心需求

#### 需求分类说明

需求分为两类：
1. **框架开发需求**：本次需要实现的功能和基础设施
2. **模型能力需求**：通过System Prompt引导模型具备的能力（不开发额外模块）

#### 1.2.1 框架开发需求（本次实现）

| 需求 | 描述 | 优先级 |
|------|------|--------|
| **异构Agent支持** | 统一的Agent Loop架构，支持CLI/PC GUI/Mobile GUI/Browser等不同类型的Agent | P0 |
| **并行执行能力** | 支持模型在单轮中并行启动多个子Agent，主对话可同时执行本地任务 | P0 |
| **双向通信机制** | 系统层自动上报 + 模型层主动通信，支持Master查看子Agent状态 | P0 |
| **Agent生命周期** | 子Agent的启动、状态监控、消息收发、终止和结果收集 | P0 |
| **三层热插拔架构** | PerceptionProvider/ReasoningEngine/ActionExecutor抽象，支持感知、推理、行动三层定制 | P0 |
| **错误上报** | 子Agent可自主重试（1-3次），持续失败后通过通信机制上报 | P1 |

#### 1.2.2 模型能力需求（System Prompt引导）

以下能力**不开发额外模块**，通过System Prompt引导模型自主决策：

| 能力 | 描述 | 实现方式 |
|------|------|----------|
| **任务分析** | 分析复杂任务可分解为哪些子任务 | System Prompt中的任务处理指导 |
| **依赖判断** | 识别子任务间的数据依赖和控制依赖 | System Prompt中的示例和原则 |
| **并行决策** | 判断哪些子任务可以并行执行 | 模型基于依赖关系自主决策 |
| **动态调整** | 根据子Agent执行结果动态决定下一步 | 模型在后续对话轮次中决策 |
| **执行方式选择** | 选择主对话直接执行还是启动子Agent | System Prompt中的CLI任务执行方式指导 |

**说明**：模型能力的核心是通过**System Prompt设计**引导模型自主决策，而不是开发TaskPlanner等显式规划模块。详见2.5节System Prompt设计和8.1节讨论记录。

#### 1.2.3 未来扩展需求（暂不实现）

| 需求 | 描述 | 阶段 |
|------|------|------|
| 系统级容错恢复 | 主Agent失败、子Agent大面积失败时的系统级处理 | 第二阶段 |
| Learning Loop | Agent系统自我学习、自我改进的元学习能力 | 第二阶段+ |
| 持续监控Agent | 7x24小时运行的监控类Agent支持 | 扩展阶段 |
| 人机协作Agent | 支持人随时与Agent交互的协作模式 | 扩展阶段 |

### 1.3 当前代码能力评估

本节从“哪些地方应复用、哪些地方才值得扩展”的角度评估现有代码，为后续方案设计（第 2 节）和集成策略（第 4 节）提供依据。

#### 1.3.1 已有能力及需要调整的部分

| 模块 | 已有能力 | 推荐策略 |
|------|---------|---------|
| **Agent主循环（`engine/query.py` / `engine/query_engine.py`）** | 已具备稳定的对话循环、工具调用、流式事件与上下文处理能力 | **优先复用**。`extended` 不应复制一套文本 Agent 主循环；只有遇到截图、DOM、设备控制等异构输入/动作时，才在外层增加编排或适配 |
| **多 Agent 协调（`swarm/`）** | 已有后端启动能力、Mailbox、Team/Teammate 概念 | **在其上扩展**。优先复用后端与生命周期管理；消息能力以适配器方式增强，但保持 Mailbox 为主事实来源 |
| **后台任务（`tasks/`）** | `BackgroundTaskManager`、任务 ID、stdout/stderr 聚合 | **直接复用** 作为所有子 Agent 的进程管理基础设施；各类型 Agent 只补自己的感知/行动适配 |
| **工具系统（`tools/`）** | 已有完善的工具抽象、注册与 MCP 能力 | **沿现有注册体系接入**。新增多 Agent 工具优先做成额外工具或插件，不重写注册主流程 |
| **插件系统（`plugins/`）** | 已支持插件发现、加载、命令、skills、agents、hooks、MCP | **首选扩展入口**。如果 `extended` 能以插件目录或额外根目录挂载，就不要先改核心入口 |
| **运行时构建（`ui/runtime.py`）** | `build_runtime(...)` 已支持 `extra_skill_dirs`、`extra_plugin_roots` | **首选集成点**。先利用现有参数挂载扩展能力，再考虑是否需要新增薄封装 |
| **Prompt/Skill 系统** | 已支持 System Prompt 组装、Skill 注册、上下文注入 | **增量追加**。扩展优先通过新增 prompt 片段、skill、agent 定义实现，而非替换整套 prompt 生成逻辑 |
| **CLI 入口（`cli.py`）** | 完整的启动参数与交互入口 | **最后才动**。仅当现有运行时参数无法满足扩展接入时，才做极小范围入口增量 |

#### 1.3.2 模块设计与解耦策略

**核心原则**：`extended/` 应该是 OpenHarness 的**外挂能力层**，而不是一套“看起来目录独立、实际上把核心能力又写了一遍”的平行实现。

为兼顾“解耦”和“享受上游演进”，需要把模块分成三类：

**A. 尽量独立放在 `extended/` 的内容**

| 模块 | 建议路径 | 说明 |
|------|------|------|
| 异构 Agent 定义与工厂 | `extended/agents/` | 管理 CLI、PC、Mobile、Browser 等 Agent 的差异化实现 |
| 感知/动作适配器 | `extended/agents/*/` | 截图、DOM、ADB、Playwright、桌面输入等特有能力应独立于核心 |
| 扩展工具 | `extended/tools/` | 例如 `spawn_agent` 的异构扩展、状态查询、消息发送、监控工具 |
| 编排与适配层 | `extended/bus/`、`extended/lifecycle/` | 负责把异构能力接到现有运行时，而不是替代运行时 |
| 扩展 prompt / skills / agents | `extended/prompts/`、`extended/skills/`、插件目录 | 通过配置和注册影响模型行为，保持与核心主循环解耦 |

**B. 应优先复用上游的内容**

- `src/openharness/ui/runtime.py` 的运行时构建与额外根目录挂载能力
- `src/openharness/plugins/` 的插件发现与加载能力
- `src/openharness/coordinator/agent_definitions.py` 的 Agent 定义合并逻辑
- `src/openharness/tools/` 的工具抽象与注册机制
- `src/openharness/engine/` 的文本推理主干、Provider 与会话管理
- `src/openharness/tasks/`、`src/openharness/swarm/backends/` 的任务和后端能力

**C. 允许触碰但必须严格控边界的热点区域**

| 热点区域 | 原则 |
|---------|------|
| `cli.py` | 只允许增加极薄入口或参数透传，不在这里堆异构逻辑 |
| `ui/runtime.py` | 尽量通过现有参数扩展；若必须加钩子，应集中在极少数行并可单独回滚 |
| `swarm/mailbox.py` 契约 | 可以适配，不应分叉出第二套权威消息存储 |
| `engine/query.py` | 可以参考、调用、组合；不应复制出长期维护的同构循环 |

#### 1.3.3 与上游 OpenHarness 的同步策略

**推荐路径（按优先级排序）**：

1. **插件化接入**：优先把 `extended` 做成独立插件、额外 skill 目录或额外 plugin root。
2. **薄适配层接入**：在 `extended/` 内包装已有运行时、工具注册、Mailbox 或 Agent 定义，不直接改核心实现。
3. **窄缝入口改动**：只有在前两步无法满足需求时，才在 `cli.py` / `runtime.py` 做最小增量接入。

**明确不推荐的做法**：

- 复制 `engine/query.py` 并长期在 `extended` 中维护第二套文本主循环
- 复制 `ui/runtime.py` 或在其旁边维护同等职责的完整运行时
- 为了扩展方便而在 `cli.py` 中加入大量 `if extended_enabled` 分支
- 在消息层同时维护 Mailbox 和独立内存队列两套真相来源

**同步时应重点关注的兼容面**：

- Loop 行为变更：如果上游调整工具调用或流式事件语义，优先修改适配层，而不是补丁式继续分叉
- Runtime 参数变更：如果 `build_runtime(...)` 参数或初始化顺序变化，应优先跟随上游接口
- 插件/工具契约变更：若上游调整 manifest、tool schema、agent registry，扩展侧应优先兼容这些入口
- Mailbox / Swarm API 变更：消息扩展必须建立在上游契约之上，避免出现双轨协议

**注**：任务规划能力仍采用**模型自主决策**方式，通过 System Prompt 与工具组合引导模型做任务拆解；若未来上游出现更成熟的 Planner 能力，应优先复用而不是再造一套。详见第 8 节。

## 2. 方案设计

### 2.1 核心设计思想：复用主干能力 + 三层可插拔扩展

这一节需要明确区分两件事：

1. **长期架构抽象**：所有 Agent 都可以用“感知 → 推理 → 行动”来理解，因此三层都应具备可插拔能力。
2. **P0 落地策略**：并不意味着 P0 就要把三层全部从 OpenHarness 中剥离出来重写。优先做法是复用上游主干，把真正异构的部分放到 `extended/`。

```
┌──────────────────────────────────────────────────────────────────┐
│                    OpenHarness 主干能力（优先复用）               │
│  Query loop / QueryEngine / Runtime / Tools / Plugins / Session │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     extended 扩展能力层（按需新增）               │
│  Perception adapters / Action executors / Prompt policies /     │
│  Heterogeneous orchestration / Agent-specific tools             │
└──────────────────────────────────────────────────────────────────┘
```

**核心结论**：

- 三层抽象是**能力边界**，不是“所有层都必须在 P0 独立实现一遍”的开发要求。
- 新增 Agent 类型时，通常至少会改**感知层**和**行动层**；推理层是否独立，要看是否需要新的模型类型、动作空间或上下文拼装。
- 对于纯文本或轻度变体的场景，应尽量继续复用 OpenHarness 现有推理主干，而不是为了抽象完整性另起一套 Loop。

**三层抽象的职责**：

| 层 | 负责什么 | 在 P0 的推荐策略 |
|----|---------|----------------|
| **感知层（Perception）** | 收集上下文、截图、DOM、设备状态、命令输出等输入 | **重点扩展**。这是异构 Agent 差异最大的层，适合优先沉淀到 `extended/agents/*/perception.py` |
| **推理层（Reasoning）** | Prompt 拼装、模型选择、动作空间约束、计划与决策 | **优先复用，按需增强**。先复用 OpenHarness 现有推理与工具调用能力，仅在视觉、多模态或专用动作空间场景下做定制 |
| **行动层（Action）** | Shell、鼠标键盘、ADB、Playwright、设备控制等执行能力 | **重点扩展**。把真实异构执行器封装在 `extended/`，避免污染核心工具体系 |

**Agent 分类保留两个维度，但聚焦近期实现范围**：

#### 维度1：设备/交互类型

| Agent类型 | 感知 | 推理 | 行动 | 近期优先级 |
|-----------|------|------|------|-----------|
| **普通对话** | 上下文历史、用户输入 | 现有文本推理 | 文本回复、工具调用 | 已有能力 |
| **CLI Agent** | 命令输出、文件状态 | 以现有推理主干为主 | Shell 命令执行 | P0 |
| **PC GUI Agent** | 截图、窗口状态 | 视觉+文本推理 | 鼠标/键盘控制 | P1 |
| **Mobile GUI Agent** | 手机截图、设备状态 | 视觉+文本推理 | ADB/HDC/Appium | P1 |
| **Browser Agent** | DOM、页面截图、元素列表 | 结构化/视觉推理 | Playwright API | P1 |

更远期的智能眼镜、IoT、车载、无人机等形态仍与这一抽象兼容，具体方向见第 9 节。

#### 维度2：工作机制类型

| 工作机制 | 说明 | 与三层抽象的关系 |
|---------|------|----------------|
| **即时响应型** | 收到请求后立即执行并返回 | 标准 Loop |
| **持续监控型** | 持续采集状态，发现异常后上报或行动 | 感知层是常驻采集，行动层偏通知/处置 |
| **人机协作型** | Agent 自主执行，但用户可中途介入 | 推理层需要处理打断、消息与恢复 |
| **批量处理型** | 一次读取大量输入，集中处理后输出 | 感知层偏批量读取，行动层偏汇总提交 |

**对后续设计的直接约束**：

- 文档里所有“统一 Loop”的表述，都应理解为**统一抽象和编排模型**，而不是承诺脱离 OpenHarness 主干另建一整套运行时。
- 文档里所有“易于扩展”的表述，都应改成“新增 Agent 类型只实现差异层，能复用的主干能力继续复用”。
- 若某个设计让 `extended` 需要长期同步 `query.py`、`runtime.py`、`cli.py` 的主要逻辑，那这个设计就不符合本方案的解耦目标。

### 2.2 统一Agent Loop架构

```python
# extended/engine/unified_loop.py
class UnifiedAgentLoop:
    """Hot-swappable Agent Loop with pluggable perception, reasoning, and action."""

    def __init__(
        self,
        perception_provider: PerceptionProvider,  # 热插拔点1：CLI/GUI/Mobile各有不同
        reasoning_engine: ReasoningEngine,        # 热插拔点2：文本推理/视觉推理/结构化推理
        action_executor: ActionExecutor,          # 热插拔点3：Shell/pynput/adb/Playwright
        context_manager: ContextManager,
    ):
        self.perception = perception_provider
        self.reasoning = reasoning_engine
        self.action = action_executor
        self.context = context_manager

    async def run_turn(self) -> TurnResult:
        """One turn: Perceive → Think → Act."""
        # 1. 感知：获取当前环境状态（热插拔：截图/命令输出/DOM/传感器）
        observation = await self.perception.observe()

        # 2. 思考：LLM推理决定下一步（热插拔：Prompt构建、模型选择、动作空间定义）
        thought = await self.reasoning.think(
            observation=observation,
            context=self.context.get_history()
        )

        # 3. 行动：执行决定的操作（热插拔：Shell/pynput/adb/Playwright执行器）
        result = await self.action.execute(thought.actions)

        # 4. 更新上下文
        self.context.update(observation, thought, result)

        return TurnResult(observation, thought, result)


# ========== 可热插拔的感知层 ==========

class PerceptionProvider(ABC):
    """Hot-swappable perception layer."""

    @abstractmethod
    async def observe(self) -> Observation:
        """Return current environment state."""
        pass


class DialogPerception(PerceptionProvider):
    """普通对话：从上下文获取用户输入."""

    async def observe(self) -> Observation:
        return Observation(
            text=self.context.get_last_user_message(),
            visual=None,
            metadata={}
        )


class ScreenshotPerception(PerceptionProvider):
    """PC UI操控：截图 + UI元素识别."""

    async def observe(self) -> Observation:
        screenshot = await self.capture_screen()
        ui_elements = await self.analyze_ui(screenshot)
        return Observation(
            text=ui_elements.describe(),  # 可访问性文本描述
            visual=screenshot,            # 图片数据
            metadata={"ui_elements": ui_elements}
        )


class MobilePerception(PerceptionProvider):
    """Mobile GUI操控：手机截图 + 设备状态."""

    async def observe(self) -> Observation:
        screenshot = await self.adb_screenshot()
        return Observation(
            text=await self.get_accessibility_tree(),
            visual=screenshot,
            metadata={"device_info": self.get_device_info()}
        )

# 未来扩展：SensorPerception（IoT）、WearablePerception（可穿戴）等


# ========== 可复用的思考和行动层 ==========

class ReasoningEngine(ABC):
    """
    推理层：可插拔组件.

    P0 阶段大部分 Agent 应通过 QueryEngineReasoning 复用 OpenHarness 现有推理主干；
    仅在视觉推理、多模态输入或专用动作空间场景下才需要自定义实现。

    定制化点（按需覆盖）：
    - System Prompt 构建（CLI专家 vs GUI专家 vs Web专家）
    - 模型选择（文本模型 vs 视觉模型）
    - 动作空间定义（Shell命令 vs 鼠标键盘 vs Playwright API）
    - 输出格式解析（不同Agent输出不同结构化格式）
    """

    @abstractmethod
    async def think(
        self,
        observation: Observation,
        context: ConversationHistory
    ) -> Thought:
        """推理决策：构建Prompt -> 调用LLM -> 解析输出为结构化Action."""
        pass

    @abstractmethod
    def build_prompt(self, observation: Observation, context: ConversationHistory) -> list:
        """构建包含System Prompt和上下文的messages."""
        pass


class ActionExecutor(ABC):
    """
    行动层：热插拔组件，不同Agent类型有不同执行器.

    定制化点：
    - 执行器类型：subprocess/pynput/adb/Playwright
    - 反馈收集：返回码/截图验证/设备响应
    - 错误处理：重试策略/错误分类/上报机制
    """

    @abstractmethod
    async def execute(self, actions: list[Action]) -> ActionResult:
        """
        执行操作并收集反馈.
        包括：执行动作 -> 收集结果 -> 错误处理
        """
        pass


class ToolBasedActionExecutor(ActionExecutor):
    """基于现有ToolRegistry的通用行动执行器."""

    async def execute(self, actions: list[Action]) -> ActionResult:
        results = []
        for action in actions:
            tool = self.tool_registry.get(action.tool_name)
            result = await tool.execute(action.parameters)
            results.append(result)
        return ActionResult(results)
```

#### 2.2.1 UnifiedAgentLoop 与 QueryEngine 的集成模式

`UnifiedAgentLoop` 是异构 Agent 的**编排抽象**，而 `QueryEngine` 是 OpenHarness 已有的推理主干（含流式调用、工具执行、自动压缩等）。两者的关系是**组合**而非替代：

```
UnifiedAgentLoop                         QueryEngine (OpenHarness 主干)
┌──────────────────┐                    ┌──────────────────────────────┐
│ 1. Perception    │ → 感知数据 →       │                              │
│    (异构差异层)  │                    │  submit_message / run_query  │
├──────────────────┤                    │  - 自动压缩                  │
│ 2. Reasoning     │ ← 内部委托 →      │  - 流式事件                  │
│    (适配层)      │                    │  - 并发工具调用              │
├──────────────────┤                    │  - 上下文管理                │
│ 3. Action        │ ← 工具调用结果 ←  │                              │
│    (异构差异层)  │                    └──────────────────────────────┘
└──────────────────┘
```

**推荐集成模式（P0）**：`ReasoningEngine` 的默认实现委托 `QueryEngine`，把感知数据转换为消息注入 `QueryEngine` 的上下文，再从其输出中提取结构化 Action：

```python
class QueryEngineReasoning(ReasoningEngine):
    """P0 默认推理实现：委托 QueryEngine 完成推理和工具调用."""

    def __init__(self, engine: QueryEngine):
        self._engine = engine

    async def think(self, observation: Observation, context: ConversationHistory) -> Thought:
        message = self.build_prompt(observation, context)
        actions = []
        async for event in self._engine.submit_message(message):
            if is_tool_call_event(event):
                actions.append(extract_action(event))
        return Thought(actions=actions)

    def build_prompt(self, observation: Observation, context: ConversationHistory) -> str:
        parts = [observation.text]
        if observation.visual:
            parts.append(f"[截图已保存至: {observation.visual_path}]")
        return "\n".join(parts)
```

**仅在以下场景需要自定义 `ReasoningEngine`**：

- 视觉推理需要直接向模型 API 发送图片（绕过 `QueryEngine` 的纯文本消息流）
- 动作空间与 OpenHarness 工具体系不兼容（如需要输出像素坐标而非工具调用）
- 需要专用模型或特殊的输出解析逻辑

这样既保持了三层抽象的扩展能力，又避免为抽象完整性复制一套推理主循环。

#### 2.2.2 上下文管理（ContextManager）

上下文管理分为两个层面：**单 Agent 内的上下文**和 **Agent 间传递的上下文**。

**设计原则**：

1. **复用 OpenHarness 现有机制**：`QueryEngine` 已具备自动压缩（`auto_compact_threshold_tokens`）和上下文窗口管理能力，P0 阶段优先复用，不引入独立的上下文管理系统。
2. **完整信息持久化到磁盘**：每个 Agent 每轮的完整信息（感知数据、推理过程、执行结果）写入本地磁盘，用于事后审计和调试。
3. **Agent 间共享的是原始信息的选取**：系统自动从完整轮次记录中选取关键字段（如最近动作、执行状态、错误信息等）供其他 Agent 或 Master 读取，图像和文件只传磁盘路径而非内容本身。这与 Agent 主动发送的消息不同——后者是模型自行生成的摘要或请求，属于模型层通信（见 2.7.2 节）。

```python
class ContextManager:
    """Agent 上下文管理."""

    def __init__(self, agent_id: str, persist_dir: Path):
        self._agent_id = agent_id
        self._persist_dir = persist_dir
        self._history: list[TurnRecord] = []

    def update(self, observation: Observation, thought: Thought, result: ActionResult):
        """记录一轮完整信息并持久化."""
        record = TurnRecord(
            turn_id=len(self._history),
            observation=observation,
            thought=thought,
            result=result,
            timestamp=datetime.now(),
        )
        self._history.append(record)
        self._persist_to_disk(record)

    def get_history(self) -> ConversationHistory:
        """返回供推理层使用的上下文（受 QueryEngine 压缩机制管理）."""
        return ConversationHistory(self._history)

    def get_shared_state(self) -> AgentSharedState:
        """从完整记录中选取关键字段，供其他 Agent 或 Master 读取.

        这是系统自动选取的原始信息子集，不是模型生成的摘要。
        图像、文件等大体积数据只包含磁盘路径而非内容本身。
        """
        latest = self._history[-1] if self._history else None
        return AgentSharedState(
            agent_id=self._agent_id,
            total_turns=len(self._history),
            last_action=latest.thought.actions if latest else [],
            last_result=latest.result.output if latest else "",
            last_error=latest.result.error if latest and latest.result.is_error else None,
            artifact_paths=self._collect_artifact_paths(),
        )

    def _persist_to_disk(self, record: TurnRecord):
        """每轮完整信息写入磁盘，包括截图原始文件等."""
        path = self._persist_dir / f"turn_{record.turn_id}.json"
        path.write_text(record.to_json())

    def _collect_artifact_paths(self) -> list[Path]:
        """收集所有产出物的路径（截图、生成文件等）."""
        ...
```

**视觉 Agent 的上下文约束**：

截图数据是视觉 Agent 的主要 token 消耗来源。具体的截图保留策略（保留最近 N 张、关键帧筛选等）属于各类型 Agent 的实现细节，在 `PerceptionProvider` 内部决定哪些截图进入当前轮次的 `Observation`，哪些只保留磁盘路径供回溯。`QueryEngine` 的自动压缩机制为此提供了基础保障。

### 2.3 异构Agent工厂

```python
# extended/agents/factory.py
class HeterogeneousAgentFactory:
    """Factory to create agents with different perception capabilities."""

    @staticmethod
    def create_cli_agent(task: str) -> UnifiedAgentLoop:
        """CLI Agent: 感知 = 命令输出 + 文件状态."""
        return UnifiedAgentLoop(
            perception=CommandOutputPerception(),
            reasoning=StandardReasoning(model="gpt-4"),
            action=ShellActionExecutor(),
            context=InMemoryContext(),
        )

    @staticmethod
    def create_pc_gui_agent(task: str) -> UnifiedAgentLoop:
        """PC GUI Agent: 感知 = 截图 + GUI分析."""
        return UnifiedAgentLoop(
            perception=ScreenshotPerception(),
            reasoning=VisionEnabledReasoning(model="gpt-4-vision"),
            action=DesktopActionExecutor(),
            context=InMemoryContext(),
        )

    @staticmethod
    def create_mobile_gui_agent(task: str) -> UnifiedAgentLoop:
        """Mobile GUI Agent: 感知 = 手机截图."""
        return UnifiedAgentLoop(
            perception=MobilePerception(),
            reasoning=VisionEnabledReasoning(model="gpt-4-vision"),
            action=MobileActionExecutor(),
            context=InMemoryContext(),
        )

    # 未来扩展：create_sensor_agent、create_wearable_agent 等
```

### 2.4 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                      Master Agent (主对话)                     │
│  ┌──────────────────┐  ┌──────────────────┐                  │
│  │ System Prompt    │  │ Progress Monitor │                  │
│  │ (任务分解指导)    │  │   进度监控       │                  │
│  └────────┬─────────┘  └──────────────────┘                  │
│           │ 主对话可同时：                                      │
│           │ 1. 直接执行CLI (shell_tool)                        │
│           │ 2. 并行启动子Agent (spawn_agent)                   │
└───────────┼──────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│                     Unified Message Bus                       │
│                  (统一消息总线 - 扩展Mailbox)                   │
│              增量消息队列，各 Agent 按需消费                     │
└──────────┬──────────────────┬──────────────────┬─────────────┘
           │                  │                  │
           ▼                  ▼                  ▼
     ┌───────────┐     ┌───────────┐     ┌────────────┐
     │ CLI Agent  │     │ PC GUI    │     │ Mobile GUI │   ...
     │            │     │ Agent     │     │ Agent      │  (未来扩展)
     └─────┬─────┘     └─────┬─────┘     └──────┬─────┘
           │                 │                   │
           └─────────────────┼───────────────────┘
                             │
                             ▼
           ┌─────────────────────────────────────────┐
           │       Unified Agent Loop Core            │
           │  ┌───────────┐ ┌──────────┐ ┌────────┐  │
           │  │ Perception│→│Reasoning │→│ Action │  │
           │  │ (热插拔)   │ │(委托QE)  │ │(热插拔)│  │
           │  └───────────┘ └──────────┘ └────────┘  │
           │       ↑              ↑            ↑      │
           │  各Agent类型    QueryEngine    各Agent类型│
           │  自定义实现     (复用主干)     自定义实现  │
           └─────────────────────────────────────────┘
```

**说明**：所有子 Agent（包括 CLI Agent）都走统一的 Agent Loop。不同类型的差异体现在 Perception 和 Action 的热插拔实现上，Reasoning 层 P0 阶段默认委托 `QueryEngine`。

### 2.5 System Prompt设计

主Agent的System Prompt需要增加任务处理指导：

```markdown
## 复杂任务处理指南

当用户提出复杂任务时，请按以下步骤处理：

1. **任务分析**：思考任务可以分解为哪些子任务
2. **依赖判断**：识别子任务间的依赖关系
   - 数据依赖：后序任务需要前序任务的输出
   - 控制依赖：某个任务必须在另一个之后执行
3. **并行执行**：对于无依赖的子任务，使用**并行工具调用**同时执行
4. **异构Agent选择**：根据任务类型选择合适的Agent：
   - `type="cli"`：纯命令行操作（编译、脚本、文件操作等）
   - `type="pc_gui"`：需要图形界面交互（点击、输入、截图验证等）
   - `type="mobile_gui"`：需要在手机上执行的图像交互操作
   - `type="browser"`：需要浏览器自动化操作（网页导航、表单填写等）

## CLI任务执行方式选择

CLI任务有两种执行方式，根据场景自主选择：

### 方式A：主对话直接执行（shell_tool）
适用场景：
- 简单、快速完成的命令（如git status, ls, cat）
- 需要立即看到结果来决定下一步
- 任务输出需要直接进入当前上下文

### 方式B：子Agent后台执行（spawn_agent(type="cli")）
适用场景：
- 耗时较长的任务（编译、测试、下载等）
- 可以和其他任务并行执行
- 失败不影响主对话继续
- 需要独立监控进度

### 并行策略
主对话可以同时进行：
1. 自己执行简单CLI任务
2. 并行启动多个后台CLI子Agent
3. 并行启动GUI类子Agent（PC/Mobile）

最大化利用并行能力，主对话不空闲等待。

**示例场景**：
- "构建项目并部署到测试环境" → 可并行：本地构建（CLI） + 准备部署环境（CLI）
- "在电脑上配置环境，同时在手机上安装APP" →
  - 主对话：git status（快速查看）
  - 后台：spawn_agent(type="cli", task="环境配置") + spawn_agent(type="mobile_gui", task="安装APP")
- "查询数据并生成报告" → 有依赖：先查询数据，看到结果后再生成报告

**执行流程**：
1. 在单轮中并行调用多个`spawn_agent`启动子任务（可同时主对话执行CLI）
2. 等待子任务完成（后续对话轮次会看到工具执行结果）
3. 根据子任务输出，决定下一步行动
```

### 2.6 Agent生命周期管理

轻量级的Agent管理器，负责任务调度和生命周期管理：

```python
class AgentLifecycleManager:
    """Lightweight manager for sub-agent lifecycle."""

    def __init__(self, message_bus: UnifiedMessageBus):
        self._active_agents: dict[str, AgentHandle] = {}
        self._message_bus = message_bus
        self._factory = HeterogeneousAgentFactory()

    async def spawn_agent(
        self,
        agent_type: Literal["cli", "pc_gui", "mobile_gui", "browser"],
        task_description: str,
        context: dict
    ) -> str:
        """Spawn a sub-agent and return agent_id."""
        agent_id = generate_agent_id()
        agent = self._factory.create(agent_type, task_description)
        handle = await self._start_agent(agent_id, agent)
        self._active_agents[agent_id] = handle
        return agent_id

    async def get_agent_status(self, agent_id: str) -> AgentStatus:
        """Query agent execution status."""

    async def send_message_to_agent(self, agent_id: str, message: str) -> None:
        """Send message to a specific agent via message bus."""

    async def terminate_agent(self, agent_id: str) -> TaskResult:
        """Terminate agent and return final result."""
```

#### 2.6.1 spawn_agent 与现有基础设施的集成路径

`spawn_agent` 的实现应根据 Agent 类型选择不同的底层执行机制，复用 OpenHarness 已有基础设施：

| Agent 类型 | 推荐底层机制 | 说明 |
|-----------|-------------|------|
| **所有子 Agent** | `BackgroundTaskManager.create_agent_task()` | 统一复用已有的 agent 子进程管理，天然支持任务 ID、输出捕获、状态查询、停止等能力 |

**选择依据**：

`BackgroundTaskManager` 已经具备任务 ID、stdout/stderr 聚合、状态查询和停止能力，这些能力对所有类型的子 Agent 都适用。CLI Agent 和 GUI Agent 的差异在于 Agent 进程内部的感知/行动循环不同，而非进程管理方式不同。`AgentLifecycleManager` 只需做薄封装，把 `TaskRecord` 映射为 `AgentHandle`。

```python
async def _start_agent(self, agent_id: str, agent_type: str, task: str) -> AgentHandle:
    """所有子 Agent 统一通过 BackgroundTaskManager 启动."""
    task_record = await self._task_manager.create_agent_task(
        prompt=task,
        description=f"{agent_type} Agent {self._next_id()}",
        cwd=self._resolve_cwd(agent_type),
    )
    return AgentHandle(
        agent_id=task_record.id,
        agent_type=agent_type,
        task_record=task_record,
    )
```

### 2.7 双向通信架构

采用**分层通信机制**：模型主动通信（可选）+ 系统自动上报（硬性机制）。

**核心传输模型**：所有通信都采用**增量消息队列**模式——发送方向队列追加新消息，接收方按需拉取未读消息。不做全量快照推送，也不要求接收方实时处理每一条消息。

#### 2.7.1 分层设计

两层通信承载的**内容类型不同**，不会产生语义重叠：

| 层 | 内容性质 | 典型内容 | 是否进入 Master 上下文 |
|----|---------|---------|---------------------|
| **系统层（硬性机制）** | 轻量级状态标志 | `running` / `completed` / `error` / `stalled` 等枚举状态 | 状态正常时**不注入**上下文，仅在异常时才作为系统事件注入 |
| **模型层（可选）** | 模型生成的富内容 | 错误详情、阶段性成果摘要、请求协助的自然语言消息 | 作为消息注入，由 Master 模型自主决定是否关注 |

```
┌──────────────────────────────────────────────────────────────┐
│                     Communication Layers                      │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────────────────────────────────────────────┐      │
│  │  Layer 2: System-Level Auto Reporting         │      │
│  │  硬性机制，轻量状态标志                               │      │
│  │  - 自动记录 Agent 执行过程                           │      │
│  │  - 增量追加状态事件到消息队列                         │      │
│  │  - 状态正常时不占用 Master 上下文窗口                 │      │
│  │  - 仅异常事件（error/stall）触发注入                  │      │
│  └────────────────────────────────────────────────────┘      │
│                              ↓                                │
│  ┌────────────────────────────────────────────────────┐      │
│  │  Layer 1: Model-Level Active Communication         │      │
│  │  可选，模型生成的富内容                               │      │
│  │  - Agent 模型主动调用工具查看其他 Agent 状态          │      │
│  │  - Agent 模型主动发送消息给其他 Agent                │      │
│  │  - 完全可选，Agent 可以选择不看不发                  │      │
│  │  - 主 Agent 应关注全局，普通 Agent 无强制要求        │      │
│  └────────────────────────────────────────────────────┘      │
│                              ↓                                │
│  ┌────────────────────────────────────────────────────┐      │
│  │  Layer 0: Unified Message Bus (基础设施)             │      │
│  │  - 基于 Mailbox 的增量消息队列                       │      │
│  │  - 支持点对点和广播                                  │      │
│  │  - 所有通信的底层支撑                                │      │
│  └────────────────────────────────────────────────────┘      │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

#### 2.7.2 模型层通信（可选）

**提供给模型的通信工具**（Agent可主动调用）：

```python
# 所有Agent（包括普通Agent和主Agent）都可用
class QueryOtherAgentTool(BaseTool):
    """Query status/progress of another agent.
    
    Use this to check what other agents are doing.
    This is optional - agent can choose not to use it.
    """
    target_agent_id: str
    query_type: Literal["status", "progress", "last_output", "full_history"]

class SendMessageToAgentTool(BaseTool):
    """Send a message to another agent.
    
    Use this to communicate with other agents (ask for help, notify completion, etc.)
    This is optional - agent can choose not to use it.
    """
    target_agent_id: str
    message: str
    priority: Literal["normal", "urgent"]

class BroadcastMessageTool(BaseTool):
    """Broadcast a message to all agents in the session.

    Use this for announcements or coordination.
    This is optional.
    """
    message: str

class ReportErrorTool(BaseTool):
    """Report error to master agent.

    Use this when task execution fails after retry.
    This is a special case of agent communication (error reporting).
    """
    error_type: str  # "network", "permission", "timeout", "logic", "ui_not_found", etc.
    error_message: str
    retry_count: int  # how many times retried before reporting
    context: dict  # additional context (screenshot, last_action, etc.)
```

**行为特点**：
- **普通Agent**：可以完全不使用这些工具，专注于自己的任务
- **主Agent**：必须使用，因为要对用户负责，需要全局把控
- **Agent间关系**：任何Agent都可以查看其他Agent（如果知道ID），完全开放
- **错误上报**：特殊但重要的通信类型，见下方"错误处理流程"

#### 错误处理流程（作为通信的一部分）

错误上报是双向通信的特殊情况，流程如下：

1. **子Agent遇到错误** → 根据错误类型判断是否可重试
   - 临时性错误（网络超时、元素未加载）：自动重试1-3次
   - 持续性错误（页面不存在、权限拒绝）：立即上报

2. **持续失败**（重试3次后仍失败）→ 调用`ReportErrorTool`上报给主Agent

3. **主Agent接收错误** → 决策如何处理：
   - 重新分配任务给其他Agent
   - 换种方式重试（如换工具/换参数）
   - 终止该子任务
   - 询问用户如何处理

4. **主Agent也失败** → 进入系统级容错（见第9节）

**关键原则**：子Agent有有限的自主重试权（避免频繁上报），但最终必须上报，不由子Agent无限重试或擅自决定任务失败。

#### 2.7.3 系统层通信（硬性机制）

系统层上报的核心定位是**轻量级状态标志**，类似心跳和事件通知，而非详细的执行日志。

**设计要点**：

1. **增量追加**：每次上报都是向消息队列追加一条新的状态事件，Master 按需拉取未读事件。
2. **内容极简**：正常状态下只是一个枚举值（`running` / `completed` / `error` / `stalled`），只有异常时才附带简要描述。
3. **按需注入上下文**：状态正常（`running`）时，Master 不需要将其注入 LLM 上下文窗口；仅在状态变更（`completed` / `error` / `stalled`）时才注入，避免无谓消耗 token 预算。

```python
class SystemReportingManager:
    """System-level auto reporting: lightweight status events."""

    async def on_agent_turn_complete(self, agent_id: str, turn_result: TurnResult):
        """Hook: called after each agent turn."""
        await self.execution_log.append(agent_id, turn_result)

        event = self._build_status_event(agent_id, turn_result)
        # 增量追加到消息队列，不维护独立消息真相
        await self.mailbox_adapter.append_event(
            recipient="master",
            event=event,
        )

    def _build_status_event(self, agent_id: str, turn_result: TurnResult) -> StatusEvent:
        """构建轻量状态事件."""
        status = self._determine_status(turn_result)  # running / completed / error / stalled
        return StatusEvent(
            agent_id=agent_id,
            status=status,
            turn_count=self._get_turn_count(agent_id),
            elapsed_seconds=self._get_elapsed(agent_id),
            detail=turn_result.error_summary if status == "error" else None,
        )


@dataclass
class StatusEvent:
    """系统层上报的最小信息单元."""
    agent_id: str
    status: Literal["running", "completed", "error", "stalled"]
    turn_count: int
    elapsed_seconds: float
    detail: str | None = None  # 仅异常时填充
```

**上下文窗口管理策略**：

系统层上报的频率、内容量和处理方式共同决定了对 Master 上下文窗口的压力。以下策略确保低开销：

| 策略 | 说明 |
|------|------|
| **正常状态不注入** | `status=running` 的事件仅更新内存中的状态表，不作为消息注入 Master 的 LLM 上下文 |
| **仅异常触发注入** | `error` / `stalled` / `completed` 才生成系统消息插入 Master 对话流 |
| **低频上报** | 默认上报频率较低（如每轮结束时或固定间隔），不做高频轮询 |
| **合并同源事件** | 同一 Agent 连续多条 `running` 事件在展示时合并为最新一条 |

**配置示例**：

```yaml
system_reporting:
  enabled: true
  interval_seconds: 30       # 最短上报间隔
  inject_to_context:
    on_error: true            # 出错时注入 Master 上下文
    on_completion: true       # 完成时注入
    on_stall: true            # 卡顿时注入
    on_running: false         # 正常运行时不注入
  event_format: "[System] Agent {id}: {status}"  # 注入时的格式
```

**系统上报示例**（仅异常事件注入主 Agent 消息流）：

```markdown
[用户消息]: 帮我同时在电脑和手机上下载安装最新版本

[工具调用]: spawn_agent(type="pc_gui", task="电脑下载安装")
            spawn_agent(type="mobile_gui", task="手机下载安装")

... (pc_gui_001 状态 running，不注入上下文) ...

[System Event]: Agent "mobile_gui_002": error - 网络连接失败 (turn 2, 30s)

[模型思考]: 手机端出错了，电脑端还在运行，我需要处理手机端的问题...

... (pc_gui_001 完成) ...

[System Event]: Agent "pc_gui_001": completed (turn 8, 120s)

[模型思考]: 电脑端已完成，现在处理手机端...
```

#### 2.7.4 统一消息总线基础设施

底层基于Mailbox的通信基础设施：

```python
class UnifiedMessageBus:
    """Unified message bus for all agent communication."""

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._mailbox_root = Path.home() / ".openharness" / "stessions" / session_id

    async def send_to_agent(self, agent_id: str, message: Message) -> None:
        """Send message to specific agent."""
        
    async def broadcast(self, message: Message, exclude: list[str] = None) -> None:
        """Broadcast to all agents."""
        
    async def subscribe_to_agent(self, agent_id: str) -> AsyncIterator[Message]:
        """Subscribe to messages from specific agent."""

@dataclass  
class Message:
    message_id: str
    timestamp: datetime
    sender: str           # agent_id or "system" or "master"
    recipient: str        # agent_id or "broadcast"
    layer: Literal["model", "system"]  # 标识是哪一层产生的消息
    type: MessageType
    payload: dict
```

**消息类型定义**：

| 类型 | 层 | 方向 | 说明 |
|------|-----|------|------|
| agent_status_query | model | Any→Any | Agent主动查询其他Agent |
| agent_message | model | Any→Any | Agent主动发送消息 |
| system_status_report | system | System→Master | 系统定期上报 |
| system_completion_report | system | System→Master | 系统任务完成上报 |
| system_error_report | system | System→Master | 系统错误上报 |
| user_message | - | User→Master | 用户输入 |
| control_command | model | Master→Any | 主Agent控制指令 |

### 2.8 主Agent工具扩展

扩展现有工具支持异构Agent管理：

```python
# extended/tools/spawn_agent_tool.py（继承扩展现有工具）
class SpawnAgentTool(BaseTool):
    """Spawn a sub-agent to execute a task.

    Use this tool when you need to:
    1. Execute a CLI task independently
    2. Perform GUI operations on PC (with screenshot loop)
    3. Control mobile device remotely
    4. Automate browser interactions

    For parallel tasks, call this tool multiple times in the same turn.
    """

    agent_type: Literal["cli", "pc_gui", "mobile_gui", "browser"] = Field(
        description="Type of agent to spawn"
    )
    task: str = Field(description="Task description for the agent")
    # ... 其他参数

# extended/tools/query_agent_status_tool.py（独立实现）
class QueryAgentStatusTool(BaseTool):
    """Query status of a running sub-agent."""

# extended/tools/send_message_to_agent_tool.py（继承扩展现有工具）
class SendMessageToAgentTool(BaseTool):
    """Send a message to a specific sub-agent."""
```

**关键设计**：`spawn_agent`工具支持`agent_type`参数，模型根据任务性质选择合适类型。多个无依赖任务可通过**并行工具调用**同时启动。

### 2.9 执行流程

```
用户输入复杂任务
        ↓
┌─────────────────────────────────────┐
│ 主Agent推理（System Prompt引导）      │
│ - 分析任务可分解性                     │
│ - 判断子任务依赖关系                   │
│ - 识别可并行部分                       │
│ - 选择执行方式（主对话执行 vs 子Agent）  │
└──────────────┬──────────────────────┘
               ↓
    ┌──────────┴──────────┐
    ↓                     ↓
 有依赖？               无依赖？
    ↓                     ↓
 顺序执行               并行执行
    ↓                     ↓
等待前一个完成      ┌─────────────────────────────────┐
    ↓             │                                 │
 执行下一个        ↓                                 ↓
        ┌──────────────────┐          ┌──────────────────────────┐
        │ 主对话直接执行     │          │  并行工具调用            │
        │ （shell_tool）    │          │  spawn_agent(type=...)   │
        │ 简单CLI任务       │          │  spawn_agent(type=...)   │
        └────────┬───────────┘          └──────────┬───────────────┘
                 │                               ↓
                 │                     ┌─────────────────────┐
                 │                     │ UnifiedMessageBus   │ ← 双向通信
                 │                     │    消息总线          │
                 │                     └─────────┬───────────┘
                 │                               ↓
                 │                     ┌───────────────────────────┐
                 │                     │ UnifiedAgentLoop           │
                 │                     │  ┌─────────┐ ┌─────────┐ │
                 │                     │  │Perception│ │Reasoning│ │
                 │                     │  │(热插拔) │ │(可复用) │ │
                 │                     │  └─────────┘ └─────────┘ │
                 │                     └─────────┬───────────────┘
                 │                               ↓
                 └────────────────────────────→ Agent执行各自任务
                                                 │
                                                 ↓
                                       ┌─────────────────────┐
                                       │ 模型看到执行结果     │
                                       │ （工具返回结果）     │
                                       └─────────┬───────────┘
                                                 ↓
                                       ┌─────────────────────┐
                                       │  决定下一步行动     │
                                       │  - 汇总结果输出     │
                                       │  - 继续执行后续任务 │
                                       └─────────────────────┘
```

**关键点**：
1. 所有子Agent共享**统一的Agent Loop架构**
2. 不同Agent类型通过**热插拔PerceptionProvider**实现
3. 主对话可同时执行本地CLI + 并行启动多个子Agent
4. 并行调度由**模型**通过并行工具调用实现

## 3. 关键技术决策

### 3.1 Agent Loop统一架构

**决策**：采用**感知-思考-行动**的统一抽象，但在 P0 中坚持“主干复用优先、差异层扩展”的实现策略。

**理由**：
1. **本质统一**：所有Agent都是观察环境→推理决策→执行行动的循环
2. **扩展有边界**：新增Agent类型应主要实现差异层，而不是复制一套完整主循环
3. **代码复用**：推理主干、工具调用、运行时与会话能力应尽量复用现有实现
4. **面向未来**：支持智能眼镜、IoT等新型Agent

**实现要点**：
- `UnifiedAgentLoop`更适合作为异构编排抽象，而不是 `query.py` 的平行复制品
- `PerceptionProvider`、`ReasoningEngine`、`ActionExecutor` 都是可插拔能力边界
- 工厂模式用于组装不同类型Agent，但优先复用上游已有主干能力

### 3.2 通信协议选择

**决策**：扩展现有 Mailbox 文件系统方案，并保持其为唯一权威消息来源。

**理由**：
- 已有成熟实现
- 原子写入保证消息不丢失
- 支持异步读写
- 易于调试（可直接查看文件）
- 避免出现 Mailbox 与扩展侧私有队列双轨并存的问题

**扩展内容**：
- 增加消息类型字段
- 增加优先级支持
- 增加广播机制
- 通过适配层补充 Master 视角聚合，而不是新建第二套持久化消息系统

### 3.3 感知层实现策略

**PC GUI Agent**：
- 截图工具：PyAutoGUI / MSS / Windows API
- GUI分析：Accessibility API + 可选的OCR/视觉模型
- 行动执行：PyAutoGUI（鼠标/键盘）

**Mobile GUI Agent**：
- 截图工具：adb screencap / Appium
- UI分析：Android Accessibility / UIAutomator
- 行动执行：adb shell input / Appium

**CLI Agent**：
- 感知：命令输出捕获、文件系统状态
- 行动：subprocess执行

### 3.4 并发模型与资源互斥

**决策**：子 Agent 使用独立子进程执行；主进程既负责编排和通信，也能直接执行任务（如通过 `shell_tool` 执行 CLI 命令）。

**理由**：
- 子进程天然隔离，一个 Agent 的阻塞操作不会影响其他 Agent 或主对话
- 与 OpenHarness 现有的 `BackgroundTaskManager`（子进程模型）和 `SubprocessBackend` 保持一致
- 同进程 `asyncio.create_task` 模式在遇到同步阻塞调用（如截图、设备通信）时会阻塞整个事件循环

**主进程的双重角色**：主进程（即主对话/Master Agent）不仅负责编排子 Agent，自身也是一个完整的 Agent，可以直接通过工具执行任务。典型模式是：主对话用 `shell_tool` 直接执行一个快速 CLI 任务，同时通过 `spawn_agent` 把耗时任务交给子 Agent 并行处理。主对话不应退化为纯粹的监控角色——如果有两个并行 CLI 任务，主对话自己做一个、再 spawn 一个，比 spawn 两个然后空等更高效。

**资源互斥约束**：

| 资源 | 约束 | 处理方式 |
|------|------|---------|
| 屏幕控制权 | 同一时刻只能有一个 PC GUI Agent 操控同一块屏幕 | 由 `AgentLifecycleManager` 在 spawn 时检查，拒绝重复分配 |
| 设备连接 | 一台 Mobile 设备同一时刻只能被一个 Agent 控制 | 设备 ID 作为资源锁，spawn 时检查占用状态 |
| 浏览器实例 | 不同 Browser Agent 可使用不同的 browser context 并行 | Playwright 原生支持多 context 隔离 |
| CLI 环境 | 多个 CLI Agent 可以并行，但需注意工作目录和文件系统冲突 | 每个 Agent 使用独立工作目录 |

### 3.5 子 Agent 工具注册

**决策**：每种类型的子 Agent 使用独立组装的 `ToolRegistry`，而非共享主 Agent 的全局工具集。

**理由**：
- 不同 Agent 类型需要不同的工具（CLI Agent 需要 shell 工具，PC GUI Agent 需要鼠标键盘工具）
- 子 Agent 不应拥有 `spawn_agent` 等编排工具（避免子 Agent 无限嵌套派生）
- 独立工具集也便于控制子 Agent 的能力边界

**实现方式**：在 `HeterogeneousAgentFactory` 中为每种 Agent 类型组装专用的工具集：

```python
def _build_tool_registry(self, agent_type: str) -> ToolRegistry:
    """为子 Agent 组装专用工具集."""
    registry = ToolRegistry()

    # 通用通信工具（所有子 Agent 共享）
    registry.register(ReportErrorTool())
    registry.register(SendMessageToAgentTool())

    if agent_type == "cli":
        registry.register(ShellTool())
        registry.register(FileReadTool())
        registry.register(FileWriteTool())
    elif agent_type == "pc_gui":
        registry.register(ScreenshotTool())
        registry.register(MouseClickTool())
        registry.register(KeyboardInputTool())
    elif agent_type == "mobile_gui":
        registry.register(AdbScreencapTool())
        registry.register(AdbInputTool())
    elif agent_type == "browser":
        registry.register(PlaywrightNavigateTool())
        registry.register(PlaywrightClickTool())
        registry.register(PlaywrightTypeTool())

    return registry
```

### 3.6 子 Agent Token 预算控制

**决策**：每个子 Agent 在创建时配置 `max_turns` 上限，防止失控的循环消耗过量 API 费用。

**机制**：

- `QueryEngine` 已内置 `max_turns` 参数，子 Agent 复用此能力即可。
- 默认值根据 Agent 类型不同而不同（CLI 任务通常轮次较少，GUI 任务可能需要更多轮次）。
- 超过 `max_turns` 后触发 `MaxTurnsExceeded`，由系统层捕获并上报给 Master。

| Agent 类型 | 建议默认 max_turns | 说明 |
|-----------|-------------------|------|
| CLI Agent | 8-16 | 命令执行通常较快收敛 |
| PC GUI Agent | 20-30 | 截图→推理→操作循环轮次较多 |
| Mobile Agent | 20-30 | 同上 |
| Browser Agent | 15-25 | 结构化操作效率高于纯视觉 |

Master Agent 在调用 `spawn_agent` 时可以通过参数覆盖默认值：

```python
spawn_agent(type="cli", task="编译项目", max_turns=20)  # 复杂编译可能需要更多轮次
```

## 4. 与现有代码的集成点（解耦设计）

**设计原则**：
1. **先找现有扩展点，再设计新边界**，不要为了目录整洁而绕开 OpenHarness 已经存在的插件、运行时和工具注册能力。
2. **`extended/` 负责异构差异，核心负责通用主干**。
3. **所有核心改动都要可枚举、可回滚、可单独审查**，避免“到处加一点 if extended”。

### 4.1 集成边界划分

| 类型 | 建议放置位置 | 设计原则 |
|------|-------------|---------|
| 异构感知与动作实现 | `extended/agents/` | 完全放在扩展侧，避免把截图、ADB、Playwright、桌面自动化等依赖带入核心 |
| 扩展工具与编排逻辑 | `extended/tools/`、`extended/lifecycle/` | 与现有 `ToolRegistry`、后台任务、Swarm 后端组合，而不是替换这些系统 |
| 扩展 Prompt / Skill / Agent 定义 | `extended/prompts/`、`extended/skills/`、插件目录 | 优先用配置和注册接入，少改核心代码 |
| 运行时和插件加载 | `src/openharness/ui/runtime.py`、`src/openharness/plugins/` | **尽量复用**。这部分越保持原样，越容易吃到上游修复和新特性 |
| 文本主循环、Provider、会话管理 | `src/openharness/engine/`、`src/openharness/api/` | **尽量复用**。除非异构输入/动作无法表达，否则不复制主循环 |

### 4.2 推荐集成路径：插件挂载优先，入口改动最后

当前仓库已经具备两个很重要的扩展入口：

- `build_runtime(..., extra_skill_dirs=..., extra_plugin_roots=...)`
- 插件加载与 Agent 定义合并机制

因此推荐集成顺序如下：

1. **把 `extended` 组织成独立包/目录**，里面放异构 Agent、工具、skills、prompt 片段。
2. **优先通过额外根目录挂载**，让运行时在构建时感知扩展，而不是先去改 `cli.py`。
3. **只有在确实需要新的用户入口时**，再在核心入口加最小的参数透传或项目侧单独 launcher。

推荐的运行时接入方式如下：

```python
from pathlib import Path

from openharness.ui.runtime import build_runtime


async def build_extended_runtime(project_root: Path):
    return await build_runtime(
        extra_plugin_roots=[project_root / "extended" / "plugins"],
        extra_skill_dirs=[project_root / "extended" / "skills"],
    )
```

这种方式的优势是：

- 复用现有 `RuntimeBundle`、插件发现和初始化流程
- 上游若增强运行时构建逻辑，扩展侧自动受益
- 不要求在第一阶段就改动 `src/openharness/cli.py`

### 4.3 薄适配层应该做什么

`extended` 仍然需要适配层，但它应该是**薄的**，职责是“接线”，不是“取代主干”。

**推荐保留的适配器/封装**：

| 模块 | 建议职责 | 约束 |
|------|---------|------|
| `extended/tools/spawn_agent_tool.py` | 在现有工具体系内增加 `agent_type`、扩展配置、状态追踪入口 | 通过工具注册接入，不重写整个工具调度 |
| `extended/bus/mailbox_adapter.py` | 在 Mailbox 语义上补充 Master/Agent 通信、状态订阅、广播语义 | **Mailbox 仍是主事实来源**，不能再造第二套权威消息队列 |
| `extended/lifecycle/manager.py` | 复用 `tasks/` 与 `swarm/backends/`，统一封装异构 Agent 的启动、查询、回收 | 只做生命周期编排，不复制上游 backend 执行器 |
| `extended/engine/unified_loop.py` | 作为异构编排抽象，管理感知/推理/行动层的组合 | 重点是异构输入输出的编排；纯文本决策与工具调用能复用则复用 |

**应避免的做法**：

- 以 `ExtendedRuntimeBundle(RuntimeBundle)` 为主路径长期演进
- 在 `cli.py` 中直接 `try import extended` 并散布扩展判断分支
- 在消息层引入独立 `_master_inbox` 作为另一套持久真相来源

如果确实需要项目级入口，优先在项目侧单独提供 `extended/launcher.py` 或脚本，而不是先把核心 CLI 改成扩展感知型。

### 4.4 Merge Hotspot 与回退策略

下面这些地方如果动了，不代表不能做，但必须在文档里标红为 merge hotspot：

| 区域 | 风险 | 处理原则 |
|------|------|---------|
| `src/openharness/engine/query.py` | 上游主循环演进频繁，最容易出现行为漂移 | 不复制；需要扩展时尽量走组合、钩子、工具或上层编排 |
| `src/openharness/ui/runtime.py` | 运行时初始化容易随上游变化 | 优先使用现有参数；若新增钩子，改动需集中且可单独 cherry-pick |
| `src/openharness/cli.py` | CLI 是高频冲突文件 | 只透传参数，不承载异构逻辑本体 |
| `src/openharness/swarm/mailbox.py` | 涉及协议与存储语义，双轨实现风险高 | 扩展用适配层完成，协议真相保持单一 |

**回退策略**：

- 任一扩展入口失效时，核心 OpenHarness 仍应能以原生模式运行。
- 任一适配层与上游不兼容时，应优先降级为“关闭该扩展能力”，而不是拖垮整个运行时。
- 所有核心改动都应能单独列出，确保后续同步上游时能快速定位冲突点。

**上游同步检查清单**：

1. `build_runtime(...)` 的参数、初始化顺序、插件加载时机是否变化
2. `ToolRegistry`、默认工具注册、插件 manifest 是否新增或变更约束
3. `agent_definitions`、Agent 注册入口是否调整
4. `swarm/mailbox.py`、后端执行器、状态查询协议是否发生破坏性变化
5. 若发生变更，优先修改 `extended` 适配层；只有确认缺少扩展钩子时，才考虑最小核心补丁

## 5. 实现路径（解耦开发）

**开发原则**：

- 每一阶段都优先验证“能否不改核心入口就接入”。
- 每一阶段都必须给出**验收标准**和**失败时的降级方案**。
- 阶段推进顺序以“先打通扩展边界，再增加异构表面” 为准，而不是先铺满所有 Agent 类型。

### 5.1 阶段一：打通扩展挂载路径 + 多 Agent 编排最小闭环（P0）

**目标**：在不改或极少改核心入口的前提下，让 `extended` 可以通过额外 plugin root / skill dir 被加载，并跑通"主对话 + 子 Agent 并行协作"的最小闭环。

**说明**：主对话本身已经具备 CLI 执行能力（通过 `shell_tool`），这里所说的"CLI Agent"是指**作为子 Agent 独立运行**的 CLI 任务——它在独立进程中执行，有自己的上下文和生命周期，主对话可以在等待它完成的同时继续做其他事。选择 CLI 类型作为第一个子 Agent 实现，是因为它的感知/行动层最简单（命令输出 + subprocess），可以最快验证多 Agent 编排链路。

**工作项**：

1. 建立 `extended/` 基础结构：
   - `extended/agents/base.py`（UnifiedAgentLoop 基础抽象）
   - `extended/agents/cli/`（CLI 子 Agent 的感知/行动实现）
   - `extended/tools/`（`spawn_agent`、`query_agent_status` 等编排工具）
   - `extended/skills/` 或插件目录
2. 使用 `build_runtime(..., extra_plugin_roots, extra_skill_dirs)` 挂载扩展目录。
3. 实现 CLI 子 Agent 的最小能力：
   - 复用 `BackgroundTaskManager` 进行子进程管理
   - 复用现有推理主干（`QueryEngineReasoning`）
   - 仅把 CLI 特有的感知/动作差异放到 `extended`
4. 补齐 prompt/skill 中关于任务拆解与并行执行的指导。

**验收标准**：

- 不修改核心 CLI 的情况下，可以通过项目侧入口或运行时构建参数加载 `extended`
- 主对话可以通过 `spawn_agent(type="cli", ...)` 启动一个后台子 Agent，同时自己继续执行其他任务
- 关闭 `extended` 时，原生 OpenHarness 行为不受影响

**失败时的降级方案**：

- 若插件挂载不足以表达能力，允许在项目侧增加独立 launcher
- 仍不建议立即改 `src/openharness/cli.py`

### 5.2 阶段二：补齐消息与生命周期编排（P0 后半）

**目标**：建立可用的多 Agent 控制面，但保持消息与任务的主事实来源仍在上游主干。

**工作项**：

1. 实现 `extended/bus/mailbox_adapter.py`
   - 补充 Master/Agent 通信语义
   - 增加状态订阅、广播、软提醒能力
2. 实现 `extended/lifecycle/manager.py`
   - 复用 `tasks/` 与 `swarm/backends/`
   - 统一启动、查询、终止异构 Agent
3. 实现配套工具：
   - `spawn_agent`
   - `query_agent_status`
   - `send_message_to_agent`

**验收标准**：

- 主 Agent 能查询子 Agent 状态并收取系统层上报
- 至少支持 2 个并行任务同时运行
- 消息层不存在 Mailbox 之外的第二套权威持久状态

### 5.3 阶段三：扩展异构交互表面（P1）

**目标**：在已经稳定的扩展边界上增加 PC、Mobile、Browser 三类异构 Agent。

**工作项**：

1. `extended/agents/pc/`
   - `mss` 截图感知
   - `pynput` 行动执行
2. `extended/agents/mobile/`
   - ADB/HDC 为主
   - Appium 保持可选
3. `extended/agents/browser/`
   - Playwright 感知与行动
   - 结合 DOM 与截图，而不是纯视觉点击

**验收标准**：

- CLI + 1 个 GUI 类 Agent 可并行运行
- Browser Agent 能稳定执行结构化页面操作
- 至少一种 GUI Agent 能与主 Agent 完成基本消息协同

### 5.4 阶段四：上游同步加固与文档收敛

**目标**：验证该方案在长期维护上成立，而不是仅在本地跑通。

**工作项**：

1. 梳理所有核心改动点，形成 merge hotspot 清单。
2. 验证上游同步流程下，哪些改动是无冲突的，哪些需要手工介入。
3. 补充贡献约束：
   - 新功能优先放 `extended/`
   - 任何核心改动必须注明原因与可替代方案
   - 新增 Agent 类型优先复用主干能力

**验收标准**：

- 可以清晰列出所有核心侵入点
- 新增扩展能力不会要求同步维护一份平行 `query.py` / `runtime.py`
- 文档能指导后续贡献者在“解耦”和“跟上游”之间做一致决策

**预估总时间**：6-8 周完成 P0/P1 核心能力，后续再按设备类型逐步扩展。

## 6. 方案对比

### 方案A：主干复用优先 + 有界扩展层（采用）

**设计**：保留 OpenHarness 的运行时、插件、工具注册和文本推理主干；`extended` 只承载异构感知、动作、编排和增量工具。

**优点**：

- 合并上游成本最低
- 可以直接受益于上游在运行时、Provider、工具体系上的修复和新能力
- 仍然保留三层抽象，支持未来继续扩展更多 Agent 类型

**代价**：

- 需要在设计上持续克制，避免为了“目录独立”滑向主干分叉
- 某些能力需要通过适配层接入，而不是最短路径直接改核心

### 方案B：`extended` 全量平行实现（放弃）

**设计**：在 `extended/` 中维护完整的 Loop、Runtime、消息系统和入口。

**放弃原因**：

- 与上游会越来越像两个项目
- 会错过上游在 `query.py`、`runtime.py`、插件体系上的持续收益
- 一旦行为分叉，后续很难判断 bug 应该修哪边

### 方案C：显式 Planner 作为可选插件（未来可选）

**设计**：当前继续使用模型自主决策；未来若上游出现更成熟的 Planner 或任务图能力，则优先以插件/工具形式复用。

**说明**：

- 当前不把 Planner 作为 P0 前置条件
- 未来即使引入 Planner，也应尽量挂在现有主干上，而不是重塑整个架构
- Planner 更像增强件，不应成为本方案对上游分叉的理由

## 7. 总结

本方案的核心立场是：

1. **`extended/` 要独立，但不能变成 OpenHarness 的平行重写版。**
2. **三层热插拔是长期抽象，不是 P0 就要三层全重写。**
3. **能复用上游主干的地方尽量复用，只把真正异构的差异沉淀在扩展侧。**
4. **所有核心侵入点都视为 merge hotspot，并且必须有降级与回退路径。**

这样设计的收益是，既能把新增能力尽量收敛在 `extended/`，也能尽可能持续享受上游 OpenHarness 的实现优化、bug 修复和新功能。

## 8. 讨论记录

本节只保留会影响实现边界的决策索引。

### 8.1 是否需要显式 TaskPlanner

**结论**：P0 不引入显式 TaskPlanner，继续采用模型自主决策。

**原因**：

- OpenHarness 已具备工具调用与并行执行能力
- 显式 Planner 会扩大系统边界，增加新的同步成本
- 如果未来上游出现成熟 Planner，应优先复用为插件或工具

### 8.2 CLI 任务在主对话还是子 Agent 执行

**结论**：两种方式都保留，由模型根据任务特征选择。

**规则**：

- 快速、上下文强相关的命令，优先由主对话直接执行
- 耗时、可并行、可独立监控的任务，优先交给子 Agent
- 主对话与子 Agent 可以并行工作，避免主线程空转等待

### 8.3 统一 Loop 与三层热插拔的定位

**结论**：三层热插拔是长期抽象；P0 优先扩展感知层和行动层，推理层尽量复用主干。

**约束**：

- “统一 Loop”是统一的**编排抽象**，不是复制一套 `query.py`（详见 2.2.1 节集成模式说明）
- 新增 Agent 类型实现差异层即可，不追求为抽象完整性重写主干
- `UnifiedAgentLoop` 主要承担异构编排职责，推理决策通过 `QueryEngineReasoning` 委托给现有主干

### 8.4 双向通信机制设计

**结论**：采用增量消息队列 + 分层通信，保持 Mailbox 为唯一权威消息来源。

**规则**：

- 所有通信采用增量消息追加模式，接收方按需拉取
- 系统层上报是轻量状态标志（`running` / `error` 等），正常时不占用上下文窗口
- 模型层通信是可选的富内容通信（错误详情、阶段性成果等），由模型自主决策是否使用
- 两层内容类型不同，不存在语义重叠和去重问题
- 不新增另一套独立持久消息队列

### 8.5 设备侧技术路线决策

这些技术选型仍有效，但详细对比移交到各自专项文档：

- PC GUI Agent：采用 `mss + pynput`，详见 [pc-agent-implementation.md](./pc-agent-implementation.md)
- Mobile GUI Agent：第一阶段以 `ADB/HDC` 为主，`Appium` 为可选增强，详见 [mobile-agent-implementation.md](./mobile-agent-implementation.md)
- Browser Agent：采用 `Playwright`，详见 [browser-agent-implementation.md](./browser-agent-implementation.md)

这些专项方案在接入时仍需遵守本文的主原则：实现可以独立，但接入路径应优先复用 OpenHarness 主干。

## 9. 系统级进阶特性（未来扩展）

本节讨论当前框架的进阶特性，属于未来扩展方向，不在第一阶段实现范围内。

### 9.1 系统级鲁棒性与容错恢复

**问题**：当主Agent决策失败、子Agent大面积失败、或系统进入不可恢复状态时，如何处理？

**讨论要点**：
- 2.7.2 节的错误处理只覆盖子Agent→主Agent的单层上报，如果主Agent自身决策失败或子Agent大面积失败，则缺少系统级的应对机制
- 需要系统级的故障检测、自动恢复、降级策略
- 可能涉及：心跳检测、故障转移、状态快照与回滚

**待讨论方向**（暂不深入）：
1. **故障检测机制**：如何检测系统级故障（非单Agent故障）
2. **自动恢复策略**：重启Agent？重置状态？切换到备用模型？
3. **降级策略**：当部分功能不可用时，如何优雅降级
4. **用户干预接口**：什么情况下必须要求用户介入

**结论**：属于第二阶段或更后期的特性，当前先记录，后续再详细设计。

---

### 9.2 系统自演进：Learning Loop（元学习）

**问题**：如何让 Agent 系统能够自我学习、自我改进，而不只是执行预设任务？

**核心思路**：Learning Loop 可以统一为 Agent Loop 的一种特殊形式——感知层读取执行历史和用户反馈，推理层分析成功/失败模式并生成改进建议，行动层修改代码、skill 或配置。它与任务执行 Agent 共享同一套三层抽象，但运行在不同层次（元学习层 vs 执行层）。

**关键设计约束**（参考 [EvoMap/evolver](https://github.com/EvoMap/evolver) 的演进协议思路）：

1. **可审计**：每次反思和改进建议都形成结构化的演进事件，支持追溯
2. **可回滚**：所有系统修改都经过版本控制，支持回退到任意历史状态
3. **人工复核**：高风险改动（如修改核心代码）应支持 review 模式，而非默认自动落地
4. **受保护边界**：核心主干代码设置保护区，自演进过程不直接覆写关键实现
5. **离线可运行**：核心能力不依赖外部网络服务

**实现方向**（待后续深入调研）：

- 反思机制：任务完成后自动分析执行轨迹
- 经验记忆：长期存储成功/失败案例，支持相似性检索
- 技能改进：基于使用效果优化 skill 实现
- 代码自修改：在沙箱环境中修改自身代码（最激进，需最严格的审计控制）

**代码位置（未来）**：`extended/meta_learning/`

**结论**：当前先实现基础 Agent Loop，Learning Loop 作为第二阶段或更后期的可选扩展。详细设计待独立文档。

---

**文档版本**：v2.0（2026-04-16）
**状态**：
- 已完成面向"解耦但持续跟上游"的结构性设计
- 补充了 UnifiedAgentLoop 与 QueryEngine 的集成模式、上下文管理、增量通信模型、并发约束与 Token 预算等关键设计
- 9.1-9.2：系统级进阶特性记录，待后续深入调研和实现