from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


SYNTHETIC_ARTIFACT_BYTES = b"P08/T11 synthetic public-safe evidence artifact\n"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(value)


@dataclass(frozen=True)
class IsolatedFixture:
    root: Path
    database_path: Path
    artifact_path: Path
    artifact_id: str
    artifact_sha256: str
    manifest_path: Path

    def request(self, *, suffix: str = "story") -> dict[str, Any]:
        return {
            "contract_revision": "1.0",
            "request_id": f"p08-t11-request-{suffix}",
            "correlation_id": f"p08-t11-correlation-{suffix}",
            "job_type": "synthetic-p08-t11-cross-page-e2e",
            "payload_ref": f"fixture:p08-t11:{suffix}",
            "input_hashes": {"synthetic_artifact": self.artifact_sha256},
            "profile_snapshot": {
                "profile_id": "p08-t11-isolated-offline",
                "role": "desktop-product-integration",
                "capability_revision": "p08-t11-caps-v1",
                "config_revision": "p08-t11-config-v1",
                "config_sha256": "A" * 64,
                "credential_refs": [],
                "execution_channel": "isolated_loopback",
                "fallback_policy": "FAIL_CLOSED",
                "resource_policy": {"fixture_root": "RUN_RELATIVE_ONLY"},
                "budget_policy": {"cost_cny": 0, "cost_usd": 0},
                "egress_policy": "ZERO_EGRESS",
                "data_classification": "SYNTHETIC_PUBLIC_SAFE",
                "module_revisions": {
                    "P08_T03": "frozen-handoff",
                    "P08_T10": "frozen-handoff",
                    "P08_T11": "construction-a-attempt001",
                },
                "source_hashes": {"synthetic_artifact": self.artifact_sha256},
                "created_at": "2026-08-26T00:00:00+00:00",
                "resolver_revision": "P08_T11_STABLE_LOCATOR_V1",
            },
            "capabilities": [
                {
                    "capability_id": "isolated-real-facade",
                    "requirement": "required",
                    "availability": "ready",
                    "qualification": "qualified",
                    "effective_enabled": True,
                    "reason_code": "NONE",
                    "required_action": "none",
                }
            ],
            "pause_supported": True,
            "resolved_task_input": {
                "classification": "SYNTHETIC_PUBLIC_SAFE",
                "artifact_id": self.artifact_id,
                "artifact_sha256": self.artifact_sha256,
                "fixture_root_policy": "EXACT_INJECTED_NO_FALLBACK",
            },
            "workflow_definition": {
                "schema_version": "P08T11SyntheticWorkflowDefinition-v1",
                "workflow_id": "p08-t11-cross-page-story",
                "nodes": [
                    {"node_id": "SETTINGS", "name": "设置"},
                    {"node_id": "INBOX", "name": "收件箱"},
                    {"node_id": "CURRENT_TASK", "name": "当前任务"},
                    {"node_id": "MESSAGES", "name": "消息"},
                    {"node_id": "SESSIONS", "name": "会话"},
                    {"node_id": "LIBRARY", "name": "资料库"},
                    {"node_id": "WORK_LOG", "name": "工作日志"},
                ],
            },
            "workflow_config": {
                "schema_version": "P08T11SyntheticWorkflowConfig-v1",
                "external_calls": 0,
                "production_roots": [],
            },
        }


def build_isolated_fixture(root: Path | str) -> IsolatedFixture:
    accepted = Path(root).resolve()
    if accepted.exists():
        raise FileExistsError(f"fixture already exists: {accepted}")
    accepted.mkdir(parents=True)
    artifact_id = "p08-t11-public-evidence-001"
    artifact_path = accepted / "public_safe" / "evidence.txt"
    _write_new(artifact_path, SYNTHETIC_ARTIFACT_BYTES)
    artifact_sha256 = _sha256(SYNTHETIC_ARTIFACT_BYTES)
    database_path = accepted / "store" / "application_facade.sqlite3"
    manifest_path = accepted / "FixtureManifest.json"
    manifest = {
        "schema_version": "P08T11IsolatedRealFixtureManifest-v1",
        "classification": "SYNTHETIC_PUBLIC_SAFE",
        "root": ".",
        "database_locator": "store/application_facade.sqlite3",
        "artifact": {
            "artifact_id": artifact_id,
            "locator": f"pr-os://artifact/__JOB_ID__/{artifact_id}",
            "relative_path": "public_safe/evidence.txt",
            "bytes": len(SYNTHETIC_ARTIFACT_BYTES),
            "sha256": artifact_sha256,
        },
        "production_fallback": False,
        "network_endpoints": [],
        "credential_refs": [],
        "private_payloads": [],
        "external_model_calls": 0,
        "provider_calls": 0,
        "cost_cny": 0,
        "cost_usd": 0,
    }
    _write_new(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return IsolatedFixture(
        root=accepted,
        database_path=database_path,
        artifact_path=artifact_path,
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        manifest_path=manifest_path,
    )
