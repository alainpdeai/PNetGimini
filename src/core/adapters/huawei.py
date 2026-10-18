import re
from typing import List, Optional
from .base import BaseAdapter

class HuaweiAdapter(BaseAdapter):
    """
    Vendor adapter for Huawei VRP devices.
    """

    BLOCK_PATTERNS = (
        r"^interface\s+\S+",
        r"^ospf(\s+\d+)?(\s+(router-id|vpn-instance)\s+\S+)?$",
        r"^bgp\s+\d+",
        r"^vlan\s+\d+",
        r"^acl\s+\S+",
        r"^user-interface\s+\S+",
        r"^aaa\b",
        r"^route-policy\s+\S+"
    )

    def get_netmiko_device_type(self, custom_device_type: str) -> str:
        if "_ssh" in custom_device_type.lower():
            return "huawei"
        return "huawei_telnet"

    def get_disable_paging_command(self) -> str:
        return "screen-length 0 temporary"

    def get_snapshot_command(self) -> str:
        return "display current-configuration"

    def get_reversal_prefix(self) -> str:
        return "undo "

    def get_block_exit_command(self) -> str:
        return "quit"

    def is_block_header(self, line: str) -> bool:
        stripped = line.strip()
        for pattern in self.BLOCK_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                return True
        return False

    def clean_raw_config(self, raw_config: str) -> List[str]:
        cleaned = []
        for raw_line in raw_config.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") or stripped.startswith("!"):
                continue
            if stripped.lower().startswith("display current-configuration"):
                continue
            if stripped.lower().startswith("current configuration"):
                continue
            if stripped.lower() == "return":
                continue
            if line.startswith(" ") or line.startswith("\t"):
                cleaned.append(" " + stripped)
            else:
                cleaned.append(stripped)
        return cleaned

    def generate_block_reversal(self, block_header: str, deployed_children: List[str], baseline_children: Optional[List[str]] = None) -> List[str]:
        reversal_cmds = []
        stripped_header = block_header.strip()

        if not baseline_children and not stripped_header.lower().startswith("interface"):
            return [f"undo {stripped_header}"]

        reversal_cmds.append(stripped_header)
        baseline_set = set(c.strip() for c in baseline_children) if baseline_children else set()

        for child in deployed_children:
            child_str = child.strip()
            if not child_str:
                continue
            if child_str in baseline_set:
                continue

            if child_str.lower().startswith("undo shutdown"):
                reversal_cmds.append("shutdown")
            elif child_str.lower().startswith("shutdown"):
                reversal_cmds.append("undo shutdown")
            elif child_str.lower().startswith("undo "):
                reversal_cmds.append(child_str[5:].strip())
            else:
                # Check if this child modifies an existing baseline attribute with a known prefix
                overridden_prefixes = (
                    "ip address ", "ospf cost ", "description ", "mtu ", "bandwidth ",
                    "ospf timer hello ", "ospf timer dead "
                )
                matched_prefix = None
                for pfx in overridden_prefixes:
                    if child_str.lower().startswith(pfx):
                        matched_prefix = pfx
                        break

                if matched_prefix:
                    baseline_val = None
                    for b in baseline_set:
                        if b.lower().startswith(matched_prefix):
                            baseline_val = b
                            break
                    if baseline_val:
                        reversal_cmds.append(baseline_val)
                    else:
                        if matched_prefix == "ip address ":
                            reversal_cmds.append("undo ip address")
                        else:
                            attr_key = matched_prefix.strip()
                            reversal_cmds.append(f"undo {attr_key}")
                else:
                    reversal_cmds.append(f"undo {child_str}")

        reversal_cmds.append(self.get_block_exit_command())
        return reversal_cmds
