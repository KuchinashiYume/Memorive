from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from memorive.models.gateway.execution_core.component_binding import (
    ComponentBinding,
    bindings_from_policy,
    verify_component_bindings,
)
from memorive.models.gateway.execution_core.runner import IsolatedJobRunner, ProcessSpec
from memorive.models.gateway.execution_core.workspace import JobWorkspace
from tests.modules.model_gateway import (
    test_codex_cli_adapter as codex,
    test_claude_code_adapter as claude,
    test_gemini_cli_adapter as gemini,
    test_qwen_code_adapter as qwen,
    test_kimi_code_adapter as kimi,
    test_codebuddy_code_adapter as codebuddy,
    test_github_copilot_cli_adapter as copilot,
)


RUNNER_FIELDS = {
    "codex": ("binary_sha256", "exec_help_sha256", "runner_sha256"),
    "claude": ("binary_sha256", "help_sha256", "runner_sha256", "model_catalog_sha256"),
    "common": ("launcher_sha256", "wrapper_sha256", "entrypoint_sha256", "help_sha256", "runner_sha256", "model_catalog_sha256"),
    "kimi": ("launcher_sha256", "wrapper_sha256", "entrypoint_sha256", "help_sha256", "git_bash_sha256", "runner_sha256", "model_catalog_sha256"),
}


class ComponentBindingTests(unittest.TestCase):
    def test_all_seven_adapter_policies_measure_their_exact_component_set(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policies = {
                "codex": (codex.policy(), RUNNER_FIELDS["codex"]),
                "claude": (claude.policy(), RUNNER_FIELDS["claude"]),
                "gemini": (gemini.policy(root), RUNNER_FIELDS["common"]),
                "qwen": (qwen.policy(root), RUNNER_FIELDS["common"]),
                "kimi": (kimi.policy(root), RUNNER_FIELDS["kimi"]),
                "codebuddy": (codebuddy.policy(), RUNNER_FIELDS["common"]),
                "copilot": (copilot.policy(), RUNNER_FIELDS["common"]),
            }
            for adapter_id, (policy, fields) in policies.items():
                with self.subTest(adapter=adapter_id):
                    bindings = bindings_from_policy(policy, fields)
                    self.assertIsNone(verify_component_bindings(bindings))
                    self.assertEqual({item.name for item in bindings}, set(fields))

    def test_missing_path_and_each_hash_drift_fail(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "component.bin"
            path.write_bytes(b"trusted")
            digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            good = ComponentBinding("wrapper_sha256", str(path), digest)
            self.assertIsNone(verify_component_bindings((good,)))
            path.write_bytes(b"drift")
            self.assertEqual(verify_component_bindings((good,)), "COMPONENT_HASH_MISMATCH")
            path.unlink()
            self.assertEqual(verify_component_bindings((good,)), "COMPONENT_PATH_MISSING")

    def test_each_adapter_each_component_missing_and_hash_drift_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policies = {
                "codex": (codex.policy(), RUNNER_FIELDS["codex"]),
                "claude": (claude.policy(), RUNNER_FIELDS["claude"]),
                "gemini": (gemini.policy(root), RUNNER_FIELDS["common"]),
                "qwen": (qwen.policy(root), RUNNER_FIELDS["common"]),
                "kimi": (kimi.policy(root), RUNNER_FIELDS["kimi"]),
                "codebuddy": (codebuddy.policy(), RUNNER_FIELDS["common"]),
                "copilot": (copilot.policy(), RUNNER_FIELDS["common"]),
            }
            for adapter_id, (policy, fields) in policies.items():
                measured = bindings_from_policy(policy, fields)
                self.assertEqual([item.name for item in measured], list(fields))
                for target in measured:
                    with self.subTest(adapter=adapter_id, component=target.name, failure="missing"):
                        bindings = []
                        for item in measured:
                            component = root / adapter_id / item.name
                            component.parent.mkdir(parents=True, exist_ok=True)
                            payload = f"{adapter_id}:{item.name}:trusted".encode("utf-8")
                            component.write_bytes(payload)
                            bindings.append(ComponentBinding(item.name, str(component), hashlib.sha256(payload).hexdigest().upper()))
                        target_path = Path(next(item.path for item in bindings if item.name == target.name))
                        target_path.unlink()
                        self.assertEqual(verify_component_bindings(tuple(bindings)), "COMPONENT_PATH_MISSING")

                    with self.subTest(adapter=adapter_id, component=target.name, failure="hash_drift"):
                        bindings = []
                        for item in measured:
                            component = root / adapter_id / item.name
                            payload = f"{adapter_id}:{item.name}:trusted".encode("utf-8")
                            component.write_bytes(payload)
                            bindings.append(ComponentBinding(item.name, str(component), hashlib.sha256(payload).hexdigest().upper()))
                        target_path = Path(next(item.path for item in bindings if item.name == target.name))
                        target_path.write_bytes(b"drift")
                        self.assertEqual(verify_component_bindings(tuple(bindings)), "COMPONENT_HASH_MISMATCH")

    def test_second_measurement_blocks_before_popen(self):
        repo = (Path(__file__).resolve().parents[3] / "source")
        schema = json.loads((repo / "memorive" / "models" / "gateway" / "execution_core" / "schemas" / "execution_execution_core.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            component = root / "component.bin"
            component.write_bytes(b"stable")
            digest = hashlib.sha256(component.read_bytes()).hexdigest().upper()
            env_names = ("SYSTEMROOT",) if os.name == "nt" else ()
            environment = {"SYSTEMROOT": os.environ["SYSTEMROOT"]} if os.name == "nt" else {}
            workspace = JobWorkspace.create(
                root,
                "job_component_binding",
                schema,
                process_allowlist=(str(Path(sys.executable).resolve()),),
                environment_allowlist=env_names,
            )
            spec = ProcessSpec(
                adapter_id="fake",
                attempt_id="attempt_001",
                argv=(str(Path(sys.executable).resolve()), "-c", "print('must-not-run')"),
                cwd=str(workspace.scratch_root),
                environment=environment,
                environment_allowlist=env_names,
                timeout_seconds=1,
                raw_cap_bytes=1024,
                component_bindings=(ComponentBinding("runner_sha256", str(component), digest),),
            )
            with patch("memorive.models.gateway.execution_core.runner.verify_component_bindings", side_effect=[None, "COMPONENT_HASH_MISMATCH"]), patch("memorive.models.gateway.execution_core.runner.subprocess.Popen") as popen:
                run = IsolatedJobRunner(schema).run(workspace, spec)
            popen.assert_not_called()
            self.assertFalse(run.receipt["process_started"])
            self.assertEqual(run.receipt["extensions"]["runner_reason_code"], "COMPONENT_HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
