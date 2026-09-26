"""Local full-exam cache. Original runs and packaged scores remain immutable."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid
from .contracts import canonical_sha256
from .store import SettingsStore

class ExamScoreHistory:
    def __init__(self, root: Path):
        self.root = Path(root)

    def rows(self):
        if not self.root.is_dir():
            return []
        rows = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                digest = row.pop("record_sha256")
                if canonical_sha256(row) != digest or row.get("schema_version") != "LocalFullExamScore-v1":
                    continue
                if type(row.get("score")) is not int or not 0 <= row["score"] <= 100:
                    continue
                if not all(isinstance(row.get(k), str) and row[k] for k in
                           ("node_id", "target_sha256", "executor_ref", "evidence_sha256", "verdict")):
                    continue
                rows.append(row)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return rows

    def lookup(self, node, target, active_executors):
        identity = canonical_sha256(dict(target))
        return next((r for r in self.rows() if r["node_id"] == node.get("node_id")
                     and r["target_sha256"] == identity and r["executor_ref"] in active_executors), None)

    def recover_completed_embedding(self, node, target, active_executors):
        """Recover exam-history full local results from their immutable plan, not a label."""
        if (node.get("node_id") != "chunk_embedding" or target.get("kind") != "LOCAL"
                or target.get("endpoint_kind") != "ollama"
                or not target.get("model_digest") or not target.get("profile_ref")):
            return None
        runs = self.root.parent / "workflow_exams"
        if not runs.is_dir():
            return None
        # Never inspect partial-tier directories, provider outputs, claims or Gold.
        for folder in sorted(runs.glob("workflow-exam-*"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                paths = [folder / "plan.json", folder / "exam_result.json"]
                if any(not p.is_file() or p.stat().st_size > 2_000_000 for p in paths):
                    continue
                plan, result = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
                digest = plan.get("plan_sha256")
                if canonical_sha256({k:v for k,v in plan.items() if k != "plan_sha256"}) != digest:
                    continue
                tier = plan.get("exam_tier") or {}
                if (plan.get("status") != "READY" or tier.get("tier") != "FULL"
                        or tier.get("selected_count") != tier.get("full_count")
                        or plan.get("node_id") != node["node_id"]
                        or plan.get("profile_kind") != "LOCAL"
                        or plan.get("profile_ref") != target["profile_ref"]
                        or plan.get("model_name") != target.get("model_name")
                        or str(plan.get("model_digest")).upper() != str(target["model_digest"]).upper()
                        or plan.get("executor_ref") not in active_executors
                        or result.get("executor_ref") not in {None, plan["executor_ref"]}
                        or result.get("execution_outcome") != "COMPLETED"
                        or result.get("scorability") != "SCOREABLE"
                        or result.get("run_id") != folder.name
                        or result.get("plan_sha256") != digest
                        or result.get("requested_model") != target["model_name"]
                        or result.get("returned_model") != target["model_name"]
                        or result.get("sample_revision") != plan.get("sample_revision")
                        or result.get("exam_category_id") != plan.get("exam_category_id")
                        or result.get("provider_received_answer_key") is not False
                        or result.get("provider_received_gold") is not False
                        or type(plan.get("maximum_model_calls")) is not int
                        or plan["maximum_model_calls"] <= 0
                        or result.get("external_model_calls") != plan["maximum_model_calls"]
                        or result.get("request_attempts") != plan["maximum_model_calls"]):
                    continue
                recovered = {**result, "executor_ref":plan["executor_ref"], "exam_tier":tier,
                    "recovered_from_missing_executor_ref":True,
                    "source_exam_result_sha256":hashlib.sha256(paths[1].read_bytes()).hexdigest().upper(),
                    "source_plan_sha256":digest}
                if self.record(node, target, recovered, active_executors):
                    return self.lookup(node, target, active_executors)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def record(self, node, target, result, active_executors):
        tier = result.get("exam_tier") or {}
        score = result.get("score")
        if (result.get("status") not in {"PASS", "FAIL"} or type(score) is not int
                or not 0 <= score <= 100 or tier.get("tier", "FULL") != "FULL"
                or result.get("stable_score_cache_eligible") is False
                or not isinstance(result.get("executor_ref"), str)
                or result.get("cache_reuse_status", "STABLE_REUSE") != "STABLE_REUSE"
                or result.get("executor_ref") not in active_executors):
            return False
        row = dict(schema_version="LocalFullExamScore-v1", node_id=node["node_id"],
                   target_sha256=canonical_sha256(dict(target)), profile_kind=target["kind"],
                   model_name=target.get("model_name"), score=score, verdict=result["status"],
                   executor_ref=result["executor_ref"], evidence_sha256=canonical_sha256(dict(result)),
                   recorded_at=datetime.now(timezone.utc).isoformat(),
                   formal_qualification_eligible=False, exam_tier=deepcopy(tier))
        for key in ("thinking", "thinking_mode", "tier", "adapter_id", "model_digest", "endpoint_kind"):
            if key in target:
                row[key] = target[key]
        for key in ("recovered_from_missing_executor_ref", "source_exam_result_sha256", "source_plan_sha256"):
            if key in result:
                row[key] = result[key]
        row["record_sha256"] = canonical_sha256(row)
        self.root.mkdir(parents=True, exist_ok=True)
        # Existing atomic persistence, a new record each time, never an overwrite.
        SettingsStore(self.root)._atomic_write(self.root / (row["recorded_at"].replace(":", "-") + "-" + uuid.uuid4().hex + ".json"), row)
        return True
