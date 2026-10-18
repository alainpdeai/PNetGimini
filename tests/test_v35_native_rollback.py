import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.device import Device
from src.models.command import Command
from src.core.adapters.factory import AdapterFactory
from src.core.device_manager import DeviceManager


class TestV35NativeRollback(unittest.TestCase):
    """
    Test suite for Task 2 (v3.5) Native Rollback ("Native First, Diff Fallback").
    Verifies:
      1. Cisco native snapshot creation and configure replace rollback execution.
      2. Native rollback error automatically falling back to surgical Diff-based reversal.
      3. Huawei gracefully skipping native replace and defaulting to Diff rollback.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir)
        self.cisco_device = Device("10.0.0.1", 30001, "admin", "admin", "cisco_ios_telnet")
        self.huawei_device = Device("10.0.0.2", 30002, "admin", "admin", "huawei_vrp_telnet")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adapter_supports_native_rollback_flag(self):
        cisco_adapter = AdapterFactory.get_adapter("cisco_ios_telnet")
        huawei_adapter = AdapterFactory.get_adapter("huawei_vrp_telnet")

        self.assertTrue(cisco_adapter.supports_native_rollback())
        self.assertFalse(huawei_adapter.supports_native_rollback())

        snap_cmd = cisco_adapter.get_native_snapshot_command("test_base.cfg")
        rb_cmd = cisco_adapter.get_native_rollback_command("test_base.cfg")
        self.assertEqual(snap_cmd, "copy running-config flash:test_base.cfg")
        self.assertEqual(rb_cmd, "configure replace flash:test_base.cfg force")

    @patch("src.core.device_manager.ConnectHandler")
    def test_cisco_native_rollback_success(self, mock_connect):
        mock_handler = MagicMock()
        mock_connect.return_value = mock_handler

        def fake_send_command(cmd, *args, **kwargs):
            if "show running-config" in cmd:
                return "hostname R1\ninterface e0/0\n"
            elif "copy running-config" in cmd:
                return "Copy complete"
            elif "configure replace" in cmd:
                return "Rollback: Rollback completed successfully."
            return ""

        mock_handler.send_command.side_effect = fake_send_command
        # Simulate failure during deployment
        mock_handler.send_config_set.side_effect = RuntimeError("Syntax error at % Invalid input")

        self.cisco_device.add_command(Command("config", ["interface e0/0", "bad_command"]))
        mgr = DeviceManager(self.cisco_device, self.output_dir, prefer_native_rollback=True)
        result = mgr.deploy_commands()

        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["rollback_applied"])
        self.assertEqual(result["rollback_type"], "NATIVE_REPLACE")

    @patch("src.core.device_manager.ConnectHandler")
    def test_cisco_native_rollback_error_falls_back_to_diff(self, mock_connect):
        mock_handler = MagicMock()
        mock_connect.return_value = mock_handler

        def fake_send_command(cmd, *args, **kwargs):
            if "show running-config" in cmd:
                return "hostname R1\ninterface e0/0\n"
            elif "copy running-config" in cmd:
                return "Copy complete"
            elif "configure replace" in cmd:
                return "% Error: Rollback: Aborted due to hardware lock"
            return ""

        mock_handler.send_command.side_effect = fake_send_command
        # send_config_set fails first time (deployment), succeeds second time (diff rollback)
        mock_handler.send_config_set.side_effect = [
            RuntimeError("Deployment error"),
            "Diff Rollback OK"
        ]

        self.cisco_device.add_command(Command("config", ["interface e0/0", "bad_command"]))
        mgr = DeviceManager(self.cisco_device, self.output_dir, prefer_native_rollback=True)
        result = mgr.deploy_commands()

        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["rollback_applied"])
        self.assertEqual(result["rollback_type"], "DIFF_BASED")

    @patch("src.core.device_manager.ConnectHandler")
    def test_huawei_does_not_attempt_native_rollback(self, mock_connect):
        mock_handler = MagicMock()
        mock_connect.return_value = mock_handler

        mock_handler.send_command.return_value = "sysname Huawei1\n"
        mock_handler.send_config_set.side_effect = [
            RuntimeError("Huawei CLI error"),
            "Diff Rollback OK"
        ]

        self.huawei_device.add_command(Command("config", ["interface ge0/0/1", "bad_command"]))
        mgr = DeviceManager(self.huawei_device, self.output_dir, prefer_native_rollback=True)
        result = mgr.deploy_commands()

        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["rollback_applied"])
        self.assertEqual(result["rollback_type"], "DIFF_BASED")
