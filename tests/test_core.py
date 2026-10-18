import unittest
import asyncio
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.adapters.factory import AdapterFactory
from src.core.adapters.cisco import CiscoAdapter
from src.core.adapters.huawei import HuaweiAdapter
from src.core.config_diff import ConfigDiffEngine
from src.core.async_engine import AsyncDeploymentEngine
from src.models.device import Device
from src.models.command import Command

class TestAdapters(unittest.TestCase):
    def test_cisco_adapter_properties(self):
        adapter = AdapterFactory.get_adapter("cisco_ios_telnet")
        self.assertIsInstance(adapter, CiscoAdapter)
        self.assertEqual(adapter.get_netmiko_device_type("cisco_ios_telnet"), "cisco_ios_telnet")
        self.assertEqual(adapter.get_netmiko_device_type("cisco_ios_ssh"), "cisco_ios")
        self.assertEqual(adapter.get_disable_paging_command(), "terminal length 0")
        self.assertEqual(adapter.get_snapshot_command(), "show running-config")
        self.assertEqual(adapter.get_reversal_prefix(), "no ")

    def test_huawei_adapter_properties(self):
        adapter = AdapterFactory.get_adapter("huawei_router_telnet")
        self.assertIsInstance(adapter, HuaweiAdapter)
        self.assertEqual(adapter.get_netmiko_device_type("huawei_router_telnet"), "huawei_telnet")
        self.assertEqual(adapter.get_netmiko_device_type("huawei_router_ssh"), "huawei")
        self.assertEqual(adapter.get_disable_paging_command(), "screen-length 0 temporary")
        self.assertEqual(adapter.get_snapshot_command(), "display current-configuration")
        self.assertEqual(adapter.get_reversal_prefix(), "undo ")

    def test_cisco_clean_config(self):
        raw = textwrap.dedent("""
        Building configuration...
        Current configuration : 1234 bytes
        !
        hostname R1
        ! Last configuration change at 12:00:00
        end
        """)
        adapter = CiscoAdapter()
        cleaned = adapter.clean_raw_config(raw)
        self.assertEqual(cleaned, ["hostname R1"])

    def test_huawei_clean_config(self):
        raw = textwrap.dedent("""
        #
        sysname AR1
        #
        return
        """)
        adapter = HuaweiAdapter()
        cleaned = adapter.clean_raw_config(raw)
        self.assertEqual(cleaned, ["sysname AR1"])

class TestDiffRollback(unittest.TestCase):
    def setUp(self):
        self.cisco_adapter = CiscoAdapter()
        self.huawei_adapter = HuaweiAdapter()

    def test_cisco_diff_reversal_new_block(self):
        baseline = "hostname R1\ninterface Ethernet0/0\n ip address 10.0.0.1 255.255.255.0"
        deployed = [
            "router ospf 1",
            "network 10.0.0.0 0.0.0.255 area 0"
        ]
        plan = ConfigDiffEngine.compute_reversion_plan(baseline, deployed, self.cisco_adapter)
        self.assertEqual(plan, ["no router ospf 1"])

    def test_cisco_diff_reversal_interface_change(self):
        baseline = "interface Ethernet0/1\n shutdown"
        deployed = [
            "interface Ethernet0/1",
            "ip address 192.168.1.1 255.255.255.0",
            "no shutdown"
        ]
        plan = ConfigDiffEngine.compute_reversion_plan(baseline, deployed, self.cisco_adapter)
        expected = [
            "interface Ethernet0/1",
            "no ip address",
            "shutdown",
            "exit"
        ]
        self.assertEqual(plan, expected)

    def test_huawei_diff_reversal(self):
        baseline = "sysname OldName\ninterface GigabitEthernet0/0/1\n shutdown"
        deployed = [
            "sysname NewName",
            "interface GigabitEthernet0/0/1",
            "ip address 192.168.1.1 255.255.255.0",
            "undo shutdown"
        ]
        plan = ConfigDiffEngine.compute_reversion_plan(baseline, deployed, self.huawei_adapter)
        expected = [
            "interface GigabitEthernet0/0/1",
            "undo ip address",
            "shutdown",
            "quit",
            "sysname OldName"
        ]
        self.assertEqual(plan, expected)

    def test_cisco_diff_reversal_attribute_restore(self):
        baseline = "interface GigabitEthernet0/1\n ip ospf cost 10\n description Uplink-Primary"
        deployed = [
            "interface GigabitEthernet0/1",
            "ip ospf cost 50",
            "bandwidth 1000000",
            "description Uplink-Modified"
        ]
        plan = ConfigDiffEngine.compute_reversion_plan(baseline, deployed, self.cisco_adapter)
        expected = [
            "interface GigabitEthernet0/1",
            "ip ospf cost 10",
            "no bandwidth",
            "description Uplink-Primary",
            "exit"
        ]
        self.assertEqual(plan, expected)

    def test_huawei_diff_reversal_attribute_restore(self):
        baseline = "interface GigabitEthernet0/0/1\n ospf cost 20\n description Core-Link"
        deployed = [
            "interface GigabitEthernet0/0/1",
            "ospf cost 100",
            "mtu 9000",
            "description Backup-Link"
        ]
        plan = ConfigDiffEngine.compute_reversion_plan(baseline, deployed, self.huawei_adapter)
        expected = [
            "interface GigabitEthernet0/0/1",
            "ospf cost 20",
            "undo mtu",
            "description Core-Link",
            "quit"
        ]
        self.assertEqual(plan, expected)

class TestAsyncEngine(unittest.TestCase):
    def test_async_engine_execution(self):
        devices = [
            Device(f"192.168.1.{i}", 30000 + i, "admin", "admin", "cisco_ios_telnet")
            for i in range(1, 6)
        ]
        engine = AsyncDeploymentEngine(devices, Path("outputs"), concurrency=3)

        # Mock deploy_commands to avoid real network calls
        with patch("src.core.device_manager.DeviceManager.deploy_commands") as mock_deploy:
            mock_deploy.return_value = {
                "device": "mock_device",
                "status": "CONFIGURED",
                "details": [],
                "error_message": None
            }
            results = asyncio.run(engine.run())
            self.assertEqual(len(results), 5)
            self.assertEqual(mock_deploy.call_count, 5)
            for r in results:
                self.assertEqual(r["status"], "CONFIGURED")

if __name__ == "__main__":
    unittest.main()
