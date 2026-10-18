"""
Declarative Health Gate Engine for PNetGimini (Phase 3 / v3.3).
Provides multi-vendor structured telemetry assertions for network operational validation:
  - Ping SLA & Latency Thresholds (max_loss_pct, max_avg_rtt_ms)
  - OSPF Neighbor Convergence & Metric Verification (FULL state, cost metric, neighbor count)
  - BGP Session & Prefix Floors (Established state, min_prefixes_received)
  - Interface Health (up/up status, CRC error increments)
  - FIB Next-Hop & Route Assertions
"""

import re
import time
import logging
from typing import Dict, Any, List, Optional, Union


class HealthGateError(RuntimeError):
    """Raised when one or more declarative health gate assertions fail."""
    def __init__(self, message: str, failures: List[Dict[str, Any]]):
        super().__init__(message)
        self.failures = failures


class HealthGateEngine:
    """
    Executes declarative quality gate assertions against live device CLI output.
    Supports Cisco IOS / XE and Huawei VRP outputs.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)

    def verify_device(self, net_connect, verify_spec: Union[Dict[str, Any], List[Any]]) -> Dict[str, Any]:
        """
        Verify device against a declarative specification.
        Returns a verification summary dict. Raises HealthGateError on assertion failure.
        """
        results = {
            "passed": True,
            "total_assertions": 0,
            "passed_assertions": 0,
            "failed_assertions": 0,
            "details": [],
            "failures": []
        }

        # If it's a list, it might be a list of strings (classic) or a list of dicts
        if isinstance(verify_spec, list):
            for item in verify_spec:
                if isinstance(item, str):
                    self._verify_classic_command(net_connect, item, results)
                elif isinstance(item, dict):
                    self._verify_structured_dict(net_connect, item, results)
        elif isinstance(verify_spec, dict):
            self._verify_structured_dict(net_connect, verify_spec, results)

        if results["failed_assertions"] > 0:
            summary_msg = f"Health Gate Failed: {results['failed_assertions']} assertion(s) violated."
            self.logger.error(summary_msg)
            raise HealthGateError(summary_msg, results["failures"])

        self.logger.info(f"Health Gate Passed: All {results['passed_assertions']} assertions satisfied.")
        return results

    def _verify_classic_command(self, net_connect, cmd: str, results: Dict[str, Any]):
        """Legacy string-based verification (e.g. ping with packet loss check)."""
        results["total_assertions"] += 1
        self.logger.info(f"Executing classic verify command: {cmd}")
        output = net_connect.send_command(cmd, read_timeout=45)
        lowered = output.lower()

        if "ping" in cmd.lower() and (
            "0.00% packet success" in lowered
            or "success rate is 0 percent" in lowered
            or "100.00% packet loss" in lowered
            or "100% packet loss" in lowered
        ):
            err_msg = f"Proactive Verification Failed: {cmd} reported 100% packet loss or 0% packet success."
            self.logger.error(err_msg)
            results["passed"] = False
            results["failed_assertions"] += 1
            results["failures"].append({"type": "classic_ping", "command": cmd, "reason": err_msg})
            raise RuntimeError(err_msg)
        else:
            results["passed_assertions"] += 1
            results["details"].append({"status": "PASSED", "type": "classic", "command": cmd, "output": output})

    def _verify_structured_dict(self, net_connect, spec_dict: Dict[str, Any], results: Dict[str, Any]):
        """Dispatch structured assertions by category."""
        if "ping" in spec_dict:
            self._verify_ping(net_connect, spec_dict["ping"], results)
        if "ospf" in spec_dict:
            self._verify_ospf(net_connect, spec_dict["ospf"], results)
        if "bgp" in spec_dict:
            self._verify_bgp(net_connect, spec_dict["bgp"], results)
        if "interfaces" in spec_dict:
            self._verify_interfaces(net_connect, spec_dict["interfaces"], results)
        if "routes" in spec_dict:
            self._verify_routes(net_connect, spec_dict["routes"], results)

    # --------------------------------------------------------------------------
    # Ping & Latency SLA Assertions
    # --------------------------------------------------------------------------
    def _verify_ping(self, net_connect, ping_specs: List[Dict[str, Any]], results: Dict[str, Any]):
        for p in ping_specs:
            results["total_assertions"] += 1
            target = p.get("target")
            count = p.get("count", 5)
            source = p.get("source")
            max_loss_pct = p.get("max_loss_pct", 0)
            max_avg_rtt_ms = p.get("max_avg_rtt_ms")

            # Formulate ping command
            cmd = f"ping {target}"
            if source:
                cmd += f" source {source}"
            if count != 5:
                cmd += f" repeat {count}"

            self.logger.info(f"[HealthGate] Running ping probe: {cmd}")
            output = net_connect.send_command(cmd, read_timeout=60)
            loss_pct, avg_rtt = self.parse_ping_output(output)

            detail = {
                "type": "ping",
                "target": target,
                "loss_pct": loss_pct,
                "avg_rtt_ms": avg_rtt,
                "max_loss_pct": max_loss_pct,
                "max_avg_rtt_ms": max_avg_rtt_ms
            }

            failed = False
            reasons = []
            if loss_pct is None or loss_pct > max_loss_pct:
                failed = True
                reasons.append(f"Packet loss {loss_pct}% exceeded threshold {max_loss_pct}%.")

            if max_avg_rtt_ms is not None and avg_rtt is not None and avg_rtt > max_avg_rtt_ms:
                failed = True
                reasons.append(f"Average RTT {avg_rtt}ms exceeded threshold {max_avg_rtt_ms}ms.")

            if failed:
                results["passed"] = False
                results["failed_assertions"] += 1
                detail["status"] = "FAILED"
                detail["reasons"] = reasons
                results["failures"].append(detail)
            else:
                results["passed_assertions"] += 1
                detail["status"] = "PASSED"

            results["details"].append(detail)

    def parse_ping_output(self, output: str) -> tuple:
        """Parse packet loss percentage and average RTT from Cisco or Huawei ping output."""
        loss_pct = None
        avg_rtt = None

        # Cisco pattern: "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms"
        cisco_match = re.search(r"Success rate is (\d+) percent.*?min/avg/max = \d+/(\d+)/\d+", output, re.S)
        if cisco_match:
            success_rate = float(cisco_match.group(1))
            loss_pct = 100.0 - success_rate
            avg_rtt = float(cisco_match.group(2))
            return loss_pct, avg_rtt

        # Cisco fallback without RTT: "Success rate is 80 percent (4/5)"
        cisco_loss_only = re.search(r"Success rate is (\d+) percent", output)
        if cisco_loss_only:
            loss_pct = 100.0 - float(cisco_loss_only.group(1))
            return loss_pct, avg_rtt

        # Huawei pattern: "0.00% packet loss" / "rtt min/avg/max = 1/3/8 ms"
        huawei_loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
        if huawei_loss:
            loss_pct = float(huawei_loss.group(1))
        huawei_rtt = re.search(r"min/avg/max = \d+/(\d+)/\d+", output)
        if huawei_rtt:
            avg_rtt = float(huawei_rtt.group(1))

        return loss_pct, avg_rtt

    # --------------------------------------------------------------------------
    # OSPF State & Metric Assertions
    # --------------------------------------------------------------------------
    def _evaluate_ospf_spec(self, spec: Dict[str, Any], output_neighbor: Optional[str], output_route: Optional[str]) -> tuple:
        """Evaluate a single OSPF specification against live show command output."""
        detail = {"type": "ospf", "spec": spec}
        reasons = []
        neighbor_ip = spec.get("neighbor_ip")
        expected_state = spec.get("expected_state", "FULL").upper()
        min_neighbors = spec.get("min_total_neighbors")
        route = spec.get("route")
        expected_metric = spec.get("expected_metric")

        # 1. Verify neighbor state
        if neighbor_ip:
            if not output_neighbor:
                reasons.append(f"OSPF neighbor {neighbor_ip} not found in adjacency table.")
            else:
                found_state = None
                for line in output_neighbor.splitlines():
                    if neighbor_ip in line:
                        parts = line.split()
                        for p in parts:
                            if any(p.startswith(s) for s in ["FULL", "2-WAY", "EXSTART", "EXCHANGE", "INIT", "ATTEMPT", "DOWN"]):
                                found_state = p.split("/")[0]
                                break
                        break

                if not found_state:
                    reasons.append(f"OSPF neighbor {neighbor_ip} not found in adjacency table.")
                elif found_state != expected_state:
                    reasons.append(f"OSPF neighbor {neighbor_ip} state '{found_state}' did not match expected '{expected_state}'.")
                detail["found_state"] = found_state

        # 2. Verify min_total_neighbors
        if min_neighbors is not None:
            if not output_neighbor:
                reasons.append(f"Total FULL OSPF neighbors below required minimum ({min_neighbors}).")
            else:
                full_count = output_neighbor.upper().count("FULL")
                detail["full_neighbor_count"] = full_count
                if full_count < min_neighbors:
                    reasons.append(f"Total FULL OSPF neighbors ({full_count}) below required minimum ({min_neighbors}).")

        # 3. Verify route cost metric
        if route and expected_metric is not None:
            if not output_route:
                reasons.append(f"OSPF route {route} not found or metric unobtainable.")
            else:
                metric_match = re.search(r"\[\d+/(\d+)\]", output_route)
                if metric_match:
                    found_metric = int(metric_match.group(1))
                    detail["found_metric"] = found_metric
                    if found_metric != expected_metric:
                        reasons.append(f"OSPF route {route} metric {found_metric} != expected {expected_metric}.")
                else:
                    reasons.append(f"OSPF route {route} not found or metric unobtainable.")

        return detail, reasons

    def _verify_ospf(self, net_connect, ospf_specs: List[Dict[str, Any]], results: Dict[str, Any]):
        output_neighbor = None
        output_route = None

        for spec in ospf_specs:
            results["total_assertions"] += 1
            neighbor_ip = spec.get("neighbor_ip")
            min_neighbors = spec.get("min_total_neighbors")
            route = spec.get("route")
            expected_metric = spec.get("expected_metric")
            timeout_seconds = spec.get("timeout_seconds", 0)
            polling_interval = spec.get("polling_interval_seconds", 2.0)

            if timeout_seconds and timeout_seconds > 0:
                self.logger.info(f"[HealthGate] Polling OSPF convergence (timeout={timeout_seconds}s, interval={polling_interval}s)...")
                deadline = time.time() + timeout_seconds
                while True:
                    if neighbor_ip or min_neighbors is not None:
                        output_neighbor = net_connect.send_command("show ip ospf neighbor")
                    if route and expected_metric is not None:
                        output_route = net_connect.send_command(f"show ip route {route}")

                    detail, reasons = self._evaluate_ospf_spec(spec, output_neighbor, output_route)
                    if not reasons:
                        failed = False
                        break
                    if time.time() >= deadline:
                        failed = True
                        break
                    time.sleep(polling_interval)
            else:
                if (neighbor_ip or min_neighbors is not None) and output_neighbor is None:
                    output_neighbor = net_connect.send_command("show ip ospf neighbor")
                if route and expected_metric is not None and output_route is None:
                    output_route = net_connect.send_command(f"show ip route {route}")

                detail, reasons = self._evaluate_ospf_spec(spec, output_neighbor, output_route)
                failed = bool(reasons)

            if failed:
                results["passed"] = False
                results["failed_assertions"] += 1
                detail["status"] = "FAILED"
                detail["reasons"] = reasons
                results["failures"].append(detail)
            else:
                results["passed_assertions"] += 1
                detail["status"] = "PASSED"

            results["details"].append(detail)

    # --------------------------------------------------------------------------
    # BGP Summary & Prefix Floor Assertions
    # --------------------------------------------------------------------------
    def _evaluate_bgp_spec(self, spec: Dict[str, Any], output_bgp: Optional[str]) -> tuple:
        """Evaluate a single BGP specification against live show ip bgp summary output."""
        peer_ip = spec.get("peer_ip")
        expected_state = spec.get("expected_state", "Established")
        min_prefixes = spec.get("min_prefixes_received", 0)
        detail = {"type": "bgp", "peer_ip": peer_ip}
        reasons = []

        if not output_bgp:
            reasons.append(f"BGP peer {peer_ip} not found in summary table.")
            return detail, reasons

        peer_line = None
        for line in output_bgp.splitlines():
            if peer_ip and peer_ip in line:
                peer_line = line.strip()
                break

        if not peer_line:
            reasons.append(f"BGP peer {peer_ip} not found in summary table.")
        else:
            tokens = peer_line.split()
            last_token = tokens[-1]
            if last_token.isdigit():
                actual_state = "Established"
                pfx_count = int(last_token)
            else:
                actual_state = last_token  # e.g., "Active", "Idle", "Connect"
                pfx_count = 0

            detail["actual_state"] = actual_state
            detail["prefixes_received"] = pfx_count

            if expected_state == "Established" and actual_state != "Established":
                reasons.append(f"BGP peer {peer_ip} state is '{actual_state}' (expected Established).")

            if min_prefixes > 0 and pfx_count < min_prefixes:
                reasons.append(f"BGP peer {peer_ip} received prefixes ({pfx_count}) < floor threshold ({min_prefixes}).")

        return detail, reasons

    def _verify_bgp(self, net_connect, bgp_specs: List[Dict[str, Any]], results: Dict[str, Any]):
        output_bgp = None
        for spec in bgp_specs:
            results["total_assertions"] += 1
            timeout_seconds = spec.get("timeout_seconds", 0)
            polling_interval = spec.get("polling_interval_seconds", 2.0)

            if timeout_seconds and timeout_seconds > 0:
                self.logger.info(f"[HealthGate] Polling BGP convergence (timeout={timeout_seconds}s, interval={polling_interval}s)...")
                deadline = time.time() + timeout_seconds
                while True:
                    output_bgp = net_connect.send_command("show ip bgp summary")
                    detail, reasons = self._evaluate_bgp_spec(spec, output_bgp)
                    if not reasons:
                        failed = False
                        break
                    if time.time() >= deadline:
                        failed = True
                        break
                    time.sleep(polling_interval)
            else:
                if output_bgp is None:
                    output_bgp = net_connect.send_command("show ip bgp summary")
                detail, reasons = self._evaluate_bgp_spec(spec, output_bgp)
                failed = bool(reasons)

            if failed:
                results["passed"] = False
                results["failed_assertions"] += 1
                detail["status"] = "FAILED"
                detail["reasons"] = reasons
                results["failures"].append(detail)
            else:
                results["passed_assertions"] += 1
                detail["status"] = "PASSED"

            results["details"].append(detail)

    # --------------------------------------------------------------------------
    # Interface Health & CRC Assertions
    # --------------------------------------------------------------------------
    def _verify_interfaces(self, net_connect, iface_specs: List[Dict[str, Any]], results: Dict[str, Any]):
        for spec in iface_specs:
            results["total_assertions"] += 1
            name = spec.get("name")
            expected_admin = spec.get("admin_status", "up").lower()
            expected_proto = spec.get("line_protocol", "up").lower()
            max_crc_errors = spec.get("max_crc_errors_delta", 0)

            output = net_connect.send_command(f"show interfaces {name}")
            detail = {"type": "interface", "interface": name}
            failed = False
            reasons = []

            # Check status line: "GigabitEthernet0/1 is up, line protocol is up"
            status_match = re.search(r"is (up|down|administratively down), line protocol is (up|down)", output)
            if status_match:
                admin_status = status_match.group(1).lower()
                line_proto = status_match.group(2).lower()
                detail["admin_status"] = admin_status
                detail["line_protocol"] = line_proto

                if expected_admin not in admin_status:
                    failed = True
                    reasons.append(f"Interface {name} admin status '{admin_status}' != '{expected_admin}'.")
                if expected_proto not in line_proto:
                    failed = True
                    reasons.append(f"Interface {name} line protocol '{line_proto}' != '{expected_proto}'.")
            else:
                failed = True
                reasons.append(f"Could not parse status for interface {name}.")

            # Check CRC errors
            crc_match = re.search(r"(\d+)\s+CRC", output)
            if crc_match:
                crc_count = int(crc_match.group(1))
                detail["crc_errors"] = crc_count
                if crc_count > max_crc_errors:
                    failed = True
                    reasons.append(f"Interface {name} has {crc_count} CRC errors (exceeds {max_crc_errors}).")

            if failed:
                results["passed"] = False
                results["failed_assertions"] += 1
                detail["status"] = "FAILED"
                detail["reasons"] = reasons
                results["failures"].append(detail)
            else:
                results["passed_assertions"] += 1
                detail["status"] = "PASSED"

            results["details"].append(detail)

    # --------------------------------------------------------------------------
    # Route Next-Hop Assertions
    # --------------------------------------------------------------------------
    def _verify_routes(self, net_connect, route_specs: List[Dict[str, Any]], results: Dict[str, Any]):
        for spec in route_specs:
            results["total_assertions"] += 1
            prefix = spec.get("prefix")
            expected_next_hop = spec.get("expected_next_hop")

            output = net_connect.send_command(f"show ip route {prefix}")
            detail = {"type": "route", "prefix": prefix}
            failed = False
            reasons = []

            if expected_next_hop:
                if f"via {expected_next_hop}" not in output and expected_next_hop not in output:
                    failed = True
                    reasons.append(f"Route {prefix} next-hop {expected_next_hop} not found in routing entry.")

            if failed:
                results["passed"] = False
                results["failed_assertions"] += 1
                detail["status"] = "FAILED"
                detail["reasons"] = reasons
                results["failures"].append(detail)
            else:
                results["passed_assertions"] += 1
                detail["status"] = "PASSED"

            results["details"].append(detail)
