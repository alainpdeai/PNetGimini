"""
EVE IaC Connector Plugin for PNetGimini (Phase 2 / v3.2).
Provides dynamic inventory discovery, console prompt synchronization, and intent binding
via the official EVE IaC Python SDK or standard REST OpenAPI endpoints.
"""

import os
import re
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.models.device import Device
from src.models.command import Command
from src.core.config_parser import ConfigParser

# Attempt optional import of official eveiac SDK
try:
    import eveiac
    from eveiac import EveIacClient
    HAS_EVEIAC = True
except ImportError:
    eveiac = None
    EveIacClient = None
    HAS_EVEIAC = False


class EveIacConnector:
    """
    Connects PNetGimini to EVE IaC (Infrastructure as Code) control plane.
    Implements:
      1. Authentication & Session Management
      2. Dynamic Topology Discovery (list_project_consoles -> Device mapping)
      3. Console Readiness Synchronization (wait_console server probe)
      4. Configuration Intent Binding (linking Day-1/Day-2 commands to discovered nodes)
    """

    DEFAULT_PROMPT_PATTERN = r"([>#]|<.+>|Press RETURN)"
    DEFAULT_TIMEOUT = 60

    DEFAULT_TYPE_MAPPINGS = {
        "huawei": ("huawei_vrp_telnet", "huawei_vrp"),
        "vrp": ("huawei_vrp_telnet", "huawei_vrp"),
        "ne40": ("huawei_vrp_telnet", "huawei_vrp"),
        "arista": ("arista_eos_telnet", "arista_eos"),
        "eos": ("arista_eos_telnet", "arista_eos"),
        "juniper": ("juniper_junos_telnet", "juniper_junos"),
        "junos": ("juniper_junos_telnet", "juniper_junos"),
        "cisco": ("cisco_ios_telnet", "cisco_ios"),
        "ios": ("cisco_ios_telnet", "cisco_ios"),
        "iol": ("cisco_ios_telnet", "cisco_ios"),
        "qemu": ("cisco_ios_telnet", "cisco_ios"),
    }

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        offline_mode: bool = False,
        custom_type_mappings: Optional[Dict[str, tuple]] = None
    ):
        """
        Initialize the EVE IaC Connector.
        Parameters:
            url: EVE IaC API endpoint (defaults to EVE_IAC_URL or http://localhost:8080)
            token: Bearer token for API authentication (defaults to EVE_IAC_TOKEN)
            username: Optional login username (defaults to EVE_IAC_USER)
            password: Optional login password (defaults to EVE_IAC_PASS)
            offline_mode: Enable offline mock simulation for tests or air-gapped environments
            custom_type_mappings: Dictionary of custom driver type mappings
        """
        self.url = (url or os.getenv("EVE_IAC_URL", "http://localhost:8080")).rstrip("/")
        self.token = token or os.getenv("EVE_IAC_TOKEN", "")
        self.username = username or os.getenv("EVE_IAC_USER", "")
        self.password = password or os.getenv("EVE_IAC_PASS", "")
        self.offline_mode = offline_mode or (os.getenv("EVE_IAC_OFFLINE", "0").lower() in ("1", "true", "yes"))
        
        self.type_mappings = dict(self.DEFAULT_TYPE_MAPPINGS)
        if custom_type_mappings:
            self.type_mappings.update(custom_type_mappings)

        self.chaos_tests: List[Dict[str, Any]] = []
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize official eveiac client or prepare REST client."""
        if self.offline_mode:
            logging.info("EveIacConnector initialized in OFFLINE / SIMULATION mode.")
            return

        if HAS_EVEIAC and EveIacClient:
            try:
                self.client = EveIacClient(base_url=self.url, token=self.token)
                logging.info(f"Initialized official EVE IaC SDK client: {self.url}")
            except Exception as e:
                logging.warning(f"Failed to initialize official EveIacClient ({e}), using REST client.")
                self.client = None
        else:
            logging.info(f"Using built-in REST client for EVE IaC API at {self.url}")

    def authenticate(self) -> bool:
        """
        Authenticate against EVE IaC API if token is not already present.
        """
        if self.offline_mode:
            self.token = "mock-offline-token-eve-iac"
            return True

        if self.token:
            return True

        if not self.username or not self.password:
            logging.warning("No EVE IaC token or login credentials provided; proceeding unauthenticated.")
            return False

        login_url = f"{self.url}/api/v1/auth/login"
        payload = json.dumps({"username": self.username, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(
            login_url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.token = data.get("token") or data.get("access_token", "")
                if self.token:
                    logging.info("Successfully authenticated with EVE IaC control plane.")
                    if self.client and hasattr(self.client, "token"):
                        self.client.token = self.token
                    return True
        except Exception as e:
            logging.error(f"Authentication failed against EVE IaC at {login_url}: {e}")
            raise RuntimeError(f"EVE IaC Authentication Failed: {e}")

        return False

    def list_consoles(self, lab_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve console records for a given lab project.
        Maps to EVE IaC `list_project_consoles(lab)`.
        Returns:
            List of dicts: [{'node': str, 'name': str, 'status': str, 'protocol': str, 'host': str, 'port': int}]
        """
        if self.offline_mode:
            return self._mock_list_consoles(lab_id)

        # 1. Try official SDK
        if self.client and hasattr(self.client, "list_project_consoles"):
            try:
                res = self.client.list_project_consoles(lab=lab_id)
                # Parse SDK response (object or dict)
                if hasattr(res, "consoles"):
                    records = []
                    for c in res.consoles:
                        records.append({
                            "node": getattr(c, "node", ""),
                            "name": getattr(c, "name", ""),
                            "status": getattr(c, "status", "running"),
                            "protocol": getattr(c, "protocol", "telnet"),
                            "host": getattr(c, "host", "127.0.0.1"),
                            "port": int(getattr(c, "port", 23))
                        })
                    return records
                elif isinstance(res, dict) and "consoles" in res:
                    return res["consoles"]
            except Exception as e:
                logging.warning(f"Official SDK list_project_consoles failed ({e}); falling back to REST endpoint.")

        # 2. REST API Fallback
        consoles_url = f"{self.url}/api/v1/projects/{lab_id}/consoles"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(consoles_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("consoles", [])
        except Exception as e:
            logging.error(f"Failed to query consoles from EVE IaC for lab '{lab_id}': {e}")
            raise RuntimeError(f"EVE IaC Consoles Discovery Failed: {e}")

    def wait_console(
        self,
        lab_id: str,
        node: str,
        pattern: str = DEFAULT_PROMPT_PATTERN,
        timeout: int = DEFAULT_TIMEOUT
    ) -> bool:
        """
        Wait for a node's console prompt readiness probe.
        Maps to EVE IaC `wait_console(lab, node, pattern, timeout)`.
        """
        if self.offline_mode:
            logging.info(f"[Offline] Console prompt verified ready for node {node} (pattern='{pattern}').")
            return True

        # 1. Try official SDK
        if self.client and hasattr(self.client, "wait_console"):
            try:
                self.client.wait_console(lab=lab_id, node=node, pattern=pattern, timeout=timeout)
                logging.info(f"Node {node} console prompt ready according to EVE IaC SDK.")
                return True
            except Exception as e:
                logging.warning(f"Official SDK wait_console failed ({e}); falling back to REST probe.")

        # 2. REST API Fallback
        wait_url = f"{self.url}/api/v1/projects/{lab_id}/consoles/{node}/wait"
        payload = json.dumps({"pattern": pattern, "timeout": timeout}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(wait_url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ready = data.get("status") in ("ready", "success", "ok") or resp.status == 200
                logging.info(f"Node {node} prompt readiness response: {data}")
                return ready
        except Exception as e:
            logging.warning(f"Console wait probe failed for node {node} in lab {lab_id}: {e}")
            return False

    def stop_node(self, lab_id: str, node: str) -> bool:
        """
        Stop a virtual machine node in the hypervisor.
        Maps to EVE IaC node stop lifecycle endpoint.
        """
        if self.offline_mode:
            logging.info(f"[Offline] Successfully stopped node '{node}' in lab '{lab_id}'.")
            return True

        if self.client and hasattr(self.client, "stop_node"):
            try:
                self.client.stop_node(lab=lab_id, node=node)
                logging.info(f"Node '{node}' stopped via EVE IaC SDK.")
                return True
            except Exception as e:
                logging.warning(f"SDK stop_node failed ({e}); falling back to REST.")

        url = f"{self.url}/api/v1/projects/{lab_id}/nodes/{node}/stop"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logging.error(f"Failed to stop node '{node}' in lab '{lab_id}': {e}")
            return False

    def wipe_node(self, lab_id: str, node: str) -> bool:
        """
        Wipe a node's ephemeral disk / nvram back to golden baseline startup config.
        Maps to EVE IaC node wipe lifecycle endpoint.
        """
        if self.offline_mode:
            logging.info(f"[Offline] Successfully wiped node '{node}' in lab '{lab_id}'.")
            return True

        if self.client and hasattr(self.client, "wipe_node"):
            try:
                self.client.wipe_node(lab=lab_id, node=node)
                logging.info(f"Node '{node}' wiped via EVE IaC SDK.")
                return True
            except Exception as e:
                logging.warning(f"SDK wipe_node failed ({e}); falling back to REST.")

        url = f"{self.url}/api/v1/projects/{lab_id}/nodes/{node}/wipe"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logging.error(f"Failed to wipe node '{node}' in lab '{lab_id}': {e}")
            return False

    def start_node(self, lab_id: str, node: str) -> bool:
        """
        Power on / start a virtual machine node in the hypervisor.
        Maps to EVE IaC node start lifecycle endpoint.
        """
        if self.offline_mode:
            logging.info(f"[Offline] Successfully started node '{node}' in lab '{lab_id}'.")
            return True

        if self.client and hasattr(self.client, "start_node"):
            try:
                self.client.start_node(lab=lab_id, node=node)
                logging.info(f"Node '{node}' started via EVE IaC SDK.")
                return True
            except Exception as e:
                logging.warning(f"SDK start_node failed ({e}); falling back to REST.")

        url = f"{self.url}/api/v1/projects/{lab_id}/nodes/{node}/start"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logging.error(f"Failed to start node '{node}' in lab '{lab_id}': {e}")
            return False

    def reset_node_lifecycle(self, lab_id: str, node: str) -> bool:
        """
        Execute full lifecycle wipe and reset sequence: stop -> wipe -> start.
        Restores clean golden state for unrecoverable guest CLI deadlocks.
        """
        logging.warning(f"Executing Tier-2 node lifecycle reset (stop -> wipe -> start) on '{node}'...")
        if not self.stop_node(lab_id, node):
            logging.error(f"Failed to stop node '{node}' during lifecycle reset.")
            return False
        if not self.wipe_node(lab_id, node):
            logging.error(f"Failed to wipe node '{node}' during lifecycle reset.")
            return False
        if not self.start_node(lab_id, node):
            logging.error(f"Failed to start node '{node}' during lifecycle reset.")
            return False
        logging.info(f"Tier-2 lifecycle reset sequence successfully executed for node '{node}'.")
        return True

    def reconcile_lab(self, lab_id: str, direction: str = "to_eve", confirm: bool = True) -> Dict[str, Any]:
        """
        Reconcile lab-wide desired state against topology.yml.
        Maps to EVE IaC `reconcile_project(direction='to_eve', confirm=True)`.
        """
        if self.offline_mode:
            logging.info(f"[Offline] Lab-wide reconciliation applied to lab '{lab_id}' (direction={direction}, confirm={confirm}).")
            return {"status": "RECONCILED", "lab_id": lab_id, "direction": direction, "confirmed": confirm}

        if self.client and hasattr(self.client, "reconcile_project"):
            try:
                res = self.client.reconcile_project(lab=lab_id, direction=direction, confirm=confirm)
                return {"status": "RECONCILED", "result": res}
            except Exception as e:
                logging.warning(f"SDK reconcile_project failed ({e}); falling back to REST.")

        url = f"{self.url}/api/v1/projects/{lab_id}/reconcile"
        payload = json.dumps({"direction": direction, "confirm": confirm}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as e:
            logging.error(f"Lab-wide reconciliation failed for lab '{lab_id}': {e}")
            raise RuntimeError(f"Lab Reconciliation Failed: {e}")

    def list_links(self, lab_id: str) -> List[Dict[str, Any]]:
        """
        List all virtual and physical topology links for a given project.
        Maps to EVE IaC `list_project_links(lab)`.
        """
        if self.offline_mode:
            return [
                {
                    "id": "link_12",
                    "src_node": "1",
                    "src_name": "Spine-01",
                    "src_port": "e0/1",
                    "dst_node": "2",
                    "dst_name": "Leaf-01",
                    "dst_port": "e0/1",
                    "status": "active"
                }
            ]

        # 1. Try official SDK
        if self.client and hasattr(self.client, "list_project_links"):
            try:
                res = self.client.list_project_links(lab=lab_id)
                if hasattr(res, "links"):
                    return [getattr(l, "__dict__", dict(l)) for l in res.links]
                elif isinstance(res, dict) and "links" in res:
                    return res["links"]
            except Exception as e:
                logging.warning(f"Official SDK list_project_links failed ({e}); falling back to REST endpoint.")

        # 2. REST API Fallback
        links_url = f"{self.url}/api/v1/projects/{lab_id}/links"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(links_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("links", [])
        except Exception as e:
            logging.error(f"Failed to query links from EVE IaC for lab '{lab_id}': {e}")
            return []

    def set_link_suspend(self, lab_id: str, link_id: str, suspended: bool = True) -> bool:
        """
        Suspend or resume a link (simulate physical cable disconnect / link flap).
        Maps to EVE IaC `set_link_suspend(lab, link, suspended)`.
        """
        state_str = "SUSPENDED (CUT)" if suspended else "RESUMED (UP)"
        if self.offline_mode:
            logging.info(f"[Offline] Link '{link_id}' in lab '{lab_id}' set to {state_str}.")
            return True

        # 1. Try official SDK
        if self.client and hasattr(self.client, "set_link_suspend"):
            try:
                self.client.set_link_suspend(lab=lab_id, link=link_id, suspended=suspended)
                logging.info(f"Link '{link_id}' successfully set to {state_str} via EVE IaC SDK.")
                return True
            except Exception as e:
                logging.warning(f"Official SDK set_link_suspend failed ({e}); falling back to REST call.")

        # 2. REST API Fallback
        suspend_url = f"{self.url}/api/v1/projects/{lab_id}/links/{link_id}/suspend"
        payload = json.dumps({"suspended": suspended}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(suspend_url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logging.info(f"Link '{link_id}' set to {state_str} (HTTP {resp.status}).")
                return resp.status in (200, 204)
        except Exception as e:
            logging.error(f"Failed to set link suspend for '{link_id}' in lab '{lab_id}': {e}")
            return False

    def apply_link_quality(
        self,
        lab_id: str,
        link_id: str,
        delay_ms: int = 0,
        jitter_ms: int = 0,
        loss_pct: float = 0.0,
        bandwidth_kbps: int = 0
    ) -> bool:
        """
        Dynamically inject latency, jitter, packet loss, or bandwidth limits via Netem.
        Maps to EVE IaC `apply_link_quality(...)`.
        """
        if self.offline_mode:
            logging.info(
                f"[Offline] Applied link quality to '{link_id}': delay={delay_ms}ms, jitter={jitter_ms}ms, "
                f"loss={loss_pct}%, bw={bandwidth_kbps}kbps."
            )
            return True

        # 1. Try official SDK
        if self.client and hasattr(self.client, "apply_link_quality"):
            try:
                self.client.apply_link_quality(
                    lab=lab_id, link=link_id, delay=delay_ms, jitter=jitter_ms, loss=loss_pct, bandwidth=bandwidth_kbps
                )
                logging.info(f"Link quality applied to '{link_id}' via EVE IaC SDK.")
                return True
            except Exception as e:
                logging.warning(f"Official SDK apply_link_quality failed ({e}); falling back to REST call.")

        # 2. REST API Fallback
        quality_url = f"{self.url}/api/v1/projects/{lab_id}/links/{link_id}/quality"
        payload = json.dumps({
            "delay_ms": delay_ms,
            "jitter_ms": jitter_ms,
            "loss_pct": loss_pct,
            "bandwidth_kbps": bandwidth_kbps
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(quality_url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logging.info(f"Link quality applied to '{link_id}' (HTTP {resp.status}).")
                return resp.status in (200, 204)
        except Exception as e:
            logging.error(f"Failed to apply link quality for '{link_id}' in lab '{lab_id}': {e}")
            return False

    def detect_device_type(self, node_name: str, protocol: str = "telnet") -> str:
        """
        Infer the Netmiko device_type based on node name and transport protocol.
        """
        name_lower = (node_name or "").lower()
        is_ssh = (protocol or "").lower() == "ssh"

        for key, (telnet_type, ssh_type) in self.type_mappings.items():
            if key in name_lower:
                return ssh_type if is_ssh else telnet_type

        # Default fallback to Cisco IOS
        return "cisco_ios" if is_ssh else "cisco_ios_telnet"

    def load_lab_devices(
        self,
        lab_id: str,
        intent_path: Optional[str] = None,
        wait_readiness: bool = True,
        default_user: Optional[str] = None,
        default_pass: Optional[str] = None,
        only_running: bool = True
    ) -> List[Device]:
        """
        Dynamically discover active target nodes from EVE IaC and convert to PNetGimini Device instances.
        Optionally synchronizes prompt readiness and attaches configuration intent commands.
        """
        self.authenticate()
        consoles = self.list_consoles(lab_id)
        logging.info(f"Discovered {len(consoles)} consoles from EVE IaC project '{lab_id}'.")

        username = default_user or os.getenv("EVE_DEFAULT_USER", "admin")
        password = default_pass or os.getenv("EVE_DEFAULT_PASS", "admin")

        devices: List[Device] = []
        for record in consoles:
            status = record.get("status", "running").lower()
            if only_running and status not in ("running", "started", "active"):
                logging.debug(f"Skipping non-running node {record.get('name')} (status: {status})")
                continue

            node_id = str(record.get("node", ""))
            node_name = str(record.get("name", node_id))
            host = record.get("host") or "127.0.0.1"
            port = int(record.get("port", 23))
            protocol = record.get("protocol", "telnet")

            if wait_readiness:
                logging.info(f"Synchronizing boot readiness for node '{node_name}' ({node_id})...")
                self.wait_console(lab_id, node_id)

            dev_type = self.detect_device_type(node_name, protocol)
            device = Device(
                ip=host,
                port=port,
                username=username,
                password=password,
                device_type=dev_type,
                name=node_name,
                node_id=node_id
            )
            devices.append(device)

        if intent_path:
            self.bind_intent(devices, intent_path)

        return devices

    def bind_intent(self, devices: List[Device], intent_path: str):
        """
        Bind configuration commands from intent file or directory to corresponding discovered devices.
        Intent can be:
          - A YAML file containing node-to-commands mapping.
          - A directory containing per-node configuration snippets (<node_name>.cfg / .yaml).
        """
        path = Path(intent_path)
        if not path.exists():
            logging.warning(f"Intent path '{intent_path}' does not exist; skipping command binding.")
            return

        device_map: Dict[str, Device] = {}
        for dev in devices:
            if dev.name:
                device_map[dev.name.lower()] = dev
            if dev.node_id:
                device_map[dev.node_id.lower()] = dev

        if path.is_file():
            self._bind_intent_from_file(device_map, path)
        elif path.is_dir():
            self._bind_intent_from_directory(device_map, path)

    def _bind_intent_from_file(self, device_map: Dict[str, Device], file_path: Path):
        """Parse YAML intent file and bind commands to matching devices."""
        import yaml
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = ConfigParser.expand_env_vars(f.read())
                data = yaml.safe_load(content)

            if not isinstance(data, dict):
                return

            # Support format 1: { "devices": [ {"name": "R1", "commands": {...}}, ... ] }
            if "devices" in data and isinstance(data["devices"], list):
                for item in data["devices"]:
                    name = (item.get("name") or "").lower()
                    if name in device_map:
                        self._attach_commands_to_device(device_map[name], item.get("commands", {}))

            # Support top-level chaos_tests: [ {...}, {...} ]
            if "chaos_tests" in data and isinstance(data["chaos_tests"], list):
                self.chaos_tests.extend(data["chaos_tests"])
                logging.info(f"Loaded {len(data['chaos_tests'])} global chaos test(s) from intent.")

            # Support format 2: { "R1_Spine": { "config": [...], "show": [...], "verify": [...] } }
            for key, val in data.items():
                if key in ("devices", "chaos_tests"):
                    continue
                k_lower = str(key).lower()
                if k_lower in device_map and isinstance(val, dict):
                    self._attach_commands_to_device(device_map[k_lower], val)
                    if "chaos_tests" in val and isinstance(val["chaos_tests"], list):
                        self.chaos_tests.extend(val["chaos_tests"])

        except Exception as e:
            logging.error(f"Error binding intent from file '{file_path}': {e}")

    def _bind_intent_from_directory(self, device_map: Dict[str, Device], dir_path: Path):
        """Scan directory for per-device intent files and bind commands."""
        for file in dir_path.iterdir():
            if not file.is_file():
                continue
            stem = file.stem.lower()
            if stem in device_map:
                dev = device_map[stem]
                if file.suffix in (".yaml", ".yml"):
                    self._bind_intent_from_file({stem: dev}, file)
                elif file.suffix in (".cfg", ".conf", ".txt"):
                    with open(file, "r", encoding="utf-8") as f:
                        lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("!")]
                        dev.add_command(Command(category="config", commands=lines))
                    logging.info(f"Bound {len(lines)} config commands from {file.name} to {dev.name}.")

    def _attach_commands_to_device(self, device: Device, commands_dict: Dict[str, List[str]]):
        """Helper to create Command objects and attach to Device."""
        for cat in ["config", "show", "verify"]:
            cmds = commands_dict.get(cat, [])
            if cmds:
                device.add_command(Command(category=cat, commands=cmds))
        logging.info(f"Attached commands to device {device.name} (Categories: {list(commands_dict.keys())}).")

    def _mock_list_consoles(self, lab_id: str) -> List[Dict[str, Any]]:
        """Mock console records for offline testing and air-gapped CI."""
        return [
            {
                "node": "1",
                "name": "Spine-01",
                "status": "running",
                "protocol": "telnet",
                "host": "127.0.0.1",
                "port": 30001
            },
            {
                "node": "2",
                "name": "Leaf-01",
                "status": "running",
                "protocol": "telnet",
                "host": "127.0.0.1",
                "port": 30002
            },
            {
                "node": "3",
                "name": "Huawei-Core",
                "status": "running",
                "protocol": "telnet",
                "host": "127.0.0.1",
                "port": 30003
            }
        ]
