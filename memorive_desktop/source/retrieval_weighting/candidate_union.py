"""Deterministic legacy/field candidate union for SEMANTIC-AGGREGATION Shadow retrieval.

The union is deliberately a pool-construction control plane.  Reciprocal Rank
Fusion (RRF) is computed from ranks only, retained as pool provenance, and is
never exposed as a seventh score provider or as a final ranking weight.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes
from .errors import ContractViolation, IdentityConflict
from .shadow_contracts import validate_retrieval_policy_snapshot


IDENTITY_KEYS = {
    "paper_id",
    "card_id",
    "card_revision",
    "unit_kind",
    "unit_id",
    "source_id",
}
UNIT_KINDS = {"card", "chunk", "field"}
_OPTIONAL_METADATA_KEYS = ("source_family", "doc_type", "provenance_state")


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractViolation(f"{field} must be a non-empty exact string")
    if "\r" in value or "\n" in value:
        raise ContractViolation(f"{field} contains a forbidden line break")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractViolation(f"{field} must be a positive integer")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ContractViolation(f"{field} must be a finite number")
    return number


def validate_candidate_identity(value: Any) -> dict[str, Any]:
    """Validate a non-textual stable candidate identity exact-set."""

    if not isinstance(value, Mapping):
        raise ContractViolation("candidate identity must be an object")
    identity = deepcopy(dict(value))
    if set(identity) != IDENTITY_KEYS:
        raise ContractViolation(
            "candidate identity keys must match the frozen non-text exact set",
            context={
                "missing": sorted(IDENTITY_KEYS - set(identity)),
                "unexpected": sorted(set(identity) - IDENTITY_KEYS),
            },
        )
    for key in ("paper_id", "card_id", "unit_id", "source_id"):
        _non_empty_string(identity[key], f"identity.{key}")
    _positive_int(identity["card_revision"], "identity.card_revision")
    unit_kind = _non_empty_string(identity["unit_kind"], "identity.unit_kind")
    if unit_kind not in UNIT_KINDS:
        raise ContractViolation("identity.unit_kind must be card, chunk, or field")
    return identity


def make_candidate_id(identity: Mapping[str, Any]) -> str:
    """Bind identity to structured IDs, never text, snippets, or embeddings."""

    checked = validate_candidate_identity(identity)
    return "uc1_" + sha256_bytes(canonical_json_bytes(checked)).lower()


def _identity_sort_key(identity: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        identity["paper_id"],
        identity["card_id"],
        identity["card_revision"],
        identity["unit_kind"],
        identity["unit_id"],
        identity["source_id"],
    )


def _checked_scope(values: Sequence[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise ContractViolation("allowed_paper_ids must be a non-empty explicit scope")
    checked = [_non_empty_string(item, "allowed_paper_ids[]") for item in values]
    if len(checked) != len(set(checked)):
        raise ContractViolation("allowed_paper_ids must not contain duplicates")
    return frozenset(checked)


def _validate_hit(
    value: Any,
    *,
    channel: str,
    allowed_paper_ids: frozenset[str],
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(value, Mapping):
        raise ContractViolation(f"channel {channel} contains a non-object hit")
    raw = deepcopy(dict(value))
    required = {
        "identity",
        "rank",
        "raw_score",
        "score_profile",
        "active",
        "rights_allowed",
    }
    missing = required - set(raw)
    if missing:
        raise ContractViolation(
            f"channel {channel} hit is missing required fields",
            context={"missing": sorted(missing)},
        )
    identity = validate_candidate_identity(raw["identity"])
    rank = _positive_int(raw["rank"], f"{channel}.rank")
    raw_score = _finite_number(raw["raw_score"], f"{channel}.raw_score")
    score_profile = _non_empty_string(raw["score_profile"], f"{channel}.score_profile")

    active = raw["active"]
    if not isinstance(active, bool):
        raise ContractViolation(f"{channel}.active must be an explicit boolean")
    rights_allowed = raw["rights_allowed"]
    if not isinstance(rights_allowed, bool):
        raise ContractViolation(f"{channel}.rights_allowed must be an explicit boolean")

    metadata: dict[str, str] = {}
    for key in _OPTIONAL_METADATA_KEYS:
        if key in raw:
            metadata[key] = _non_empty_string(raw[key], f"{channel}.{key}")

    checked = {
        "identity": identity,
        "rank": rank,
        "raw_score": raw_score,
        "score_profile": score_profile,
        "metadata": metadata,
    }
    if active is not True:
        return checked, "INACTIVE"
    if identity["paper_id"] not in allowed_paper_ids:
        return checked, "OUT_OF_EXPLICIT_SCOPE"
    if rights_allowed is not True:
        return checked, "RIGHTS_NOT_ALLOWED"
    return checked, None


def build_unified_candidate_pool(
    ranked_channels: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    allowed_paper_ids: Sequence[str],
    policy_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one hard-filtered, rank-only RRF candidate pool.

    Each hit must carry an explicit structured ``identity`` plus ``active`` and
    ``rights_allowed`` booleans.  Unknown eligibility is an input contract
    failure; explicit false values are auditable hard-filter rejections.
    """

    if not isinstance(ranked_channels, Mapping) or not ranked_channels:
        raise ContractViolation("ranked_channels must be a non-empty mapping")
    snapshot = validate_retrieval_policy_snapshot(policy_snapshot)
    aggregation_policy = snapshot["aggregation_policy"]
    pool_size = _positive_int(aggregation_policy["top_k"], "policy.top_k")
    rrf_k = _positive_int(aggregation_policy["rrf_k"], "policy.rrf_k")
    per_card_cap = _positive_int(
        aggregation_policy["per_card_cap"], "policy.per_card_cap"
    )
    channel_caps = aggregation_policy["channel_caps"]
    scope = _checked_scope(allowed_paper_ids)

    channel_names = [_non_empty_string(name, "channel name") for name in ranked_channels]
    if len(channel_names) != len(set(channel_names)):
        raise ContractViolation("ranked channel names must be unique")
    missing_channel_policies = set(channel_names) - set(channel_caps)
    if missing_channel_policies:
        raise ContractViolation(
            "ranked channel is absent from the frozen channel-cap policy",
            context={"channels": sorted(missing_channel_policies)},
        )

    grouped: dict[str, dict[str, Any]] = {}
    filter_counts: Counter[str] = Counter()
    truncated_by_channel: dict[str, int] = {}

    for channel in sorted(channel_names):
        raw_hits = ranked_channels[channel]
        if isinstance(raw_hits, (str, bytes)) or not isinstance(raw_hits, Sequence):
            raise ContractViolation(f"ranked_channels[{channel}] must be a sequence")
        eligible: list[dict[str, Any]] = []
        observed_ranks: set[int] = set()
        prior_rank = 0
        channel_ids: set[str] = set()
        for raw_hit in raw_hits:
            hit, rejection = _validate_hit(
                raw_hit,
                channel=channel,
                allowed_paper_ids=scope,
            )
            rank = hit["rank"]
            if rank in observed_ranks or rank <= prior_rank:
                raise ContractViolation(
                    f"channel {channel} ranks must be unique and strictly increasing"
                )
            observed_ranks.add(rank)
            prior_rank = rank
            if rejection is not None:
                filter_counts[rejection] += 1
                continue
            candidate_id = make_candidate_id(hit["identity"])
            if candidate_id in channel_ids:
                raise IdentityConflict(
                    "one channel contains duplicate structured candidate identity",
                    context={"channel": channel, "candidate_id": candidate_id},
                )
            channel_ids.add(candidate_id)
            hit["candidate_id"] = candidate_id
            eligible.append(hit)

        retained = eligible[: channel_caps[channel]]
        truncated_by_channel[channel] = max(0, len(eligible) - len(retained))
        for hit in retained:
            candidate_id = hit["candidate_id"]
            identity = hit["identity"]
            metadata = hit["metadata"]
            if candidate_id not in grouped:
                grouped[candidate_id] = {
                    "identity": identity,
                    "metadata": metadata,
                    "channel_hits": [],
                }
            else:
                if grouped[candidate_id]["identity"] != identity:
                    raise IdentityConflict("candidate ID collision across structured identities")
                existing_metadata = grouped[candidate_id]["metadata"]
                for key, metadata_value in metadata.items():
                    if key in existing_metadata and existing_metadata[key] != metadata_value:
                        raise IdentityConflict(
                            "candidate metadata conflicts across ranked channels",
                            context={"candidate_id": candidate_id, "field": key},
                        )
                    existing_metadata[key] = metadata_value
            grouped[candidate_id]["channel_hits"].append(
                {
                    "channel": channel,
                    "rank": hit["rank"],
                    "raw_score": hit["raw_score"],
                    "score_profile": hit["score_profile"],
                }
            )

    candidates: list[dict[str, Any]] = []
    for candidate_id, value in grouped.items():
        hits = sorted(value["channel_hits"], key=lambda item: (item["channel"], item["rank"]))
        rrf_terms = [1.0 / (rrf_k + item["rank"]) for item in hits]
        identity = value["identity"]
        row = {
            "candidate_id": candidate_id,
            **deepcopy(identity),
            **deepcopy(value["metadata"]),
            "pool_rrf_score": math.fsum(rrf_terms),
            "channel_count": len(hits),
            "channel_hits": hits,
            "active": True,
            "scope_eligible": True,
            "rights_allowed": True,
        }
        candidates.append(row)

    candidates.sort(
        key=lambda item: (
            -item["pool_rrf_score"],
            _identity_sort_key(item),
            item["candidate_id"],
        )
    )
    selected: list[dict[str, Any]] = []
    per_card_counts: defaultdict[tuple[str, str, int], int] = defaultdict(int)
    per_card_skipped = 0
    for candidate in candidates:
        card_identity = (
            candidate["paper_id"],
            candidate["card_id"],
            candidate["card_revision"],
        )
        if per_card_counts[card_identity] >= per_card_cap:
            per_card_skipped += 1
            continue
        selected.append(candidate)
        per_card_counts[card_identity] += 1
        if len(selected) == pool_size:
            break

    underfill_count = pool_size - len(selected)
    underfill_reasons: list[dict[str, Any]] = []
    if underfill_count:
        underfill_reasons.append(
            {"reason_code": "ELIGIBLE_POOL_EXHAUSTED", "count": underfill_count}
        )
        if per_card_skipped:
            underfill_reasons.append(
                {"reason_code": "PER_CARD_CAP_EXHAUSTION", "count": per_card_skipped}
            )
        rejected_total = sum(filter_counts.values())
        if rejected_total:
            underfill_reasons.append(
                {"reason_code": "HARD_FILTER_REJECTIONS", "count": rejected_total}
            )
        truncated_total = sum(truncated_by_channel.values())
        if truncated_total:
            underfill_reasons.append(
                {"reason_code": "CHANNEL_CAP_TRUNCATION", "count": truncated_total}
            )

    return {
        "object_type": "UnifiedCandidatePool",
        "schema_version": "1.0",
        "fusion_method": "rank_only_rrf_v1",
        "rrf_is_provider": False,
        "rrf_k": rrf_k,
        "requested_capacity": pool_size,
        "populated_count": len(selected),
        "underfilled": bool(underfill_count),
        "underfill_count": underfill_count,
        "underfill_reasons": underfill_reasons,
        "hard_filter_report": {
            "active_only": True,
            "explicit_paper_scope": sorted(scope),
            "rights_required": True,
            "rejection_counts": [
                {"reason_code": reason, "count": filter_counts[reason]}
                for reason in sorted(filter_counts)
            ],
        },
        "channel_caps": {channel: channel_caps[channel] for channel in sorted(channel_names)},
        "per_card_cap": per_card_cap,
        "channel_truncation": [
            {"channel": channel, "count": truncated_by_channel[channel]}
            for channel in sorted(truncated_by_channel)
            if truncated_by_channel[channel]
        ],
        "candidates": selected,
    }
