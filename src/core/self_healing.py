"""
Dual-Tier Disaster Recovery & Self-Healing Engine for PNetGimini (Phase 4 / v3.5).
Provides automated escalation from guest-level CLI rollback failures (Tier 1)
to EVE IaC hypervisor-level infrastructure lifecycle actions (Tier 2):
  - Isolated Node Wipe & Restart (stop -> wipe -> start)
  - Lab-wide Desired State Drift Reconciliation (reconcile_project)
  - Console Prompt Synchronization and Re-connection Probing
  - NodeStateMachine Lifecycle Transition Coordination
"""

import logging
from typing import Dict, Any, Optional

from src.models.state import NodeState, NodeStateMachine
from src.plugins.eve_iac_connector import EveIacConnector


class SelfHealingError(RuntimeError):
    """Raised when Tier-2 infrastructure self-healing recovery actions fail."""
    def __init__(self, message: str, node: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.node = node
        self.details = details or {}


class SelfHealingEngine:
    """
    Coordinates Tier-2 disaster recovery escalations when in-guest CLI operations deadlock,
    fail authentication permanently, or exhaust Tier-1 rollback attempts.
    """

    def __init__(
        self,
        connector: EveIacConnector,
        logger: Optional[logging.Logger] = None
    ):
        self.connector = connector
        self.logger = logger or logging.getLogger(__name__)

    def heal_node(
        self,
        lab_id: str,
        node_key: str,
        state_machine: Optional[NodeStateMachine] = None,
        reason: str = "Rollback exhaustion"
    ) -> Dict[str, Any]:
        """
        Escalates an unrecoverable node failure to hypervisor Tier-2 recovery:
          1. Transition state machine to ESCALATED_TIER2.
          2. Execute stop -> wipe -> start sequence to reset to golden startup configuration.
          3. Transition state machine to BOOTING.
          4. Wait for console prompt to verify node readiness.
          5. Transition state machine to CONSOLE_READY.
        """
        self.logger.warning(
            f"[Tier2 SelfHealing] Escalating node '{node_key}' in lab '{lab_id}' "
            f"due to: {reason}"
        )

        # 1. State machine transition to ESCALATED_TIER2
        if state_machine:
            state_machine.transition_to(NodeState.ESCALATED_TIER2)

        # 2. Hypervisor lifecycle reset (stop -> wipe -> start)
        success = self.connector.reset_node_lifecycle(lab_id, node_key)
        if not success:
            err_msg = f"Tier-2 hypervisor reset sequence failed for node '{node_key}'."
            self.logger.critical(f"[Tier2 SelfHealing] {err_msg}")
            raise SelfHealingError(err_msg, node=node_key)

        # 3. Transition to BOOTING
        if state_machine:
            state_machine.transition_to(NodeState.BOOTING)

        # 4. Wait for console readiness
        self.logger.info(f"[Tier2 SelfHealing] Waiting for console readiness on node '{node_key}'...")
        console_ready = self.connector.wait_console(lab_id, node_key, timeout=120)
        if not console_ready:
            err_msg = f"Console readiness probe timed out after Tier-2 reset for node '{node_key}'."
            self.logger.error(f"[Tier2 SelfHealing] {err_msg}")
            raise SelfHealingError(err_msg, node=node_key)

        # 5. Transition to CONSOLE_READY
        if state_machine:
            state_machine.transition_to(NodeState.CONSOLE_READY)

        self.logger.info(
            f"[Tier2 SelfHealing] Successfully healed node '{node_key}' back to CONSOLE_READY."
        )

        return {
            "status": "RECOVERED",
            "tier": 2,
            "action": "WIPE_AND_RESTART",
            "node": node_key,
            "lab_id": lab_id,
            "reason": reason,
            "console_ready": True
        }

    def reconcile_topology(
        self,
        lab_id: str,
        direction: str = "to_eve",
        confirm: bool = True
    ) -> Dict[str, Any]:
        """
        Escalates to lab-wide topology state reconciliation against topology.yml.
        """
        self.logger.warning(
            f"[Tier2 SelfHealing] Initiating lab-wide reconciliation for lab '{lab_id}' "
            f"(direction='{direction}', confirm={confirm})."
        )
        return self.connector.reconcile_lab(lab_id, direction=direction, confirm=confirm)
