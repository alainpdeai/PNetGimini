"""
Declarative Operational Intent Schema & Pydantic Data Contract.
Serves as the Single Source of Truth for PNetGimini operational intent files.
Supports apiVersion versioning and automated JSON Schema export.
"""

import json
from typing import Dict, List, Optional, Literal, Union
from pydantic import BaseModel, Field, ConfigDict


class PingAssertion(BaseModel):
    """Declarative ICMP ping probe assertion."""
    target: str = Field(description="Destination IP address or hostname to probe")
    count: int = Field(default=5, ge=1, le=100, description="Number of ICMP packets to transmit")
    source: Optional[str] = Field(default=None, description="Source interface or IP address (e.g. Loopback0)")
    max_loss_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Maximum permitted packet loss percentage (0.0 = 100% delivery required)")
    max_avg_rtt_ms: Optional[float] = Field(default=None, ge=0.0, description="Maximum permitted average round-trip latency in milliseconds")
    vrf: Optional[str] = Field(default=None, description="VRF instance name for ping transmission")


class OspfAssertion(BaseModel):
    """Declarative OSPF convergence and metric assertion."""
    neighbor_ip: Optional[str] = Field(default=None, description="Expected neighbor IP address")
    expected_state: Literal["FULL", "2-WAY", "EXSTART", "EXCHANGE", "INIT", "ATTEMPT", "DOWN"] = Field(
        default="FULL",
        description="Target OSPF adjacency state"
    )
    interface: Optional[str] = Field(default=None, description="Local interface connected to the OSPF neighbor")
    min_total_neighbors: Optional[int] = Field(default=None, ge=0, description="Minimum count of FULL neighbors required on the device")
    route: Optional[str] = Field(default=None, description="IP prefix to assert in the routing table (e.g. 10.200.0.0/24)")
    expected_metric: Optional[int] = Field(default=None, ge=1, description="Expected calculated OSPF metric cost for the route")
    expected_path_type: Optional[Literal["O", "O IA", "O E1", "O E2"]] = Field(default=None, description="Expected OSPF route type")
    polling_interval_seconds: float = Field(default=2.0, ge=0.5, le=30.0, description="Retry interval while waiting for convergence")
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0, description="Maximum wait time before declaring assertion failure")


class BgpAssertion(BaseModel):
    """Declarative BGP peering session assertion."""
    peer_ip: str = Field(description="BGP neighbor IP address")
    expected_state: Literal["Established", "Active", "Connect", "Idle", "OpenSent", "OpenConfirm"] = Field(
        default="Established",
        description="Target BGP peering state"
    )
    min_prefixes_received: int = Field(default=0, ge=0, description="Minimum accepted prefix floor to prevent blackholing")
    polling_interval_seconds: float = Field(default=2.0, ge=0.5, le=30.0, description="Retry interval while waiting for convergence")
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0, description="Maximum wait time before declaring peering timeout")


class InterfaceAssertion(BaseModel):
    """Declarative interface health assertion."""
    name: str = Field(description="Canonical interface name (e.g. GigabitEthernet0/1)")
    admin_status: Literal["up", "down"] = Field(default="up", description="Expected administrative status")
    line_protocol: Literal["up", "down"] = Field(default="up", description="Expected line protocol status")
    max_crc_errors_delta: int = Field(default=0, ge=0, description="Maximum permitted increment in interface CRC errors")


class RouteAssertion(BaseModel):
    """Declarative routing table FIB assertion."""
    prefix: str = Field(description="Destination prefix in CIDR notation (e.g. 10.200.0.0/24)")
    protocol: Literal["ospf", "bgp", "static", "connected", "direct"] = Field(description="Expected routing protocol origin")
    expected_next_hop: Optional[str] = Field(default=None, description="Expected next-hop forwarding gateway IP")


class HealthGateVerify(BaseModel):
    """Structured collection of declarative telemetry assertions."""
    ping: Optional[List[PingAssertion]] = Field(default=None, description="ICMP ping SLA assertions")
    ospf: Optional[List[OspfAssertion]] = Field(default=None, description="OSPF adjacency & metric assertions")
    bgp: Optional[List[BgpAssertion]] = Field(default=None, description="BGP session & prefix floor assertions")
    interfaces: Optional[List[InterfaceAssertion]] = Field(default=None, description="Interface operational state assertions")
    routes: Optional[List[RouteAssertion]] = Field(default=None, description="FIB route & next-hop assertions")


class ChaosAssertion(BaseModel):
    """Performance deadlines during digital twin chaos injection."""
    max_failover_loss_packets: int = Field(default=1, ge=0, description="Maximum allowed packet loss during failover")
    max_convergence_time_ms: int = Field(default=1500, ge=10, le=60000, description="Maximum allowed convergence latency in ms")


class ChaosTestSpec(BaseModel):
    """Digital twin environmental chaos experiment specification."""
    name: str = Field(description="Descriptive identifier of the chaos test")
    action: Literal["link_suspend", "suspend", "flap", "apply_quality", "quality", "degrade"] = Field(
        default="link_suspend",
        description="EVE IaC environmental primitive to inject"
    )
    target_link: str = Field(
        description="Target link expression, e.g. 'Spine-01:e0/1 <-> Leaf-01:e0/1' or canonical 'link_12'"
    )
    duration_seconds: float = Field(default=10.0, ge=0.1, le=3600.0, description="Duration of physical outage")
    delay_ms: Optional[int] = Field(default=None, ge=0, description="Injected Netem latency delay in ms")
    jitter_ms: Optional[int] = Field(default=None, ge=0, description="Injected Netem jitter in ms")
    loss_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Injected Netem packet loss percentage")
    assertions: Optional[ChaosAssertion] = Field(default=None, description="Failover convergence criteria")


class DeviceOperationalIntent(BaseModel):
    """Operational Day-1/Day-2 intent for a single device node."""
    config: List[str] = Field(default_factory=list, description="Day-1 / Day-2 configuration CLI command sequence")
    show: List[str] = Field(default_factory=list, description="Operational telemetry snapshot retrieval commands")
    verify: Optional[Union[HealthGateVerify, List[str]]] = Field(
        default=None,
        description="Declarative health gate assertions or classic string verify commands"
    )
    chaos_tests: Optional[List[ChaosTestSpec]] = Field(
        default=None,
        description="Environmental chaos experiments linked to this device path"
    )


class IntentMetadata(BaseModel):
    """Metadata describing the intent manifest."""
    name: Optional[str] = Field(default=None, description="Deployment manifest name")
    author: Optional[str] = Field(default=None, description="Author or Git committer")
    description: Optional[str] = Field(default=None, description="Operational purpose of the deployment")


class OperationalIntentDocument(BaseModel):
    """
    Standardized, version-controlled top-level Operational Intent manifest.
    Adheres to Kubernetes-style apiVersion / kind schema conventions.
    """
    model_config = ConfigDict(extra="allow")

    apiVersion: Literal["pnetgimini/v1alpha1"] = Field(
        default="pnetgimini/v1alpha1",
        description="Schema specification version"
    )
    kind: Literal["OperationalIntent"] = Field(
        default="OperationalIntent",
        description="Resource kind"
    )
    metadata: Optional[IntentMetadata] = Field(default=None, description="Manifest metadata")
    devices: Dict[str, DeviceOperationalIntent] = Field(
        default_factory=dict,
        description="Mapping of device hostnames / keys to operational intents"
    )

    @classmethod
    def export_json_schema(cls, output_path: Optional[str] = None) -> str:
        """Exports the formal JSON Schema for tooling and IDE validation."""
        schema_dict = cls.model_json_schema()
        schema_str = json.dumps(schema_dict, indent=2)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(schema_str)
        return schema_str
