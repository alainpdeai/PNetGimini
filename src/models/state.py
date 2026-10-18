"""
Node Lifecycle State Machine for PNetGimini & EVE IaC Integration.
Defines canonical node lifecycle states, valid state transitions, and
generates Mermaid state diagrams for architectural specification alignment.
"""

from enum import Enum
from typing import Dict, Set, Tuple


class NodeState(str, Enum):
    """
    Standardized lifecycle states of a virtualized network node in the digital twin:
      - ALLOCATED: Node resource reserved in topology.yml, VM not yet booted.
      - BOOTING: VM powered on by EVE-NG hypervisor, booting kernel / NOS.
      - CONSOLE_READY: Serial/Telnet/OOB console emits recognizable shell prompt.
      - OOB_READY: Out-of-band proxy daemon is up and Netmiko/SSH session is established.
      - CONFIGURABLE: Day-1/Day-2 configuration push underway via AsyncDeploymentEngine.
      - VERIFIED: Operational health gate assertions (OSPF/BGP/Ping) successfully passed.
      - CONVERGED: Chaos/FRR convergence assertions verified under digital twin stress.
      - FAILED_ROLLBACK: Deployment failure detected, Tier-1 diff reversal applied.
      - ESCALATED_TIER2: Unrecoverable node deadlock, escalated to hypervisor stop/wipe/start.
    """
    ALLOCATED = "allocated"
    BOOTING = "booting"
    CONSOLE_READY = "console_ready"
    OOB_READY = "oob_ready"
    CONFIGURABLE = "configurable"
    VERIFIED = "verified"
    CONVERGED = "converged"
    FAILED_ROLLBACK = "failed_rollback"
    ESCALATED_TIER2 = "escalated_tier2"


# Permitted state transition table
VALID_TRANSITIONS: Dict[NodeState, Set[NodeState]] = {
    NodeState.ALLOCATED: {NodeState.BOOTING},
    NodeState.BOOTING: {NodeState.CONSOLE_READY, NodeState.ESCALATED_TIER2},
    NodeState.CONSOLE_READY: {NodeState.OOB_READY, NodeState.ESCALATED_TIER2},
    NodeState.OOB_READY: {NodeState.CONFIGURABLE, NodeState.ESCALATED_TIER2},
    NodeState.CONFIGURABLE: {NodeState.VERIFIED, NodeState.FAILED_ROLLBACK},
    NodeState.VERIFIED: {NodeState.CONVERGED, NodeState.FAILED_ROLLBACK},
    NodeState.CONVERGED: set(),  # Terminal desired state
    NodeState.FAILED_ROLLBACK: {NodeState.VERIFIED, NodeState.ESCALATED_TIER2},
    NodeState.ESCALATED_TIER2: {NodeState.BOOTING},  # Wipe/Restart cycle
}


class NodeStateMachine:
    """Enforces state validity and transition rules for lab nodes."""

    def __init__(self, node_key: str, initial_state: NodeState = NodeState.ALLOCATED):
        self.node_key = node_key
        self.current_state = initial_state

    def transition_to(self, new_state: NodeState) -> NodeState:
        """Transitions node to new_state if the edge is permitted."""
        allowed = VALID_TRANSITIONS.get(self.current_state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid node state transition for '{self.node_key}': "
                f"cannot move from '{self.current_state.value}' to '{new_state.value}'. "
                f"Allowed target states: {[s.value for s in allowed]}"
            )
        self.current_state = new_state
        return self.current_state

    @classmethod
    def generate_mermaid_diagram(cls) -> str:
        """Generates Mermaid stateDiagram-v2 definition directly from Python transitions."""
        lines = ["stateDiagram-v2"]
        lines.append("    [*] --> allocated")
        for src, dsts in VALID_TRANSITIONS.items():
            for dst in sorted(dsts, key=lambda s: s.value):
                lines.append(f"    {src.value} --> {dst.value}")
        lines.append("    converged --> [*]")
        return "\n".join(lines)
