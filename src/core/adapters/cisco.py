import re
from typing import List, Optional
from .base import BaseAdapter

class CiscoAdapter(BaseAdapter):
    """
    Vendor adapter for Cisco IOS and IOS-XE devices.
    """

    BLOCK_PATTERNS = (
        r"^interface\s+\S+",
        r"^router\s+\S+",
        r"^line\s+\S+",
        r"^ip\s+access-list\s+\S+",
        r"^route-map\s+\S+",
        r"^vlan\s+\d+",
        r"^policy-map\s+\S+",
        r"^class-map\s+\S+",
        r"^vrf\s+definition\s+\S+",
        r"^ip\s+dhcp\s+pool\s+\S+"
    )

    def get_netmiko_device_type(self, custom_device_type: str) -> str:
        if "_ssh" in custom_device_type.lower():
            return "cisco_ios"
        return "cisco_ios_telnet"

    def get_disable_paging_command(self) -> str:
        return "terminal length 0"

    def get_snapshot_command(self) -> str:
        return "show running-config"

    def get_reversal_prefix(self) -> str:
        return "no "

    def get_block_exit_command(self) -> str:
        return "exit"

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
            if stripped.startswith("!"):
                continue
            if stripped.lower().startswith("building configuration"):
                continue
            if stripped.lower().startswith("current configuration"):
                continue
            if stripped.lower().startswith("last configuration change"):
                continue
            if stripped.lower().startswith("nvram config last updated"):
                continue
            if stripped.lower() == "end":
                continue
            # If line is not indented (top-level), strip both sides; otherwise preserve 1-level indent
            if line.startswith(" ") or line.startswith("\t"):
                cleaned.append(" " + stripped)
            else:
                cleaned.append(stripped)
        return cleaned

    def generate_block_reversal(self, block_header: str, deployed_children: List[str], baseline_children: Optional[List[str]] = None) -> List[str]:
        reversal_cmds = []
        stripped_header = block_header.strip()

        # If the block itself did not exist in the baseline (e.g. router ospf 1, or new ACL),
        # the cleanest, safest reversal is to negate the entire block at global config level
        if not baseline_children and not stripped_header.lower().startswith("interface"):
            # Can safely negate router, vlan, access-list, dhcp pool, etc.
            return [f"no {stripped_header}"]

        # For interfaces or blocks that exist in baseline, enter block and reverse child commands
        reversal_cmds.append(stripped_header)
        baseline_set = set(c.strip() for c in baseline_children) if baseline_children else set()

        for child in deployed_children:
            child_str = child.strip()
            if not child_str:
                continue
            if child_str in baseline_set:
                continue  # Already in baseline, no need to touch

            # Reverse specific attributes
            if child_str.lower().startswith("no "):
                # If deployed "no shutdown", reverse to "shutdown"
                reversal_cmds.append(child_str[3:].strip())
            elif child_str.lower().startswith("shutdown"):
                reversal_cmds.append("no shutdown")
            else:
                # Check if this child modifies an existing baseline attribute with a known prefix
                overridden_prefixes = (
                    "ip address ", "ip ospf cost ", "bandwidth ", "delay ", "mtu ",
                    "description ", "duplex ", "speed ", "ip ospf priority ",
                    "ip ospf dead-interval ", "ip ospf hello-interval "
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
                            reversal_cmds.append("no ip address")
                        else:
                            attr_key = matched_prefix.strip()
                            reversal_cmds.append(f"no {attr_key}")
                else:
                    reversal_cmds.append(f"no {child_str}")

        reversal_cmds.append(self.get_block_exit_command())
        return reversal_cmds

    def supports_native_rollback(self) -> bool:
        """Cisco IOS and IOS-XE support atomic configure replace from stored snapshot."""
        return True

    def get_native_snapshot_command(self, filename: str) -> Optional[str]:
        """Archive running configuration to flash storage."""
        clean_name = filename.strip().replace(" ", "_")
        return f"copy running-config flash:{clean_name}"

    def get_native_rollback_command(self, filename: str) -> Optional[str]:
        """Atomically replace running configuration from flash snapshot."""
        clean_name = filename.strip().replace(" ", "_")
        return f"configure replace flash:{clean_name} force"
