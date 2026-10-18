import logging
import os
from pathlib import Path
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
import datetime
import re
from typing import List, Optional, Any, Dict

from ..models.device import Device
from .adapters.factory import AdapterFactory
from .config_diff import ConfigDiffEngine

CLI_ERROR_PATTERNS = [
    re.compile(r"%\s*(invalid input|incomplete command|ambiguous command|bad IP address|authorization failed)", re.IGNORECASE),
    re.compile(r"Error:\s*(unrecognized command|wrong parameter|incomplete command|ambiguous command|too many parameters|invalid parameter)", re.IGNORECASE),
    re.compile(r"%\s*error", re.IGNORECASE),
    re.compile(r"syntax error", re.IGNORECASE),
]

class DeviceManager:
    """
    Manages device connections, command deployment, and intelligent diff-based recovery.
    Supports multi-vendor abstraction (Cisco, Huawei) and structured snapshot lifecycle management.
    """
    def __init__(
        self,
        device: Device,
        outputs_dir: Path,
        prefer_native_rollback: bool = False,
        self_healing_engine: Optional[Any] = None
    ):
        self.device = device
        self.logger = logging.getLogger(__name__)
        self.outputs_dir = outputs_dir
        self.snapshots_dir = self.outputs_dir / "snapshots"
        self.adapter = AdapterFactory.get_adapter(self.device.device_type)
        self.prefer_native_rollback = prefer_native_rollback
        self.self_healing_engine = self_healing_engine

    def _create_connection_info(self) -> dict:
        """
        Creates the connection dictionary for Netmiko using vendor adapter mappings.
        Supports password, passwordless telnet, SSH private key authentication, and enable secret.
        """
        enable_secret = getattr(self.device, "secret", self.device.password) or self.device.password
        netmiko_type = self.adapter.get_netmiko_device_type(self.device.device_type)
        
        conn_dict = {
            "device_type": netmiko_type,
            "host": self.device.ip,
            "port": self.device.port,
            "username": self.device.username,
            "password": self.device.password,
            "secret": enable_secret,
            "timeout": 30,
            "global_delay_factor": 2,  # Increased delay factor for virtual lab stability
        }

        # SSH Private Key authentication support
        if getattr(self.device, "key_file", None):
            conn_dict["use_keys"] = True
            conn_dict["key_file"] = self.device.key_file
            if getattr(self.device, "passphrase", None):
                conn_dict["passphrase"] = self.device.passphrase

        return conn_dict

    def deploy_commands(self) -> dict:
        """
        Connects to the device and deploys commands with an intelligent diff-based rollback mechanism.
        """
        self.logger.info(f"Starting deployment for device: {self.device.ip}:{self.device.port} ({self.device.device_type})")
        result = {
            "device": str(self.device),
            "status": "ERROR",
            "details": [],
            "error_message": None,
            "snapshot_file": None,
            "rollback_applied": False,
            "rollback_type": None
        }

        net_connect = None
        initial_config = ""
        deployed_config_commands: List[str] = []
        
        try:
            # Step 1: Establish connection
            net_connect = ConnectHandler(**self._create_connection_info())
            result["status"] = "CONNECTED"
            self.logger.info(f"Successfully connected to device: {self.device.ip}")
            
            # Enter privileged EXEC mode if applicable
            try:
                net_connect.enable()
                self.logger.info(f"Successfully entered privileged EXEC / system mode.")
            except Exception as enable_err:
                self.logger.debug(f"Enable mode skipped or not required: {enable_err}")

            # Optimization: Disable paging using vendor-specific command
            paging_cmd = self.adapter.get_disable_paging_command()
            self.logger.info(f"Disabling pagination with: '{paging_cmd}'")
            try:
                net_connect.send_command(paging_cmd)
            except Exception as paging_err:
                self.logger.warning(f"Could not disable pagination with '{paging_cmd}': {paging_err}")
            
            # Step 2: Save current running-config for baseline snapshot
            snapshot_cmd = self.adapter.get_snapshot_command()
            self.logger.info(f"Capturing pre-change running configuration snapshot via: '{snapshot_cmd}'")
            initial_config = net_connect.send_command(snapshot_cmd, read_timeout=90)
            
            # Ensure snapshots directory exists
            self.snapshots_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
            backup_filename = f"{self.device.ip}_{self.device.port}_snapshot_{timestamp}.conf"
            backup_path = self.snapshots_dir / backup_filename
            
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(initial_config)
            result["snapshot_file"] = str(backup_path)
            self.logger.info(f"Pre-change snapshot archived to: {backup_path}")

            # If native rollback is preferred and supported, archive on-device snapshot
            native_enabled = (
                self.prefer_native_rollback or getattr(self.device, "prefer_native_rollback", False)
            )
            if native_enabled and self.adapter.supports_native_rollback():
                try:
                    native_snapshot_name = f"pnet_base_{timestamp}.cfg"
                    native_snap_cmd = self.adapter.get_native_snapshot_command(native_snapshot_name)
                    if native_snap_cmd:
                        self.logger.info(f"Archiving on-device baseline snapshot via: '{native_snap_cmd}'")
                        net_connect.send_command(native_snap_cmd, read_timeout=90)
                        result["native_snapshot_name"] = native_snapshot_name
                except Exception as native_snap_err:
                    self.logger.warning(f"On-device native snapshot capture failed ({native_snap_err}); relying on local snapshot.")

            self.logger.info("--- Device live output started ---")

            # Step 3: Deploy new commands
            for command_obj in self.device.commands:
                category = command_obj.category.lower()
                commands = command_obj.commands
                
                self.logger.info(f"  --- Executing command category: {category} ---")
                category_result = {
                    "category": category,
                    "commands_sent": commands,
                    "output": "",
                    "status": "SUCCESS"
                }
                
                try:
                    if category == "config":
                        self.logger.info(f"Deploying configuration set ({len(commands)} commands)...")
                        # Track commands intended/attempted for precision diff rollback in case of failure
                        deployed_config_commands.extend(commands)
                        output = net_connect.send_config_set(
                            commands, 
                            delay_factor=2, 
                            read_timeout=30, 
                            cmd_verify=False
                        )
                        self.logger.info(output)

                        # Inspect output for silent CLI syntax / execution errors
                        for pattern in CLI_ERROR_PATTERNS:
                            match = pattern.search(output)
                            if match:
                                err_msg = f"CLI configuration error detected on {self.device.ip}: '{match.group(0)}'"
                                self.logger.error(err_msg)
                                raise RuntimeError(err_msg)

                    elif category == "show":
                        output = ""
                        for cmd in commands:
                            if "ping" in cmd.lower():
                                self.logger.info(f"Executing ping with extended timeout: {cmd}")
                                cmd_output = net_connect.send_command(cmd, read_timeout=60)
                            else:
                                cmd_output = net_connect.send_command(cmd)
                            output += cmd_output + "\n"
                            self.logger.info(cmd_output)

                    elif category in ("verify", "check", "assert"):
                        # Declarative Health Gate & proactive verification (Phase 3 / v3.3)
                        from src.core.health_gate import HealthGateEngine
                        health_gate = HealthGateEngine(self.logger)
                        gate_results = health_gate.verify_device(net_connect, commands)
                        category_result["health_gate"] = gate_results
                        output = f"Health Gate Passed: {gate_results['passed_assertions']}/{gate_results['total_assertions']} assertions satisfied."

                    else:
                        output = f"Unsupported command category '{category}'."
                        category_result["status"] = "SKIPPED"
                        self.logger.warning(output)

                    category_result["output"] = output
                    result["details"].append(category_result)
                
                except Exception as command_e:
                    self.logger.error(f"Failed to execute {category} command: {command_e}")
                    category_result["status"] = "ERROR"
                    category_result["output"] = str(command_e)
                    result["details"].append(category_result)
                    raise

            self.logger.info("--- Device live output ended ---")
            result["status"] = "CONFIGURED"

        except (NetmikoAuthenticationException, NetmikoTimeoutException) as e:
            self.logger.error(f"Connection or authentication failed: {e}")
            result["status"] = "ERROR"
            result["error_message"] = f"Connection or authentication failed: {e}"

        except Exception as e:
            self.logger.critical(f"An error occurred during deployment on {self.device.ip}: {e}", exc_info=True)
            result["status"] = "ERROR"
            result["error_message"] = str(e)
            
            if net_connect and initial_config:
                try:
                    self.logger.warning(f"Deployment failed on {self.device.ip}. Computing rollback strategy...")
                    try:
                        net_connect.send_command(self.adapter.get_disable_paging_command())
                    except Exception as rb_paging_err:
                        self.logger.debug(f"Paging command skipped during rollback: {rb_paging_err}")

                    # 1. Try Native Rollback first if preferred and available
                    native_applied = False
                    if result.get("native_snapshot_name") and self.adapter.supports_native_rollback():
                        try:
                            rb_cmd = self.adapter.get_native_rollback_command(result["native_snapshot_name"])
                            if rb_cmd:
                                self.logger.warning(f"Applying NATIVE atomic rollback via: '{rb_cmd}'")
                                native_out = net_connect.send_command(rb_cmd, read_timeout=120)
                                self.logger.info(native_out)
                                if "% Error" not in native_out and "Aborted" not in native_out:
                                    result["rollback_applied"] = True
                                    result["rollback_type"] = "NATIVE_REPLACE"
                                    native_applied = True
                                    self.logger.info(f"Native atomic rollback completed successfully on {self.device.ip}.")
                                else:
                                    self.logger.warning("Native rollback reported errors; falling back to Diff reversal.")
                        except Exception as native_err:
                            self.logger.warning(f"Native rollback failed ({native_err}); falling back to Diff reversal.")

                    # 2. Diff Fallback
                    if not native_applied:
                        reversal_plan = ConfigDiffEngine.compute_reversion_plan(
                            baseline_raw=initial_config,
                            deployed_commands=deployed_config_commands,
                            adapter=self.adapter
                        )
                        if reversal_plan:
                            self.logger.warning(f"Applying intelligent DIFF-BASED rollback ({len(reversal_plan)} commands): {reversal_plan}")
                            rollback_output = net_connect.send_config_set(
                                reversal_plan,
                                delay_factor=2,
                                read_timeout=60,
                                cmd_verify=False
                            )
                            result["rollback_applied"] = True
                            result["rollback_type"] = "DIFF_BASED"
                            self.logger.info(f"Diff-based rollback completed successfully on {self.device.ip}.")
                        else:
                            self.logger.warning("No specific diff calculated; falling back to clean baseline restore.")
                            clean_baseline = ConfigDiffEngine.get_clean_baseline_commands(initial_config, self.adapter)
                            rollback_output = net_connect.send_config_set(
                                clean_baseline,
                                delay_factor=2,
                                read_timeout=120,
                                cmd_verify=False
                            )
                            result["rollback_applied"] = True
                            result["rollback_type"] = "CLEAN_BASELINE"
                            self.logger.info(f"Clean baseline restore completed on {self.device.ip}.")

                except Exception as rollback_e:
                    self.logger.critical(f"Tier-1 rollback execution failed on {self.device.ip}: {rollback_e}", exc_info=True)
                    result["error_message"] += f" | Rollback also failed: {rollback_e}"
                    result["tier1_exhausted"] = True

                    # 3. Escalate to Tier 2 if SelfHealingEngine is configured
                    if self.self_healing_engine and getattr(self.device, "lab_id", None):
                        try:
                            node_key = getattr(self.device, "node_id", self.device.name)
                            heal_result = self.self_healing_engine.heal_node(
                                lab_id=self.device.lab_id,
                                node_key=node_key,
                                reason=f"Tier-1 rollback failure: {rollback_e}"
                            )
                            result["tier2_healing"] = heal_result
                        except Exception as heal_err:
                            self.logger.critical(f"Tier-2 self-healing escalation also failed: {heal_err}")
                            result["error_message"] += f" | Tier-2 escalation failed: {heal_err}"
            else:
                result["tier1_exhausted"] = True
                if self.self_healing_engine and getattr(self.device, "lab_id", None):
                    try:
                        node_key = getattr(self.device, "node_id", self.device.name)
                        heal_result = self.self_healing_engine.heal_node(
                            lab_id=self.device.lab_id,
                            node_key=node_key,
                            reason=f"Connection/Auth failure: {e}"
                        )
                        result["tier2_healing"] = heal_result
                    except Exception as heal_err:
                        self.logger.critical(f"Tier-2 self-healing escalation failed: {heal_err}")
                        result["error_message"] += f" | Tier-2 escalation failed: {heal_err}"
        
        finally:
            if net_connect:
                try:
                    net_connect.disconnect()
                except Exception:
                    pass
                
        return result