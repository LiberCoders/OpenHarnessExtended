# 讨论纪要：AgentLifecycleManager 底层调用路径选择

**日期**：2026-04-16  
**背景**：在 `heterogeneous-multi-agent-design.md`（2.6.1 节）中，设计文档建议 `AgentLifecycleManager` 统一调用 `BackgroundTaskManager.create_agent_task()`。本次讨论的核心问题是：实现时应直接调用 `BackgroundTaskManager`，还是经由已有的 `SubprocessBackend`？

---

## 一、现有代码结构梳理

### 调用链（现状）

```
AgentTool / SendMessageTool
    └─ BackendRegistry.get_executor("subprocess")
           └─ SubprocessBackend
                  ├─ spawn()       → BackgroundTaskManager.create_agent_task()
                  ├─ send_message() → BackgroundTaskManager.write_to_task()
                  └─ shutdown()    → BackgroundTaskManager.stop_task()
```

### 各层职责

| 组件 | 所在模块 | 职责 |
|------|---------|------|
| `BackgroundTaskManager` | `tasks/manager.py` | **进程基础设施**：创建/监控子进程，管理 task_id、stdout/stderr 聚合、状态查询、停止 |
| `SubprocessBackend` | `swarm/subprocess_backend.py` | **Swarm 执行后端**：实现 `TeammateExecutor` 协议，维护 `agent_id (name@team) → task_id` 映射，包装对 BTM 的调用 |
| `BackendRegistry` | `swarm/registry.py` | 后端注册与自动探测（subprocess / tmux / in_process），提供扩展点 |
| `spawn_utils` | `swarm/spawn_utils.py` | 独立工具函数：命令构建、环境变量继承、CLI flag 传播 |

---

## 二、两种方案的对比

### 方案 A：经由 `SubprocessBackend`
`AgentLifecycleManager` 通过 `BackendRegistry` 获取 `SubprocessBackend`，复用其 `spawn()` 接口。

**优点**
- 与现有 `AgentTool` / `SendMessageTool` 保持一致
- `SendMessageTool` 的 `name@team` 格式消息路由开箱即用
- 后端可替换（未来可换 tmux/in_process）

**缺点**
- `TeammateSpawnConfig` 没有 `agent_type` 字段，为异构 agent 设计的接口语义不匹配
- `SubprocessBackend.spawn()` 内部固定生成 `openharness --task-worker` 命令，异构 agent（pc_ui / mobile_ui / browser）需要完全不同的命令，只能绕道 `config.command` 传入，意味着 `SubprocessBackend` 的命令构建逻辑形同虚设
- 强行套用 `name@team` 的 peer-agent 语义，而实际场景是 master-worker 异构编排

### 方案 B：直接调用 `BackgroundTaskManager`（文档 2.6.1 方向）
`AgentLifecycleManager` 直接持有 `BackgroundTaskManager` 引用，调用 `create_agent_task(command=...)`，自行维护 `agent_id → task_id` 映射。

**优点**
- `create_agent_task()` 接受显式 `command=` 参数，`HeterogeneousAgentFactory` 可按 agent 类型自由构建命令
- 不受 `TeammateSpawnConfig` 接口约束，`AgentLifecycleManager` 的抽象更干净
- `spawn_utils` 里的工具函数（`build_inherited_env_vars`、`build_inherited_cli_flags`、`get_teammate_command`）均为独立函数，可直接 import 使用，不需要经由 `SubprocessBackend`

**缺点**
- 需要自行维护 `agent_id → task_id` 映射（少量额外代码）
- 与 `SendMessageTool` 的 `name@team` 路由不直接兼容（需通过 plain task_id 调用 `write_to_task`）

---

## 三、结论：采用方案 B

### 核心判断

`SubprocessBackend` 和新设计的 `AgentLifecycleManager` 服务于**两种不同的 Agent 关系模型**：

| | `SubprocessBackend` / Swarm | `AgentLifecycleManager` / 异构编排 |
|-|-----------------------------|------------------------------------|
| **关系模型** | Peer（对等 Teammate） | Master-Worker（主从调度） |
| **Agent 标识** | `name@team` | 按类型生成的 agent_id |
| **命令构建** | 统一 `--task-worker` | 按类型各异（cli / pc_ui / mobile_ui / browser） |
| **差异化维度** | 同质 agent，后端可换（subprocess/tmux） | 异质 agent，进程内部感知/行动能力不同 |

强行让 `AgentLifecycleManager` 套 `SubprocessBackend` 会造成接口语义错配，且对命令构建逻辑没有实际复用价值（最终必须通过 `config.command` 绕过）。

### 文档 2.6.1 方向正确，但伪代码有一处需补充

文档中的伪代码省略了 `command=` 参数，实际实现**必须显式传入**：

```python
# 文档伪代码（不完整）
task_record = await self._task_manager.create_agent_task(
    prompt=task,
    description=f"{agent_type} Agent {self._next_id()}",
    cwd=self._resolve_cwd(agent_type),
)

# 正确实现（需补充 command= 参数）
command = self._factory.build_command(agent_type)   # HeterogeneousAgentFactory 按类型构建
task_record = await self._task_manager.create_agent_task(
    prompt=task,
    description=f"{agent_type} Agent {self._next_id()}",
    cwd=self._resolve_cwd(agent_type),
    command=command,   # ← 必须传入，否则 BTM 会 fallback 到旧的默认命令
)
```

若省略 `command=`，`BackgroundTaskManager.create_agent_task()` 会 fallback 到 `python -m openharness --api-key <key>` 这个旧默认命令，而非各 agent 类型对应的专属命令。

---

## 四、实现指引

### 正确的依赖关系

```
AgentLifecycleManager
    ├─ 直接持有：BackgroundTaskManager（via get_task_manager()）
    ├─ 直接 import：spawn_utils.build_inherited_env_vars()
    ├─                spawn_utils.build_inherited_cli_flags()
    ├─                spawn_utils.get_teammate_command()
    └─ 不经由：SubprocessBackend / BackendRegistry
```

### `spawn_utils` 的正确使用方式

`spawn_utils` 中的函数是独立的纯函数，不依赖 `SubprocessBackend`，可以直接在 `HeterogeneousAgentFactory` 中使用：

```python
from openharness.swarm.spawn_utils import (
    build_inherited_cli_flags,
    build_inherited_env_vars,
    get_teammate_command,
)

class HeterogeneousAgentFactory:
    def build_command(self, agent_type: str, model: str | None = None) -> str:
        env_vars = build_inherited_env_vars()
        flags = build_inherited_cli_flags(model=model)
        base_cmd = get_teammate_command()

        if agent_type == "cli":
            cmd_parts = [base_cmd, "-m", "openharness", "--task-worker"] + flags
        elif agent_type == "pc_ui":
            cmd_parts = [base_cmd, "-m", "openharness_extended.agents.pc", "--worker"] + flags
        elif agent_type == "mobile_ui":
            cmd_parts = [base_cmd, "-m", "openharness_extended.agents.mobile", "--worker"] + flags
        elif agent_type == "browser":
            cmd_parts = [base_cmd, "-m", "openharness_extended.agents.browser", "--worker"] + flags
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

        env_prefix = " ".join(f"{k}={v!r}" for k, v in env_vars.items())
        command = " ".join(cmd_parts)
        return f"{env_prefix} {command}" if env_prefix else command
```

### 关于消息发送

由于绕过了 `SubprocessBackend`，`SendMessageTool` 的 `name@team` 路由不可用。消息发送应直接通过 `BackgroundTaskManager.write_to_task(task_id, message)` 完成，或在 `AgentLifecycleManager` 中封装一个 `send_message(agent_id, message)` 方法，内部查自己维护的 `agent_id → task_id` 映射后调用 `write_to_task`。

---

## 五、需要同步到文档的修改点

| 文档位置 | 现有内容 | 需要补充/修正 |
|---------|---------|-------------|
| `heterogeneous-multi-agent-design.md` § 2.6.1 伪代码 | 缺少 `command=` 参数 | 补充 `command=self._factory.build_command(agent_type)`，并说明不传 command 时的 fallback 风险 |
| `heterogeneous-multi-agent-design.md` § 2.6.1 文字说明 | 未说明 `spawn_utils` 可直接使用 | 补充说明 `spawn_utils` 函数为独立工具，`AgentLifecycleManager` 可直接 import |
| `heterogeneous-multi-agent-design.md` § 1.3 表格 | "在其上扩展" swarm 层表述较模糊 | 澄清：复用 `spawn_utils` 工具函数，不必经由 `SubprocessBackend` executor 接口 |
