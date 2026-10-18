import abc
import re
from typing import List, Optional

class BaseAdapter(abc.ABC):
    """
    Abstract Base Adapter defining the standard interface for network vendor operations.
    Handles syntax normalization, pagination control, snapshot commands, and reversal generation.
    """

    @abc.abstractmethod
    def get_netmiko_device_type(self, custom_device_type: str) -> str:
        """Map custom YAML device_type string to official Netmiko device type."""
        pass

    @abc.abstractmethod
    def get_disable_paging_command(self) -> str:
        """Command to disable pagination in terminal sessions."""
        pass

    @abc.abstractmethod
    def get_snapshot_command(self) -> str:
        """Command to dump running configuration."""
        pass

    @abc.abstractmethod
    def get_reversal_prefix(self) -> str:
        """Command prefix used to negate/undo a configuration statement (e.g. 'no ' or 'undo ')."""
        pass

    @abc.abstractmethod
    def clean_raw_config(self, raw_config: str) -> List[str]:
        """Strip non-configuration noise (banners, timestamps, banners, comments) from raw snapshot."""
        pass

    @abc.abstractmethod
    def is_block_header(self, line: str) -> bool:
        """Determine if a command line opens a configuration sub-context/block (e.g. interface, router)."""
        pass

    @abc.abstractmethod
    def get_block_exit_command(self) -> str:
        """Command to exit current configuration block (e.g. 'exit' or 'quit')."""
        pass

    @abc.abstractmethod
    def generate_block_reversal(self, block_header: str, deployed_children: List[str], baseline_children: Optional[List[str]] = None) -> List[str]:
        """
        Generate precise reversal commands for a configuration block.
        If baseline_children is None or empty, completely negates the block.
        If baseline_children exists, restores modified attributes.
        """
        pass

    def supports_native_rollback(self) -> bool:
        """Indicates whether this vendor OS supports native atomic configuration replacement."""
        return False

    def get_native_snapshot_command(self, filename: str) -> Optional[str]:
        """Command to archive baseline running-config onto device local storage."""
        return None

    def get_native_rollback_command(self, filename: str) -> Optional[str]:
        """Command to atomically replace running-config with the archived snapshot."""
        return None
