"""Strict, read-only KNOWLEDGE_ADMISSION/RUNTIME_LOG/ARTIFACT_REGISTRY adapters into the DataContracts ResearchEvent view."""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import Any

from .canonical import (
    canonical_json_bytes,
    forbidden_phrase,
    parse_datetime,
    require_safe_text,
    sha256_bytes,
    typed_payload_hash,
    validate_source_value,
)
from .errors import SourceRecordError
from .artifact_registry_wire import extract_artifact_registry_event

FAILURE_SEMANTICS = {
    "BLOCKED": "fail_closed",
    "ERROR": "quarantined",
    "FAIL": "fail_closed",
    "NOT_ASSESSED": "no_eligibility",
}
IMMUTABLE_FIELDS = [
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "parent_refs",
    "provenance_refs",
    "supersedes",
]

KNOWLEDGE_ADMISSION_STATES = {"pending", "active", "quarantined"}
KNOWLEDGE_ADMISSION_TRANSITIONS = {
    ("pending", "active"),
    ("pending", "quarantined"),
    ("active", "quarantined"),
}
EVIDENCE_REVIEW_TRIGGER = "EvidenceReview校核结果"
KNOWLEDGE_ADMISSION_TRIGGERS = {"我手动", "占位", "自动规则", "EvidenceReview校核结果", "分级信任准入"}
EVIDENCE_REVIEW_REPORT_TOKEN = re.compile(r"(?:^|;\s*)report=([A-Za-z0-9_-]+)(?=$|;\s*)")

ATTEMPT_RESULTS = {"success", "failure", "incomplete", "error", "cancelled", "not_assessed"}
PAPER_OUTCOMES = {"success", "failure", "partial", "not_run"}
PUBLICATION_STATUSES = {"not_requested", "not_published", "published", "publication_failed"}
LIFECYCLE_STATUSES = {"completed", "aborted", "invalidated"}
ACCEPTANCE_VERDICTS = {"PASS", "FAIL", "NOT_ASSESSED"}
RECORD_KINDS = {"terminal", "amendment"}

RUNTIME_LOG_COMMON = {
    "schema_version",
    "record_type",
    "ledger_type",
    "record_id",
    "record_kind",
    "run_id",
    "created_at",
    "source_evidence_refs",
}
ATTEMPT_BASE = {
    "attempt_id",
    "logical_operation_id",
    "attempt_no",
    "task_id",
    "stage",
    "paper_id",
    "retry_of_attempt_id",
    "business_repair_of",
    "route_snapshot_id",
    "execution_config_hash",
}
PAPER_FIELDS = {
    "outcome_id",
    "paper_id",
    "outcome_category",
    "outcome_code",
    "reason_codes",
    "final_artifact_refs",
    "supersedes_record_id",
    "reprocesses_run_id",
}
PUBLICATION_FIELDS = {
    "publication_event_id",
    "publication_status",
    "manifest_ref",
    "subject_artifact_refs",
    "receipt_ref",
    "supersedes_record_id",
    "absence_reason",
}

_OFFICIAL_FILE_SPECS = {
    "run_ledger.schema": (
        ("run_ledger/schema.py", 12472, "238956DC959F8C1A332ABEFD512C174135CC52109760512429946DF0F438E69F"),
    ),
    "artifact_registry.schema_v2": (
        ("artifact_registry/schema.py", 18715, "AFD52E3A35D160675C0B9A267988F3DED5E1E26573C0CC48039B1CC352C1EC0F"),
        ("artifact_registry/schema_v2.py", 22568, "35A6F282527308340006486A0F554F8EE425800D534C697FD95E892750CD8C35"),
    ),
}
OFFICIAL_VALIDATOR_RECEIPTS: dict[str, dict[str, Any]] = {}


def _official_callable(module_name: str, function_name: str):
    """Import a hash-pinned formal validator from the exact embedded/formal repo.

    An already-imported module from another checkout is a control-plane mismatch,
    not a reusable fallback.  This prevents ambient ``sys.path`` order from
    silently selecting a mutable or stale validator.
    """

    package_root = Path(__file__).resolve().parent
    embedded_repo = package_root.parents[1] / "repo"
    formal_repo = package_root.parent
    specs = _OFFICIAL_FILE_SPECS.get(module_name)
    if specs is None:
        _error("OFFICIAL_VALIDATOR_UNAVAILABLE", f"unfrozen official module {module_name}", "schema_version")
    expected_repo = embedded_repo if all((embedded_repo / item[0]).is_file() for item in specs) else formal_repo
    if not all((expected_repo / item[0]).is_file() for item in specs):
        _error("OFFICIAL_VALIDATOR_UNAVAILABLE", f"frozen official module {module_name} is unavailable", "schema_version")
    expected_repo = expected_repo.resolve()
    top_package = module_name.split(".", 1)[0]

    def assert_loaded_origin() -> None:
        expected_package = expected_repo / top_package
        for loaded_name, loaded in tuple(sys.modules.items()):
            if loaded_name != top_package and not loaded_name.startswith(top_package + "."):
                continue
            loaded_file = getattr(loaded, "__file__", None)
            if loaded_file is None:
                _error("OFFICIAL_VALIDATOR_ORIGIN_MISMATCH", f"{loaded_name} has no file origin", "schema_version")
            try:
                Path(loaded_file).resolve().relative_to(expected_package)
            except ValueError:
                _error("OFFICIAL_VALIDATOR_ORIGIN_MISMATCH", f"{loaded_name} is outside frozen repo", "schema_version")

    assert_loaded_origin()
    expected_text = str(expected_repo)
    sys.path[:] = [item for item in sys.path if str(Path(item or ".").resolve()) != expected_text]
    sys.path.insert(0, expected_text)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        _error("OFFICIAL_VALIDATOR_UNAVAILABLE", f"frozen official module {module_name} is unavailable", "schema_version")
    assert_loaded_origin()
    expected_module = (expected_repo / (module_name.replace(".", "/") + ".py")).resolve()
    if Path(getattr(module, "__file__", "")).resolve() != expected_module:
        _error("OFFICIAL_VALIDATOR_ORIGIN_MISMATCH", f"{module_name} did not resolve to exact file", "schema_version")
    receipt_files = []
    for relative, expected_bytes, expected_hash in specs:
        frozen = expected_repo / relative
        raw = frozen.read_bytes()
        observed_hash = sha256_bytes(raw)
        if len(raw) != expected_bytes or observed_hash != expected_hash:
            _error("OFFICIAL_VALIDATOR_HASH_MISMATCH", f"{relative} differs from frozen authority", "schema_version")
        receipt_files.append(
            {"path": relative, "bytes": expected_bytes, "sha256": expected_hash}
        )
    OFFICIAL_VALIDATOR_RECEIPTS[module_name] = {
        "expected_repo": str(expected_repo),
        "module_file": str(expected_module),
        "files": receipt_files,
    }
    validator = getattr(module, function_name, None)
    if not callable(validator):
        _error("OFFICIAL_VALIDATOR_UNAVAILABLE", f"{module_name}.{function_name} is unavailable", "schema_version")
    return validator
RUN_TERMINAL_FIELDS = {
    "schema_version",
    "record_type",
    "ledger_type",
    "record_id",
    "run_id",
    "lifecycle_status",
    "acceptance_verdict",
    "terminal_at",
    "source_evidence_refs",
    "supersedes_record_id",
}


def _error(code: str, message: str, *fields: str) -> None:
    raise SourceRecordError(code, message, field_names=fields)


def _exact(record: dict[str, Any], expected: set[str], label: str) -> None:
    if set(record) != expected:
        _error(
            "SOURCE_FIELDS_MISMATCH",
            f"{label} fields mismatch",
            *(sorted(expected ^ set(record))),
        )


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    try:
        return require_safe_text(value, field, allow_empty=allow_empty)
    except (TypeError, ValueError) as exc:
        _error("INVALID_IDENTITY_FIELD", str(exc), field)
    raise AssertionError("unreachable")


def _output_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    result = _text(value, field, allow_empty=allow_empty)
    hit = forbidden_phrase(result)
    if hit:
        _error("FORBIDDEN_OUTPUT_SEMANTICS", f"{field} contains forbidden phrase", field)
    return result


def _optional_text(value: Any, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _text_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = True,
    output_facing: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        _error("INVALID_LIST_FIELD", f"{field} must be a valid list", field)
    reader = _output_text if output_facing else _text
    result = [reader(item, f"{field}[{index}]") for index, item in enumerate(value)]
    return result


def _aware(value: Any, field: str) -> str:
    try:
        return parse_datetime(_text(value, field), field_name=field).isoformat()
    except ValueError as exc:
        _error("INVALID_TIMESTAMP", str(exc), field)
    raise AssertionError("unreachable")


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error("INVALID_INTEGER_FIELD", f"{field} must be a positive integer", field)
    return value


def _enum(value: Any, allowed: set[str], field: str) -> str:
    result = _text(value, field)
    if result not in allowed:
        _error("INVALID_ENUM_VALUE", f"{field} is unsupported", field)
    return result


def _safe_ref(value: Any, field: str) -> str:
    """Keep public opaque references; hash path-like references before output."""

    ref = _output_text(value, field)
    normalized = ref.replace("\\", "/")
    absolute = bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith(("/", "//"))
    traversal = ".." in normalized.split("/")
    file_uri = normalized.casefold().startswith("file:")
    if absolute or traversal or file_uri:
        return "opaque-ref:sha256:" + sha256_bytes(ref.encode("utf-8"))
    return ref


def _validate_raw_text_safety(value: Any, field: str = "record") -> None:
    """Reject Unicode/control hazards everywhere without semanticizing raw text."""

    if isinstance(value, str):
        try:
            require_safe_text(value, field, allow_empty=True)
        except (TypeError, ValueError):
            _error("UNSAFE_SOURCE_TEXT", "source text contains unsafe Unicode or boundary whitespace", field)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_raw_text_safety(item, f"{field}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            try:
                require_safe_text(key, f"{field} key")
            except (TypeError, ValueError):
                _error("UNSAFE_SOURCE_TEXT", "source key contains unsafe Unicode or boundary whitespace", field)
            _validate_raw_text_safety(item, f"{field}.{key}")


def _source_anchor(value: Any) -> str:
    ref = _output_text(value, "source_record_ref")
    if "\\" in ref or ".." in ref or not re.fullmatch(r"[^/#]+(?:/[^/#]+)*#line=[1-9][0-9]*", ref):
        _error("INVALID_SOURCE_RECORD_REF", "source_record_ref must be a frozen relative line anchor", "source_record_ref")
    return ref


def _event_id(source_system: str, stable_source_identity: str) -> str:
    digest = sha256_bytes(f"{source_system}|{stable_source_identity}".encode("utf-8"))
    return "RE-" + digest[:32]


def _common_research_event(
    *,
    event_id: str,
    event_kind: str,
    occurred_at: str,
    source_record_ref: str,
    authority_refs: list[str],
) -> dict[str, Any]:
    common_refs = [source_record_ref] + [value for value in authority_refs if value != source_record_ref]
    payload = {
        "object_id": event_id,
        "schema_version": "2.0",
        "revision": 1,
        "producer": "RESEARCH_REPORTS",
        "consumers": ["RESEARCH_REPORTS"],
        "state": "recorded",
        "parent_refs": [],
        # The source line anchor must be independently frozen by the DataContracts
        # reference-authority manifest.  Projection hashes are not authorities.
        "provenance_refs": common_refs,
        "supersedes": None,
        "immutable_fields": IMMUTABLE_FIELDS,
        "failure_semantics": FAILURE_SEMANTICS,
        "event_kind": event_kind,
        "occurred_at": occurred_at,
        "source_refs": common_refs,
    }
    payload["content_hash"] = typed_payload_hash(payload)
    return {"object_type": "ResearchEvent", "payload": payload}


def _finalize(
    *,
    source_system: str,
    source_record_ref: str,
    source_content_hash: str,
    stable_source_identity: str,
    event_kind: str,
    occurred_at: str,
    observed_at: str,
    subject_refs: list[str],
    evidence_refs: list[str],
    status_axis: str,
    from_state: str | None,
    to_state: str,
    daily_category: str,
    weekly_categories: list[str],
    monthly_categories: list[str],
    explicit_tags: list[str],
    display_text: str,
    source_details: dict[str, Any],
    common_authority_refs: list[str] | None = None,
    verification_status: str = "not_applicable",
) -> dict[str, Any]:
    source_record_ref = _source_anchor(source_record_ref)
    source_content_hash = _text(source_content_hash, "source_content_hash")
    if not re.fullmatch(r"[A-F0-9]{64}", source_content_hash):
        _error("INVALID_SOURCE_CONTENT_HASH", "source content hash must be uppercase SHA-256", "source_content_hash")
    stable_source_identity = _output_text(stable_source_identity, "stable_source_identity")
    if parse_datetime(observed_at, field_name="observed_at") < parse_datetime(occurred_at, field_name="occurred_at"):
        _error("INVALID_OBSERVATION_ORDER", "observed_at must not precede occurred_at", "occurred_at", "observed_at")
    event_id = _event_id(source_system, stable_source_identity)
    projection = {
        "object_type": "ResearchEvent",
        "domain_contract_version": "1.1",
        "event_id": event_id,
        "event_kind": _output_text(event_kind, "event_kind"),
        "occurred_at": occurred_at,
        "observed_at": observed_at,
        "source_system": source_system,
        "source_record_ref": source_record_ref,
        "source_content_hash": source_content_hash,
        "subject_refs": sorted({_output_text(value, "subject_ref") for value in subject_refs}),
        "evidence_refs": sorted({_safe_ref(value, "evidence_ref") for value in evidence_refs} or {source_record_ref}),
        "status_axis": _output_text(status_axis, "status_axis"),
        "from_state": None if from_state is None else _output_text(from_state, "from_state"),
        "to_state": _output_text(to_state, "to_state"),
        "daily_category": daily_category,
        "weekly_categories": list(dict.fromkeys(weekly_categories)),
        "monthly_categories": list(dict.fromkeys(monthly_categories)),
        "explicit_tags": sorted({_output_text(value, "explicit_tag") for value in explicit_tags}),
        "display_text": _output_text(display_text, "display_text"),
        "verification_status": verification_status,
        "source_details": source_details,
    }
    try:
        validate_source_value(projection, "ResearchEvent projection")
    except (TypeError, ValueError) as exc:
        code = "FORBIDDEN_OUTPUT_SEMANTICS" if "forbidden semantic phrase" in str(exc) else "UNSAFE_OUTPUT_PROJECTION"
        _error(code, "normalized output projection is unsafe", "source_details")
    projection_hash = sha256_bytes(canonical_json_bytes(projection))
    derived_common_refs = list(common_authority_refs or [])
    for evidence_ref in projection["evidence_refs"]:
        if (
            evidence_ref != source_record_ref
            and re.fullmatch(r"[^/#]+(?:/[^/#]+)*#line=[1-9][0-9]*", evidence_ref)
            and evidence_ref not in derived_common_refs
        ):
            derived_common_refs.append(evidence_ref)
    derived_common_refs.sort()
    return {
        **projection,
        "projection_hash": {
            "algorithm": "sha256",
            "hash_kind": "rfc8785_jcs_sha256",
            "scope": "DOMAIN_PROJECTION",
            "value": projection_hash,
        },
        "common_object": _common_research_event(
            event_id=event_id,
            event_kind=event_kind,
            occurred_at=occurred_at,
            source_record_ref=source_record_ref,
            authority_refs=derived_common_refs,
        ),
    }


def _resolve_evidence_review(
    record: dict[str, Any],
    *,
    data_id: str,
    to_state: str,
    evidence_review_authority: dict[str, dict[str, Any]] | None,
) -> tuple[str, str | None, list[str]]:
    if to_state != "active":
        return "not_applicable", None, []
    if record["trigger"] != EVIDENCE_REVIEW_TRIGGER:
        return "unknown", None, []
    reason = record.get("reason")
    reason = _text(reason, "reason")
    matches = EVIDENCE_REVIEW_REPORT_TOKEN.findall(reason)
    if len(matches) != 1:
        return "unknown", None, []
    report_id = _output_text(matches[0], "evidence_review_report_id")
    authority = (evidence_review_authority or {}).get(report_id)
    if not isinstance(authority, dict):
        return "unknown", report_id, []
    checked = authority.get("checked_count")
    exact = (
        authority.get("report_id") == report_id
        and authority.get("paper_id") == data_id
        and authority.get("verdict") == "PASS"
        and isinstance(checked, int)
        and not isinstance(checked, bool)
        and checked > 0
        and isinstance(authority.get("report_sha256"), str)
        and re.fullmatch(r"[A-F0-9]{64}", authority["report_sha256"])
        and isinstance(authority.get("authority_ref"), str)
    )
    if not exact:
        return "unknown", report_id, []
    refs = [authority["authority_ref"], f"sha256:file_bytes:{authority['report_sha256']}"]
    return "verified_by_authoritative_evidence_review_report", report_id, refs


def _adapt_knowledge_admission(
    record: dict[str, Any],
    ref: str,
    raw_hash: str,
    evidence_review_authority: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    required = {"ts", "module", "record_type", "event_category", "data_id", "from", "to", "trigger", "reason"}
    if set(record) != required:
        _error("SOURCE_FIELDS_MISMATCH", "KNOWLEDGE_ADMISSION record fields mismatch", *(sorted(required ^ set(record))))
    if record["module"] != "KNOWLEDGE_ADMISSION" or record["record_type"] != "reason" or record["event_category"] != "review_status":
        _error("UNSUPPORTED_KNOWLEDGE_ADMISSION_RECORD", "only formal KNOWLEDGE_ADMISSION review_status reason records are supported", "module", "record_type", "event_category")
    occurred_at = _aware(record["ts"], "ts")
    observed_at = occurred_at
    data_id = _output_text(record["data_id"], "data_id")
    from_state = _enum(record["from"], KNOWLEDGE_ADMISSION_STATES, "from")
    to_state = _enum(record["to"], KNOWLEDGE_ADMISSION_STATES, "to")
    if (from_state, to_state) not in KNOWLEDGE_ADMISSION_TRANSITIONS:
        _error("ILLEGAL_KNOWLEDGE_ADMISSION_TRANSITION", "KNOWLEDGE_ADMISSION transition is not legal", "from", "to")
    trigger = _output_text(record["trigger"], "trigger")
    if trigger not in KNOWLEDGE_ADMISSION_TRIGGERS:
        _error("UNSUPPORTED_KNOWLEDGE_ADMISSION_TRIGGER", "KNOWLEDGE_ADMISSION trigger is outside the frozen enum", "trigger")
    _text(record["reason"], "reason")
    # The formal natural key is the eight structural fields.  ``reason`` is
    # still bound by source_content_hash, so two reason variants become a
    # same-ID/different-content blocking conflict rather than two events.
    identity_fields = {key: record[key] for key in sorted(required - {"reason"})}
    stable = "KnowledgeAdmissionROW-" + sha256_bytes(canonical_json_bytes(identity_fields))
    if to_state == "quarantined":
        daily = "失败"
    else:
        daily = "修改"
    verified, report_id, authority_refs = _resolve_evidence_review(
        record, data_id=data_id, to_state=to_state, evidence_review_authority=evidence_review_authority
    )
    text = f"KNOWLEDGE_ADMISSION 状态变化 {data_id}: {from_state} -> {to_state}"
    if to_state == "active" and verified == "unknown":
        text += "；校核状态未知"
    weekly = ["研究进展"] if daily in {"新增", "修改"} else ["未验证项"]
    if daily == "失败":
        weekly = ["风险与待办"]
    monthly = ["知识库结构变化"]
    if daily in {"待审", "失败"}:
        monthly.append("风险与未评估维度")
    return _finalize(
        source_system="KNOWLEDGE_ADMISSION",
        source_record_ref=ref,
        source_content_hash=raw_hash,
        stable_source_identity=stable,
        event_kind="knowledge_admission.review_status_changed",
        occurred_at=occurred_at,
        observed_at=observed_at,
        subject_refs=[data_id],
        evidence_refs=[ref] + authority_refs,
        status_axis="review_status",
        from_state=from_state,
        to_state=to_state,
        daily_category=daily,
        weekly_categories=weekly,
        monthly_categories=monthly,
        explicit_tags=[trigger],
        display_text=text,
        source_details={
            "wire": "knowledge_admission_reason_review_status",
            "trigger": trigger,
            "evidence_review_report_id": report_id,
            "evidence_review_authority_bound": verified == "verified_by_authoritative_evidence_review_report",
        },
        common_authority_refs=authority_refs,
        verification_status=verified,
    )


def _validate_runtime_log_common(record: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    if record.get("schema_version") != "2.0":
        _error("UNSUPPORTED_RUNTIME_LOG_SCHEMA", "RUNTIME_LOG source must use schema_version 2.0", "schema_version")
    record_type = _enum(record.get("record_type"), {"ledger_record", "run_terminal_event"}, "record_type")
    record_id = _output_text(record.get("record_id"), "record_id")
    run_id = _output_text(record.get("run_id"), "run_id")
    evidence = _text_list(record.get("source_evidence_refs"), "source_evidence_refs", allow_empty=False, output_facing=True)
    return record_type, record_id, run_id, evidence


def _runtime_log_status(record: dict[str, Any]) -> tuple[str, str, str, list[str], str, dict[str, Any]]:
    record_type, record_id, run_id, _ = _validate_runtime_log_common(record)
    if record_type == "run_terminal_event":
        expected = RUN_TERMINAL_FIELDS
        # ledger_type is an explicit null in the current formal v2 stream.
        if set(record) != expected:
            _error("SOURCE_FIELDS_MISMATCH", "RUNTIME_LOG run terminal fields mismatch", *(sorted(set(record) ^ expected)))
        if record.get("ledger_type") is not None:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "run terminal ledger_type must be null", "ledger_type")
        lifecycle = _enum(record.get("lifecycle_status"), LIFECYCLE_STATUSES, "lifecycle_status")
        acceptance = _enum(record.get("acceptance_verdict"), ACCEPTANCE_VERDICTS, "acceptance_verdict")
        if lifecycle in {"aborted", "invalidated"} and acceptance != "NOT_ASSESSED":
            _error("INVALID_RUNTIME_LOG_COMBINATION", "aborted/invalidated requires NOT_ASSESSED", "lifecycle_status", "acceptance_verdict")
        supersedes = _optional_text(record.get("supersedes_record_id"), "supersedes_record_id")
        if supersedes == record_id:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "run terminal cannot supersede itself", "supersedes_record_id")
        status = f"{lifecycle}/{acceptance}"
        if lifecycle != "completed" or acceptance == "FAIL":
            daily = "失败"
        elif acceptance == "NOT_ASSESSED":
            daily = "待审"
        else:
            daily = "修改"
        return "run_terminal", status, daily, [run_id], "terminal_at", {
            "record_type": record_type,
            "lifecycle_status": lifecycle,
            "acceptance_verdict": acceptance,
        }

    ledger_type = _enum(record.get("ledger_type"), {"attempt", "paper_outcome", "publication"}, "ledger_type")
    record_kind = _enum(record.get("record_kind"), RECORD_KINDS, "record_kind")
    _aware(record.get("created_at"), "created_at")
    if ledger_type == "attempt":
        terminal = {"started_at", "finished_at", "result_category", "error_code"}
        amendment = {"amends_attempt_id", "amendment_fields"}
        expected = RUNTIME_LOG_COMMON | ATTEMPT_BASE | (terminal if record_kind == "terminal" else amendment)
        if set(record) != expected:
            _error("SOURCE_FIELDS_MISMATCH", "RUNTIME_LOG attempt fields mismatch", *(sorted(set(record) ^ expected)))
        attempt_id = _output_text(record.get("attempt_id"), "attempt_id")
        if record_kind == "terminal" and record_id != attempt_id:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "terminal attempt record_id must equal attempt_id", "record_id", "attempt_id")
        _output_text(record.get("logical_operation_id"), "logical_operation_id")
        _positive_int(record.get("attempt_no"), "attempt_no")
        _output_text(record.get("task_id"), "task_id")
        _output_text(record.get("stage"), "stage")
        paper_id = _optional_text(record.get("paper_id"), "paper_id")
        retry = _optional_text(record.get("retry_of_attempt_id"), "retry_of_attempt_id")
        repair = _optional_text(record.get("business_repair_of"), "business_repair_of")
        route = _optional_text(record.get("route_snapshot_id"), "route_snapshot_id")
        config_hash = _optional_text(record.get("execution_config_hash"), "execution_config_hash")
        if not route and not config_hash:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "attempt requires route or execution config", "route_snapshot_id", "execution_config_hash")
        if retry and repair:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "retry and business repair are orthogonal", "retry_of_attempt_id", "business_repair_of")
        if record_kind == "terminal":
            _aware(record.get("started_at"), "started_at")
            _aware(record.get("finished_at"), "finished_at")
            status = _enum(record.get("result_category"), ATTEMPT_RESULTS, "result_category")
            _optional_text(record.get("error_code"), "error_code")
            occurred_field = "finished_at"
        else:
            amends = _output_text(record.get("amends_attempt_id"), "amends_attempt_id")
            if amends != attempt_id:
                _error("INVALID_RUNTIME_LOG_COMBINATION", "amendment must identify amended attempt", "amends_attempt_id")
            fields = record.get("amendment_fields")
            if not isinstance(fields, dict) or not fields or set(fields) - {"accounting", "provider_identity", "token_usage", "cost"}:
                _error("INVALID_RUNTIME_LOG_COMBINATION", "invalid attempt amendment fields", "amendment_fields")
            status = "amended"
            occurred_field = "created_at"
        if status == "success" or status == "amended":
            daily = "修改"
        elif status == "not_assessed":
            daily = "待审"
        else:
            daily = "失败"
        subjects = [attempt_id, run_id] + ([paper_id] if paper_id else [])
        return "attempt", status, daily, subjects, occurred_field, {
            "record_type": record_type,
            "ledger_type": ledger_type,
            "record_kind": record_kind,
        }

    if ledger_type == "paper_outcome":
        expected = RUNTIME_LOG_COMMON | PAPER_FIELDS
        if set(record) != expected:
            _error("SOURCE_FIELDS_MISMATCH", "RUNTIME_LOG paper outcome fields mismatch", *(sorted(set(record) ^ expected)))
        outcome_id = _output_text(record.get("outcome_id"), "outcome_id")
        paper_id = _output_text(record.get("paper_id"), "paper_id")
        if record_id != outcome_id:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "paper outcome record_id must equal outcome_id", "record_id", "outcome_id")
        status = _enum(record.get("outcome_category"), PAPER_OUTCOMES, "outcome_category")
        _output_text(record.get("outcome_code"), "outcome_code")
        _text_list(record.get("reason_codes"), "reason_codes", allow_empty=False, output_facing=True)
        _text_list(record.get("final_artifact_refs"), "final_artifact_refs", output_facing=True)
        supersedes = _optional_text(record.get("supersedes_record_id"), "supersedes_record_id")
        _optional_text(record.get("reprocesses_run_id"), "reprocesses_run_id")
        if record_kind == "amendment" and not supersedes:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "paper amendment requires supersedes", "supersedes_record_id")
        daily = "失败" if status in {"failure", "partial", "not_run"} else "修改"
        return "paper_outcome", status, daily, [outcome_id, paper_id], "created_at", {
            "record_type": record_type,
            "ledger_type": ledger_type,
            "record_kind": record_kind,
        }

    expected = RUNTIME_LOG_COMMON | PUBLICATION_FIELDS
    allowed_variants = [expected, expected - {"absence_reason"}]
    if set(record) not in allowed_variants:
        _error("SOURCE_FIELDS_MISMATCH", "RUNTIME_LOG publication fields mismatch", *(sorted(set(record) ^ expected)))
    publication_id = _output_text(record.get("publication_event_id"), "publication_event_id")
    if record_id != publication_id:
        _error("INVALID_RUNTIME_LOG_COMBINATION", "publication record_id must equal event ID", "record_id", "publication_event_id")
    status = _enum(record.get("publication_status"), PUBLICATION_STATUSES, "publication_status")
    manifest_ref = _optional_text(record.get("manifest_ref"), "manifest_ref")
    subjects = _text_list(record.get("subject_artifact_refs"), "subject_artifact_refs", output_facing=True)
    receipt_ref = _optional_text(record.get("receipt_ref"), "receipt_ref")
    supersedes = _optional_text(record.get("supersedes_record_id"), "supersedes_record_id")
    absence = _optional_text(record.get("absence_reason"), "absence_reason")
    if record_kind == "amendment" and not supersedes:
        _error("INVALID_RUNTIME_LOG_COMBINATION", "publication amendment requires supersedes", "supersedes_record_id")
    if status == "not_requested":
        if manifest_ref is not None or receipt_ref is not None or absence != "NO_PUBLICATION_REQUEST":
            _error("INVALID_RUNTIME_LOG_COMBINATION", "invalid not_requested evidence combination", "publication_status")
    elif status == "not_published":
        if not manifest_ref or receipt_ref is not None or absence is not None:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "invalid not_published evidence combination", "publication_status")
    elif status == "published":
        if not manifest_ref or not receipt_ref or not subjects or absence is not None:
            _error("INVALID_RUNTIME_LOG_COMBINATION", "published requires manifest, receipt and subjects", "publication_status")
    elif not manifest_ref or absence is not None:
        _error("INVALID_RUNTIME_LOG_COMBINATION", "publication_failed requires manifest only", "publication_status")
    daily = "失败" if status == "publication_failed" else "待审" if status in {"not_requested", "not_published"} else "修改"
    return "publication", status, daily, [publication_id, run_id] + subjects, "created_at", {
        "record_type": record_type,
        "ledger_type": ledger_type,
        "record_kind": record_kind,
    }


def _adapt_runtime_log(record: dict[str, Any], ref: str, raw_hash: str) -> dict[str, Any]:
    official_validate_record = _official_callable("run_ledger.schema", "validate_record")
    try:
        official_validate_record(record)
    except Exception as exc:
        _error("INVALID_RUNTIME_LOG_OFFICIAL_WIRE", type(exc).__name__, "record_type")
    axis, status, daily, subjects, occurred_field, details = _runtime_log_status(record)
    occurred_at = _aware(record.get(occurred_field), occurred_field)
    observed_at = _aware(record.get("created_at", record.get("terminal_at")), "observed_at")
    record_id = _output_text(record["record_id"], "record_id")
    evidence = [ref] + [_safe_ref(value, "source_evidence_ref") for value in record["source_evidence_refs"]]
    tags: list[str] = []
    weekly = ["研究进展"] if daily == "修改" else ["未验证项"] if daily == "待审" else ["风险与待办"]
    monthly = ["知识库结构变化"] if daily == "修改" else ["风险与未评估维度"]
    return _finalize(
        source_system="RUNTIME_LOG",
        source_record_ref=ref,
        source_content_hash=raw_hash,
        stable_source_identity=record_id,
        event_kind=f"runtime_log.{axis}",
        occurred_at=occurred_at,
        observed_at=observed_at,
        subject_refs=subjects,
        evidence_refs=evidence,
        status_axis=axis,
        from_state=None,
        to_state=status,
        daily_category=daily,
        weekly_categories=weekly,
        monthly_categories=monthly,
        explicit_tags=tags,
        display_text=f"RUNTIME_LOG {axis} {record_id}: {status}",
        source_details=details,
    )


def _adapt_artifact_registry(record: dict[str, Any], ref: str, raw_hash: str) -> dict[str, Any]:
    official_validate_event = _official_callable("artifact_registry.schema_v2", "validate_event")
    try:
        official_validate_event(record)
    except Exception as exc:
        _error("INVALID_ARTIFACT_REGISTRY_OFFICIAL_WIRE", type(exc).__name__, "payload")
    facts = extract_artifact_registry_event(record)
    artifact_id = facts["artifact_id"]
    risk = bool(facts["unresolved"] or facts["conflicts"])
    weekly = ["研究进展"] + (["风险与待办"] if risk else [])
    monthly = ["知识库结构变化"] + (["风险与未评估维度"] if risk else [])
    evidence = [ref] + [_safe_ref(value, "artifact_registry_evidence_ref") for value in facts["evidence_refs"]]
    if facts["event_kind"] == "artifact_registry.state_transition_observed":
        axis = "artifact_review_status"
    elif facts["event_kind"] == "artifact_registry.artifact_location_updated":
        axis = "artifact_location"
    elif facts["event_kind"] == "artifact_registry.lineage_resolved":
        axis = "artifact_lineage"
    else:
        axis = "artifact_registry"
    details = {
        "event_schema_version": facts["event_schema_version"],
        "registry_event_id": facts["registry_event_id"],
        "registry_event_type": facts["registry_event_type"],
        "artifact_type": facts["artifact_type"],
        "artifact_content_hash": facts["artifact_content_hash"],
        "parent_artifacts": facts["parents"],
        "supersedes_artifact_ids": sorted(
            item["parent_artifact_id"]
            for item in facts["parents"]
            if item["relation"] == "supersedes"
        ),
        "unresolved_parent_requirements": facts["unresolved"],
        "conflicting_candidates": facts["conflicts"],
        "ledger_record_refs": facts["ledger_refs"],
        "locator": facts["locator"],
        "prior_head_event_id": facts["prior_head_event_id"],
    }
    return _finalize(
        source_system="ARTIFACT_REGISTRY",
        source_record_ref=ref,
        source_content_hash=raw_hash,
        stable_source_identity=facts["stable_source_identity"],
        event_kind=facts["event_kind"],
        occurred_at=facts["occurred_at"],
        observed_at=facts["observed_at"],
        subject_refs=[artifact_id],
        evidence_refs=evidence,
        status_axis=axis,
        from_state=facts["from_state"],
        to_state=facts["to_state"],
        daily_category=facts["daily_category"],
        weekly_categories=weekly,
        monthly_categories=monthly,
        explicit_tags=facts["explicit_tags"],
        display_text=f"ARTIFACT_REGISTRY Registry event {facts['registry_event_id']} for Artifact {artifact_id}",
        source_details=details,
    )


def adapt_record(
    record: dict[str, Any],
    *,
    source_system: str,
    source_record_ref: str,
    source_content_hash: str,
    evidence_review_authority: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        _error("SOURCE_RECORD_NOT_OBJECT", "source record must be a JSON object")
    _validate_raw_text_safety(record)
    if source_system == "KNOWLEDGE_ADMISSION":
        return _adapt_knowledge_admission(record, source_record_ref, source_content_hash, evidence_review_authority)
    if source_system == "RUNTIME_LOG":
        return _adapt_runtime_log(record, source_record_ref, source_content_hash)
    if source_system == "ARTIFACT_REGISTRY":
        return _adapt_artifact_registry(record, source_record_ref, source_content_hash)
    _error("UNSUPPORTED_SOURCE_SYSTEM", "source_system must be KNOWLEDGE_ADMISSION, RUNTIME_LOG or ARTIFACT_REGISTRY", "source_system")
    raise AssertionError("unreachable")
