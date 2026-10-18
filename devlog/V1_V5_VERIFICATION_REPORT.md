# Phase 1: 代码与现实核实报告 (V1–V5 Verification Report)

**核实执行时间**: 2026-10-18  
**核实方法**: 纯只读源码审查 + 测试套件覆盖率执行（未改动任何代码）  
**执行者**: Antigravity × TraceGraph 架构守门人  
**基线测试状态**: 52/52 PASSED (1.22s)  

---

## 1. 核实结论总览表

| 项 | 待核实内容 | 当前状态归类 | 核心发现与现状事实 | 对后续开发决策的影响 |
|:---|:---|:---:|:---|:---|
| **V1** | `ConfigDiffEngine` 如何处理“修改值”（改 IP、改 OSPF cost） | 🟡 **部分已解决，存在参数级盲区** | 1. `ip address` 和 `hostname`/`sysname` 已具备基线比对与旧值还原逻辑。<br/>2. 但对 `ip ospf cost` 等其他参数，退化为 `no <cmd>`，会导致配置变为协议默认值而非还原基线旧值。 | 在 Phase 4 需建立属性匹配字典，或者在支持的设备上推进原生事务回滚（IOS `configure replace`）。 |
| **V2** | asyncio 与 Netmiko（同步库）的结合方式与并发模型 | 🟢 **实现里已经解决** | 1. `AsyncDeploymentEngine` 明确使用了 `await asyncio.to_thread(mgr.deploy_commands)` 将阻塞 I/O 派发至工作线程池。<br/>2. 采用 `asyncio.Semaphore(self.max_concurrency)` 进行精确并发流控。 | **无需修改代码**，只需在规范文档 §2 和 §3 补充 `asyncio.to_thread` 线程池分发说明。 |
| **V3** | 单元测试数量（25/36/52）真实性与失败路径覆盖度 | 🟡 **数量真实，异常分支待补充** | 1. 52 个单元测试全部真实存在且 100% 通过（耗时 1.22s）。<br/>2. 覆盖了断言违背触发回滚的路径。<br/>3. 但尚未覆盖“回滚自身执行失败”和“探针抛出未捕获异常”等边缘分支。 | 在 Phase 2 与 Phase 4 补充负向边界测试；按 P3-1 建议将测试数量剥离出架构规范正文，移入 CHANGELOG。 |
| **V4** | 混沌测试中途异常退出时，链路是否一定被恢复 | 🟡 **进程内已保证，跨进程缺少清理** | 1. `ChaosOrchestrator.run_chaos_test` 已用 `try ... finally` 包裹，探针抛异常时 `finally` 保证恢复链路。<br/>2. 但缺少启动时的残留扫描清理（防进程被 SIGKILL 遗留挂起），也缺少异步上下文管理器封装。 | 在 Phase 2 中重构为 `asynccontextmanager`，并在引擎初始化时增加 `clean_residual_suspended_links` 扫尾。 |
| **V5** | 健康门断言是一次性检查还是带超时的轮询 | 🔴 **确实存在风险（急需改代码）** | 1. `HealthGateEngine` 的 OSPF 与 BGP 检查均为**纯单次瞬态执行（Single-shot）**。<br/>2. 刚下发配置后协议需要几秒至几十秒收敛，单次检查必然误报并引发不必要的 Diff 回滚！ | **高优先级代码重构**。在 Phase 4 为 OSPF/BGP 断言引入 `polling_interval`（如 2s）与 `timeout_seconds`（如 30s）重试循环。 |

---

## 2. 深度技术代码剖析 (Code Evidences)

### 2.1 V1: `ConfigDiffEngine` & `CiscoAdapter` 取反实现
在 [`src/core/adapters/cisco.py`](file:///D:/OneDrive/2025/Alain/pythonprogram/PnetGimini/src/core/adapters/cisco.py#L100-L113) 中：
```python
elif child_str.lower().startswith("ip address "):
    # 查找基线是否已有 IP 地址
    baseline_ip = None
    for b in baseline_set:
        if b.lower().startswith("ip address "):
            baseline_ip = b
            break
    if baseline_ip:
        reversal_cmds.append(baseline_ip)  # ✅ 正确还原旧 IP
    else:
        reversal_cmds.append("no ip address")
else:
    reversal_cmds.append(f"no {child_str}")  # ⚠️ 盲区：ip ospf cost 20 会被变成 no ip ospf cost 20
```
* **技术诊断**: 代码作者此前已经意识到了 IP 和 Hostname 覆盖的问题，并做了基线检索还原。但对于其他非 IP 的属性行（OSPF cost、MTU、Bandwidth、Description），目前的 `else:` 统一退化为了加 `no ` 前缀。如果基线原来是 `ip ospf cost 10`，下发了 `ip ospf cost 20`，回滚执行 `no ip ospf cost 20` 后，Cisco 设备会恢复为接口默认开销计算值（如 1），而不是基线的 10。

### 2.2 V2: `AsyncDeploymentEngine` 协程与线程池结合
在 [`src/core/async_engine.py`](file:///D:/OneDrive/2025/Alain/pythonprogram/PnetGimini/src/core/async_engine.py#L38-L40) 中：
```python
async with self.current_semaphore:
    # Offload blocking Netmiko I/O to worker thread without blocking the asyncio event loop
    result = await asyncio.to_thread(mgr.deploy_commands)
```
* **技术诊断**: 实现非常标准规范。`asyncio.to_thread` 是 Python 3.9+ 官方推荐的无缝桥接同步阻塞库的标准机制，搭配 `asyncio.Semaphore` 控制线程并发上限，既保护了事件循环不被阻塞，又避免了手工维护 ThreadPoolExecutor 的复杂性。文档此前未详细解释这层包装，导致评审方产生“Netmiko 同步阻塞如何与 asyncio 协程共存”的疑问。只需在规范补齐描述即可。

### 2.3 V4: `ChaosOrchestrator` 混沌恢复保障
在 [`src/plugins/chaos_orchestrator.py`](file:///D:/OneDrive/2025/Alain/pythonprogram/PnetGimini/src/plugins/chaos_orchestrator.py#L92-L144) 中：
```python
try:
    if action in ("link_suspend", "suspend", "flap"):
        self.connector.set_link_suspend(lab_id, link_id, suspended=True)
        link_suspended = True
    ...
finally:
    if link_suspended:
        self.connector.set_link_suspend(lab_id, link_id, suspended=False)
```
* **技术诊断**: 针对代码运行期间的 probe 异常、断言失败，代码已有 `finally` 兜底恢复。但如果整个进程被外部强制 kill，或者用户在使用自定义流程时未通过 `run_chaos_test` 调用，链路状态可能驻留。因此需要：
  1. 提供上下文管理器：`async with orchestrator.fault_context(lab_id, link_id):`
  2. 提供开机扫描恢复：`orchestrator.reset_all_lab_impairments(lab_id)`

### 2.4 V5: `HealthGateEngine` 瞬时单次断言隐患
在 [`src/core/health_gate.py`](file:///D:/OneDrive/2025/Alain/pythonprogram/PnetGimini/src/core/health_gate.py#L198-L225) 中：
```python
if (neighbor_ip or min_neighbors is not None) and output_neighbor is None:
    output_neighbor = net_connect.send_command("show ip ospf neighbor")

# 立即比较状态
if found_state != expected_state:
    failed = True
    reasons.append(...)
```
* **技术诊断**: 无任何重试循环。网络设备从接口 `no shutdown` 到 OSPF 经过 Init/2-Way/ExStart/Exchange 到达 Full 通常需要 5~15 秒，BGP 从 Connect/Active 到 Established 需要 10~30 秒。立即检查 100% 触发 `HealthGateError` 并导致不必要的 Diff 回滚。这是当前系统中最关键的逻辑漏洞。

---

## 3. Phase 1 完成确认与下一步动作

Phase 1 核实工作已全部完成，5 个技术疑点已彻底查清并固化。  
**结论**：
- V2 无需改代码，只补文档；
- V1、V4、V5 需按计划在 Phase 2 和 Phase 4 进行代码级升级；
- V3 测试数量属实，需剥离出规范正文。

即刻进入 **Phase 2: 解决 P0 缺陷（代码与文档同步）**！
