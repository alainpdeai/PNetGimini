# Changelog

All notable changes to the PNetGimini project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-10-18

### Overview
This release implements Task 1 (v3.5 Dual-Tier Self-Healing Escalation Engine) and Task 2 ("Native First, Diff Fallback" Rollback Plugin), closing the disaster recovery and atomic transaction gap.

### Added
- **Dual-Tier Self-Healing Escalation Engine (`src/core/self_healing.py`)**:
  - `SelfHealingEngine`: Coordinates automated escalation from failed in-guest CLI operations (Tier 1) to EVE IaC infrastructure lifecycle actions (Tier 2).
  - Hypervisor lifecycle reset (`reset_node_lifecycle`): Executes `stop -> wipe -> start` sequence to return unresponsive nodes back to golden startup-config.
  - Lab-wide desired state drift reconciliation (`reconcile_topology`): Aligns live hypervisor state against `topology.yml` via EVE IaC `reconcile_project(direction='to_eve', confirm=True)`.
  - Node state machine transition tracking across `ESCALATED_TIER2 -> BOOTING -> CONSOLE_READY`.
- **EVE IaC Lifecycle API Support (`src/plugins/eve_iac_connector.py`)**:
  - Added `stop_node`, `wipe_node`, `start_node`, `reset_node_lifecycle`, and `reconcile_lab` with dual-mode support (SDK and REST OpenAPI fallback).
- **"Native First, Diff Fallback" Rollback Engine (`src/core/device_manager.py`)**:
  - Added `prefer_native_rollback` and `self_healing_engine` integration into `DeviceManager`.
  - On-device baseline snapshot archiving: Captures atomic snapshot (e.g. `flash:pnet_base_<timestamp>.cfg`) prior to command deployment.
  - Native atomic rollback: Evaluates `configure replace flash:... force` (Cisco IOS/XE). If native rollback reports errors or is unsupported, automatically falls back to `ConfigDiffEngine` (Tier 1 diff reversal).
  - Tier-1 failure escalation: Automatically triggers `SelfHealingEngine.heal_node()` if both native and diff rollbacks fail.
- **Vendor Adapter Extensions (`src/core/adapters/base.py`, `cisco.py`, `huawei.py`)**:
  - Added `supports_native_rollback()`, `get_native_snapshot_command()`, `get_native_rollback_command()`.
  - Cisco adapter returns `True` with atomic flash replacement; Huawei adapter cleanly defaults to Diff fallback.

### Test Suite Metrics
- **Total Test Cases**: 77 (expanded from 68)
- **Passing**: 77 (100% success rate)
- **Failures / Errors**: 0
- **Execution Time**: ~0.47s
- **New Test Modules**:
  - `tests/test_v35_native_rollback.py`: 4 tests (flag checking, native replace success, error fallback to diff, Huawei default diff)
  - `tests/test_v35_self_healing.py`: 5 tests (offline lifecycle, node reset with state machine, reset failure handling, topology reconcile, DeviceManager tier-2 escalation)

---

## [1.3.0] - 2026-10-18

### Overview
This milestone resolves all P0 architectural review findings from Alain Degreffe (EVE-NG / EVE IaC creator), incorporates Pydantic v2 schema solidification, implements surgical attribute-level diff reversal, and introduces routing convergence polling.

### Added
- **Formal Node Lifecycle State Machine (`src/models/state.py`)**:
  - `NodeState` enumeration (`allocated`, `booting`, `console_ready`, `oob_ready`, `configurable`, `deployed`, `operational`, `degraded`, `escalated_tier2`, `destroyed`).
  - Strict `VALID_TRANSITIONS` table with programmatic validation and automated Mermaid diagram generation (`generate_mermaid_diagram`).
- **Guaranteed Chaos Environmental Recovery (`src/plugins/chaos_orchestrator.py`)**:
  - `link_fault_context`: Synchronous/asynchronous context manager guaranteeing `un_suspend` and quality impairment clearing inside a `try ... finally` block.
  - `reset_all_lab_impairments(lab_id)`: Orchestrator startup sweep that queries active lab links and automatically resets residual impairments left by interrupted runs.
  - Deterministic `resolve_link_id()`: Translates human-readable link expressions (`"Spine-01:e0/1 <-> Leaf-01:e0/1"`) into stable IaC keys, raising an explicit `ValueError` on unresolvable endpoints.
- **Pydantic v2 Intent Schema (`src/models/intent.py`)**:
  - Declarative data models with `apiVersion: pnetgimini/v1alpha1` and `kind: OperationalIntent`.
  - Structured sub-models: `PingAssertion`, `OspfAssertion`, `BgpAssertion`, `InterfaceAssertion`, `RouteAssertion`, `ChaosTestSpec`, `ChaosAssertion`.
  - Exported canonical JSON Schema to [`docs/schemas/operational_intent_schema.json`](docs/schemas/operational_intent_schema.json).
- **Precision Attribute Baseline Reversal (`src/core/adapters/cisco.py`, `src/core/adapters/huawei.py`)**:
  - `ConfigDiffEngine` detects overridden interface attributes (`ip ospf cost`, `bandwidth`, `mtu`, `description`, etc.) and restores original baseline values instead of blindly negating with `no` or `undo`.
  - Refined Huawei `BLOCK_PATTERNS` to prevent false positive matching on interface OSPF sub-attributes.
- **Routing Convergence Polling (`src/core/health_gate.py`)**:
  - Added non-blocking retry loops with `polling_interval_seconds` and `timeout_seconds` in `_verify_ospf` and `_verify_bgp` to eliminate false-positive rollbacks during normal protocol convergence.
- **TraceGraph Architectural Integration**:
  - Continuous safety gatekeeper via `light-arch-guard` MCP server and AST graphify engine.
  - Archived legacy unreferenced scripts (`archive/`).

### Changed
- **Decomposed Health Gate Subroutines**:
  - Extracted `_evaluate_ospf_spec` and `_evaluate_bgp_spec` into modular functions, reducing function line length below TraceGraph thresholds and lowering static risk score.
- **Documentation Alignment (`docs/EVE_IAC_INTEGRATION_SPEC.md`)**:
  - Updated to v1.3.0, incorporating §1.3 Node State Machine, §2 `asyncio.to_thread` execution model, §3.4.3 restoration guarantees, §4 JSON Schema notes, §5.2 step numbering alignment (9a/9b/10/11), §7 API inventory alignment, and §8 Formal Acceptance Criteria.
- **Zero Chinese Characters Compliance**:
  - Sanitized docstrings and comments across `src/models/command.py` and `src/utils/logger.py`.

### Test Suite Metrics
- **Total Test Cases**: 68 (expanded from 52 in v1.2.0)
- **Passing**: 68 (100% success rate)
- **Failures / Errors**: 0
- **Execution Time**: ~0.43s
- **Test Modules**:
  - `tests/test_core.py`: 10 passed (adapters, attribute diff reversal, async engine)
  - `tests/test_full_pipeline.py`: 8 passed (parser, reports, diff rollback, deployment)
  - `tests/test_v31_security.py`: 8 passed (masking, env expansion, ssh keys)
  - `tests/test_v32_eve_iac_plugin.py`: 11 passed (connector, token auth, oob inventory)
  - `tests/test_v33_health_gate.py`: 26 passed (health gate assertions, convergence retry/timeout, chaos context, state machine)
  - `tests/test_v33_intent_schema.py`: 5 passed (pydantic validation, json schema export)

---

## [1.2.0] - 2026-10-18

### Added
- Declarative Health Gate Engine (`src/core/health_gate.py`) supporting ping SLAs, OSPF states, BGP peerings, interface CRCs, and route table assertions.
- Chaos Orchestration Plugin (`src/plugins/chaos_orchestrator.py`) integrating EVE IaC link suspension and Netem impairment injection.

---

## [1.1.0] - 2026-10-17

### Added
- EVE IaC Automation Connector (`src/plugins/eve_iac_connector.py`) supporting REST API authentication, project console discovery, and dynamic inventory binding.
- Out-of-band proxy connectivity for virtualized digital twin labs.

---

## [1.0.0] - 2026-10-15

### Added
- Initial automated multi-vendor configuration deployment engine (Cisco IOS, Huawei VRP).
- Async deployment engine with concurrency throttling and Netmiko integration.
- `ConfigDiffEngine` intra-node diff calculation and baseline rollback.
