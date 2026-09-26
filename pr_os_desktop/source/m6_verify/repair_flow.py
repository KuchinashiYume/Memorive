"""Single-round producer repair flow for final Card output.

Initial Card -> one whole-Card review -> one consolidated distillation-model
repair -> one review of changed items only -> omit unresolved
items with tombstones -> atomically finalize Card v6 -> M8 active.
"""
from __future__ import annotations
from m9_gateway.prompt_cache import split_repair_sources, source_first, canonical

import copy
import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from m2_distill.card import (
    CONTENT_FIELDS,
    CRED_FLAGGED,
    CRED_UNKNOWN,
    CRED_VERIFIED,
    DANGER_UNKNOWN_FIELD,
    DANGER_UNKNOWN_KD,
    LIST_FIELDS,
    finalize_card_after_repair,
    detect_card_lang,
)
from m8_state_machine import ACTIVE, DecisionError, Trigger, transition
from m9_gateway import call as gateway_call
from m9_gateway.errors import GatewayError
from m11_log import log_reason

from . import report as _report
from . import transcription as _tx
from .errors import BatchContractExhausted
from .mechanical import _read_frontmatter
from .problems import Problem


ENV_M6_REPAIR_DIR = "PROS_M6_REPAIR_DIR"
REPAIR_MAX_TOKENS = 8192
REPAIR_MAX_TOKENS_RETRY = 16384
REPAIR_TIMEOUT = 180
REPAIR_BATCH_MAX_TARGETS = 3
_VALID_RULES = frozenset(f"R{i}" for i in range(1, 10))
_ACTIONS = frozenset({"replace", "reanchor", "drop"})
_DROP_DISPOSITIONS = frozenset({"source_conflict", "missing_anchor", "unrecoverable"})
_KEY_DATA_LOCATION_RE = re.compile(r"^key_data\[(\d+)]$")
_ITEM_PREFIX_RE = re.compile(r"^item_index=(\d+)\s*[；;]\s*")
_TOKEN_RE = re.compile(r"(?u)\b[^\W_]{3,}\b")
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "m9_gateway"
    / "prompts"
    / "distill_repair"
    / "v2.md"
)


@dataclass(frozen=True)
class RepairAdmitResult:
    outcome: str
    verify_calls: int
    distill_repair_calls: int
    initial_report_id: str | None = None
    final_report_id: str | None = None
    completion_status: str | None = None
    omissions: list | None = None
    repair_trace_path: str | None = None
    failure_reason: str | None = None
    root_reason_code: str | None = None

    @property
    def sonnet_calls(self):
        """Deprecated Python alias; persisted receipts use verify_calls."""
        return self.verify_calls


def _failure_projection(error):
    current=error;seen=set()
    while id(current) not in seen:
        seen.add(id(current))
        following=current.__cause__ or current.__context__
        if following is None:break
        current=following
    message=(str(current) or type(current).__name__)[:500]
    winerror=getattr(current,'winerror',None)
    code=(f'WINERROR_{winerror}' if isinstance(winerror,int)
          else re.sub(r'[^A-Z0-9_:-]+','_',f'{type(current).__name__}:{message}'.upper()).strip('_')[:120])
    return f'{type(current).__name__}:{message}',(code or type(current).__name__.upper())


class RepairContractError(DecisionError):
    """DeepSeek repair JSON cannot be safely mapped to the reviewed items."""


def _repair_dir() -> Path:
    configured = os.environ.get(ENV_M6_REPAIR_DIR)
    if configured:
        return Path(configured)
    return _report.report_path("placeholder").parent / "repairs"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _first_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise RepairContractError("DeepSeek repair response contains no JSON object")
    depth = 0
    quoted = False
    escaped = False
    end = None
    for index, char in enumerate(text[start:], start=start):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise RepairContractError("DeepSeek repair JSON object is truncated")
    try:
        obj = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise RepairContractError(f"DeepSeek repair JSON is invalid: {exc}") from exc
    if not isinstance(obj, dict):
        raise RepairContractError("DeepSeek repair response must be a JSON object")
    return obj


def _problem_item_index(problem: Problem) -> int:
    if problem.quote:
        try:
            evidence = json.loads(problem.quote)
        except json.JSONDecodeError as exc:
            raise RepairContractError(f"M6 problem evidence is invalid JSON: {exc}") from exc
        value = evidence.get("item_index") if isinstance(evidence, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    match = _ITEM_PREFIX_RE.match(problem.detail or "")
    if match:
        return int(match.group(1))
    raise RepairContractError(f"M6 problem has no safe item_index: {problem.location}")


def _row_key(row: dict) -> tuple[str, int]:
    location = row.get("location")
    item_index = row.get("item_index")
    if not isinstance(location, str) or not location:
        raise RepairContractError("repair target location must be non-empty")
    if not isinstance(item_index, int) or isinstance(item_index, bool) or item_index < 0:
        raise RepairContractError("repair target item_index must be a non-negative integer")
    return location, item_index


def _target_value(fm: dict, location: str, item_index: int):
    if location.startswith("by_field."):
        field = location.split(".", 1)[1]
        if field not in CONTENT_FIELDS:
            raise RepairContractError(f"unknown core-field location: {location}")
        value = fm.get(field)
        if isinstance(value, list):
            if item_index >= len(value):
                raise RepairContractError(f"item_index outside {location}: {item_index}")
            return value[item_index]
        if item_index != 0:
            raise RepairContractError(f"scalar field cannot use item_index {item_index}: {location}")
        return value
    match = _KEY_DATA_LOCATION_RE.fullmatch(location)
    if match:
        outer_index = int(match.group(1))
        key_data = fm.get("key_data")
        if item_index != 0 or not isinstance(key_data, list) or outer_index >= len(key_data):
            raise RepairContractError(f"invalid key_data target: {location}/{item_index}")
        return key_data[outer_index]
    raise RepairContractError(f"unmapped repair target: {location}")


def _target_field(location: str) -> str:
    if location.startswith("by_field."):
        return location.split(".", 1)[1]
    if _KEY_DATA_LOCATION_RE.fullmatch(location):
        return "key_data"
    raise RepairContractError(f"unmapped repair target: {location}")


def _candidate_chunk_ids(
    item_text: str,
    problem_rows: list[dict],
    current_ids: list[str],
    chunk_texts: dict[str, str],
    *,
    limit: int | None = None,
) -> list[str]:
    probes = [item_text]
    probes.extend(str(row.get("card_quote") or "") for row in problem_rows)
    probes.extend(str(row.get("source_quote") or "") for row in problem_rows)
    tokens = {
        token.casefold()
        for probe in probes
        for token in _TOKEN_RE.findall(probe)
    }
    ranked = []
    for chunk_id, text in chunk_texts.items():
        folded = text.casefold()
        score = sum(1 + min(len(token), 12) / 12 for token in tokens if token in folded)
        if any(probe and probe.casefold() in folded for probe in probes):
            score += 20
        if chunk_id in current_ids:
            score += 100
        if score > 0:
            ranked.append((-score, chunk_id))
    ranked.sort()
    # All existing evidence belongs to the reviewed item. Never truncate its
    # anchors before asking a producer to replace the entire compound field.
    out = list(dict.fromkeys(cid for cid in current_ids if cid in chunk_texts))
    added = 0
    for chunk_id in [chunk_id for _score, chunk_id in ranked]:
        if limit is not None and added >= limit:
            break
        if chunk_id in chunk_texts and chunk_id not in out:
            out.append(chunk_id)
            added += 1
    return out


def _protected_clauses(value, problem_rows):
    if not isinstance(value, str):
        return []
    quotes = [str(row.get('card_quote') or '').strip() for row in problem_rows]
    quotes = [q for q in quotes if q and q in value]
    if not quotes:
        return []  # No guessed repair boundary; whole-item review still applies.
    clauses = re.split(r'(?<=[。！？!?；;])\s*|(?<=[.!?])\s+(?=[A-Z])', value)
    return [part for part in clauses if part.strip() and not any(q in part or part in q for q in quotes)]


def _build_repair_bundle(
    fm: dict,
    rows: list[dict],
    subjects_by_location: dict,
    chunk_texts: dict[str, str],
) -> dict:
    actual_subjects_by_location = {
        subject.location: subject for subject in _tx._collect_subjects(fm)
    }
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[_row_key(row)].append(row)
    targets = []
    for (location, item_index), problem_rows in sorted(grouped.items()):
        value = _target_value(fm, location, item_index)
        # Initial review may expand absence claims to every document chunk.
        # That temporary review scope is not a Card anchor and must not become
        # the repair prompt's current evidence set.
        subject = actual_subjects_by_location.get(location)
        current_ids = list(subject.chunk_ids) if subject is not None else []
        candidates = _candidate_chunk_ids(
            str(value),
            problem_rows,
            current_ids,
            chunk_texts,
            limit=16,
        )
        targets.append({
            "location": location,
            "item_index": item_index,
            "current_value": value,
            "protected_clauses": _protected_clauses(value, problem_rows),
            "current_source_ids": current_ids,
            "sonnet_problems": [
                {
                    key: row.get(key)
                    for key in (
                        "rule",
                        "card_quote",
                        "source_quote",
                        "evidence_relation",
                        "detail",
                    )
                }
                for row in problem_rows
            ],
            "candidate_sources": [
                {
                    "chunk_id": chunk_id,
                    "text": chunk_texts[chunk_id],
                }
                for chunk_id in candidates
            ],
        })
    paper_id = ((fm.get("source_anchor") or {}).get("paper_id")
                if isinstance(fm.get("source_anchor"), dict) else None)
    return {
        "paper_id": paper_id,
        "source_language_rule": (
            "Preserve the source language. Unicode names, citations, variables, "
            "diacritics and genuine multilingual source text are allowed."
        ),
        "targets": targets,
    }


def _partition_repair_bundle(
    bundle: dict,
    *,
    max_targets: int = REPAIR_BATCH_MAX_TARGETS,
) -> list[dict]:
    """Partition repairs by identical source scope, then by bounded target count.

    Identical source scopes stay adjacent so each batch has the same canonical
    SOURCE_CONTEXT prefix. This keeps provider prompt-cache reuse possible while
    bounding the response size for every repair request.
    """
    if not isinstance(max_targets, int) or isinstance(max_targets, bool) or max_targets <= 0:
        raise ValueError("M6_REPAIR_BATCH_MAX_TARGETS_INVALID")
    base = {key: copy.deepcopy(value) for key, value in bundle.items() if key != "targets"}
    targets = list(bundle.get("targets", []))
    signatures = []
    for target in targets:
        sources = target.get("candidate_sources")
        if not isinstance(sources, list):
            raise RepairContractError("repair target candidate_sources must be a list")
        signatures.append(tuple(sorted({
            row.get("chunk_id")
            for row in sources
            if isinstance(row, dict) and isinstance(row.get("chunk_id"), str)
        })))
    signature_counts = {
        signature: signatures.count(signature) for signature in set(signatures)
    }
    grouped: dict[tuple, list[dict]] = {}
    for target, signature in zip(targets, signatures):
        # Exact matching source scopes stay together even across fields. A
        # one-off scope joins siblings in the same Card field, using their
        # same-paper source union as one stable cache prefix.
        group_key = (
            ("SOURCE_SCOPE", signature)
            if signature_counts[signature] > 1
            else ("FIELD", _target_field(target["location"]))
        )
        grouped.setdefault(group_key, []).append(copy.deepcopy(target))
    batches = []
    for targets in grouped.values():
        for start in range(0, len(targets), max_targets):
            batch_targets = copy.deepcopy(targets[start:start + max_targets])
            source_by_id = {}
            for target in batch_targets:
                for row in target["candidate_sources"]:
                    chunk_id, text = row["chunk_id"], row["text"]
                    if chunk_id in source_by_id and source_by_id[chunk_id] != text:
                        raise RepairContractError("repair batch source text conflict")
                    source_by_id[chunk_id] = text
            shared_sources = [
                {"chunk_id": chunk_id, "text": source_by_id[chunk_id]}
                for chunk_id in sorted(source_by_id)
            ]
            for target in batch_targets:
                target["candidate_sources"] = copy.deepcopy(shared_sources)
            batches.append({**copy.deepcopy(base), "targets": batch_targets})
    return batches


def _build_repair_prompt(bundle: dict) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    if template.count("__REPAIR_BUNDLE_JSON__") != 1:
        raise RuntimeError("distill repair prompt must contain one bundle marker")
    sources, dynamic = split_repair_sources(bundle)
    task = template.replace("__REPAIR_BUNDLE_JSON__", canonical(dynamic))
    task += "\nEach target candidate_source_ids refers only to that target's allowed sources in SOURCE_CONTEXT."
    return source_first("__SOURCE__\n" + task, "__SOURCE__", sources)


def _default_distill_call(
    prompt: str,
    *,
    bypass_cache: bool = False,
    max_tokens: int = REPAIR_MAX_TOKENS,
) -> dict:
    return gateway_call(
        "distill",
        {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "timeout": REPAIR_TIMEOUT,
        },
        bypass_cache=bypass_cache,
    )


def _literal_repair_quote(quote: str, chunk_text: str, label: str, chunk_id: str) -> str:
    if quote in chunk_text:
        return quote
    recovered = _tx._recover_unique_pdf_whitespace_quote(quote, chunk_text)
    if recovered is not None:
        return recovered
    case_matches = list(re.finditer(re.escape(quote), chunk_text, flags=re.IGNORECASE))
    if len(case_matches) == 1:
        match = case_matches[0]
        return chunk_text[match.start():match.end()]
    # Feed the actual rejected evidence back to the existing technical retry.
    # The response remains invalid; never replace it with a guessed source span.
    raise RepairContractError(
        f'{label} is not a literal chunk substring; chunk_id={chunk_id}; '
        f'rejected_quote={json.dumps(quote[:360], ensure_ascii=False)}; '
        f'original_source_excerpt={json.dumps(chunk_text[:1200], ensure_ascii=False)}. '
        'Copy an exact original span; do not change words, numbers or symbols.')


def _validate_anchor(anchor: dict, chunk_texts: dict[str, str], index: int) -> dict:
    if not isinstance(anchor, dict):
        raise RepairContractError(f"anchors[{index}] must be an object")
    chunk_id = anchor.get("chunk_id")
    quote = anchor.get("quote")
    section_page = anchor.get("section_page")
    if not isinstance(chunk_id, str) or chunk_id not in chunk_texts:
        raise RepairContractError(f"anchors[{index}].chunk_id is not a real same-paper chunk")
    if not isinstance(quote, str) or not quote:
        raise RepairContractError(f"anchors[{index}].quote is not a literal chunk substring")
    quote = _literal_repair_quote(quote, chunk_texts[chunk_id], f'anchors[{index}].quote', chunk_id)
    if section_page is not None and not isinstance(section_page, str):
        raise RepairContractError(f"anchors[{index}].section_page must be string or null")
    return {"chunk_id": chunk_id, "quote": quote, "section_page": section_page}


def _validate_key_data_replacement(value: dict, chunk_texts: dict[str, str]) -> dict:
    required = ("value", "unit", "metric", "sample", "stat", "quote", "chunk_id")
    if not isinstance(value, dict):
        raise RepairContractError("key_data replacement must be an object")
    for key in required:
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise RepairContractError(f"key_data replacement.{key} must be non-empty")
    if value["chunk_id"] not in chunk_texts:
        raise RepairContractError("key_data replacement quote/chunk is not literal same-paper evidence")
    out = dict(value)
    out['quote'] = _literal_repair_quote(value['quote'], chunk_texts[value['chunk_id']],
        'key_data replacement.quote', value['chunk_id'])
    out["needs_review"] = True
    return out


def _validate_repairs(
    obj: dict,
    expected: set[tuple[str, int]],
    fm: dict,
    chunk_texts: dict[str, str],
    protection: dict | None = None,
) -> list[dict]:
    rows = obj.get("repairs")
    if not isinstance(rows, list):
        raise RepairContractError("DeepSeek repair object must contain a repairs list")
    actual = []
    seen = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise RepairContractError(f"repairs[{index}] must be an object")
        key = _row_key(raw)
        if key not in expected or key in seen:
            raise RepairContractError(f"repairs[{index}] target is unexpected or duplicated: {key}")
        seen.add(key)
        action = raw.get("action")
        if action not in _ACTIONS:
            raise RepairContractError(f"repairs[{index}].action is invalid: {action!r}")
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise RepairContractError(f"repairs[{index}].reason must be non-empty")
        anchors_raw = raw.get("anchors")
        if not isinstance(anchors_raw, list):
            raise RepairContractError(f"repairs[{index}].anchors must be a list")
        anchors = [
            _validate_anchor(anchor, chunk_texts, anchor_index)
            for anchor_index, anchor in enumerate(anchors_raw)
        ]
        replacement = raw.get("replacement")
        disposition = raw.get("disposition")
        protected = (protection or {}).get(key, ())
        if protected and (action == 'drop' or action == 'replace' and (
                not isinstance(replacement, str) or any(part not in replacement for part in protected))):
            raise RepairContractError('targeted repair removed or rewrote unaffected clauses')
        if action == "drop":
            if replacement is not None or anchors:
                raise RepairContractError("drop requires replacement=null and anchors=[]")
            if disposition not in _DROP_DISPOSITIONS:
                raise RepairContractError("drop requires a supported disposition")
        elif action == "reanchor":
            if replacement is not None or not anchors:
                raise RepairContractError("reanchor requires replacement=null and non-empty anchors")
            disposition = None
        else:
            if not anchors:
                raise RepairContractError("replace requires non-empty anchors")
            field = _target_field(key[0])
            if field == "key_data":
                replacement = _validate_key_data_replacement(replacement, chunk_texts)
            elif not isinstance(replacement, str) or not replacement.strip():
                raise RepairContractError("core-field replacement must be a non-empty string")
            disposition = None
        _target_value(fm, *key)
        actual.append({
            "location": key[0],
            "item_index": key[1],
            "action": action,
            "replacement": replacement,
            "anchors": anchors,
            "disposition": disposition,
            "reason": reason.strip(),
        })
    if seen != expected:
        raise RepairContractError(f"DeepSeek repair coverage mismatch: expected={sorted(expected)} actual={sorted(seen)}")
    return actual


def _call_and_validate_repairs(
    prompt: str,
    *,
    expected: set[tuple[str, int]],
    fm: dict,
    chunk_texts: dict[str, str],
    call_distill,
    counts: dict[str, int],
    protection: dict | None = None,
) -> list[dict]:
    last_error = None
    for attempt_index, bypass_cache in enumerate((False, True, True)):
        counts["distill"] += 1
        try:
            attempt_prompt = prompt if not bypass_cache else (
                prompt
                + "\n\nM6_DISTILL_REPAIR_TECHNICAL_RETRY\n"
                + f"The previous response failed the controlled technical attempt: {last_error}. "
                  "Return the complete same repair bundle once; do not change the semantic task."
            )
            if call_distill is _default_distill_call:
                response = call_distill(
                    attempt_prompt,
                    bypass_cache=bypass_cache,
                    max_tokens=(
                        REPAIR_MAX_TOKENS
                        if attempt_index == 0
                        else REPAIR_MAX_TOKENS_RETRY
                    ),
                )
            else:
                response = call_distill(
                    attempt_prompt,
                    bypass_cache=bypass_cache,
                )
            raw = response.get("text") if isinstance(response, dict) else response
            return _validate_repairs(
                _first_json_object(str(raw or "")),
                expected,
                fm,
                chunk_texts,
                protection,
            )
        except (RepairContractError, GatewayError) as exc:
            last_error = exc
    raise RepairContractError(
        f"DeepSeek repair batch initial attempt plus two retries failed: {last_error}"
    )


def _apply_repairs_in_memory(fm: dict, repairs: list[dict]) -> tuple[dict, set[tuple[str, int]]]:
    working = copy.deepcopy(fm)
    dropped = set()
    for repair in repairs:
        location, item_index = repair["location"], repair["item_index"]
        action = repair["action"]
        if action == "drop":
            dropped.add((location, item_index))
            continue
        if location.startswith("by_field."):
            field = location.split(".", 1)[1]
            if action == "replace":
                value = working[field]
                if isinstance(value, list):
                    value[item_index] = repair["replacement"]
                else:
                    working[field] = repair["replacement"]
            anchors = working["source_anchor"]["by_field"][field]
            existing = {
                (anchor.get("chunk_id"), anchor.get("quote"))
                for anchor in anchors
                if isinstance(anchor, dict)
            }
            for anchor in repair["anchors"]:
                key = (anchor["chunk_id"], anchor["quote"])
                if key not in existing:
                    anchors.append(dict(anchor))
                    existing.add(key)
            continue
        match = _KEY_DATA_LOCATION_RE.fullmatch(location)
        if not match:
            raise RepairContractError(f"unmapped repair target: {location}")
        outer_index = int(match.group(1))
        if action == "replace":
            working["key_data"][outer_index] = dict(repair["replacement"])
        else:
            first = repair["anchors"][0]
            working["key_data"][outer_index]["chunk_id"] = first["chunk_id"]
            working["key_data"][outer_index]["quote"] = first["quote"]
    return working, dropped


def _filtered_batch_input(
    working: dict,
    target_keys: set[tuple[str, int]],
    chunk_texts: dict[str, str],
) -> tuple[dict, dict, dict[str, list[int]]]:
    subjects = _tx._collect_subjects(working)
    by_location = {subject.location: subject for subject in subjects}
    grouped: dict[str, list[int]] = defaultdict(list)
    for location, item_index in sorted(target_keys):
        grouped[location].append(item_index)
    rendered = []
    source_ids = []
    seen_sources = set()
    for location, indexes in grouped.items():
        subject = by_location.get(location)
        if subject is None:
            raise RepairContractError(f"repaired subject disappeared: {location}")
        item_texts = _tx._card_item_texts(subject.value_text)
        if any(index >= len(item_texts) for index in indexes):
            raise RepairContractError(f"repaired item index outside current value: {location}/{indexes}")
        rendered.append({
            "location": location,
            "field": subject.field,
            "card_items": [
                {"item_index": index, "text": item_texts[index]}
                for index in indexes
            ],
            "chunk_ids": list(subject.chunk_ids),
        })
        for chunk_id in subject.chunk_ids:
            if chunk_id not in seen_sources:
                seen_sources.add(chunk_id)
                source_ids.append(chunk_id)
    return ({
        "subjects": rendered,
        "sources": {chunk_id: chunk_texts.get(chunk_id, "") for chunk_id in source_ids},
    }, by_location, dict(grouped))


def _partition_filtered_review_input(
    filtered: dict,
    subjects_by_location: dict,
    chunk_texts: dict[str, str],
) -> list[dict]:
    """Bound repaired-item review without changing original item indexes."""
    rows_by_location = {row["location"]: row for row in filtered["subjects"]}
    proxies = []
    for location, row in rows_by_location.items():
        subject = subjects_by_location.get(location)
        if subject is None:
            raise RepairContractError(f"repaired review subject disappeared: {location}")
        proxy_value = "\n".join(
            "- " + str(item["text"]).replace("\r", " ").replace("\n", " ")
            for item in row["card_items"]
        )
        proxies.append(subject._replace(
            value_text=proxy_value,
            chunk_ids=list(row["chunk_ids"]),
        ))
    partitions = _tx._partition_batch_subjects(proxies, chunk_texts)
    batches = []
    for partition in partitions:
        subjects = []
        source_ids = []
        seen_sources = set()
        for subject in partition:
            row = copy.deepcopy(rows_by_location[subject.location])
            row["chunk_ids"] = list(subject.chunk_ids)
            subjects.append(row)
            for chunk_id in subject.chunk_ids:
                if chunk_id not in seen_sources:
                    seen_sources.add(chunk_id)
                    source_ids.append(chunk_id)
        batches.append({
            "subjects": subjects,
            "sources": {
                chunk_id: chunk_texts.get(chunk_id, "") for chunk_id in source_ids
            },
        })
    return batches


def _make_report(
    card_path: Path,
    fm: dict,
    problems: list[Problem],
    checked_locations: list[str],
    *,
    suffix: str,
) -> _report.M6Report:
    verify_provider, verify_model = _tx._verify_slot()
    distill_model = fm.get("distill_model")
    same_source, same_reason = _tx._same_source(distill_model, verify_provider, verify_model)
    paper_id = ((fm.get("source_anchor") or {}).get("paper_id")
                if isinstance(fm.get("source_anchor"), dict) else None) or "unknown"
    report_id = _report.gen_report_id(paper_id).replace("-M6-", f"-M6-{suffix}-", 1)
    verdict = "FAIL" if any(problem.kind in _VALID_RULES for problem in problems) else "PASS"
    checked_core = [location for location in checked_locations if location.startswith("by_field.")]
    return _report.M6Report(
        report_id=report_id,
        paper_id=paper_id,
        card=str(card_path),
        verdict=verdict,
        problems=problems,
        confidence=(
            _report.CONFIDENCE_SAME_SOURCE
            if same_source
            else _report.CONFIDENCE_CROSS_SOURCE
        ),
        same_source_reason=same_reason,
        upgrade_pending=(
            [f"{location}(核心字段,同供应商复核,可配置其他供应商再核)" for location in checked_core]
            if same_source
            else []
        ),
        distill_model=distill_model,
        verify_model=verify_model,
        checked_at=_tx._now_iso(),
        checked_count=len(checked_locations),
        checked_locations=list(checked_locations),
    )


def _initial_review(
    card_path: Path,
    fm: dict,
    chunk_texts: dict[str, str],
    *,
    call_verify,
    retry_verify,
    counts: dict[str, int],
) -> tuple[list[dict], list[Problem], list[str], dict, dict]:
    subjects = _tx._expand_absence_scope(_tx._collect_subjects(fm), chunk_texts)
    eligible = []
    malformed = []
    checked_locations = []
    for subject in subjects:
        chunk_text = "\n\n".join(chunk_texts.get(cid, "") for cid in subject.chunk_ids).strip()
        if not subject.value_text.strip() or not chunk_text:
            malformed.append(Problem(
                source="transcription",
                severity="warn",
                kind="malformed_subject",
                location=subject.location,
                field=subject.field,
                chunk_id=subject.chunk_ids[0] if subject.chunk_ids else None,
                detail="无字段值 / 无 chunk 原文,跳过回原文复核(未发空 subject)",
            ))
            continue
        eligible.append(subject)
        checked_locations.append(subject.location)
    if not eligible:
        raise DecisionError("M6 whole-Card initial review has zero eligible subjects")
    batches = _tx._partition_batch_subjects(eligible, chunk_texts)
    by_location = {subject.location: subject for subject in eligible}
    expected_items = {
        subject.location: list(range(len(_tx._card_item_texts(subject.value_text))))
        for subject in eligible
    }

    def semantic_call(prompt: str) -> str:
        counts["verify"] += 1
        counts["initial_verify"] += 1
        return call_verify(prompt)

    def technical_retry_call(prompt: str) -> str:
        counts["verify"] += 1
        counts["initial_verify"] += 1
        return retry_verify(prompt)

    checkpoint_root = card_path.parent / ".m6_node04_batches"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    all_rows, all_problems = [], list(malformed)
    for batch_index, batch_subjects in enumerate(batches):
        batch_input = _tx._build_batch_input(batch_subjects, chunk_texts)
        batch_expected_locations = [subject.location for subject in batch_subjects]
        batch_expected_items = {
            row["location"]: [item["item_index"] for item in row["card_items"]]
            for row in batch_input["subjects"]
        }
        batch_by_location = {subject.location: subject for subject in batch_subjects}
        prompt = _tx._build_batch_initial_prompt(batch_input)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
        checkpoint_path = checkpoint_root / f"batch-{batch_index:04d}-{prompt_sha256[:16]}.json"
        rows = None
        if checkpoint_path.is_file():
            try:
                saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if (
                    saved.get("schema_version") == "MemoNode04BatchCheckpoint-v2"
                    and saved.get("status") == "VALIDATED"
                    and saved.get("prompt_sha256") == prompt_sha256
                    and saved.get("expected_locations") == batch_expected_locations
                    and saved.get("expected_items") == batch_expected_items
                    and isinstance(saved.get("rows"), list)
                ):
                    rows = saved["rows"]
                    problems = _tx._validate_batch_rows(
                        rows, batch_by_location, chunk_texts
                    )
                    log_reason("M6", "node04_batch_checkpoint_hit",
                        f"04 batch {batch_index + 1}/{len(batches)} reused validated checkpoint",
                        event_category="verify", target=str(card_path))
            except (OSError, ValueError, TypeError, DecisionError):
                rows = None
        if rows is None:
            rows, problems = _tx._run_batch_stage_with_one_contract_retry(
                prompt,
                stage=f"initial review batch {batch_index + 1}/{len(batches)}",
                expected_locations=batch_expected_locations,
                expected_items=batch_expected_items,
                subjects_by_location=batch_by_location,
                chunk_texts=chunk_texts,
                call=semantic_call,
                retry_call=technical_retry_call,
            )
            payload = {
                "schema_version": "MemoNode04BatchCheckpoint-v2",
                "status": "VALIDATED",
                "batch_index": batch_index,
                "batch_count": len(batches),
                "prompt_sha256": prompt_sha256,
                "expected_locations": batch_expected_locations,
                "expected_items": batch_expected_items,
                "source_ids_sha256": hashlib.sha256(json.dumps(
                    sorted(batch_input["sources"]), ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")).hexdigest().upper(),
                "rows": rows,
            }
            _atomic_write(checkpoint_path, json.dumps(
                payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")
            ).encode("utf-8") + b"\n")
        all_rows.extend(rows)
        all_problems.extend(problems)
    unique_rows = []
    seen_rows = set()
    for row in all_rows:
        identity = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if identity not in seen_rows:
            seen_rows.add(identity)
            unique_rows.append(row)
    return unique_rows, all_problems, checked_locations, by_location, expected_items


def _build_omissions(
    original: dict,
    initial_rows: list[dict],
    repairs: list[dict],
    dropped: set[tuple[str, int]],
    final_problems: list[Problem],
    *,
    initial_report_id: str,
    final_report_id: str | None,
    attempt_counts: dict[str, int],
) -> list[dict]:
    rows_by_key: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in initial_rows:
        rows_by_key[_row_key(row)].append(row)
    repairs_by_key = {(row["location"], row["item_index"]): row for row in repairs}
    final_by_key: dict[tuple[str, int], list[Problem]] = defaultdict(list)
    for problem in final_problems:
        final_by_key[(problem.location, _problem_item_index(problem))].append(problem)
    omissions = []
    for location, report_item_index in sorted(dropped):
        repair = repairs_by_key.get((location, report_item_index))
        final_hits = final_by_key.get((location, report_item_index), [])
        if final_hits:
            disposition = "final_verify_failed"
            reason = "Targeted review still rejected the repaired item."
        elif repair:
            disposition = repair.get("disposition") or "unrecoverable"
            reason = repair.get("reason") or "DeepSeek could not safely repair the item."
        else:
            disposition = "unverified"
            reason = "The item was not safely reviewed."
        outer_index = report_item_index
        if _KEY_DATA_LOCATION_RE.fullmatch(location):
            outer_index = int(_KEY_DATA_LOCATION_RE.fullmatch(location).group(1))
        related_anchors = []
        if location.startswith("by_field."):
            field = location.split(".", 1)[1]
            anchors = ((original.get("source_anchor") or {}).get("by_field") or {}).get(field)
            if isinstance(anchors, list):
                related_anchors = [copy.deepcopy(anchor) for anchor in anchors if isinstance(anchor, dict)]
            elif isinstance(anchors, dict):
                related_anchors = [copy.deepcopy(anchors)]
        else:
            match = _KEY_DATA_LOCATION_RE.fullmatch(location)
            key_data = original.get("key_data") or []
            if match and int(match.group(1)) < len(key_data):
                item = key_data[int(match.group(1))]
                if isinstance(item, dict) and item.get("chunk_id"):
                    related_anchors = [{
                        "chunk_id": item.get("chunk_id"),
                        "quote": item.get("quote"),
                    }]
        source_anchor = original.get("source_anchor") or {}
        targeted_calls = (
            attempt_counts["targeted_verify"]
            if repair and repair.get("action") != "drop"
            else 0
        )
        omissions.append({
            "paper_id": source_anchor.get("paper_id"),
            "location": location,
            "item_index": outer_index,
            "report_item_index": report_item_index,
            "disposition": disposition,
            "removed_value": _target_value(original, location, report_item_index),
            "reason": reason,
            "initial_rules": sorted({row.get("rule") for row in rows_by_key[(location, report_item_index)] if row.get("rule")}),
            "final_rules": sorted({problem.kind for problem in final_hits}),
            "status": "removed_from_final_card",
            "related_anchors": related_anchors,
            "attempts": {
                "initial_review_calls": attempt_counts["initial_verify"],
                "repair_calls": attempt_counts["distill"],
                "targeted_review_calls": targeted_calls,
            },
            "initial_report_id": initial_report_id,
            "final_report_id": final_report_id,
        })
    return omissions


def _final_payload(
    original: dict,
    working: dict,
    dropped: set[tuple[str, int]],
    omissions: list[dict],
    checked_locations: set[str],
    retained_clauses: dict | None = None,
) -> tuple[dict, dict, list, dict]:
    # System warnings follow the Card's language; scientific field values and
    # omission provenance are left intact.
    chinese = detect_card_lang(original.get(field) for field in CONTENT_FIELDS) == 'zh'
    unknown_field = DANGER_UNKNOWN_FIELD if chinese else 'Unverified field; check the source before citing.'
    unknown_number = DANGER_UNKNOWN_KD if chinese else 'Unverified value; check the source before citing.'
    omitted_field = ('⚠⚠ 该字段数据缺失·已从最终 Card 删除·须查 omissions 与 M6 报告' if chinese
                     else 'Field omitted after review; see omissions and the verification report.')
    fields = {field: copy.deepcopy(working.get(field)) for field in CONTENT_FIELDS}
    core_removals: dict[str, set[int]] = defaultdict(set)
    kd_removals = set()
    for location, report_item_index in dropped:
        if location.startswith("by_field."):
            core_removals[location.split(".", 1)[1]].add(report_item_index)
        else:
            match = _KEY_DATA_LOCATION_RE.fullmatch(location)
            if match:
                kd_removals.add(int(match.group(1)))
    for field, indexes in core_removals.items():
        value = fields[field]
        if isinstance(value, list):
            value = [((retained_clauses or {}).get((f'by_field.{field}',index)) or item)
                     for index, item in enumerate(value)
                     if index not in indexes or (f'by_field.{field}',index) in (retained_clauses or {})]
            fields[field] = value or None
        elif 0 in indexes:
            fields[field] = (retained_clauses or {}).get((f'by_field.{field}',0))

    key_data = []
    for index, item in enumerate(copy.deepcopy(working.get("key_data") or [])):
        if index in kd_removals:
            continue
        location = f"key_data[{index}]"
        item["credibility"] = CRED_VERIFIED if location in checked_locations else CRED_UNKNOWN
        item["credibility_note"] = None
        item["danger"] = None if location in checked_locations else unknown_number
        key_data.append(item)

    omission_counts = defaultdict(int)
    for omission in omissions:
        omission_counts[omission["location"]] += 1
    field_credibility = {}
    for field in CONTENT_FIELDS:
        location = f"by_field.{field}"
        value = fields[field]
        if value is None or value == []:
            field_credibility[field] = {
                "credibility": CRED_FLAGGED,
                "note": f"Data omitted after M6 repair flow; see omissions for {location}",
                "danger": omitted_field,
            }
        elif any(key[0] == location for key in (retained_clauses or {})):
            field_credibility[field] = {
                'credibility': CRED_FLAGGED,
                'note': 'Failed clauses omitted; unaffected source text retained. See omissions.',
                'danger': ('部分子句校核未通过；保留未涉及内容，请核对来源。' if chinese
                           else 'Some clauses failed review; unaffected text retained. Check the source.'),
            }
        elif location in checked_locations:
            note = (
                f"{omission_counts[location]} failed item(s) omitted; retained items passed M6"
                if omission_counts[location]
                else None
            )
            field_credibility[field] = {
                "credibility": CRED_VERIFIED,
                "note": note,
                "danger": None,
            }
        else:
            field_credibility[field] = {
                "credibility": CRED_UNKNOWN,
                "note": None,
                "danger": unknown_field,
            }
    by_field = copy.deepcopy(working["source_anchor"]["by_field"])
    return fields, by_field, key_data, field_credibility


def repair_and_admit(
    card_path,
    *,
    call_verify=None,
    call_distill=None,
    region_check=None,
    bypass_cache: bool = False,
) -> RepairAdmitResult:
    """Run the user-approved single-round repair flow and output the final Card."""
    card_path = Path(card_path)
    counts = {"verify": 0, "distill": 0, "initial_verify": 0, "targeted_verify": 0}
    fm = _read_frontmatter(card_path)
    _tx._ownership_gate(fm)
    if region_check is not None:
        region_check()
    elif call_verify is None:
        _tx._default_region_check()
    chunk_texts = _tx._load_chunk_texts(card_path)

    if call_verify is None:
        def verify_call(prompt: str) -> str:
            return _tx._default_call_verify(prompt, bypass_cache=bypass_cache)

        def verify_retry_call(prompt: str) -> str:
            return _tx._default_call_verify(prompt, bypass_cache=True)
    else:
        verify_call = call_verify
        verify_retry_call = call_verify
    distill_call = call_distill or _default_distill_call

    try:
        initial_rows, initial_problems, checked_locations, subjects_by_location, _expected = _initial_review(
            card_path,
            fm,
            chunk_texts,
            call_verify=verify_call,
            retry_verify=verify_retry_call,
            counts=counts,
        )
    except (BatchContractExhausted, DecisionError) as exc:
        log_reason(
            "M6",
            "repair_flow_initial_escalated",
            f"整卡初审技术失败，无法识别可保留项；卡留 pending:{exc}",
            event_category="verify",
            target=str(card_path),
        )
        failure_reason,root_reason_code=_failure_projection(exc)
        return RepairAdmitResult(
            "escalated", counts["verify"], counts["distill"],
            failure_reason=failure_reason, root_reason_code=root_reason_code,
        )

    initial_report = _make_report(
        card_path,
        fm,
        initial_problems,
        checked_locations,
        suffix="initial",
    )
    _report.write_report(initial_report)
    repair_rows = [row for row in initial_rows if row.get("rule") in _VALID_RULES]
    repairs = []
    working = copy.deepcopy(fm)
    dropped: set[tuple[str, int]] = set()
    final_problems: list[Problem] = []
    final_report = None
    technical_repair_failure = None
    repair_batch_count = 0
    repair_batch_failures = []
    final_review_batch_count = 0
    final_review_batch_failures = []

    if repair_rows:
        bundle = _build_repair_bundle(fm, repair_rows, subjects_by_location, chunk_texts)
        batches = _partition_repair_bundle(bundle)
        repair_batch_count = len(batches)
        failed_targets = set()
        for batch_index, repair_batch in enumerate(batches):
            expected = {_row_key(row) for row in repair_batch["targets"]}
            prompt = _build_repair_prompt(repair_batch)
            try:
                repairs.extend(_call_and_validate_repairs(
                    prompt,
                    expected=expected,
                    fm=fm,
                    chunk_texts=chunk_texts,
                    call_distill=distill_call,
                    counts=counts,
                    protection={
                        _row_key(row): row.get("protected_clauses", ())
                        for row in repair_batch["targets"]
                    },
                ))
            except (RepairContractError, DecisionError) as exc:
                failed_targets.update(expected)
                failure = {
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "target_count": len(expected),
                    "targets": sorted([list(key) for key in expected]),
                    "reason": str(exc),
                }
                repair_batch_failures.append(failure)
                log_reason(
                    "M6",
                    "repair_flow_distill_batch_unavailable",
                    f"DeepSeek 返修分块 {batch_index + 1}/{len(batches)} 在本块两次重试后仍不可用；"
                    f"只删除本分块受影响项并继续:{exc}",
                    event_category="verify",
                    target=str(card_path),
                )
        if repairs:
            working, dropped = _apply_repairs_in_memory(fm, repairs)
        dropped.update(failed_targets)
        if repair_batch_failures:
            technical_repair_failure = "; ".join(
                f"batch {row['batch_index'] + 1}/{row['batch_count']}: {row['reason']}"
                for row in repair_batch_failures
            )

        repair_targets = {
            (row["location"], row["item_index"])
            for row in repairs
            if row["action"] != "drop"
        }
        if repair_targets:
            batch_input, final_subjects, expected_items = _filtered_batch_input(
                working,
                repair_targets,
                chunk_texts,
            )
            final_batches = _partition_filtered_review_input(
                batch_input, final_subjects, chunk_texts
            )
            final_review_batch_count = len(final_batches)
            final_checked_locations = []
            all_final_problems = []

            def final_call(prompt_text: str) -> str:
                counts["verify"] += 1
                counts["targeted_verify"] += 1
                return verify_call(prompt_text)

            def final_retry_call(prompt_text: str) -> str:
                counts["verify"] += 1
                counts["targeted_verify"] += 1
                return verify_retry_call(prompt_text)

            for batch_index, final_batch in enumerate(final_batches):
                batch_expected_items = {
                    row["location"]: [
                        item["item_index"] for item in row["card_items"]
                    ]
                    for row in final_batch["subjects"]
                }
                batch_expected_locations = list(batch_expected_items)
                batch_targets = {
                    (location, item_index)
                    for location, item_indexes in batch_expected_items.items()
                    for item_index in item_indexes
                }
                batch_subjects = {
                    row["location"]: final_subjects[row["location"]]._replace(
                        chunk_ids=list(row["chunk_ids"])
                    )
                    for row in final_batch["subjects"]
                }
                initial_for_batch = [
                    row for row in repair_rows if _row_key(row) in batch_targets
                ]
                final_prompt = (
                    _tx._build_batch_review_prompt(
                        final_batch,
                        initial_for_batch,
                        prior_source_context=final_batch["sources"],
                    )
                    + "\nM6_REPAIR_TARGETED_FINAL\n"
                    "Review only the repaired Card items in the current batch. Do not re-review "
                    "unchanged fields.\n"
                )
                try:
                    _final_rows, batch_problems = _tx._run_batch_stage_with_one_contract_retry(
                        final_prompt,
                        stage=(
                            f"targeted repaired-item final review batch "
                            f"{batch_index + 1}/{len(final_batches)}"
                        ),
                        expected_locations=batch_expected_locations,
                        expected_items=batch_expected_items,
                        subjects_by_location=batch_subjects,
                        chunk_texts=chunk_texts,
                        call=final_call,
                        retry_call=final_retry_call,
                    )
                except BatchContractExhausted as exc:
                    dropped.update(batch_targets)
                    failure = {
                        "batch_index": batch_index,
                        "batch_count": len(final_batches),
                        "target_count": len(batch_targets),
                        "targets": sorted([list(key) for key in batch_targets]),
                        "reason": str(exc),
                    }
                    final_review_batch_failures.append(failure)
                    log_reason(
                        "M6",
                        "repair_flow_final_batch_unavailable",
                        f"审核模型变更项复核分块 {batch_index + 1}/{len(final_batches)} "
                        f"在本块两次重试后仍不可用；只删除本分块变更项并继续:{exc}",
                        event_category="verify",
                        target=str(card_path),
                    )
                    continue
                final_checked_locations.extend(batch_expected_locations)
                all_final_problems.extend(batch_problems)
                for problem in batch_problems:
                    dropped.add((problem.location, _problem_item_index(problem)))

            final_problems = all_final_problems
            if final_checked_locations:
                final_report = _make_report(
                    card_path,
                    working,
                    final_problems,
                    list(dict.fromkeys(final_checked_locations)),
                    suffix="targeted-final",
                )
                _report.write_report(final_report)

    final_report_id = final_report.report_id if final_report else None
    omissions = _build_omissions(
        fm,
        repair_rows,
        repairs,
        dropped,
        final_problems,
        initial_report_id=initial_report.report_id,
        final_report_id=final_report_id,
        attempt_counts=counts,
    )
    # A compound field is not an atomic disposable item. If the exact disputed
    # clause is known, preserve its unaffected original clauses even when the
    # producer/reviewer is unavailable. Keep the result flagged and the actual
    # removed text in the omission record; never turn partial work into complete.
    retained_clauses = {}
    for omission in omissions:
        key = (omission['location'], omission['report_item_index'])
        relevant = [row for row in repair_rows if _row_key(row) == key]
        value = _target_value(fm, *key)
        protected = _protected_clauses(value, relevant)
        if protected and key[0].startswith('by_field.'):
            retained = ''.join(protected)
            removed = value
            for part in protected:
                removed = removed.replace(part, '', 1)
            retained_clauses[key] = retained
            omission.update(removed_value=removed, retained_value=retained,
                            granularity='clause', status='failed_clauses_removed_unaffected_retained')
    fields, by_field, key_data, field_credibility = _final_payload(
        fm,
        working,
        dropped,
        omissions,
        set(checked_locations),
        retained_clauses,
    )
    if not any(value for value in fields.values()) and not key_data:
        return RepairAdmitResult(
            "escalated",
            counts["verify"],
            counts["distill"],
            initial_report_id=initial_report.report_id,
            final_report_id=final_report_id,
            omissions=omissions,
            failure_reason="No admissible Card content remained after governed repair",
            root_reason_code="M6_NO_ADMISSIBLE_CONTENT",
        )
    completion_status = "passed_with_omissions" if omissions else "complete"

    repair_dir = _repair_dir()
    repair_dir.mkdir(parents=True, exist_ok=True)
    pre_repair_path = repair_dir / f"{initial_report.report_id}.pre-repair-card.md"
    if pre_repair_path.exists():
        raise FileExistsError(f"pre-repair Card audit snapshot already exists: {pre_repair_path}")
    _atomic_write(pre_repair_path, card_path.read_bytes())
    finalize_card_after_repair(
        card_path,
        fields=fields,
        by_field=by_field,
        key_data=key_data,
        field_credibility=field_credibility,
        omissions=omissions,
        completion_status=completion_status,
    )
    reason = (
        "M6 单轮返修准入:配置模型整卡初审→蒸馏模型有界分块返修→"
        f"审核模型仅复核变更项;initial_report={initial_report.report_id};"
        f"final_report={final_report_id or 'none'};omissions={len(omissions)};"
        f"completion_status={completion_status}"
    )
    transition(card_path, ACTIVE, trigger=Trigger.GRADED_ADMIT, reason=reason)

    trace = {
        "flow": "distill__whole_card_review__batch_repair__changed_item_review__tombstone",
        "card": str(card_path),
        "paper_id": initial_report.paper_id,
        "initial_report_id": initial_report.report_id,
        "final_report_id": final_report_id,
        "verify_calls": counts["verify"],
        "verify_provider": _tx._verify_slot()[0],
        "verify_model": _tx._verify_slot()[1],
        "distill_repair_calls": counts["distill"],
        "repair_batch_count": repair_batch_count,
        "repair_batch_failures": repair_batch_failures,
        "final_review_batch_count": final_review_batch_count,
        "final_review_batch_failures": final_review_batch_failures,
        "repairs": repairs,
        "omissions": omissions,
        "completion_status": completion_status,
        "pre_repair_card": str(pre_repair_path),
        "pre_repair_sha256": hashlib.sha256(pre_repair_path.read_bytes()).hexdigest(),
        "final_card_sha256": hashlib.sha256(card_path.read_bytes()).hexdigest(),
    }
    if technical_repair_failure is not None:
        trace['technical_repair_failure'] = technical_repair_failure
    trace_path = repair_dir / f"{initial_report.report_id}.json"
    _atomic_write(
        trace_path,
        json.dumps(trace, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    log_reason(
        "M6",
        "repair_and_admit",
        reason,
        event_category="verify",
        target=str(card_path),
    )
    return RepairAdmitResult(
        "active",
        counts["verify"],
        counts["distill"],
        initial_report_id=initial_report.report_id,
        final_report_id=final_report_id,
        completion_status=completion_status,
        omissions=omissions,
        repair_trace_path=str(trace_path),
    )
