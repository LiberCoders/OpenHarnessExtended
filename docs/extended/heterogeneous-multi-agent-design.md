# 异构多Agent并行协作框架设计文档

## 1. 需求分析

### 1.1 场景描述

用户下发一个复杂任务后，主对话（Master Agent）需要：
1. **任务理解与分解**：分析任务结构，识别可并行执行的子任务
2. **异构子任务分发**：
   - **CLI任务**：可在本地直接通过命令行完成
   - **电脑UI任务**：需要截图→推理→操作→再截图的循环
   - **手机UI任务**：需要在移动设备上执行类似的操作循环
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
| **异构Agent支持** | 统一的Agent Loop架构，支持CLI/PC UI/Mobile/Browser等不同类型的Agent | P0 |
| **并行执行能力** | 支持模型在单轮中并行启动多个子Agent，主对话可同时执行本地任务 | P0 |
| **双向通信机制** | 框架层自动上报 + 模型层主动通信，支持Master查看子Agent状态 | P0 |
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

#### 1.3.1 已有能力及需要调整的部分

| 模块 | 已有能力 | 需要调整/扩展的部分 |
|------|---------|---------------------|
| **Agent Loop（engine/query.py）** | 观察→推理→行动循环<br>流式事件输出<br>工具并行执行 | ⚠️ 需要**提取核心Loop逻辑**封装为`UnifiedAgentLoop`<br>⚠️ 需要支持**热插拔三层组件**（Perception/Reasoning/Action）<br>⚠️ 当前Loop是文本对话专用，需要抽象为通用框架 |
| **多Agent协调（swarm/）** | subprocess/in_process/tmux/iterm2后端<br>Team/Teammate概念<br>Mailbox消息系统 | ⚠️ 需要扩展`spawn_agent`工具支持**agent_type参数**<br>⚠️ 需要扩展Mailbox支持**Master↔Agent双向通信**<br>⚠️ 需要增加框架层**自动上报机制**（不仅是Agent主动发送）<br>✅ 后端复用，生命周期管理复用 |
| **后台任务（tasks/）** | BackgroundTaskManager<br>stdin/stdout通信<br>任务ID管理 | ✅ 可直接复用作为CLI Agent的基础设施<br>⚠️ 需要扩展支持其他Agent类型的启动（UI/Mobile/Browser）<br>⚠️ 需要增加**Agent状态查询接口** |
| **通信机制** | StreamEvent流（主Agent内部）<br>Mailbox（子Agent间）<br>Channel Bus | ⚠️ 需要**统一为UnifiedMessageBus**<br>⚠️ 需要支持**消息类型扩展**（status/progress/error/query）<br>⚠️ 需要支持**广播和点对点**两种模式 |
| **工具系统（tools/）** | 43+工具实现<br>MCP协议支持 | ⚠️ 需要增加`spawn_agent`的**agent_type参数**<br>⚠️ 需要新增`query_agent_status`工具<br>⚠️ 需要新增`send_message_to_agent`工具<br>⚠️ 需要新增`report_error`工具 |
| **Prompt系统（prompts/）** | System Prompt组装<br>上下文管理 | ⚠️ 需要增加**任务分解指导**<br>⚠️ 需要增加**并行执行指导**<br>⚠️ 需要增加**CLI任务执行方式选择指导** |
| **集成入口** | `cli.py`启动入口<br>`runtime.py`运行时构建 | ⚠️ 增加`--enable-extended`参数<br>⚠️ **扩展RuntimeBundle**加载extended组件（详见4.2节）<br>⚠️ 保持向后兼容（不启用时不加载extended） |

#### 1.3.2 模块设计与解耦策略

**设计原则**：尽量将新功能放在`extended/`目录下，最小化对OpenHarness核心代码的改动，便于与上游同步。

**新建模块（完全独立，放在extended目录）**：

| 模块 | 路径 | 描述 | 优先级 |
|------|------|------|--------|
| 统一Agent Loop | `extended/engine/unified_loop.py` | 三层热插拔的Agent Loop框架 | P0 |
| 三层抽象接口 | `extended/agents/base.py` | PerceptionProvider/ReasoningEngine/ActionExecutor | P0 |
| CLI Agent实现 | `extended/agents/cli/` | CLI Agent的三层具体实现 | P0 |
| PC UI Agent实现 | `extended/agents/pc/` | PC UI Agent的三层实现 | P1 |
| Mobile Agent实现 | `extended/agents/mobile/` | Mobile Agent的三层实现 | P1 |
| Browser Agent实现 | `extended/agents/browser/` | Browser Agent的三层实现 | P1 |
| 统一消息总线 | `extended/bus/unified_bus.py` | 扩展Mailbox的双向通信 | P0 |
| Agent生命周期 | `extended/lifecycle/manager.py` | 封装现有生命周期管理 | P0 |
| 进度监控 | `extended/monitor/progress.py` | 多Agent进度聚合 | P1 |

**扩展现有模块（最小化改动，使用继承/组合）**：

| 现有模块 | 扩展方式 | 改动内容 |
|---------|---------|---------|
| `tools/agent_tool.py` | 继承扩展 | 创建`extended/tools/spawn_agent_tool.py`，继承并添加`agent_type`参数 |
| `swarm/mailbox.py` | 组合扩展 | 创建`extended/bus/mailbox_adapter.py`，包装并扩展Mailbox功能 |
| `prompts/system_prompt.py` | 配置扩展 | 创建`extended/prompts/extended_system_prompt.py`，添加额外指导 |
| `cli.py` | 入口扩展 | 创建`extended/cli_ext.py`，添加extended专用命令行参数 |

**复用无需改动的模块**：
- ✅ `tasks/manager.py` - 直接复用
- ✅ `swarm/backends/` - 直接复用
- ✅ `api/` - 直接复用
- ✅ `tools/registry.py` - 直接复用
- ✅ `engine/query.py` - 参考其Loop逻辑，但不直接修改

#### 1.3.3 与上游OpenHarness的同步策略

**解耦原则**：
1. **不修改OpenHarness核心文件**：除非绝对必要，否则不改动`src/openharness/`下的现有文件
2. **使用继承和组合**：通过继承现有类或包装现有功能来扩展
3. **配置驱动**：通过配置文件和依赖注入集成，而非硬编码
4. **插件化设计**：extended模块作为OpenHarness的插件运行

**同步便利性**：
- `sync-upstream.bat`可以安全地合并OpenHarness更新
- extended目录独立，不会与上游代码冲突
- 只有明确的扩展点（如工具注册、prompt组装）会与上游交互
- 使用版本兼容性检查，确保扩展与上游API兼容

**冲突处理预案**：
- 如果上游大幅重构了Loop逻辑，我们需要同步调整`extended/engine/unified_loop.py`
- 如果上游修改了Mailbox API，我们需要同步调整`extended/bus/mailbox_adapter.py`
- 核心设计（三层热插拔、双向通信）在extended中独立维护

**注**：关于任务规划能力，决定采用**模型自主决策**方式（通过System Prompt引导），不额外开发显式TaskPlanner模块。详见第8节讨论记录8.1。

## 2. 方案设计

### 2.1 核心设计思想：统一的Agent Loop + 三层热插拔

**核心洞察**：所有Agent本质上都是 **感知 → 思考 → 行动** 的循环，但**每一层**的实现都可以根据Agent类型定制。

```
┌──────────────────────────────────────────────────────────────────┐
│                     Unified Agent Loop                         │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │  Perception  │ →  │   Reasoning  │ →  │    Action    │       │
│  │    感知层     │    │    推理层     │    │    行动层     │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│        ↑                  ↑                  ↑                │
│        │                  │                  │                │
│        └──────────────────┴──────────────────┘                │
│                     (三层都可热插拔)                            │
└──────────────────────────────────────────────────────────────────┘
```

**关键理解**：不是只有感知层需要热插拔，**推理层**和**行动层**同样需要针对不同Agent类型定制。

**三层热插拔的具体差异**：

| Agent类型 | 感知层差异 | 推理层差异 | 行动层差异 |
|-----------|-----------|-----------|-----------|
| **普通对话** | 上下文历史 | 标准文本推理 | 文本回复 |
| **CLI Agent** | 命令输出 | Prompt：命令行专家 | Shell执行 |
| **PC UI Agent** | 屏幕截图 | **视觉+文本推理**<br>Prompt：GUI操作专家<br>模型：GPT-4V | 鼠标/键盘控制 |
| **Mobile Agent** | 设备截图 | **视觉+文本推理**<br>Prompt：移动端UI专家 | ADB命令 |
| **Browser Agent** | DOM+截图 | **结构化推理**<br>Prompt：Web自动化专家 | Playwright API |
| **传感器Agent** | 时序数据 | **数据分析推理** | 控制指令 |

**三层定制化细节**：

1. **感知层 (PerceptionProvider)**
   - 输入方式：截图 vs 命令输出 vs DOM树 vs 传感器
   - 数据格式：PIL.Image vs str vs dict

2. **推理层 (ReasoningEngine)**
   - **Prompt拼装**：不同Agent有不同System Prompt
     - CLI："你是命令行专家..."
     - PC UI："你是GUI操作助手，分析截图..."
     - Browser："DOM结构如下..."
   - **模型选择**：文本模型(GPT-4) vs 视觉模型(GPT-4V)
   - **动作空间**：模型输出格式不同
     - CLI：`{"command": "ls -la"}`
     - PC UI：`{"action": "click", "x": 100, "y": 200}`
     - Browser：`{"action": "click", "selector": "#btn"}`

3. **行动层 (ActionExecutor)**
   - **执行器**：subprocess vs pynput vs adb vs Playwright
   - **反馈收集**：返回码 vs 截图验证 vs 设备响应
   - **错误处理**：命令失败 vs 点击失败 vs 元素找不到

**Agent分类：两个维度**

Agent可以从两个维度分类：**设备/交互维度**（从哪感知、如何行动）和**工作机制维度**（如何运行、协作方式）。

#### 维度1：设备/交互类型（Where & How to Interact）

| Agent类型 | 感知(Perception) | 思考(Reasoning) | 行动(Action) | 典型场景 |
|-----------|-----------------|-----------------|-------------|---------|
| **普通对话** | 上下文历史、用户输入 | 标准LLM推理 | 文本回复、工具调用 | 通用问答 |
| **CLI Agent** | 命令输出、文件状态 | 标准LLM推理 | Shell命令执行 | 编译、脚本、运维 |
| **PC UI Agent** | 截图 + UI元素识别 | 视觉理解+推理 | 鼠标/键盘操作 | 桌面应用操作 |
| **Mobile UI Agent** | 手机截图 + 设备状态 | 视觉理解+推理 | 触控操作(adb/appium) | 手机APP测试 |
| **浏览器Agent** | DOM状态 + 页面截图 | 视觉+结构推理 | 点击/输入/导航 | 网页自动化 |
| **智能眼镜** | 摄像头画面 + 环境视觉 | 视觉理解+推理 | 语音/手势反馈 | AR助手 |
| **智能手表** | 传感器数据(心率/步数/位置) | 数据分析推理 | 振动/屏幕显示/语音 | 健康监测 |
| **IoT设备** | 设备状态(温度/湿度/电量) | 阈值判断/预测 | 控制指令下发 | 智能家居 |
| **车载系统** | 车辆状态(速度/油量/路况) | 驾驶辅助推理 | 语音提醒/控制 | 智能驾驶 |
| **无人机** | 摄像头 + GPS + 传感器 | 空间推理+导航 | 飞行控制/拍摄 | 航拍/巡检 |

#### 维度2：工作机制类型（How to Work & Collaborate）

| 工作机制 | 感知特点 | 执行模式 | 适用场景 | 与普通Agent的关系 |
|---------|---------|---------|---------|------------------|
| **即时响应型** | 事件触发（用户输入/系统事件） | 收到请求→立即处理→返回结果 | 通用任务处理 | 最常见的基础模式 |
| **持续监控型** | 7x24持续采集数据流 | 持续运行→阈值判断→异常时行动 | 系统监控、安全监控、健康监测 | 可叠加到CLI/UI等Agent上 |
| **人机协作型** | 用户输入 + 协作状态 | 自主执行，但用户可随时介入交互 | 需要人类灵活介入的场景 | 可叠加到任何设备类型 |
| **批量处理型** | 一次性读取大量数据 | 批量处理→汇总结果→统一输出 | 数据分析、日志处理 | 独立任务模式 |
| **主动探索型** | 自主收集环境信息 | 主动采集→分析→给用户建议 | 智能推荐、用户画像分析 | 持续监控的智能化版本 |
| **被动服务型** | 等待调用 | 接收到请求→执行→返回 | API服务、工具函数 | 最简化的Agent模式 |

#### 组合示例

实际Agent往往是两个维度的组合：

```
持续监控 + PC UI = 桌面自动化监控助手
├─ 感知：截图 + UI状态 + 7x24采集
├─ 检测：UI异常/性能问题
└─ 行动：自动修复或报警

人机协作 + Mobile UI = 手机测试协作Agent
├─ 感知：手机截图 + 测试步骤状态 + 用户输入
├─ 模式：Agent自主执行测试，测试员可随时介入指导
└─ 行动：执行触控操作

主动探索 + CLI = 智能运维Agent
├─ 感知：持续收集服务器日志/指标
├─ 分析：学习用户习惯，主动发现问题
└─ 建议：给用户优化建议
```

**关键优势**：
- **Loop统一**：核心逻辑不变，只需热插拔感知模块
- **易于扩展**：新增Agent类型只需实现新的PerceptionProvider
- **代码复用**：思考层和行动层可复用现有实现

### 2.2 统一Agent Loop架构

```python
# extended/engine/unified_loop.py
class UnifiedAgentLoop:
    """Hot-swappable Agent Loop with pluggable perception, reasoning, and action."""

    def __init__(
        self,
        perception_provider: PerceptionProvider,  # 热插拔点1：CLI/UI/Mobile各有不同
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
    """Mobile UI操控：手机截图 + 设备状态."""

    async def observe(self) -> Observation:
        screenshot = await self.adb_screenshot()
        return Observation(
            text=await self.get_accessibility_tree(),
            visual=screenshot,
            metadata={"device_info": self.get_device_info()}
        )


class SensorPerception(PerceptionProvider):
    """IoT/智能手表：读取传感器数据."""

    async def observe(self) -> Observation:
        sensor_data = await self.read_sensors()
        return Observation(
            text=sensor_data.describe(),
            visual=None,
            metadata={"sensor_data": sensor_data}
        )


# ========== 可复用的思考和行动层 ==========

class ReasoningEngine(ABC):
    """
    推理层：热插拔组件，不同Agent类型有不同实现.

    定制化点：
    - System Prompt构建（CLI专家 vs GUI专家 vs Web专家）
    - 模型选择（GPT-4文本 vs GPT-4V视觉 vs 专用模型）
    - 动作空间定义（Shell命令 vs 鼠标键盘 vs Playwright API）
    - 输出格式解析（不同Agent输出不同结构化格式）
    """

    @abstractmethod
    async def think(
        self,
        observation: Observation,
        context: ConversationHistory
    ) -> Thought:
        """
        推理决策.
        包括：构建Prompt -> 调用LLM -> 解析输出为结构化Action
        """
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
    async def execute(self, thought: Thought) -> ExecutionResult:
        """
        执行操作并收集反馈.
        包括：执行动作 -> 收集结果 -> 错误处理
        """
        pass

    async def execute(self, actions: list[Action]) -> ActionResult:
        results = []
        for action in actions:
            tool = self.tool_registry.get(action.tool_name)
            result = await tool.execute(action.parameters)
            results.append(result)
        return ActionResult(results)
```

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
    def create_pc_ui_agent(task: str) -> UnifiedAgentLoop:
        """PC UI Agent: 感知 = 截图 + UI分析."""
        return UnifiedAgentLoop(
            perception=ScreenshotPerception(),
            reasoning=VisionEnabledReasoning(model="gpt-4-vision"),
            action=DesktopActionExecutor(),
            context=InMemoryContext(),
        )

    @staticmethod
    def create_mobile_agent(task: str) -> UnifiedAgentLoop:
        """Mobile UI Agent: 感知 = 手机截图."""
        return UnifiedAgentLoop(
            perception=MobilePerception(),
            reasoning=VisionEnabledReasoning(model="gpt-4-vision"),
            action=MobileActionExecutor(),
            context=InMemoryContext(),
        )

    @staticmethod
    def create_sensor_agent(device_type: str) -> UnifiedAgentLoop:
        """IoT/可穿戴设备 Agent: 感知 = 传感器."""
        return UnifiedAgentLoop(
            perception=SensorPerception(device_type),
            reasoning=DataAnalysisReasoning(),
            action=DeviceControlExecutor(),
            context=InMemoryContext(),
        )
```

### 2.4 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Master Agent (主对话)                    │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │ System Prompt    │  │ Progress Monitor │                │
│  │ (任务分解指导)   │  │    进度监控      │                │
│  └────────┬─────────┘  └──────────────────┘                │
│           │ 主对话可同时：                                     │
│           │ 1. 直接执行CLI (shell_tool)                      │
│           │ 2. 并行启动子Agent (spawn_agent)                  │
└───────────┼──────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Unified Message Bus                      │
│                  (统一消息总线 - 扩展Mailbox)                  │
└─────────────┬─────────────────────────────┬───────────────────┘
              │                             │
    ┌─────────┴─────────┐       ┌───────────┴────────────┐
    │                   │       │                        │
    ▼                   ▼       ▼                        ▼
┌─────────┐      ┌──────────┐ ┌──────────┐      ┌──────────────┐
│ CLI Agent│      │ PC UI    │ │ Mobile UI│      │   Sensor     │
│ (子进程) │      │ Agent    │ │ Agent    │      │   Agent      │
│         │      │(截图循环) │ │(远程控制)│      │(数据采集)   │
└────┬────┘      └────┬─────┘ └────┬─────┘      └──────┬───────┘
     │                │            │                   │
     │  ┌─────────────┴────────────┴───────────────────┘
     │  │
     │  ▼
     │ ┌───────────────────────────────────────────┐
     │ │      Unified Agent Loop Core               │
     │ │  ┌─────────┐  ┌─────────┐  ┌─────────┐     │
     │ │  │Perception│→ │Reasoning│→ │ Action  │     │
     │ │  │(热插拔)  │  │(可复用) │  │(可复用) │     │
     │ │  └─────────┘  └─────────┘  └─────────┘     │
     │ └───────────────────────────────────────────┘
     │                ↑
     │                │ 热插拔不同的PerceptionProvider
     └────────────────┘
              (CLI/PC/Mobile/Sensor各自实现)
```

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
   - `type="pc_ui"`：需要图形界面交互（点击、输入、截图验证等）
   - `type="mobile_ui"`：需要在手机上执行的操作

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
3. 并行启动UI类子Agent（PC/Mobile）

最大化利用并行能力，主对话不空闲等待。

**示例场景**：
- "构建项目并部署到测试环境" → 可并行：本地构建（CLI） + 准备部署环境（CLI）
- "在电脑上配置环境，同时在手机上安装APP" →
  - 主对话：git status（快速查看）
  - 后台：spawn_agent(type="cli", task="环境配置") + spawn_agent(type="mobile_ui", task="安装APP")
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

    def __init__(self):
        self._active_agents: dict[str, UnifiedAgentLoop] = {}
        self._message_bus: UnifiedMessageBus
        self._factory = HeterogeneousAgentFactory()

    async def spawn_agent(
        self,
        agent_type: Literal["cli", "pc_ui", "mobile_ui", "sensor"],
        task_description: str,
        context: dict
    ) -> str:
        """Spawn a sub-agent and return agent_id."""
        # 使用工厂创建对应类型的Agent
        agent = self._factory.create(agent_type, task_description)
        agent_id = generate_agent_id()
        self._active_agents[agent_id] = agent
        # 启动Agent的Loop
        asyncio.create_task(agent.run_loop())
        return agent_id

    async def get_agent_status(self, agent_id: str) -> AgentStatus:
        """Query agent execution status."""

    async def send_message_to_agent(self, agent_id: str, message: str) -> None:
        """Send message to a specific agent."""

    async def terminate_agent(self, agent_id: str) -> TaskResult:
        """Terminate agent and return final result."""
```

### 2.7 双向通信架构

采用**分层通信机制**：模型主动通信（可选）+ 框架被动上报（硬性机制）

#### 2.7.1 分层设计

```
┌─────────────────────────────────────────────────────────────────┐
│                     Communication Layers                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Layer 2: Framework-Level Passive Reporting (硬性机制)     │    │
│  │  - 自动记录所有Agent执行过程                               │    │
│  │  - 定期向Master Agent上报状态快照                          │    │
│  │  - 可配置开关和参数（频率/内容/阈值）                        │    │
│  │  - 不写入普通Agent消息通道，只发给Master                    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              ↓                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Layer 1: Model-Level Active Communication (模型主动)    │    │
│  │  - Agent模型主动调用工具查看其他Agent状态                  │    │
│  │  - Agent模型主动发送消息给其他Agent                       │    │
│  │  - 完全可选，Agent可以选择不看不发                       │    │
│  │  - 对普通Agent无强制要求，主Agent例外（必须关注全局）       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              ↓                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Layer 0: Unified Message Bus (基础设施)                   │    │
│  │  - 基于Mailbox的统一消息通道                              │    │
│  │  - 支持点对点和广播                                        │    │
│  │  - 所有通信的底层支撑                                      │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
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

#### 2.7.3 框架层通信（硬性机制）

**框架自动记录和上报**（无需模型干预）：

```python
class FrameworkReportingManager:
    """Framework-level passive reporting mechanism.
    
    This runs independently of the agent model's decisions.
    """
    
    def __init__(self):
        self.reporting_config: ReportingConfig
        self.execution_log: ExecutionLogStore
    
    async def on_agent_turn_complete(self, agent_id: str, turn_result: TurnResult):
        """Hook: called after each agent turn."""
        # 1. 记录到执行日志（本地存储）
        await self.execution_log.append(agent_id, turn_result)
        
        # 2. 检查是否需要上报给Master（基于配置）
        if self.should_report_to_master(agent_id):
            report = self.generate_report(agent_id)
            # 插入到Master Agent的消息队列（作为系统事件）
            await self.insert_to_master_inbox(
                type="framework_report",
                agent_id=agent_id,
                data=report,
                importance=self.calculate_importance(report)
            )
    
    def should_report_to_master(self, agent_id: str) -> bool:
        """Check if should report based on config."""
        config = self.reporting_config
        
        # 条件1：固定时间间隔
        if time_since_last_report(agent_id) >= config.interval_seconds:
            return True
        
        # 条件2：重要事件（完成/错误/异常长时间）
        if has_important_event(agent_id):
            return True
        
        # 条件3：Agent长时间无响应
        if is_agent_idle_too_long(agent_id, config.idle_threshold):
            return True
        
        return False
```

**配置示例**：

```yaml
# config/reporting.yaml
framework_reporting:
  enabled: true  # 总开关
  
  # 定期上报配置
  interval_reporting:
    enabled: true
    interval_seconds: 30  # 每30秒上报一次
    content_level: "summary"  # "minimal" | "summary" | "detailed" | "full"
  
  # 事件触发的上报
  event_reporting:
    enabled: true
    on_completion: true  # 任务完成时上报
    on_error: true       # 出错时上报
    on_stall: true       # 卡顿时上报
  
  # 上报内容配置
  report_content:
    include_steps_count: true
    include_last_action: true
    include_last_output: true
    include_progress_percentage: true
    include_execution_time: true
    include_screenshot_thumbnail: false  # 可选，减小数据量
  
  # Master Agent特殊处理
  master_agent:
    soft_reminder: true  # 将框架报告作为软提醒插入消息流
    reminder_format: "[System] Agent {id} update: {summary}"
    allow_ignore: true   # Master可以选择忽略不处理
```

**框架报告示例**（插入到主Agent消息流）：

```markdown
[用户消息]: 帮我同时在电脑和手机上下载安装最新版本

[模型思考]: 这是一个可以并行的任务...

[工具调用]: spawn_agent(type="pc_ui", task="电脑下载安装") 
            spawn_agent(type="mobile_ui", task="手机下载安装")

[等待...]

[System Event - Framework Report]: 
Agent "pc_ui_001" update: 
- Status: running
- Steps completed: 5
- Last action: 点击下载按钮
- Last output: "下载进度 45%"
- Running for: 45 seconds
- Progress: 45%

[模型思考]: 看起来正常，继续等待...

[System Event - Framework Report]:
Agent "mobile_ui_002" update:
- Status: error
- Steps completed: 2
- Last action: 点击App Store
- Error: "网络连接失败"
- Running for: 30 seconds

[模型思考]: 手机端出错了，我需要询问用户是否重试还是跳过...
```

#### 2.7.4 统一消息总线基础设施

底层基于Mailbox的通信基础设施：

```python
class UnifiedMessageBus:
    """Unified message bus for all agent communication."""

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._mailbox_root = Path.home() / ".openharness" / "sessions" / session_id

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
    sender: str           # agent_id or "framework" or "master"
    recipient: str        # agent_id or "broadcast"
    layer: Literal["model", "framework"]  # 标识是哪一层产生的消息
    type: MessageType
    payload: dict
```

**消息类型定义**：

| 类型 | 层 | 方向 | 说明 |
|------|-----|------|------|
| agent_status_query | model | Any→Any | Agent主动查询其他Agent |
| agent_message | model | Any→Any | Agent主动发送消息 |
| framework_status_report | framework | Framework→Master | 框架定期上报 |
| framework_completion_report | framework | Framework→Master | 框架任务完成上报 |
| framework_error_report | framework | Framework→Master | 框架错误上报 |
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
    2. Perform UI operations on PC (with screenshot loop)
    3. Control mobile device remotely
    4. Interact with IoT/sensor devices

    For parallel tasks, call this tool multiple times in the same turn.
    """

    agent_type: Literal["cli", "pc_ui", "mobile_ui", "sensor"] = Field(
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

**决策**：采用**感知-思考-行动**的统一Loop架构，通过热插拔感知层支持异构Agent

**理由**：
1. **本质统一**：所有Agent都是观察环境→推理决策→执行行动的循环
2. **易于扩展**：新增Agent类型只需实现PerceptionProvider
3. **代码复用**：思考层和行动层可复用现有实现
4. **面向未来**：支持智能眼镜、IoT等新型Agent

**实现要点**：
- `UnifiedAgentLoop`作为核心框架
- `PerceptionProvider`作为热插拔接口
- 工厂模式创建不同类型Agent

### 3.2 通信协议选择

**决策**：扩展现有Mailbox文件系统方案

**理由**：
- 已有成熟实现
- 原子写入保证消息不丢失
- 支持异步读写
- 易于调试（可直接查看文件）

**扩展内容**：
- 增加消息类型字段
- 增加优先级支持
- 增加广播机制

### 3.3 感知层实现策略

**PC UI Agent**：
- 截图工具：PyAutoGUI / MSS / Windows API
- UI分析：Accessibility API + 可选的OCR/视觉模型
- 行动执行：PyAutoGUI（鼠标/键盘）

**Mobile UI Agent**：
- 截图工具：adb screencap / Appium
- UI分析：Android Accessibility / UIAutomator
- 行动执行：adb shell input / Appium

**CLI Agent**：
- 感知：命令输出捕获、文件系统状态
- 行动：subprocess执行

**Sensor Agent**（未来扩展）：
- 感知：设备API / MQTT / 蓝牙
- 行动：控制指令下发

## 4. 与现有代码的集成点（解耦设计）

**设计原则**：
1. 尽量将新功能放在`extended/`目录下，保持与OpenHarness核心代码的解耦
2. 对现有模块的扩展采用继承、组合、适配器模式，而非直接修改
3. 使用依赖注入和配置驱动的方式集成，便于后续同步上游更新

### 4.1 新建模块（extended目录，完全独立）

| extended模块 | 功能 | 依赖的OpenHarness模块 |
|--------------|------|----------------------|
| `extended/engine/unified_loop.py` | 统一Agent Loop框架 | 参考`engine/query.py`的Loop逻辑，但不直接修改 |
| `extended/agents/base.py` | 三层抽象接口（Perception/Reasoning/Action） | 独立定义，不依赖现有Agent实现 |
| `extended/agents/cli/` | CLI Agent三层实现 | 组合使用`tasks/manager.py` |
| `extended/agents/pc/` | PC UI Agent三层实现 | 独立实现，使用mss/pynput库 |
| `extended/agents/mobile/` | Mobile Agent三层实现 | 独立实现，使用adb/hdc命令 |
| `extended/agents/browser/` | Browser Agent三层实现 | 独立实现，使用Playwright |
| `extended/bus/unified_bus.py` | 统一消息总线 | 适配器模式包装`swarm/mailbox.py` |
| `extended/lifecycle/manager.py` | Agent生命周期管理 | 组合使用`swarm/backends/` |
| `extended/tools/spawn_agent_tool.py` | 扩展的spawn_agent工具 | 继承并扩展现有AgentTool |
| `extended/prompts/extended_prompts.py` | 扩展的System Prompt | 配置方式扩展现有prompts |

### 4.2 扩展现有模块（最小化改动，使用继承/适配器）

| 现有模块 | 扩展方式 | 改动说明 |
|---------|---------|---------|
| `tools/agent_tool.py` | **继承扩展** | 创建`extended/tools/spawn_agent_tool.py`，继承AgentTool并添加`agent_type`参数 |
| `swarm/mailbox.py` | **适配器模式** | 创建`extended/bus/mailbox_adapter.py`，包装Mailbox并扩展双向通信功能 |
| `prompts/system_prompt.py` | **配置扩展** | 创建`extended/prompts/extended_prompts.py`，在现有prompt基础上追加extended指导 |
| `cli.py` | **入口扩展** | 创建`extended/cli_ext.py`，添加`--enable-extended`等参数，主入口判断是否加载extended |
| `ui/runtime.py` | **运行时扩展** | 创建`extended/runtime_ext.py`，继承RuntimeBundle并添加extended组件 |

### 4.3 直接复用无需改动的模块

| 模块 | 复用方式 | 说明 |
|------|---------|------|
| `tasks/manager.py` | 直接导入使用 | CLI Agent的后台执行直接复用 |
| `swarm/backends/subprocess_backend.py` | 直接导入使用 | Agent启动后端直接复用 |
| `api/` | 直接导入使用 | LLM API调用直接复用 |
| `tools/registry.py` | 直接导入使用 | 工具注册机制直接复用 |
| `config/` | 直接导入使用 | 配置系统直接复用 |

### 4.4 集成架构与实现方案

#### 4.4.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenHarness Core                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  cli.py      │    │ runtime.py   │    │  swarm/      │   │
│  │  (入口)       │───→│ (运行时构建)  │───→│  (后端启动)   │   │
│  └──────────────┘    └──────────────┘    └──────┬───────┘   │
└─────────────────────────────────────────────────┼───────────┘
                                                   │
                                                   ▼
┌────────────────────────────────────────────────────────────────┐
│                      extended/  (我们的代码)                     │
│                                                                │
│  ┌────────────────────────────────────────────────────────┐ │
│  │               ExtendedRuntimeBundle                     │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │           UnifiedAgentLoop (统一Loop框架)         │  │ │
│  │  │  ┌───────────┐  ┌───────────┐  ┌───────────┐      │  │ │
│  │  │  │Perception │→ │ Reasoning │→ │  Action   │      │  │ │
│  │  │  │  (截图等) │  │ (LLM推理)  │  │ (执行操作) │      │  │ │
│  │  │  └───────────┘  └───────────┘  └───────────┘      │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                           ↑                           │ │
│  │                           │ 通过总线通信                │ │
│  │                           ↓                           │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │           UnifiedMessageBus (消息总线)            │  │ │
│  │  │  ┌────────────────────────────────────────────┐   │  │ │
│  │  │  │  适配器模式包装 openharness.swarm.mailbox  │   │  │ │
│  │  │  │  - 支持Master↔Agent双向消息                 │   │  │ │
│  │  │  │  - 支持广播和点对点                         │   │  │ │
│  │  │  │  - 支持框架层自动上报                       │   │  │ │
│  │  │  └────────────────────────────────────────────┘   │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                           ↑                           │ │
│  │                           │ 启动和管理                │ │
│  │                           ↓                           │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │         AgentLifecycleManager (生命周期)          │  │ │
│  │  │  ┌────────────────────────────────────────────┐   │  │ │
│  │  │  │  组合使用 openharness.swarm.backends       │   │  │ │
│  │  │  │  - spawn(): 启动Agent子进程               │   │  │ │
│  │  │  │  - get_status(): 查询状态                  │   │  │ │
│  │  │  │  - terminate(): 终止Agent                   │   │  │ │
│  │  │  └────────────────────────────────────────────┘   │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

#### 4.4.2 Runtime扩展模式（推荐）

通过继承OpenHarness的RuntimeBundle来集成：

```python
# extended/runtime_ext.py

from openharness.ui.runtime import RuntimeBundle
from extended.engine.unified_loop import UnifiedAgentLoop
from extended.agents.factory import HeterogeneousAgentFactory
from extended.bus.unified_bus import UnifiedMessageBus
from extended.lifecycle.manager import AgentLifecycleManager

class ExtendedRuntimeBundle(RuntimeBundle):
    """扩展的运行时，支持异构Agent."""
    
    def __init__(self, *args, enable_extended=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_extended = enable_extended
        
        if enable_extended:
            # 初始化消息总线（适配器模式包装现有Mailbox）
            self.message_bus = UnifiedMessageBus(
                session_id=self.session_id,
                mailbox=getattr(self, 'mailbox', None)  # 复用现有Mailbox
            )
            
            # 初始化生命周期管理器（组合使用现有后端）
            self.lifecycle_manager = AgentLifecycleManager(
                backend_registry=self.backend_registry,
                message_bus=self.message_bus
            )
            
            # Agent工厂
            self.agent_factory = HeterogeneousAgentFactory(
                api_client=self.api_client,
                message_bus=self.message_bus
            )
    
    async def spawn_heterogeneous_agent(self, agent_type: str, task: str, **kwargs):
        """启动异构Agent（新增方法）."""
        if not self.enable_extended:
            raise RuntimeError("Extended functionality not enabled")
        
        return await self.lifecycle_manager.spawn_agent(
            agent_type=agent_type,
            task_description=task,
            **kwargs
        )
```

#### 4.4.3 Adapter模式（备选）

如果不便继承，可使用Adapter包装现有Runtime：

```python
# extended/adapter.py

class OpenHarnessAdapter:
    """将UnifiedAgentLoop适配到OpenHarness现有框架."""
    
    def __init__(self, runtime_bundle: RuntimeBundle):
        self.runtime = runtime_bundle
        # 复用runtime中的组件
        self.api_client = runtime_bundle.api_client
        self.tool_registry = runtime_bundle.tool_registry
        self.backend_registry = getattr(runtime_bundle, 'backend_registry', None)
    
    def create_agent(self, agent_type: str, task: str) -> UnifiedAgentLoop:
        """创建特定类型的Agent."""
        from extended.agents.factory import HeterogeneousAgentFactory
        
        # 根据agent_type创建对应的Perception/Reasoning/Action
        agent = HeterogeneousAgentFactory.create(
            agent_type=agent_type,
            api_client=self.api_client
        )
        return agent
```

#### 4.4.4 消息总线（UnifiedMessageBus）实现

```python
# extended/bus/unified_bus.py

from openharness.swarm.mailbox import Mailbox

class UnifiedMessageBus:
    """
    统一消息总线，包装OpenHarness的Mailbox，扩展双向通信能力.
    """
    
    def __init__(self, session_id: str, mailbox: Optional[Mailbox] = None):
        self.session_id = session_id
        # 如果传入现有Mailbox，包装它；否则创建新的
        self._mailbox = mailbox or Mailbox(session_id)
        self._master_inbox = []  # Master的消息队列（扩展）
    
    async def send_to_agent(self, agent_id: str, message: Message) -> None:
        """Master发送消息给Agent."""
        # 使用底层Mailbox
        await self._mailbox.send(agent_id, message)
    
    async def send_to_master(self, agent_id: str, message: Message) -> None:
        """Agent发送消息给Master（扩展功能）."""
        message.sender = agent_id
        message.recipient = "master"
        self._master_inbox.append(message)
    
    async def get_master_messages(self) -> List[Message]:
        """Master获取发给它的消息（包括框架上报）."""
        messages = self._master_inbox.copy()
        self._master_inbox.clear()
        return messages
    
    async def broadcast(self, message: Message) -> None:
        """广播给所有Agent."""
        for agent_id in self._mailbox.list_agents():
            await self.send_to_agent(agent_id, message)
```

#### 4.4.5 生命周期管理（AgentLifecycleManager）实现

```python
# extended/lifecycle/manager.py

from openharness.swarm.backends import get_backend_registry

class AgentLifecycleManager:
    """
    Agent生命周期管理器.
    组合使用OpenHarness的swarm/backends/，但提供更高级的接口.
    """
    
    def __init__(self, backend_registry=None, message_bus=None):
        # 复用OpenHarness的后端注册表
        self._backends = backend_registry or get_backend_registry()
        self._message_bus = message_bus
        self._active_agents: Dict[str, AgentInstance] = {}
    
    async def spawn_agent(
        self,
        agent_type: str,           # "cli", "pc_ui", "mobile_ui", "browser"
        task_description: str,
        cwd: str,
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """启动一个Agent."""
        # 1. 选择后端（复用OpenHarness逻辑）
        backend = self._backends.get_executor("subprocess")
        
        # 2. 构建配置
        config = TeammateSpawnConfig(
            name=f"{agent_type}_{uuid.uuid4().hex[:8]}",
            agent_type=agent_type,
            prompt=task_description,
            cwd=cwd,
            model=model,
        )
        
        # 3. 启动（复用OpenHarness的backend逻辑）
        result = await backend.spawn(config)
        agent_id = result.agent_id
        
        # 4. 创建Agent实例（使用我们的UnifiedAgentLoop）
        from extended.agents.factory import HeterogeneousAgentFactory
        agent = HeterogeneousAgentFactory.create(agent_type, agent_id)
        self._active_agents[agent_id] = agent
        
        return agent_id
    
    async def get_status(self, agent_id: str) -> AgentStatus:
        """查询Agent状态."""
        agent = self._active_agents.get(agent_id)
        if not agent:
            return AgentStatus(status="unknown")
        return await agent.get_status()
    
    async def terminate(self, agent_id: str) -> None:
        """终止Agent."""
        agent = self._active_agents.pop(agent_id, None)
        if agent:
            await agent.terminate()
```

#### 4.4.6 集成入口（最小化改动）

```python
# cli.py 或 runtime.py 中的集成点

# 尝试导入extended模块（如果不存在也不影响核心功能）
try:
    from extended.runtime_ext import ExtendedRuntimeBundle
    EXTENDED_AVAILABLE = True
except ImportError:
    EXTENDED_AVAILABLE = False

def build_runtime(enable_extended=False):
    """构建运行时，支持可选的extended功能."""
    if enable_extended and EXTENDED_AVAILABLE:
        # 使用extended运行时
        return ExtendedRuntimeBundle(enable_extended=True)
    else:
        # 使用标准运行时
        from openharness.ui.runtime import RuntimeBundle
        return RuntimeBundle()

# 命令行参数扩展（在cli.py中添加）
@app.callback()
def main(
    ctx: typer.Context,
    enable_extended: bool = typer.Option(
        False, "--enable-extended",
        help="启用heterogeneous multi-agent扩展功能"
    ),
    # ... 其他参数
):
    """启用扩展功能."""
    if enable_extended:
        ctx.obj = build_runtime(enable_extended=True)
```

**好处**：
- OpenHarness核心代码无需感知extended的存在
- 用户可以通过`--enable-extended`参数启用扩展功能
- 即使extended模块有问题，也不会影响核心功能
- 便于`sync-upstream.bat`安全地合并上游更新

## 5. 实现路径（解耦开发）

**开发原则**：
- 所有新功能在`extended/`目录下独立开发
- 每阶段都在独立目录完成，可单独测试
- 随时可安全同步上游OpenHarness更新

### 5.1 阶段一：建立extended目录 + CLI Agent（2周）

**目标**：在`extended/`目录下建立基础框架，实现CLI Agent

1. **建立目录结构**
   ```
   extended/
   ├── engine/unified_loop.py      # 统一Loop框架
   ├── agents/
   │   ├── base.py                  # 三层抽象
   │   └── cli/
   │       ├── perception.py        # 命令输出感知
   │       ├── reasoning.py         # CLI专用推理
   │       └── action.py            # Shell执行
   ├── bus/unified_bus.py           # 消息总线
   └── tools/spawn_agent_tool.py    # 扩展工具
   ```

2. **实现统一Loop框架**
   - PerceptionProvider / ReasoningEngine / ActionExecutor 抽象接口
   - UnifiedAgentLoop 核心循环

3. **实现CLI Agent**
   - 复用`tasks/manager.py`执行命令
   - 在extended目录下独立实现三层

4. **扩展System Prompt**（`extended/prompts/`）
   - 任务分解和并行执行指导
   - CLI任务执行方式选择指导

5. **测试**
   - 验证extended模块可独立加载
   - 测试CLI双模式执行

### 5.2 阶段二：PC UI Agent（2-3周）

**目标**：在`extended/agents/pc/`下实现PC UI Agent

1. **实现PC UI三层**（`extended/agents/pc/`）
   - `mss_perception.py`：mss截图感知
   - `pynput_action.py`：鼠标键盘控制
   - `vision_reasoning.py`：视觉推理（GPT-4V）

2. **复用参考实现**
   - 适配`computer_action_executor.py`核心方法
   - 包装为ActionExecutor接口

3. **测试**
   - CLI + PC UI并行执行
   - 主对话本地执行 + PC子Agent并行

### 5.3 阶段三：Mobile + Browser Agent（2-3周）

**目标**：增加Mobile和Browser Agent支持

1. **实现Mobile Agent**（`extended/agents/mobile/`）
   - `adb_perception.py` / `hdc_perception.py`
   - `adb_action.py` / `hdc_action.py`
   - 支持Android和鸿蒙设备

2. **实现Browser Agent**（`extended/agents/browser/`）
   - `playwright_perception.py`：DOM+截图
   - `playwright_action.py`：Playwright API
   - `web_reasoning.py`：Web自动化专用Prompt

3. **完整异构测试**
   - CLI + PC UI + Mobile + Browser同时执行
   - 测试模型自主决策能力

### 5.4 阶段四：集成完善（1-2周）

**目标**：完善与OpenHarness集成，确保平滑合并上游

1. **集成入口**（`extended/`根目录）
   - `cli_ext.py`：命令行入口扩展
   - `runtime_ext.py`：运行时扩展
   - `__init__.py`：模块导出

2. **同步策略验证**
   - 验证`sync-upstream.bat`可安全合并
   - 测试extended独立于上游核心代码
   - 文档化冲突解决预案

3. **文档和示例**
   - 使用文档（如何启用extended）
   - 典型场景示例
   - 贡献者指南（如何添加新Agent）

**预估总时间**：6-8周（核心功能），8-10周（含扩展和完善）

**解耦开发优势**：
- 每阶段在extended目录独立开发
- 可随时安全同步上游更新
- extended未完成时不影响OpenHarness核心

## 6. 方案对比

### 方案A：统一Loop架构（采用）

**设计**：统一的感知-思考-行动Loop，通过热插拔感知层支持异构Agent

**优点**：
- **架构统一**：所有Agent共享同一套Loop逻辑
- **易于扩展**：新增Agent类型只需实现PerceptionProvider
- **代码复用**：思考层和行动层高度复用
- **面向未来**：天然支持智能眼镜、IoT等新型设备
- **主对话并行**：主对话可同时执行本地CLI + 并行启动多个子Agent

**缺点**：
- 需要重构现有query.py提取核心Loop
- 感知层实现有一定工作量

### 方案B：独立Loop实现（放弃）

**设计**：每类Agent独立实现完整的Loop逻辑

**放弃原因**：
- 代码重复，维护困难
- 新增Agent类型工作量大
- 难以保证行为一致性

### 方案C：显式规划+统一Loop（未来可选）

**设计**：在统一Loop基础上，可选地增加TaskPlanner模块

**说明**：
- 当前采用模型自主决策（无需显式规划）
- 如果未来OpenHarness增加了TaskPlanner功能，本扩展版本可直接复用
- 相当于多一个工具，模型愿意用就用，不用就按现有方式自主决策
- 只要有思考，规划自然会在思考中体现

## 7. 总结

本方案设计了一个支持异构多Agent并行协作的框架，核心创新点：

1. **统一Agent Loop架构**：感知-思考-行动模式，通过热插拔感知层支持所有Agent类型
2. **模型自主决策**：通过System Prompt引导模型判断任务依赖，无需显式TaskPlanner
3. **异构支持**：CLI/UI/Mobile/IoT统一抽象，易于扩展新型Agent
4. **并行执行**：
   - 主对话可同时执行本地CLI任务（快速）
   - 并行启动多个后台子Agent（耗时任务）
   - 最大化利用OpenHarness的多工具并行机制
5. **双向通信**：Master和Agents通过统一消息总线实时通信
6. **面向未来**：架构天然支持智能眼镜、可穿戴设备、IoT等新型Agent

## 8. 讨论记录

本节记录方案设计过程中的关键讨论和决策。

### 8.1 是否需要TaskPlanner？

**问题**：是否需要显式的TaskPlanner模块来分解任务和管理依赖？

**讨论要点**：
- 初始方案设计了一个显式TaskPlanner，生成带依赖的DAG执行计划
- 考虑到模型本身具备推理能力，可以让模型自主判断任务依赖关系
- OpenHarness的`query.py`已经支持多工具并行执行

**决策**：**不需要TaskPlanner**，采用模型自主决策

**理由**：
1. **架构简洁**：无需额外的规划模块，复用模型的推理能力
2. **灵活性高**：模型可以根据实时执行结果动态调整计划
3. **契合现有架构**：OpenHarness已支持并行工具调用
4. **自然处理依赖**：模型根据执行历史决定下一步，无需预计算

**未来兼容性**：
- 如果未来OpenHarness增加了TaskPlanner功能，本扩展版本可直接复用
- 相当于多一个工具，模型愿意用就用，不愿意用就按现有方式自主决策
- 只要有思考，规划自然会在思考中体现

**实现方式**：
- 通过System Prompt引导模型分析任务依赖
- 模型并行调用多个`spawn_agent`启动无依赖任务
- 有依赖的任务，模型等待前置任务完成后再执行

**示例**：
```
用户："构建项目并部署到测试环境"

模型推理：
1. 任务可分解：本地构建 + 准备部署环境
2. 这两个任务无数据依赖，可并行
3. 并行调用：spawn_agent(type="cli", task="构建") + spawn_agent(type="cli", task="准备环境")
4. 等待两个都完成
5. 然后执行：spawn_agent(type="cli", task="部署")
```

### 8.2 CLI任务在主对话还是子Agent执行？

**问题**：CLI任务应该由主对话直接执行，还是作为独立子Agent执行？

**讨论要点**：
- 初始想法是区分简单/复杂任务：简单任务主对话执行，复杂任务子Agent执行
- 深入讨论后发现：主对话本身就是一个执行单元，可以同时做多件事
- 关键洞察：**主对话可以同时执行本地CLI + 并行启动多个后台子Agent**

**决策**：**两种执行方式都支持，由模型自主选择**

**System Prompt指导原则**：

```markdown
## CLI任务执行方式选择

根据任务特点和当前场景，自主选择执行方式：

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
3. 并行启动UI类子Agent（PC/Mobile）

最大化利用并行能力，主对话不空闲等待。
```

**示例场景**：

```
用户："构建前端项目，同时在手机上测试最新版"

模型决策：
1. 主对话执行：git status（快速确认分支状态）
2. 并行启动：
   - spawn_agent(type="cli", task="npm run build")
   - spawn_agent(type="mobile_ui", task="安装并测试APP")
3. 主对话继续：查看build配置是否有问题
4. 等待后台Agent完成，汇总结果
```

**优势**：
1. **最大化并行度**：主对话和多个子Agent同时工作
2. **灵活性**：模型根据实时情况动态选择
3. **不浪费资源**：主对话不会空闲等待
4. **一致性**：CLI子Agent和其他类型子Agent统一管理

### 8.3 统一的Agent Loop架构

**问题**：不同类型Agent（CLI/UI/Mobile）的Loop是否可以统一？

**讨论要点**：
- 初始思考：每类Agent似乎有不同的Loop逻辑
- 深入分析：发现所有Agent本质都是**感知 → 思考 → 行动**
- 差异只在"感知"层的实现方式不同

**核心洞察**：

```
所有Agent都是：感知 → 思考 → 行动

两个分类维度：
1. 设备/交互维度（Where）：感知从哪里来，行动到哪里去
2. 工作机制维度（How）：如何感知、何时思考、怎样行动

维度1 - 设备/交互类型（Where & How to Interact）：
┌─────────────────────────────────────────────────────┐
│  Agent类型      │  感知(Perception)                  │
├─────────────────────────────────────────────────────┤
│  普通对话        │  上下文历史、用户输入                │
│  CLI Agent     │  命令输出、文件状态                  │
│  PC UI         │  截图 + 当前UI状态                   │
│  Mobile UI     │  手机截图 + 设备状态                 │
│  浏览器Agent    │  DOM状态 + 页面截图                  │
│  智能眼镜       │  摄像头拍照 + 环境视觉               │
│  智能手表       │  传感器数据(心率/步数/温度)          │
│  IoT设备       │  设备状态(温度/湿度/电量)            │
│  车载系统       │  车辆状态(速度/油量/路况)            │
│  无人机        │  摄像头 + GPS + 传感器               │
└─────────────────────────────────────────────────────┘

维度2 - 工作机制类型（How to Work & Collaborate）：
┌─────────────────────────────────────────────────────┐
│  工作机制        │  运行模式                          │
├─────────────────────────────────────────────────────┤
│  即时响应型       │  请求→处理→返回（标准模式）            │
│  持续监控型       │  7x24采集→检测→异常时行动             │
│  人机协作型       │  自主执行，用户可随时介入交互            │
│  批量处理型       │  批量读取→分析→汇总输出               │
│  主动探索型       │  自主收集→分析→主动建议               │
│  被动服务型       │  等待调用→快速响应                    │
└─────────────────────────────────────────────────────┘

关键理解：两个维度是正交的，可以组合
例如：持续监控型 + PC UI = 桌面自动化监控助手
```

**工作机制的Loop适配**：

所有工作机制都适配统一的感知-思考-行动Loop：

| 工作机制 | 感知 | 思考 | 行动 | Loop适配说明 |
|---------|------|------|------|-------------|
| 即时响应型 | 用户请求 | 标准推理 | 返回结果 | 标准Loop |
| 持续监控型 | 持续数据流 | 阈值/异常判断 | 报警/处置 | 感知层持续采集，思考层检测 |
| 人机协作型 | 用户输入+协作状态 | 协作流程推理 | 自主执行+响应用户介入 | 用户可随时发消息介入执行过程 |
| 批量处理型 | 批量数据 | 批量分析 | 汇总输出 | 感知=批量读取 |
| 主动探索型 | 自主采集信息 | 分析+建议生成 | 主动推送 | 感知层主动收集而非被动等待 |
| 被动服务型 | API请求 | 快速处理 | 返回结果 | 简化的即时响应 |

**结论**：感知-思考-行动的范式足够通用，所有工作机制都可以映射到这个Loop中。

**补充讨论：Agent分类的两个维度**

在讨论过程中，发现Agent类型可以从两个维度理解：

**维度1：设备/交互类型**（之前主要考虑的）
- 回答"从哪里感知，如何行动"
- CLI、PC UI、Mobile UI、浏览器、智能眼镜、IoT、车载系统、无人机等

**维度2：工作机制类型**（讨论深入后补充的）
- 回答"如何运行，如何协作"
- 即时响应、持续监控、人机协作（随时可交互）、批量处理、主动探索、被动服务等

**关键理解**：
- 这两个维度是正交的，可以组合
- 例如：持续监控 + PC UI = 桌面自动化监控助手
- 例如：人机协作 + Mobile UI = 可交互的手机测试Agent（测试员可随时介入）
- 例如：主动探索 + CLI = 智能运维分析Agent
- 例如：持续监控 + 智能手表 = 24小时健康监测Agent

**补充讨论：三层热插拔的深入理解**

在进一步讨论中发现，不只是感知层需要热插拔，**推理层**和**行动层**同样需要热插拔：

**推理层差异（之前理解不充分）**：
- **Prompt拼装完全不同**：
  - CLI Agent："你是命令行专家，擅长使用bash..."
  - PC UI Agent："你是GUI操作助手，请分析这张截图..."
  - Browser Agent："DOM结构如下，元素列表：..."
- **模型选择不同**：文本模型 vs 视觉模型(GPT-4V) vs 专用模型
- **动作空间不同**：
  - CLI输出：`{"command": "ls", "args": ["-la"]}`
  - PC UI输出：`{"action": "click", "x": 100, "y": 200}`
  - Browser输出：`{"action": "click", "selector": "#submit"}`
- **上下文构建不同**：是否包含历史截图？是否包含DOM变化？

**行动层差异（之前理解不充分）**：
- **执行器不同**：subprocess vs pynput vs adb vs Playwright
- **反馈收集不同**：命令返回码 vs 截图验证 vs 设备响应
- **错误处理不同**：命令失败重试 vs 点击失败重试 vs 元素找不到处理

**修正后的架构设计**：
```
UnifiedAgentLoop:
  - perception: PerceptionProvider  ← 热插拔点1（CLI/UI/Mobile各有不同）
  - reasoning: ReasoningEngine    ← 热插拔点2（Prompt/模型/动作空间）
  - action: ActionExecutor        ← 热插拔点3（Shell/pynput/adb/Playwright）
```

**三层热插拔示例**：

| Agent | 感知层 | 推理层 | 行动层 |
|-------|--------|--------|--------|
| CLI | CommandOutputPerception | StandardReasoning<br>(Prompt: CLI专家) | ShellActionExecutor |
| PC UI | ScreenshotPerception | VisionEnabledReasoning<br>(Prompt: GUI专家, 模型: GPT-4V) | DesktopActionExecutor<br>(pynput) |
| Mobile | MobilePerception | VisionEnabledReasoning<br>(Prompt: Mobile专家, 模型: GPT-4V) | MobileActionExecutor<br>(adb) |
| Browser | BrowserPerception<br>(DOM+截图) | WebAutomationReasoning<br>(Prompt: Web专家, 结构化输出) | BrowserActionExecutor<br>(Playwright) |

**决策**：**采用统一的Agent Loop架构 + 三层热插拔**，每层都可以根据Agent类型定制

**架构优势**：
1. **Loop统一**：核心流程不变（感知→思考→行动）
2. **三层热插拔**：感知、推理、行动都可以定制
3. **灵活组合**：可复用某一层（如多个视觉Agent复用VisionEnabledReasoning）
4. **易于扩展**：新增Agent类型只需实现差异层
5. **面向未来**：天然支持所有组合

**代码实现**：详见2.1-2.3节及2.2节的组合示例

### 8.4 双向通信机制设计

**问题**：如何实现主Agent和子Agent之间的双向通信？

**讨论要点**：
- 需要支持Agent主动查看其他Agent状态
- 需要支持Agent主动发送消息给其他Agent
- 主Agent必须全局把控，普通Agent可以选择不看/不发
- 即使Agent模型不主动上报，框架也应该记录并定期上报
- 需要区分模型层（可选）和框架层（硬性）

**决策**：**分层通信机制**

**Layer 1 - 模型层通信（可选）**：
- 提供工具给Agent模型主动调用：`QueryOtherAgentTool`、`SendMessageToAgentTool`
- 完全可选，普通Agent可以专注于自己的任务
- 主Agent例外，必须使用这些工具（对用户负责）
- 任何Agent都可以查看其他Agent（开放透明）

**Layer 2 - 框架层通信（硬性机制）**：
- 框架自动记录所有Agent执行过程
- 定期向Master Agent上报状态快照（可配置频率/内容）
- 作为**软提醒**插入主Agent消息流，Master可以选择忽略
- 重要事件（完成/错误/卡顿）立即上报
- 不写入普通Agent消息通道，只发给Master

**关键设计原则**：
1. **不强制普通Agent**：Agent模型可以完全不看/不发，专注于任务
2. **Master必须知情**：通过框架层硬性机制确保Master掌握全局
3. **软干预机制**：框架上报是软提醒，Master自主判断是否干预
4. **可配置**：所有机制都有开关和参数

**配置示例**：
```yaml
framework_reporting:
  enabled: true
  interval_seconds: 30
  master_agent:
    soft_reminder: true  # 作为软提醒插入消息流
    allow_ignore: true   # Master可以忽略
```

**上报示例**：
```
[System Event] Agent "pc_ui_001": 下载进度45%，运行45秒
[System Event] Agent "mobile_ui_002": 错误 - 网络连接失败
```

Master Agent看到后可以：
- 忽略（觉得正常）
- 立即干预（询问用户是否重试）
- 稍后处理（等PC端完成后再处理手机问题）

---

### 8.5 Mobile UI Agent实现方案选择

**问题**：Mobile UI Agent应该使用哪种技术方案？ADB、HDC还是Appium？

**候选方案**：
1. **ADB** (Android Debug Bridge) - Android命令行工具
2. **HDC** (HarmonyOS Device Connector) - 鸿蒙命令行工具
3. **Appium** - 跨平台自动化测试框架

**详细对比**：详见 [mobile-agent-implementation.md](./mobile-agent-implementation.md)

**核心差异总结**：

| 维度 | ADB/HDC | Appium |
|------|---------|--------|
| 复杂度 | 低 | 高 |
| 启动速度 | 快（毫秒级） | 慢（秒级） |
| 元素定位 | 坐标点击 | 丰富的定位方式（ID/XPath等） |
| 架构要求 | 无额外依赖 | 需要Appium Server |
| 跨平台 | ADB仅Android，HDC仅鸿蒙 | Android+iOS |
| 智能等待 | 需自实现 | 原生支持 |

**决策**：**第一阶段采用ADB/HDC为主，可选Appium作为补充**

**理由**：
1. **架构契合**：ADB/HDC简单直接，与我们的统一Agent Loop架构完美契合
2. **低延迟**：截图和点击操作延迟低（~100ms），适合实时Loop
3. **轻量级**：无需维护额外的Appium Server基础设施
4. **足够使用**：截图+坐标点击+OCR/视觉模型，足以完成大部分UI操作任务
5. **鸿蒙支持**：同时实现HDC支持，覆盖Android和鸿蒙

**具体实现**：
- 主要使用`adb shell screencap`截图 + `adb shell input tap`点击
- 结合视觉模型（GPT-4V）识别截图中的可点击元素坐标
- 可选使用`uiautomator dump`获取UI层次辅助理解界面

**代码位置**：
- `extended/agents/mobile/adb_perception.py` - ADB感知层
- `extended/agents/mobile/adb_action.py` - ADB行动层
- `extended/agents/mobile/hdc_perception.py` - HDC感知层（鸿蒙）
- `extended/agents/mobile/hdc_action.py` - HDC行动层（鸿蒙）

**未来扩展**：
- 如果确实有精确定位元素的需求，可添加Appium作为可选方案
- 通过工厂模式支持：`MobilePerceptionFactory.create("appium", ...)`

---

### 8.6 PC UI Agent实现方案选择

**问题**：PC UI Agent应该使用哪种技术方案？

**候选方案**：
1. **MSS + pynput**：轻量级，纯Python，跨平台
2. **PyAutoGUI**：简单易用，功能全面
3. **Windows API (pywin32)**：功能最强，仅Windows
4. **Playwright/Selenium**：浏览器为主，桌面支持有限

**参考实现**：用户提供了基于 `mss + pynput` 的实现参考
- 位置：`D:\repo\public\Mininglamp-AI-mano-skill\mano-skill\visual\computer\`
- 核心文件：`computer_action_executor.py`, `computer_use_util.py`
- 技术栈：mss（截图）+ pynput（鼠标/键盘控制）

**详细对比**：详见 [pc-agent-implementation.md](./pc-agent-implementation.md)

**核心对比**：

| 维度 | MSS+pynput | PyAutoGUI | Windows API |
|------|------------|-----------|-------------|
| 复杂度 | ⭐ 低 | ⭐ 低 | ⭐⭐⭐ 高 |
| 性能 | ⚡ 快 | 🐢 一般 | ⚡ 快 |
| 跨平台 | ✅ Win/Mac/Linux | ✅ Win/Mac/Linux | ❌ 仅Windows |
| UI元素获取 | ❌ 无 | ⚠️ 图像匹配 | ✅ Accessibility |

**决策**：**采用MSS + pynput方案**

**理由**：
1. **参考验证**：已有参考实现验证可行
2. **架构契合**：轻量高效，与统一Agent Loop架构完美契合
3. **性能优秀**：mss截图速度快（~20ms），满足实时Loop需求
4. **跨平台**：一套代码支持Windows/Mac/Linux
5. **足够使用**：截图+坐标点击+视觉模型，能完成大部分UI操作

**技术组合**：
```
感知层：mss (截图) + 视觉模型 (理解界面)
行动层：pynput (鼠标/键盘控制)
坐标处理：模型输出基于1920x1080，实际按屏幕比例缩放
```

**重要说明：窗口聚焦问题**

**澄清误解**：pynput**不**需要窗口在后台，键盘输入会进入当前**焦点窗口**。

**具体情况**：
- **鼠标点击**：可以在任何位置工作，点击会自动聚焦该位置的窗口
- **键盘输入**：会发送到当前具有键盘焦点的窗口

**解决方案**：
1. **先点击后输入**：点击目标输入框（聚焦窗口），再输入文字
2. **使用窗口API**：Windows下可用`SetForegroundWindow`强制聚焦
3. **这不是严重限制**：只需要正确的操作顺序

**示例操作序列**：
```
用户："在记事本中输入Hello"

模型执行：
1. 截图查看桌面
2. 识别记事本图标位置 (x1, y1)
3. 点击图标打开/聚焦记事本
4. 截图确认记事本已打开
5. 识别输入框位置 (x2, y2)
6. 点击输入框（确保聚焦）
7. 输入 "Hello"
```

**代码位置**：
- `extended/agents/pc/mss_perception.py` - 基于mss的感知层
- `extended/agents/pc/pynput_action.py` - 基于pynput的行动层
- **可直接复用参考实现**：`computer_action_executor.py`中的核心方法

**关键实现要点**（来自参考实现）：
1. **坐标缩放**：`_xy()`方法处理不同屏幕分辨率的坐标转换
2. **平滑移动**：`_mouse_move()`实现鼠标平滑移动动画
3. **剪贴板输入**：`_type_text()`使用剪贴板粘贴避免输入法问题
4. **跨平台支持**：根据`platform.system()`使用不同命令

**未来扩展（可选）**：
- 如果需要UI元素精确识别，可添加Windows Accessibility API（仅Windows）
- 或者使用OCR+视觉模型识别截图中的元素

---

### 8.7 Browser/Web Agent实现方案选择

**问题**：浏览器/Web操控应该使用哪种技术方案？

**候选方案**：
1. **Playwright**（微软）- 现代、多浏览器、CDP基础
2. **Selenium**（开源）- 老牌、生态丰富
3. **Puppeteer**（Google）- Chrome专用、Node.js
4. **CDP原生** - 最底层、需自行封装

**详细对比**：详见 [browser-agent-implementation.md](./browser-agent-implementation.md)

**核心对比**：

| 维度 | Playwright | Selenium | Puppeteer | CDP原生 |
|------|------------|----------|-----------|---------|
| 性能 | ⚡ 快 | 🐢 一般 | ⚡ 快 | ⚡ 最快 |
| 浏览器支持 | Chromium/Firefox/WebKit | 几乎所有 | 仅Chrome | 仅Chrome |
| Python支持 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ Pyppeteer | ⭐⭐ |
| API现代性 | ⭐⭐⭐ 自动等待 | ⭐⭐ 手动等待 | ⭐⭐⭐ | ⭐ |
| 元素信息 | ⭐⭐⭐ 丰富 | ⭐⭐ 一般 | ⭐⭐⭐ | ⭐⭐ |

**决策**：**采用Playwright**

**理由**：
1. **最适合AI Agent场景**：可以直接获取可交互元素列表（带坐标、文本、类型），LLM无需OCR即可理解页面
2. **技术现代**：自动等待、智能重试、减少错误
3. **架构契合**：支持async/await，完美适配UnifiedAgentLoop
4. **多浏览器**：Chromium/Firefox/WebKit，不只是Chrome
5. **性能优秀**：基于CDP，速度快

**关键优势（相比PC/Mobile Agent）**：
```
PC/Mobile Agent: 截图 → AI视觉识别 → 坐标点击
Browser Agent:   DOM树 + 元素列表 → 直接精准操作
```

**技术组合**：
```
感知层：Playwright (截图 + query_selector_all获取元素列表)
行动层：Playwright API (goto, click, fill, scroll等)
```

**代码位置**：
- `extended/agents/browser/playwright_perception.py` - 基于Playwright的感知层
- `extended/agents/browser/playwright_action.py` - 基于Playwright的行动层

**示例使用**：
```python
spawn_agent(
    type="browser",
    task="在京东搜索iPhone，找到价格最低的商品",
    config={"headless": False, "browser": "chromium"}
)
```

---

---

## 9. 系统级进阶特性（未来扩展）

本节讨论当前框架的进阶特性，属于未来扩展方向，不在第一阶段实现范围内。

### 9.1 系统级鲁棒性与容错恢复

**问题**：当主Agent决策失败、子Agent大面积失败、或系统进入不可恢复状态时，如何处理？

**讨论要点**：
- 第8.7节的错误处理只覆盖子Agent→主Agent的单层上报
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

**问题**：如何让Agent系统能够自我学习、自我改进，而不只是执行预设任务？

**背景调研**：hermes-agent提出了"learning loop"概念，其核心思想是Agent不仅执行任务，还通过执行反馈持续学习和改进。

**Learning Loop的核心概念**（基于meta-learning通用框架）：

```
普通Agent Loop（执行层）:
感知(任务输入) → 思考(如何完成) → 行动(执行) → 输出(结果)
        ↑                                    │
        └──────────── 单次任务 ──────────────┘

Learning Loop（元学习层）:
感知(执行历史+反馈) → 思考(哪些做得好/不好) → 行动(改进策略/技能/代码)
        ↑                                          │
        └──────────── 持续演进 ────────────────────┘
```

**Learning Loop与传统Agent Loop的对比**：

| 维度 | Agent Loop（执行层） | Learning Loop（元学习层） |
|------|---------------------|-------------------------|
| 目标 | 完成具体任务 | 改进完成任务的策略 |
| 感知 | 当前任务上下文 | 历史执行数据+反馈 |
| 思考 | 如何完成当前任务 | 哪些策略有效，如何改进 |
| 行动 | 执行任务操作 | 修改代码/配置/skill |
| 频率 | 每轮对话执行 | 周期性或触发式执行 |
| 输出 | 任务结果 | 系统改进（代码/知识） |

**Learning Loop与统一Agent Loop的关系**：

**观点**：Learning Loop**可以**统一为Agent Loop的一种特殊形式，但需要明确层次：

```python
# 层次1：基础Agent Loop（所有Agent共享）
class BaseAgentLoop:
    async def run(self):
        obs = await self.perception.observe()
        thought = await self.reasoning.think(obs)
        result = await self.action.execute(thought)
        return result

# 层次2：任务执行Agent（直接继承）
class TaskAgent(BaseAgentLoop):
    """普通任务执行Agent"""
    perception = TaskPerceptionProvider()
    reasoning = StandardReasoning()
    action = TaskActionExecutor()

# 层次3：元学习Agent（也是Agent Loop，但感知/思考/行动不同）
class MetaLearningAgent(BaseAgentLoop):
    """
    Learning Loop - 元学习Agent
    它也是Agent Loop，但：
    - 感知 = 收集执行历史、用户反馈、错误日志
    - 思考 = 分析成功/失败模式，生成改进建议
    - 行动 = 修改代码、更新skill、调整配置
    """
    perception = ExecutionHistoryPerception()  # 读取执行日志
    reasoning = ImprovementAnalysisReasoning()  # 分析如何改进
    action = SystemModificationExecutor()  # 修改系统本身
```

**Learning Loop的具体实现方向**（待调研完善）：

基于通用的meta-learning框架，可能的组件包括：

1. **反思（Reflection）**
   - 在任务完成后，分析执行轨迹
   - 识别哪些步骤做得好，哪些可以优化
   - 生成反思报告

2. **记忆（Memory）**
   - 长期存储成功案例和失败案例
   - 支持基于相似性的检索
   - 形成"经验库"

3. **技能改进（Skill Improvement）**
   - 基于使用频率和效果，优化skill实现
   - 自动合并相似的skill
   - 淘汰不常用的skill

4. **代码自我修改（Self-Modification）**
   - 在安全的沙箱环境中，Agent可以修改自己的代码
   - 需要版本控制和回滚机制
   - 人工确认或自动测试验证

**需要进一步调研**（hermes-agent的具体实现）：

当前搜索未能找到hermes-agent的详细文档，需要进一步调研：
- hermes-agent的learning loop具体包含哪些阶段？
- 它是如何收集反馈的？
- 它是如何安全地修改代码的？
- 有哪些 safeguards 防止自我修改导致系统崩溃？

**与当前框架的关系**：

1. **当前第一阶段**：只实现基础Agent Loop（感知-思考-行动的任务执行）
2. **第二阶段（可选）**：在基础Loop上增加Learning Loop作为可选组件
3. **统一性**：保持架构一致性，Learning Loop也是热插拔的PerceptionProvider的一种特殊形式

**代码位置（未来）**：
- `extended/meta_learning/` - 元学习相关模块（未来扩展）
- `extended/meta_learning/reflection.py` - 反思机制
- `extended/meta_learning/skill_optimization.py` - 技能改进
- `extended/meta_learning/self_modification.py` - 代码自修改（最激进）

**结论**：
- Learning Loop与Agent Loop**可以统一**（都是感知-思考-行动）
- 但属于**不同层次**（执行层 vs 元学习层）
- 当前先实现基础Loop，Learning Loop作为未来扩展方向
- 需要进一步调研hermes-agent和其他self-improving agent框架的具体实现

---

**文档版本**：v1.8（2024-04-15）
**状态**：
- 8.1-8.7：核心架构决策完成
- 9.1-9.2：系统级进阶特性记录，待后续深入调研和实现