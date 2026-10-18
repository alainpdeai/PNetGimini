import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.device import Device
from src.models.command import Command
from src.models.state import NodeState, NodeStateMachine
from src.plugins.eve_iac_connector import EveIacConnector
from src.core.self_healing import SelfHealingEngine, SelfHealingError
from src.core.device_manager import DeviceManager


class TestV35SelfHealing(unittest.TestCase):
    """
    Test suite for Task 1 (v3.5) Dual-Tier Self-Healing Escalation Engine.
    Verifies:
      1. EveIacConnector lifecycle methods (stop, wipe, start, reset, reconcile).
      2. SelfHealingEngine node recovery and NodeStateMachine transitions.
      3. SelfHealingEngine topology drift reconciliation.
      4. DeviceManager automated escalation from failed Tier-1 rollback to Tier-2 reset.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir)
        self.connector = EveIacConnector(offline_mode=True)
        self.healing_engine = SelfHealingEngine(self.connector)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_eve_iac_connector_lifecycle_offline(self):
        self.assertTrue(self.connector.stop_node("lab_1", "1"))
        self.assertTrue(self.connector.wipe_node("lab_1", "1"))
        self.assertTrue(self.connector.start_node("lab_1", "1"))
        self.assertTrue(self.connector.reset_node_lifecycle("lab_1", "1"))

        rec = self.connector.reconcile_lab("lab_1")
        self.assertEqual(rec["status"], "RECONCILED")
        self.assertEqual(rec["direction"], "to_eve")
        self.assertTrue(rec["confirmed"])

    def test_self_healing_node_lifecycle_and_state_machine(self):
        sm = NodeStateMachine("Spine-01", initial_state=NodeState.FAILED_ROLLBACK)

        heal_result = self.healing_engine.heal_node(
            lab_id="dc_fabric",
            node_key="Spine-01",
            state_machine=sm,
            reason="Diff rollback loop detected"
        )

        self.assertEqual(heal_result["status"], "RECOVERED")
        self.assertEqual(heal_result["tier"], 2)
        self.assertEqual(heal_result["action"], "WIPE_AND_RESTART")
        self.assertEqual(heal_result["node"], "Spine-01")
        self.assertEqual(sm.current_state, NodeState.CONSOLE_READY)

    def test_self_healing_reset_failure_raises_error(self):
        mock_connector = MagicMock()
        mock_connector.reset_node_lifecycle.return_value = False
        engine = SelfHealingEngine(mock_connector)
        sm = NodeStateMachine("Leaf-01", initial_state=NodeState.FAILED_ROLLBACK)

        with self.assertRaises(SelfHealingError) as ctx:
            engine.heal_node("lab_test", "Leaf-01", state_machine=sm)
        self.assertIn("Tier-2 hypervisor reset sequence failed", str(ctx.exception))

    def test_self_healing_reconcile_topology(self):
        mock_connector = MagicMock()
        mock_connector.reconcile_lab.return_value = {"status": "RECONCILED", "nodes": 4}
        engine = SelfHealingEngine(mock_connector)

        res = engine.reconcile_topology("lab_prod", direction="to_eve", confirm=True)
        self.assertEqual(res["status"], "RECONCILED")
        mock_connector.reconcile_lab.assert_called_once_with("lab_prod", direction="to_eve", confirm=True)

    @patch("src.core.device_manager.ConnectHandler")
    def test_device_manager_escalates_to_tier2_when_tier1_fails(self, mock_connect):
        mock_handler = MagicMock()
        mock_connect.return_value = mock_handler

        mock_handler.send_command.return_value = "hostname R1\n"
        # Deployment fails, then rollback also fails
        mock_handler.send_config_set.side_effect = [
            RuntimeError("Config push syntax failure"),
            RuntimeError("Diff rollback failed: session timed out")
        ]

        dev = Device("10.0.0.1", 30001, "admin", "admin", "cisco_ios_telnet")
        dev.lab_id = "lab_twin"
        dev.node_id = "Spine-01"
        dev.add_command(Command("config", ["interface e0/0", "bad_command"]))

        mgr = DeviceManager(
            device=dev,
            outputs_dir=self.output_dir,
            self_healing_engine=self.healing_engine
        )
        result = mgr.deploy_commands()

        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result.get("tier1_exhausted"))
        self.assertIn("tier2_healing", result)
        self.assertEqual(result["tier2_healing"]["status"], "RECOVERED")
        self.assertEqual(result["tier2_healing"]["tier"], 2)
        self.assertEqual(result["tier2_healing"]["action"], "WIPE_AND_RESTART")
