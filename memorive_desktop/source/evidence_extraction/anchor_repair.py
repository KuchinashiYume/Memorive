"""Controlled recovery for invented EVIDENCE_EXTRACTION core-field chunk identifiers.

The repair surface is deliberately narrow: one consolidated DeepSeek call may
replace or remove only the invalid anchor entries identified by the local
validator.  Field values and every unrelated anchor remain authoritative.  If
the repair response is unavailable or violates the contract, invalid anchor
entries are removed locally only when each affected field still retains at
least one real same-paper anchor.
"""
from __future__ import annotations
from model_gateway.prompt_cache import source_first, split_repair_sources, canonical

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .card import CONTENT_FIELDS
from .normalize import quote_in_text


_RANGE_ID_RE = re.compile(
    r"^(?P<paper>.+)#c(?P<start>\d{4})(?:_|-|–)(?:c)?(?P<end>\d{4})$"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff][A-Za-z0-9_.\-/\u3400-\u9fff]{2,}")
_REPAIR_KEYS = {
    "field",
    "anchor_index",
    "bad_chunk_id",
    "action",
    "replacement_anchors",
    "reason",
}
_ANCHOR_KEYS = {"chunk_id", "quote"}


class AnchorRepairContractError(ValueError):
    """The targeted repair response did not match its frozen patch contract."""


@dataclass(frozen=True)
class CoreAnchorIssue:
    field: str
    anchor_index: int
    bad_chunk_id: str
    quote: str | None
    candidate_ids: tuple[str, ...]

    @property
    def path(self) -> str:
        return f"{self.field}.anchors[{self.anchor_index}]"

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "anchor_index": self.anchor_index,
            "path": self.path,
            "bad_chunk_id": self.bad_chunk_id,
            "quote": self.quote,
            "candidate_ids": list(self.candidate_ids),
        }


@dataclass
class AnchorRepairOutcome:
    obj: dict
    status: str
    authoritative_sha256_before: str
    authoritative_sha256_after: str
    issues: list[CoreAnchorIssue]
    repairs: list[dict]
    dropped_anchor_entries: list[dict]
    prompt_version: str
    attempts: int
    error: str | None = None

    def trace(self, paper_id: str) -> dict:
        return {
            "schema_version": 1,
            "paper_id": paper_id,
            "status": self.status,
            "authoritative_sha256_before": self.authoritative_sha256_before,
            "authoritative_sha256_after": self.authoritative_sha256_after,
            "issues": [issue.as_dict() for issue in self.issues],
            "repairs": copy.deepcopy(self.repairs),
            "dropped_anchor_entries": copy.deepcopy(self.dropped_anchor_entries),
            "prompt_version": self.prompt_version,
            "attempts": self.attempts,
            "error": self.error,
        }


def canonical_sha256(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _field_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


def _range_candidates(
    bad_chunk_id: str,
    chunk_texts: dict[str, str],
    candidate_chunk_ids: set[str],
) -> list[str]:
    match = _RANGE_ID_RE.fullmatch(bad_chunk_id)
    if not match:
        return []
    start = int(match.group("start"))
    end = int(match.group("end"))
    if end < start or end - start > 20:
        return []
    paper = match.group("paper")
    return [
        candidate
        for number in range(start, end + 1)
        if (candidate := f"{paper}#c{number:04d}") in chunk_texts
        and candidate in candidate_chunk_ids
    ]


def _rank_candidates(
    node: dict,
    bad_chunk_id: str,
    quote: str | None,
    chunk_texts: dict[str, str],
    candidate_chunk_ids: set[str],
    *,
    limit: int = 8,
) -> list[str]:
    explicit = _range_candidates(bad_chunk_id, chunk_texts, candidate_chunk_ids)
    probes = [_field_text(node.get("value")), str(quote or "")]
    tokens = {
        token.casefold()
        for probe in probes
        for token in _TOKEN_RE.findall(probe)
        if len(token) >= 4
    }
    ranked = []
    for chunk_id, text in chunk_texts.items():
        if chunk_id not in candidate_chunk_ids:
            continue
        folded = text.casefold()
        score = sum(1 + min(len(token), 16) / 16 for token in tokens if token in folded)
        if quote and quote_in_text(quote, text):
            score += 100
        if score:
            ranked.append((-score, chunk_id))
    ranked.sort()
    current = []
    for anchor in node.get("anchors") or []:
        chunk_id = anchor.get("chunk_id") if isinstance(anchor, dict) else None
        if chunk_id in chunk_texts and chunk_id in candidate_chunk_ids:
            current.append(chunk_id)
    ordered = []
    for chunk_id in explicit + current + [chunk_id for _score, chunk_id in ranked]:
        if chunk_id not in ordered:
            ordered.append(chunk_id)
        if len(ordered) >= limit:
            break
    return ordered


def collect_core_anchor_issues(
    obj: dict,
    chunk_texts: dict[str, str],
    *,
    candidate_chunk_ids: set[str] | None = None,
) -> list[CoreAnchorIssue]:
    issues = []
    eligible = set(chunk_texts) if candidate_chunk_ids is None else set(candidate_chunk_ids)
    if not isinstance(obj, dict):
        return issues
    for field in CONTENT_FIELDS:
        node = obj.get(field)
        if not isinstance(node, dict) or not isinstance(node.get("anchors"), list):
            continue
        for anchor_index, anchor in enumerate(node["anchors"]):
            if not isinstance(anchor, dict):
                continue
            chunk_id = anchor.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id.strip() or chunk_id in chunk_texts:
                continue
            quote = anchor.get("quote")
            quote = quote if isinstance(quote, str) and quote.strip() else None
            issues.append(
                CoreAnchorIssue(
                    field=field,
                    anchor_index=anchor_index,
                    bad_chunk_id=chunk_id,
                    quote=quote,
                    candidate_ids=tuple(
                        _rank_candidates(node, chunk_id, quote, chunk_texts, eligible)
                    ),
                )
            )
    return issues


def _build_bundle(
    obj: dict,
    issues: list[CoreAnchorIssue],
    paper_id: str,
    chunk_texts: dict[str, str],
) -> dict:
    targets = []
    for issue in issues:
        node = obj[issue.field]
        targets.append(
            {
                "field": issue.field,
                "anchor_index": issue.anchor_index,
                "bad_chunk_id": issue.bad_chunk_id,
                "current_quote": issue.quote,
                "field_value": copy.deepcopy(node.get("value")),
                "field_sha256": canonical_sha256(node),
                "allowed_chunk_ids": list(issue.candidate_ids),
                "candidate_sources": [
                    {
                        "chunk_id": chunk_id,
                        "text": chunk_texts[chunk_id],
                    }
                    for chunk_id in issue.candidate_ids
                ],
            }
        )
    return {
        "paper_id": paper_id,
        "authoritative_sha256": canonical_sha256(obj),
        "targets": targets,
    }


def _first_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise AnchorRepairContractError("repair response contains no JSON object")
    depth = 0
    in_string = False
    escaped = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise AnchorRepairContractError("repair response JSON object is incomplete")
    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise AnchorRepairContractError(f"repair response is invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AnchorRepairContractError("repair response must be a JSON object")
    return parsed


def render_repair_prompt(prompt_body: str, marker: str, bundle: dict) -> str:
    """Compact wire projection; original bundle still validates every exact target."""
    sources, dynamic = split_repair_sources(bundle)
    fields = {}
    for target in dynamic['targets']:
        value = target.pop('field_value')
        field = target['field']
        if field in fields and fields[field] != value:
            raise AnchorRepairContractError('conflicting frozen field values')
        fields[field] = value
    dynamic['frozen_field_values'] = fields
    task = prompt_body.replace(marker, canonical(dynamic))
    task += ('\nWire layout: frozen_field_values maps each target field to its unchanged value. '
             'candidate_source_ids / allowed_chunk_ids refer to the shared SOURCE_CONTEXT. '
             'Each target retains exactly its own allowed_chunk_ids. Do not widen them.')
    return source_first('__SOURCE__\n' + task, '__SOURCE__', sources)


def _validate_repairs(
    response_obj: dict,
    bundle: dict,
    chunk_texts: dict[str, str],
) -> list[dict]:
    if set(response_obj) != {"paper_id", "authoritative_sha256", "repairs"}:
        raise AnchorRepairContractError("repair response has unexpected top-level keys")
    if response_obj["paper_id"] != bundle["paper_id"]:
        raise AnchorRepairContractError("repair response paper_id mismatch")
    if response_obj["authoritative_sha256"] != bundle["authoritative_sha256"]:
        raise AnchorRepairContractError("repair response authoritative hash mismatch")
    rows = response_obj["repairs"]
    if not isinstance(rows, list):
        raise AnchorRepairContractError("repairs must be a list")
    expected = {
        (target["field"], target["anchor_index"], target["bad_chunk_id"]): target
        for target in bundle["targets"]
    }
    actual = set()
    validated = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _REPAIR_KEYS:
            raise AnchorRepairContractError(f"repairs[{row_index}] has an invalid shape")
        field = row["field"]
        anchor_index_value = row["anchor_index"]
        bad_chunk_id = row["bad_chunk_id"]
        if not isinstance(field, str) or not field:
            raise AnchorRepairContractError(f"repairs[{row_index}].field must be a non-empty string")
        if (
            not isinstance(anchor_index_value, int)
            or isinstance(anchor_index_value, bool)
            or anchor_index_value < 0
        ):
            raise AnchorRepairContractError(f"repairs[{row_index}].anchor_index must be a non-negative integer")
        if not isinstance(bad_chunk_id, str) or not bad_chunk_id:
            raise AnchorRepairContractError(f"repairs[{row_index}].bad_chunk_id must be a non-empty string")
        key = (field, anchor_index_value, bad_chunk_id)
        if key not in expected or key in actual:
            raise AnchorRepairContractError(f"repairs[{row_index}] target is unexpected or duplicated")
        actual.add(key)
        action = row["action"]
        if action not in {"replace", "drop_anchor"}:
            raise AnchorRepairContractError(f"repairs[{row_index}].action is invalid")
        reason = row["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise AnchorRepairContractError(f"repairs[{row_index}].reason must be non-empty")
        anchors = row["replacement_anchors"]
        if not isinstance(anchors, list):
            raise AnchorRepairContractError(f"repairs[{row_index}].replacement_anchors must be a list")
        if action == "replace" and not anchors:
            raise AnchorRepairContractError("replace requires at least one replacement anchor")
        if action == "drop_anchor" and anchors:
            raise AnchorRepairContractError("drop_anchor requires an empty replacement list")
        allowed = set(expected[key]["allowed_chunk_ids"])
        cleaned = []
        seen = set()
        for anchor_index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict) or set(anchor) != _ANCHOR_KEYS:
                raise AnchorRepairContractError(
                    f"repairs[{row_index}].replacement_anchors[{anchor_index}] has an invalid shape"
                )
            chunk_id = anchor["chunk_id"]
            quote = anchor["quote"]
            if (
                not isinstance(chunk_id, str)
                or chunk_id not in allowed
                or chunk_id not in chunk_texts
            ):
                raise AnchorRepairContractError("replacement chunk_id is outside the frozen whitelist")
            if quote is not None:
                if not isinstance(quote, str) or not quote.strip() or not quote_in_text(quote, chunk_texts[chunk_id]):
                    raise AnchorRepairContractError("replacement quote is not a literal same-chunk substring")
                quote = quote.strip()
            anchor_key = (chunk_id, quote)
            if anchor_key in seen:
                continue
            seen.add(anchor_key)
            cleaned.append({"chunk_id": chunk_id, "quote": quote})
        validated.append(
            {
                "field": key[0],
                "anchor_index": key[1],
                "bad_chunk_id": key[2],
                "action": action,
                "replacement_anchors": cleaned,
                "reason": reason.strip(),
            }
        )
    if actual != set(expected):
        raise AnchorRepairContractError("repair response did not cover every frozen target exactly once")
    return validated


def _apply_repairs(obj: dict, repairs: list[dict]) -> tuple[dict, list[dict]]:
    working = copy.deepcopy(obj)
    dropped = []
    for repair in sorted(
        repairs,
        key=lambda row: (row["field"], -row["anchor_index"]),
    ):
        anchors = working[repair["field"]]["anchors"]
        index = repair["anchor_index"]
        if index >= len(anchors) or anchors[index].get("chunk_id") != repair["bad_chunk_id"]:
            raise AnchorRepairContractError("authoritative anchor changed before patch application")
        if repair["action"] == "drop_anchor":
            dropped.append(
                {
                    "field": repair["field"],
                    "anchor_index": index,
                    "bad_chunk_id": repair["bad_chunk_id"],
                    "reason": repair["reason"],
                    "disposition": "deepseek_declared_unrecoverable",
                }
            )
            replacement = []
        else:
            replacement = copy.deepcopy(repair["replacement_anchors"])
        anchors[index:index + 1] = replacement
    return working, dropped


def _drop_invalid_locally(
    obj: dict,
    issues: list[CoreAnchorIssue],
    reason: str,
) -> tuple[dict, list[dict]]:
    working = copy.deepcopy(obj)
    dropped = []
    for issue in sorted(issues, key=lambda item: (item.field, -item.anchor_index)):
        anchors = working[issue.field]["anchors"]
        if issue.anchor_index >= len(anchors) or anchors[issue.anchor_index].get("chunk_id") != issue.bad_chunk_id:
            raise AnchorRepairContractError("invalid anchor changed before local removal")
        anchors.pop(issue.anchor_index)
        dropped.append(
            {
                "field": issue.field,
                "anchor_index": issue.anchor_index,
                "bad_chunk_id": issue.bad_chunk_id,
                "reason": reason,
                "disposition": "local_invalid_anchor_removal",
            }
        )
    return working, dropped


def _empty_anchor_fields(obj: dict) -> list[str]:
    return [
        field
        for field in CONTENT_FIELDS
        if not isinstance((obj.get(field) or {}).get("anchors"), list)
        or not obj[field]["anchors"]
    ]


def recover_core_anchors_once(
    obj: dict,
    *,
    paper_id: str,
    chunk_texts: dict[str, str],
    prompt_body: str,
    prompt_version: str,
    call_repair: Callable[[str], object],
    recoverable_errors: tuple[type[BaseException], ...] = (),
    candidate_chunk_ids: set[str] | None = None,
) -> AnchorRepairOutcome:
    """Repair all invented core anchors in one call, then apply local fallback."""
    before = canonical_sha256(obj)
    issues = collect_core_anchor_issues(
        obj,
        chunk_texts,
        candidate_chunk_ids=candidate_chunk_ids,
    )
    if not issues:
        return AnchorRepairOutcome(
            obj=copy.deepcopy(obj),
            status="not_needed",
            authoritative_sha256_before=before,
            authoritative_sha256_after=before,
            issues=[],
            repairs=[],
            dropped_anchor_entries=[],
            prompt_version=prompt_version,
            attempts=0,
        )
    bundle = _build_bundle(obj, issues, paper_id, chunk_texts)
    marker = "__ANCHOR_REPAIR_BUNDLE_JSON__"
    if prompt_body.count(marker) != 1:
        raise RuntimeError("anchor repair prompt must contain exactly one bundle marker")
    prompt = render_repair_prompt(prompt_body, marker, bundle)
    repairs = []
    dropped = []
    repair_error = None
    try:
        response = call_repair(prompt)
        raw = response.get("text") if isinstance(response, dict) else response
        repairs = _validate_repairs(_first_json_object(str(raw or "")), bundle, chunk_texts)
        working, dropped = _apply_repairs(obj, repairs)
        remaining = collect_core_anchor_issues(
            working,
            chunk_texts,
            candidate_chunk_ids=candidate_chunk_ids,
        )
        if remaining:
            raise AnchorRepairContractError("patched object still contains invented chunk ids")
    except AnchorRepairContractError as exc:
        repair_error = f"{type(exc).__name__}: {exc}"
        working, dropped = _drop_invalid_locally(obj, issues, repair_error)
        repairs = []
    except recoverable_errors as exc:
        repair_error = f"{type(exc).__name__}: {exc}"
        working, dropped = _drop_invalid_locally(obj, issues, repair_error)
        repairs = []

    empty_fields = _empty_anchor_fields(working)
    if empty_fields:
        status = "unrecoverable"
        repair_error = (
            f"{repair_error + '; ' if repair_error else ''}"
            f"safe removal would leave fields without anchors: {empty_fields}"
        )
    elif dropped:
        status = "repaired_with_anchor_omissions" if repairs else "fallback_dropped_invalid_anchors"
    else:
        status = "repaired"
    return AnchorRepairOutcome(
        obj=working,
        status=status,
        authoritative_sha256_before=before,
        authoritative_sha256_after=canonical_sha256(working),
        issues=issues,
        repairs=repairs,
        dropped_anchor_entries=dropped,
        prompt_version=prompt_version,
        attempts=1,
        error=repair_error,
    )


def write_trace_exclusive(folder: Path, paper_id: str, outcome: AnchorRepairOutcome) -> Path:
    """Write an additive audit sidecar without overwriting an earlier attempt."""
    folder = Path(folder)
    stem = f"[EvidenceExtractionAnchorRepair][{paper_id}][{outcome.authoritative_sha256_before[:12]}]"
    payload = json.dumps(outcome.trace(paper_id), ensure_ascii=False, indent=2).encode("utf-8")
    for sequence in range(1, 1000):
        suffix = "" if sequence == 1 else f"[{sequence}]"
        path = folder / f"{stem}{suffix}.json"
        try:
            with path.open("xb") as stream:
                stream.write(payload)
            return path
        except FileExistsError:
            continue
    raise FileExistsError(f"unable to allocate unique EVIDENCE_EXTRACTION anchor repair trace in {folder}")
