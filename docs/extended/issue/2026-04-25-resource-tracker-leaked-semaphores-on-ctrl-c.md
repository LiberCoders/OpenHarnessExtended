# 问题排查报告：Ctrl+C 退出 OpenHarness 后 `resource_tracker` 报泄漏 semaphore

**日期**：2026-04-25
**涉及组件**：React 终端前端 (`frontend/terminal`) ↔ Python backend host (`src/openharness/ui/backend_host.py`) ↔ 异构 expert IPC (`src/openharness/extended/channel/*`)
**症状严重程度**：噪声告警 (no functional impact)，但每次退出都打印 traceback 影响体验

---

## 一、问题现象

通过 `run.sh` 启动 OpenHarness（`oh` → React 前端 spawn Python backend），下发一个会调用 `delegate_to_expert` 的任务。任务成功执行并返回结果，最终结果总结也正常显示。然后用 **Ctrl+C** 退出，终端尾部稳定出现：

```
> shift+enter newline  enter send  / commands  ↑↓ history  ctrl+c exit
/usr/lib/python3.10/multiprocessing/resource_tracker.py:224: UserWarning:
resource_tracker: There appear to be 6 leaked semaphore objects to clean up at shutdown
  warnings.warn('resource_tracker: There appear to be %d '
```

`6` 这个数字会随会话中 spawn 过的 expert 数量线性变化（一次 1 expert 是 6，会话中多次 delegate 时观察到 12）。

---

## 二、相关架构概要

```
┌────────────────────────┐  spawn (stdio pipe)  ┌────────────────────────┐
│ React frontend (Node)  │ ───────────────────► │ Python backend host    │
│ Ink + tsx 运行         │ ◄─── JSONL events ── │ asyncio main loop      │
└────────────────────────┘                      └─────────┬──────────────┘
                                                          │ mp.get_context("spawn")
                                                          ▼
                                                ┌────────────────────────┐
                                                │ Expert worker process  │
                                                │ (mobile_gui 等)        │
                                                └────────────────────────┘
                                  通过 LeaderQueueChannel = 2×mp.Queue
```

- Backend 每一次 `delegate_to_expert` 在 `src/openharness/extended/channel/ipc.py:create_channel()` 里创建 **2 条 `multiprocessing.Queue`**（downlink + uplink），用 spawn context 启动 expert worker 子进程。
- 每条 `multiprocessing.Queue` 内部持有 **3 个 `SemLock`**（`_sem` / `_rlock` / `_wlock`），所以一次 delegation = **2 × 3 = 6 个 semaphore** 被 `resource_tracker` 跟踪。

---

## 三、初步排查与历史尝试

在介入前用户已经做过以下尝试（提交在 working tree，git status 可见）：

1. `LeaderQueueChannel.close()` / `WorkerQueueChannel.close()` 真的去调 `Queue.close()` + `Queue.join_thread()`
2. `ChannelRegistry.close_all_channels()` 在 `close_runtime` 中被触发
3. 给 `ChannelRegistry` 加 `atexit` fallback
4. `App.tsx` 改成 Ctrl+C 时**先发 shutdown JSON 给后端，5 秒兜底再 force exit**
5. `backend_host.py` 把 `shutdown` event 推迟到 `close_runtime` 之后再 emit

但都没解决问题。

---

## 四、根因定位

### 4.1 从警告本身入手

警告由 `multiprocessing/resource_tracker.py:224` 打印。`resource_tracker` 是 Python 在第一次创建 `mp.Queue` 时启动的**独立子进程**：父进程通过 pipe 告知它"我注册了某个共享资源"，父进程退出时 pipe 关闭，resource_tracker 检查自己的 cache，**还有未注销的资源就报 leak warning**。

数量精确等于 `expert 数 × 6`，强烈指向 `LeaderQueueChannel` 持有的两条 queue 的 SemLock 没被注销。

### 4.2 谁负责注销 SemLock？

读 `cpython/Lib/multiprocessing/synchronize.py` (Python 3.10) 的 `SemLock.__init__`：

```python
if self._semlock.name is not None:
    from .resource_tracker import register
    register(self._semlock.name, "semaphore")
    util.Finalize(self, SemLock._cleanup, (self._semlock.name,),
                  exitpriority=0)

@staticmethod
def _cleanup(name):
    from .resource_tracker import unregister
    sem_unlink(name)
    unregister(name, "semaphore")
```

注销发生在 `util.Finalize` 注册的 `_cleanup` 回调里，**这个回调只在两种时机会跑**：

- SemLock 对象被 GC（weakref 触发）
- Python 解释器**正常退出**（atexit 走 `util._exit_function`）

### 4.3 关键发现：为什么 `Queue.close()` 不够

在 `cpython/Lib/multiprocessing/queues.py` 中，`Queue.close()` 只关闭了 reader pipe 和 buffer-drain 线程的 finalize，**完全没有触碰 `_sem` / `_rlock` / `_wlock`**。这三个 SemLock 仍然是 Queue 实例的强引用。

只要 Queue 对象本身没被 GC，三个 SemLock 就不会被 GC，`_cleanup` 也就不会跑。

### 4.4 确认 SIGTERM 才是真凶

最小复现脚本：

```python
# /tmp/leak_test8.py
import multiprocessing as mp
import os, time
QUEUES, PROCS = [], []
def worker(d, u, idx):
    msg = d.get()
    u.put(f"worker {idx} got: {msg}")

ctx = mp.get_context('spawn')
d = ctx.Queue(); u = ctx.Queue()
QUEUES.append((d, u))
p = ctx.Process(target=worker, args=(d, u, 0)); p.start()
PROCS.append(p)
d.put("hello")
print(u.get())
print(f'pid={os.getpid()}, waiting for SIGTERM...', flush=True)
time.sleep(60)
```

运行：

```bash
python /tmp/leak_test8.py &
sleep 1
kill -TERM $!
```

输出（被 SIGTERM 杀）：

```
worker 0 got: hello
pid=..., waiting for SIGTERM...
[Terminated]
/.../multiprocessing/resource_tracker.py:224: UserWarning:
resource_tracker: There appear to be 6 leaked semaphore objects ...
```

而 `kill -TERM` 换成自然返回（脚本结束），就**完全没有警告**。

→ **结论**：默认 SIGTERM handler 直接终止解释器，Python 不跑 atexit、不做 GC，`SemLock._cleanup` 没机会执行，resource_tracker 就把这些 sem 当作"被异常退出的进程遗弃的"打 warning。

### 4.5 为什么 OpenHarness 触发了这个路径

读 `frontend/terminal/src/hooks/useBackendSession.ts`：

```typescript
const child = spawn(command, args, {stdio: ['pipe', 'pipe', 'inherit'], detached: useDetachedGroup, ...});
...
const killChild = (): void => {
    if (!child.killed) {
        process.kill(-child.pid, 'SIGTERM');  // 杀整个 process group
        ...
    }
};
process.on('exit', killChild);
```

React 前端 unmount（`exit()` 调用后的 useEffect cleanup）就会 SIGTERM 后端的整个进程组。

按 Ink 默认行为，**Ctrl+C 立即触发 `exit()`** → unmount → SIGTERM Python → 没有任何 cleanup 机会 → resource_tracker leak warning。

用户加的"Ctrl+C 先发 shutdown JSON + 5s timer"是有效的"温柔关闭"路径，但只要：

- backend 在 `close_runtime` 内某处 hang ≥ 5s（实测中 `mcp_manager.close()` / `stop_docker_sandbox` / `SESSION_END` hook 都可能慢）
- 或者 backend 已经 emit 了 shutdown，但 React 紧接着 unmount → killChild → SIGTERM 又赶在 Python 解释器收尾**之前**到达

leak warning 就会复现。这就是为什么用户的多次修改都"看似对但不解决问题"。

---

## 五、解决方案

针对 4.4 的根因（"SIGTERM 时 SemLock 不被 unregister"），最稳的办法是 **在 backend 进程里装一个 SIGTERM handler，收到信号时同步释放 channel queues 再让信号默认行为执行**。

辅以：

- `LeaderQueueChannel.close()` 真的关闭 queue + 丢引用
- `ChannelRegistry.close_all_channels()` 清空 `_handles` + `gc.collect()` —— 让 `SemLock._cleanup` 这条路径**在 SIGTERM 还没到之前**就跑完
- `close_runtime` 中把 `close_all_channels` 排在所有"可能慢的步骤"**之前**
- React 端 Ctrl+C 走 graceful 路径（发 shutdown JSON + 5s 兜底 timer）

形成两条互补路径：

| 路径 | 触发条件 | 释放 sem 的位置 |
|------|---------|----------------|
| **优雅路径**（90%）| backend 在 5s 内跑完 `close_runtime` | `close_runtime` 早期调用 `close_all_channels` → `gc.collect()` → SemLock Finalize |
| **SIGTERM 兜底路径** | backend 卡住或 React 提早 SIGTERM | SIGTERM handler 同步 `close_all_channels` → 重发 SIGTERM |

两条路径都能保证警告不再出现。

---

## 六、最终修改清单（最小集）

```
 frontend/terminal/src/App.tsx                | 27 +++++++++++---
 src/openharness/extended/channel/ipc.py      | 38 +++++++++++++++++--
 src/openharness/extended/channel/registry.py | 25 +++++++++++++
 src/openharness/ui/backend_host.py           | 52 ++++++++++++++++++++++++---
 src/openharness/ui/runtime.py                | 11 ++++++
 5 files changed, 142 insertions(+), 11 deletions(-)
```

### 6.1 `src/openharness/ui/backend_host.py`（核心修复）

新增 `_install_sigterm_channel_cleanup()`，在 `run_backend_host()` 启动时安装：

```python
def _install_sigterm_channel_cleanup() -> None:
    if os.name == "nt":
        return

    def _handler(_signum, _frame):
        try:
            from openharness.extended.channel import get_channel_registry
            get_channel_registry().close_all_channels()
        except Exception:
            pass
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)  # 重发使退出码保持 143

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass
```

同时把 `shutdown` event 改成**所有 `close_runtime` 完成后**才 emit，避免前端在 cleanup 中途就 unmount。

### 6.2 `src/openharness/extended/channel/registry.py`

```python
def close_all_channels(self) -> None:
    for handle in list(self._handles.values()):
        try:
            handle.channel.close()
        except Exception:
            pass
    self._handles.clear()           # 丢引用 (registry 持有的)
    self._task_to_expert.clear()
    gc.collect()                    # 立刻触发 SemLock._cleanup 这条 Finalize
```

`gc.collect()` 是关键 —— 没有它，引用即便丢了也得等下一次 GC 运行才会跑 Finalize，而 SIGTERM 可能比 GC 早到。

### 6.3 `src/openharness/extended/channel/ipc.py`

```python
class LeaderQueueChannel:
    def close(self) -> None:
        if self._transport != "queue":
            return
        _close_multiprocessing_queue(self._downlink_queue)  # close + join_thread
        _close_multiprocessing_queue(self._uplink_queue)
        self._downlink_queue = None   # 丢引用 (channel 持有的)
        self._uplink_queue = None
```

注意三层引用都得断开，SemLock weakref 才能 fire：
1. `ChannelRegistry._handles` → `ExpertHandle.channel` → `LeaderQueueChannel._downlink_queue` → `Queue._sem` 等

### 6.4 `src/openharness/ui/runtime.py`

把 `close_all_channels` 提到 `_shutdown_async_agents` 之后、`stop_docker_sandbox` / `mcp_manager.close()` / `SESSION_END` hook **之前**。慢步骤被 SIGTERM 中断时，sem 已经早释放完了。

### 6.5 `frontend/terminal/src/App.tsx`

Ctrl+C 触发"发 shutdown JSON + 5s 兜底 timer"，让 backend 走 graceful 路径：

```typescript
if (key.ctrl && chunk === 'c') {
    session.sendRequest({type: 'shutdown'});
    forceExitTimerRef.current = setTimeout(() => exit(), GRACEFUL_BACKEND_EXIT_MS);
    return;
}
```

backend 自然退出后 React `useBackendSession` 收到 child `exit` 事件，`onExit` 清掉 timer 并 unmount。

---

## 七、验证方法

### 7.1 隔离单元测试（不带 OpenHarness 全量启动）

复用排查阶段写的复现脚本作为回归基准：

```bash
# A) 不调 close_all_channels，仅装 SIGTERM handler（验证 handler 能兜底）
cat > /tmp/leak_test_baseline_sigterm.py << 'EOF'
import os, signal, sys, time
sys.path.insert(0, '<repo>/src')
from openharness.ui.backend_host import _install_sigterm_channel_cleanup
from openharness.extended.channel.ipc import create_channel
from openharness.extended.channel.registry import get_channel_registry, ExpertHandle

reg = get_channel_registry()
for i in range(2):
    ch = create_channel(f"final_{i}")
    reg.register(ExpertHandle(
        expert_id=f"final_{i}", expert_type="mobile_gui",
        task_id=f"final_{i}", channel=ch, process=None, task="dummy",
    ))

_install_sigterm_channel_cleanup()
print(f"channels created, sleeping...", flush=True)
time.sleep(60)
EOF

python /tmp/leak_test_baseline_sigterm.py 2>&1 &
PID=$!
sleep 1
kill -TERM $PID
wait $PID
sleep 1   # 给 resource_tracker 时间打印
# 期望：终端无 "leaked semaphore" warning、无 KeyError traceback
```

```bash
# B) 走 close_all_channels 优雅路径
cat > /tmp/leak_test_graceful.py << 'EOF'
import sys, os, time
sys.path.insert(0, '<repo>/src')
from openharness.extended.channel.ipc import create_channel
from openharness.extended.channel.registry import get_channel_registry, ExpertHandle
reg = get_channel_registry()
for i in range(2):
    ch = create_channel(f"g_{i}")
    reg.register(ExpertHandle(
        expert_id=f"g_{i}", expert_type="mobile_gui",
        task_id=f"g_{i}", channel=ch, process=None, task="dummy"))
reg.close_all_channels()
time.sleep(60)
EOF

python /tmp/leak_test_graceful.py 2>&1 &
PID=$!
sleep 1
kill -TERM $PID
# 期望：同上，无任何告警
```

### 7.2 端到端验证（实际场景）

```bash
./run.sh
# 在 OpenHarness 中下发一个会触发 delegate_to_expert 的任务
# 等待任务完成、看到结果总结
# 按 Ctrl+C 退出
```

**通过条件**：终端最后一行下方**不再**出现：

```
/.../resource_tracker.py:224: UserWarning:
resource_tracker: There appear to be N leaked semaphore objects ...
```

### 7.3 排查中常见的"伪修复"陷阱

| 误以为有效但其实无效 | 真实原因 |
|---|---|
| 只调 `Queue.close()` + `Queue.join_thread()` | 不释放 SemLock 引用，sem 不被注销 |
| 只丢引用不调 `gc.collect()` | GC 可能在 SIGTERM 之后才跑 |
| 只在 `atexit` 里清理 | SIGTERM 默认 handler 不跑 atexit |
| 只在前端做 graceful timer | 后端慢步骤超过 timer 时仍被 SIGTERM |
| 加 `_resource_tracker.unregister(name)` 手动注销 | 会和 `SemLock._cleanup` 的 Finalize **重复 unregister**，resource_tracker 会打出 6 个 `KeyError: '/mp-xxx'` traceback（更难看） |

最后一条特别值得注意 —— 排查中曾经写过手动 unregister 的方案，验证发现把"6 个 leak warning"换成了"6 个 KeyError traceback"，原因是 SemLock 的 Finalize 也会 unregister 一次，第二次就 set.remove 失败。最终选了"丢引用 + gc.collect()"方案，让 Finalize 自己跑且只跑一次。

---

## 八、经验总结

1. **`multiprocessing.Queue.close()` 不释放底层 SemLock**。要真正彻底释放，必须丢掉所有 Python 层引用 + 触发 GC，让 `util.Finalize` 注册的 `_cleanup` 跑完。

2. **`atexit` 不是 SIGTERM 的兜底**。Python 默认 SIGTERM handler 直接 `_exit`，不走 atexit。要在 SIGTERM 里做事，必须自己 `signal.signal(SIGTERM, ...)`。

3. **跨语言/跨进程的"优雅关闭"协议要双向收尾**。React 发 shutdown 给 Python，Python 跑 cleanup，但**Python 必须能扛住 React 反悔（force kill）**。前端 timer + 后端 SIGTERM handler 这种"互不信任、双重防御"组合最稳。

4. **`resource_tracker` 是诊断工具，不是 cleanup 工具**。它的报警表示"你的 cleanup 不到位"，而不是"我帮你修了"。它确实会调 `sem_unlink` 兜底，但 warning 已经被打到 stderr，会污染终端体验。

5. **复现 + 二分是关键**。排查全程一直在最小化 reproduce —— 从 `mp.Queue + SIGTERM` 的纯脚本，到加 `close()` 的脚本，到加手动 unregister 的脚本，到加 `gc.collect()` 的脚本，再到加 SIGTERM handler 的脚本。每一步都能立即看到 warning 出/不出，才能锁定每个改动是否必要。最终最小修复就是这个过程的产物。

---

## 九、相关参考

- CPython `Lib/multiprocessing/synchronize.py` — `SemLock.__init__` / `_cleanup`
- CPython `Lib/multiprocessing/queues.py` — `Queue.close` / `_finalize_close`
- CPython `Lib/multiprocessing/resource_tracker.py` — 主循环、UNREGISTER 处理、shutdown 时 leak warning
- 项目相关代码：
  - `src/openharness/extended/channel/ipc.py` (`LeaderQueueChannel`, `create_channel`)
  - `src/openharness/extended/channel/registry.py` (`ChannelRegistry.close_all_channels`)
  - `src/openharness/ui/backend_host.py` (`ReactBackendHost.run`, `_install_sigterm_channel_cleanup`)
  - `src/openharness/ui/runtime.py` (`close_runtime`)
  - `frontend/terminal/src/App.tsx` (Ctrl+C handler)
  - `frontend/terminal/src/hooks/useBackendSession.ts` (`spawn` + `killChild`)
