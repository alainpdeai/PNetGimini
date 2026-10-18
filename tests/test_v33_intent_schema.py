"""
Test suite for Phase 3 (P1-2) Pydantic Operational Intent Schema.
Verifies parsing, data type constraints, and schema export.
"""

import unittest
from pydantic import ValidationError
from src.models.intent import (
    OperationalIntentDocument,
    DeviceOperationalIntent,
    HealthGateVerify,
    PingAssertion,
    OspfAssertion,
    BgpAssertion,
    ChaosTestSpec
)


class TestOperationalIntentSchema(unittest.TestCase):

    def test_valid_full_intent_document(self):
        doc_data = {
            "apiVersion": "pnetgimini/v1alpha1",
            "kind": "OperationalIntent",
            "metadata": {
                "name": "DC-Spine-Leaf-Intent",
                "author": "NetDevOps",
                "description": "BGP evpn baseline"
            },
            "devices": {
                "Spine-01": {
                    "config": ["hostname Spine-01", "interface GigabitEthernet0/1"],
                    "show": ["show ip interface brief"],
                    "verify": {
                        "ping": [
                            {"target": "10.1.1.2", "count": 5, "max_loss_pct": 0.0}
                        ],
                        "ospf": [
                            {"neighbor_ip": "10.1.1.2", "expected_state": "FULL", "expected_metric": 20}
                        ],
                        "bgp": [
                            {"peer_ip": "192.168.1.1", "expected_state": "Established", "min_prefixes_received": 10}
                        ]
                    },
                    "chaos_tests": [
                        {
                            "name": "Link Failover",
                            "action": "link_suspend",
                            "target_link": "Spine-01:e0/1 <-> Leaf-01:e0/1",
                            "duration_seconds": 15,
                            "assertions": {
                                "max_failover_loss_packets": 1,
                                "max_convergence_time_ms": 1500
                            }
                        }
                    ]
                }
            }
        }
        doc = OperationalIntentDocument.model_validate(doc_data)
        self.assertEqual(doc.apiVersion, "pnetgimini/v1alpha1")
        self.assertIn("Spine-01", doc.devices)
        spine = doc.devices["Spine-01"]
        self.assertEqual(len(spine.config), 2)
        self.assertEqual(spine.verify.ospf[0].expected_state, "FULL")

    def test_backward_compatible_string_verify(self):
        doc_data = {
            "devices": {
                "Leaf-01": {
                    "config": ["hostname Leaf-01"],
                    "show": ["show version"],
                    "verify": ["ping 10.1.1.1", "ping 10.1.1.2"]
                }
            }
        }
        doc = OperationalIntentDocument.model_validate(doc_data)
        leaf = doc.devices["Leaf-01"]
        self.assertIsInstance(leaf.verify, list)
        self.assertEqual(len(leaf.verify), 2)

    def test_invalid_ospf_state_raises(self):
        invalid_data = {
            "neighbor_ip": "10.1.1.2",
            "expected_state": "NON_EXISTENT_STATE"
        }
        with self.assertRaises(ValidationError):
            OspfAssertion.model_validate(invalid_data)

    def test_invalid_ping_count_raises(self):
        invalid_data = {
            "target": "10.1.1.2",
            "count": 0  # ge=1 constraint
        }
        with self.assertRaises(ValidationError):
            PingAssertion.model_validate(invalid_data)

    def test_json_schema_export(self):
        schema_str = OperationalIntentDocument.export_json_schema()
        self.assertIn('"apiVersion"', schema_str)
        self.assertIn('"pnetgimini/v1alpha1"', schema_str)
        self.assertIn('"OperationalIntent"', schema_str)


if __name__ == "__main__":
    unittest.main()
