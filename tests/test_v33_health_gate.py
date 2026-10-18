import unittest
from unittest.mock import MagicMock, patch

from src.core.health_gate import HealthGateEngine, HealthGateError
from src.plugins.eve_iac_connector import EveIacConnector
from src.plugins.chaos_orchestrator import ChaosOrchestrator


class TestHealthGateEngine(unittest.TestCase):
    """
    Test suite for Phase 3 (v3.3) Declarative Health Gate Engine.
    Verifies Ping SLA, OSPF neighbor states & metrics, BGP summaries, interface CRC,
    and routing next-hop assertions.
    """

    def setUp(self):
        self.engine = HealthGateEngine()

    def test_parse_ping_output_cisco(self):
        cisco_good = (
            "Sending 5, 100-byte ICMP Echos to 10.1.1.2, timeout is 2 seconds:\n"
            "!!!!!\n"
            "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms\n"
        )
        loss, rtt = self.engine.parse_ping_output(cisco_good)
        self.assertEqual(loss, 0.0)
        self.assertEqual(rtt, 2.0)

        cisco_partial = (
            "Sending 5, 100-byte ICMP Echos to 10.1.1.2, timeout is 2 seconds:\n"
            ".!!!!\n"
            "Success rate is 80 percent (4/5), round-trip min/avg/max = 1/5/12 ms\n"
        )
        loss2, rtt2 = self.engine.parse_ping_output(cisco_partial)
        self.assertEqual(loss2, 20.0)
        self.assertEqual(rtt2, 5.0)

    def test_parse_ping_output_huawei(self):
        huawei_good = (
            "5 packet(s) transmitted, 5 packet(s) received, 0.00% packet loss\n"
            "round-trip min/avg/max = 1/3/8 ms\n"
        )
        loss, rtt = self.engine.parse_ping_output(huawei_good)
        self.assertEqual(loss, 0.0)
        self.assertEqual(rtt, 3.0)

    def test_verify_ping_success(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms\n"
        )
        spec = {
            "ping": [
                {"target": "10.1.1.2", "count": 5, "max_loss_pct": 0, "max_avg_rtt_ms": 20}
            ]
        }
        res = self.engine.verify_device(mock_net, spec)
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_assertions"], 1)

    def test_verify_ping_loss_failure(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Success rate is 60 percent (3/5), round-trip min/avg/max = 2/10/20 ms\n"
        )
        spec = {
            "ping": [
                {"target": "10.1.1.2", "count": 5, "max_loss_pct": 0}
            ]
        }
        with self.assertRaises(HealthGateError) as ctx:
            self.engine.verify_device(mock_net, spec)
        self.assertIn("Packet loss 40.0% exceeded threshold 0%", str(ctx.exception.failures))

    def test_verify_ping_rtt_failure(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Success rate is 100 percent (5/5), round-trip min/avg/max = 20/45/80 ms\n"
        )
        spec = {
            "ping": [
                {"target": "10.1.1.2", "count": 5, "max_avg_rtt_ms": 30}
            ]
        }
        with self.assertRaises(HealthGateError) as ctx:
            self.engine.verify_device(mock_net, spec)
        self.assertIn("Average RTT 45.0ms exceeded threshold 30ms", str(ctx.exception.failures))

    def test_verify_ospf_full_success(self):
        mock_net = MagicMock()
        mock_net.send_command.side_effect = [
            # show ip ospf neighbor
            "Neighbor ID     Pri   State           Dead Time   Address         Interface\n"
            "1.1.1.1           1   FULL/BDR        00:00:34    10.1.1.2        GigabitEthernet0/1\n"
            "2.2.2.2           1   FULL/DR         00:00:31    10.1.1.6        GigabitEthernet0/2\n",
            # show ip route 10.200.0.0/24
            "Routing entry for 10.200.0.0/24\n"
            "  Known via \"ospf 100\", distance 110, metric 20, type intra area\n"
            "    [110/20] via 10.1.1.2, 00:05:12, GigabitEthernet0/1\n"
        ]
        spec = {
            "ospf": [
                {"neighbor_ip": "10.1.1.2", "expected_state": "FULL"},
                {"min_total_neighbors": 2},
                {"route": "10.200.0.0/24", "expected_metric": 20}
            ]
        }
        res = self.engine.verify_device(mock_net, spec)
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_assertions"], 3)

    def test_verify_ospf_state_failure(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Neighbor ID     Pri   State           Dead Time   Address         Interface\n"
            "1.1.1.1           1   2-WAY/DROTHER   00:00:34    10.1.1.2        GigabitEthernet0/1\n"
        )
        spec = {
            "ospf": [
                {"neighbor_ip": "10.1.1.2", "expected_state": "FULL"}
            ]
        }
        with self.assertRaises(HealthGateError) as ctx:
            self.engine.verify_device(mock_net, spec)
        self.assertIn("state '2-WAY' did not match expected 'FULL'", str(ctx.exception.failures))

    def test_verify_bgp_established_and_prefixes(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
            "192.168.100.2   4        65002     120     125        5    0    0 01:23:45       15\n"
        )
        spec = {
            "bgp": [
                {"peer_ip": "192.168.100.2", "expected_state": "Established", "min_prefixes_received": 10}
            ]
        }
        res = self.engine.verify_device(mock_net, spec)
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_assertions"], 1)

    def test_verify_bgp_idle_failure(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
            "192.168.100.2   4        65002       0       0        0    0    0 never    Active\n"
        )
        spec = {
            "bgp": [
                {"peer_ip": "192.168.100.2", "expected_state": "Established"}
            ]
        }
        with self.assertRaises(HealthGateError) as ctx:
            self.engine.verify_device(mock_net, spec)
        self.assertIn("state is 'Active' (expected Established)", str(ctx.exception.failures))

    def test_verify_interfaces_and_crc(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "GigabitEthernet0/1 is up, line protocol is up\n"
            "  Hardware is Gigabit Ethernet, address is 5000.0001.0000\n"
            "  0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
        )
        spec = {
            "interfaces": [
                {"name": "GigabitEthernet0/1", "admin_status": "up", "line_protocol": "up", "max_crc_errors_delta": 0}
            ]
        }
        res = self.engine.verify_device(mock_net, spec)
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_assertions"], 1)

    def test_verify_routes_next_hop(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = (
            "Routing entry for 10.200.0.0/24\n"
            "  [110/20] via 10.1.1.2, 00:05:12, GigabitEthernet0/1\n"
        )
        spec = {
            "routes": [
                {"prefix": "10.200.0.0/24", "expected_next_hop": "10.1.1.2"}
            ]
        }
        res = self.engine.verify_device(mock_net, spec)
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_assertions"], 1)

    def test_backward_compatible_classic_verify(self):
        mock_net = MagicMock()
        mock_net.send_command.return_value = "Success rate is 100 percent (5/5)\n"
        res = self.engine.verify_device(mock_net, ["ping 10.1.1.2"])
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_assertions"], 1)


class TestChaosOrchestrator(unittest.TestCase):
    """
    Test suite for Phase 3 (v3.3) Digital Twin Chaos Orchestrator.
    Verifies link resolution, link suspend fault injection, active probe execution,
    and automatic link restoration.
    """

    def setUp(self):
        self.connector = EveIacConnector(offline_mode=True)
        self.orchestrator = ChaosOrchestrator(self.connector)

    def test_link_name_resolution(self):
        resolved = self.orchestrator.resolve_link_id("lab_dc.unl", "Spine-01:e0/1 <-> Leaf-01:e0/1")
        self.assertEqual(resolved, "link_12")

        # Direct ID stays direct ID
        direct = self.orchestrator.resolve_link_id("lab_dc.unl", "link_99")
        self.assertEqual(direct, "link_99")

    def test_run_chaos_test_offline_success(self):
        test_spec = {
            "name": "Spine-Leaf Failover Test",
            "action": "link_suspend",
            "target_link": "link_12",
            "duration_seconds": 0.05,
            "assertions": {
                "max_failover_loss_packets": 1,
                "max_convergence_time_ms": 500
            }
        }
        mock_probe = MagicMock(return_value={"loss_packets": 1, "convergence_time_ms": 120})

        with patch.object(self.connector, "set_link_suspend", wraps=self.connector.set_link_suspend) as mock_suspend:
            result = self.orchestrator.run_chaos_test("lab_dc.unl", test_spec, probe_func=mock_probe)

            self.assertTrue(result["passed"])
            self.assertEqual(result["link_id"], "link_12")
            # Verify set_link_suspend was called with True (cut) then False (restore)
            self.assertEqual(mock_suspend.call_count, 2)
            mock_suspend.assert_any_call("lab_dc.unl", "link_12", suspended=True)
            mock_suspend.assert_any_call("lab_dc.unl", "link_12", suspended=False)

    def test_run_chaos_test_loss_assertion_failure(self):
        test_spec = {
            "name": "Excessive Drop Flap Test",
            "action": "link_suspend",
            "target_link": "link_12",
            "duration_seconds": 0.05,
            "assertions": {
                "max_failover_loss_packets": 0  # Requires zero packet drop
            }
        }
        # Probe reports 3 packets lost
        mock_probe = MagicMock(return_value={"loss_packets": 3, "convergence_time_ms": 800})

        result = self.orchestrator.run_chaos_test("lab_dc.unl", test_spec, probe_func=mock_probe)
        self.assertFalse(result["passed"])
        self.assertIn("Failover loss (3 pkts) exceeded maximum (0).", result["reasons"])

    def test_run_suite(self):
        tests = [
            {
                "name": "Test 1",
                "action": "link_suspend",
                "target_link": "link_12",
                "duration_seconds": 0.02
            },
            {
                "name": "Test 2 Quality",
                "action": "apply_quality",
                "target_link": "link_12",
                "delay_ms": 20,
                "duration_seconds": 0.02
            }
        ]
        suite_res = self.orchestrator.run_suite("lab_dc.unl", tests)
        self.assertEqual(len(suite_res), 2)
        self.assertTrue(suite_res[0]["passed"])
        self.assertTrue(suite_res[1]["passed"])

    def test_link_fault_context_guarantees_restoration_on_exception(self):
        with patch.object(self.connector, "set_link_suspend", wraps=self.connector.set_link_suspend) as mock_suspend:
            try:
                with self.orchestrator.link_fault_context("lab_dc.unl", "link_12"):
                    mock_suspend.assert_called_with("lab_dc.unl", "link_12", suspended=True)
                    raise RuntimeError("Simulated probe process crash")
            except RuntimeError as e:
                self.assertIn("Simulated probe", str(e))

            # Verify finally block restored the link
            mock_suspend.assert_called_with("lab_dc.unl", "link_12", suspended=False)

    def test_reset_all_lab_impairments(self):
        with patch.object(self.connector, "set_link_suspend") as mock_suspend, \
             patch.object(self.connector, "apply_link_quality") as mock_quality:
            cleaned = self.orchestrator.reset_all_lab_impairments("lab_dc.unl")
            self.assertGreaterEqual(cleaned, 1)
            mock_suspend.assert_called_with("lab_dc.unl", "link_12", suspended=False)
            mock_quality.assert_called_with("lab_dc.unl", "link_12", delay_ms=0, jitter_ms=0, loss_pct=0.0)

    def test_unresolvable_target_link_raises_error(self):
        with self.assertRaises(ValueError):
            self.orchestrator.resolve_link_id("lab_dc.unl", "NonExistent-01:e0/1 <-> NonExistent-02:e0/1")


class TestNodeStateMachine(unittest.TestCase):
    """Test suite for Phase 2 node lifecycle state machine."""

    def test_valid_transitions(self):
        from src.models.state import NodeStateMachine, NodeState
        sm = NodeStateMachine("n_1")
        self.assertEqual(sm.current_state, NodeState.ALLOCATED)

        sm.transition_to(NodeState.BOOTING)
        sm.transition_to(NodeState.CONSOLE_READY)
        sm.transition_to(NodeState.OOB_READY)
        sm.transition_to(NodeState.CONFIGURABLE)
        sm.transition_to(NodeState.VERIFIED)
        sm.transition_to(NodeState.CONVERGED)
        self.assertEqual(sm.current_state, NodeState.CONVERGED)

    def test_invalid_transition_raises(self):
        from src.models.state import NodeStateMachine, NodeState
        sm = NodeStateMachine("n_1")
        with self.assertRaises(ValueError):
            sm.transition_to(NodeState.CONVERGED)

    def test_generate_mermaid_diagram(self):
        from src.models.state import NodeStateMachine
        diagram = NodeStateMachine.generate_mermaid_diagram()
        self.assertIn("stateDiagram-v2", diagram)
        self.assertIn("allocated --> booting", diagram)
        self.assertIn("converged --> [*]", diagram)


if __name__ == "__main__":
    unittest.main()

