"""Read-only, replaceable external model references. Never an exam scorer.

Only public metadata is requested. No documents, prompts, Gold or inference
requests enter this service. The existing no-redirect HTTPS transport and
atomic settings writer are reused; settings keep environment names, not keys.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import base64
import binascii
import csv
from datetime import datetime, timezone
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import socket
from threading import RLock
import time
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import uuid
import zipfile

from .contracts import canonical_sha256, immutable, normalize_external_api_base_url, scan_sensitive, utc_now
from .model_validation import UrllibHttpTransport
from .store import SettingsStore
from .metadata_transport import (PublicMetadataHttpTransport, MetadataRateLimitError,
    metadata_error_code, metadata_rate_limit, transient_metadata_error,
    LIVEBENCH_GITHUB_API, GITHUB_API_VERSION)
from .livebench_reference import DEFAULT_ENDPOINT as LIVEBENCH_ENDPOINT, release_urls, normalized_rows as livebench_rows

SCHEMA = "ExternalModelReference-v1"


def _github_commit(body):
    try:
        data = json.loads(body)
        sha = data.get('sha')
        if (not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha)
                or data.get('url') != LIVEBENCH_GITHUB_API + '/commits/' + sha
                or data.get('html_url') != 'https://github.com/LiveBench/new-livebench/commit/' + sha):
            raise ValueError
        return sha
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('LIVEBENCH_GITHUB_COMMIT_INVALID') from exc


def _github_content(body, path, commit):
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise ValueError('LIVEBENCH_GITHUB_CONTENT_INVALID') from exc
    if (not isinstance(data, dict) or data.get('type') != 'file'
            or data.get('path') != path or data.get('name') != path.rsplit('/', 1)[-1]):
        raise ValueError('LIVEBENCH_GITHUB_IDENTITY_INVALID')
    # GitHub Contents JSON documents full base64 support only through 1 MB.
    # An object/empty response above that API boundary is not a successful file.
    size = data.get('size')
    if type(size) is not int or not 0 < size <= 1024 * 1024:
        raise ValueError('LIVEBENCH_GITHUB_SIZE_UNSUPPORTED')
    if data.get('encoding') != 'base64' or not isinstance(data.get('content'), str):
        raise ValueError('LIVEBENCH_GITHUB_CONTENT_UNAVAILABLE')
    try:
        raw = base64.b64decode(data['content'].replace('\n', '').replace('\r', ''), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('LIVEBENCH_GITHUB_CONTENT_INVALID') from exc
    if len(raw) != size:
        raise ValueError('LIVEBENCH_GITHUB_SIZE_MISMATCH')
    blob = hashlib.sha1(b'blob ' + str(size).encode('ascii') + b'\0' + raw, usedforsecurity=False).hexdigest()
    if data.get('sha') != blob:
        raise ValueError('LIVEBENCH_GITHUB_HASH_MISMATCH')
    if (data.get('git_url') != LIVEBENCH_GITHUB_API + '/git/blobs/' + blob
            or data.get('html_url') != 'https://github.com/LiveBench/new-livebench/blob/' + commit + '/' + path):
        raise ValueError('LIVEBENCH_GITHUB_IDENTITY_INVALID')
    return raw, dict(path=path, commit=commit, git_blob_sha1=blob, bytes=size,
                     decoded_sha256=hashlib.sha256(raw).hexdigest())


PUBLIC_PRESET_VERSION = "PR-OS-PublicModelReferences-v1"
FORMATS = {"ECB_FX_XML", "ARTIFICIAL_ANALYSIS", "EPOCH_CSV_ZIP", "OPENROUTER", "REFERENCE_JSON_V1", "LIVEBENCH_RELEASE"}
AA_METRICS = {
    "artificial_analysis_intelligence_index", "artificial_analysis_coding_index",
    "artificial_analysis_agentic_index", "aa_lcr", "aa_omniscience_accuracy",
    "aa_omniscience_non_hallucination_rate", "ifbench", "hle", "gpqa_diamond",
    "critpt", "mmmu_pro", "artificial_analysis_multilingual_index",
}
ROLE_METRICS = {
    "CARD_DISTILLER": {"aa_lcr", "ifbench", "aa_omniscience_non_hallucination_rate", "gpqa_diamond", "artificial_analysis_multilingual_index"},
    "CARD_REVIEWER": {"aa_lcr", "ifbench", "aa_omniscience_accuracy", "aa_omniscience_non_hallucination_rate"},
    "ANALYSIS_PRIMARY": {"aa_lcr", "ifbench", "aa_omniscience_non_hallucination_rate", "hle", "gpqa_diamond", "critpt"},
    "ANALYSIS_REVIEWER": {"aa_lcr", "ifbench", "aa_omniscience_non_hallucination_rate", "gpqa_diamond", "critpt"},
    "RERANKER_TEXT": {"aa_lcr", "ifbench", "artificial_analysis_multilingual_index"},
    "OCR_PAGE": {"mmmu_pro", "artificial_analysis_multilingual_index"},
    "EMBEDDING_TEXT": set(), "DOCUMENT_PROCESSING": set(),
}
AUTO_REFRESH_INTERVAL_DAYS = 7
for _role in ('CARD_DISTILLER','CARD_REVIEWER','ANALYSIS_PRIMARY','ANALYSIS_REVIEWER','RERANKER_TEXT'):
    ROLE_METRICS[_role].update({'livebench_language','livebench_instruction_following','livebench_reasoning'})
for _role in ('ANALYSIS_PRIMARY','ANALYSIS_REVIEWER'):
    ROLE_METRICS[_role].update({'livebench_data_analysis','livebench_math'})


def default_sources() -> list[dict[str, Any]]:
    rows = [
        # Keep the persisted slot id for existing replaceable-source bindings;
        # display name, format and upstream provenance explicitly become LiveBench.
        ("artificial-analysis", "LiveBench", "LIVEBENCH_RELEASE", LIVEBENCH_ENDPOINT, "", "LICENSE_REVIEW_REQUIRED"),
        ("epoch-ai", "Epoch AI", "EPOCH_CSV_ZIP", "https://epoch.ai/data/benchmark_data.zip", "", "ATTRIBUTION_AND_UPSTREAM_LICENSES"),
        ("openrouter", "OpenRouter", "OPENROUTER", "https://openrouter.ai/api/v1/models", "", "PER_DATASET_LICENSE_REVIEW"),
    ]
    result = [dict(source_id=i, display_name=n, format=f, endpoint=u,
                 credential_env=e, credential_origin=_origin(u) if e else "",
                 enabled=True, refresh_mode="WEEKLY", ttl_days=30,
                 redistribution=l) for i, n, f, u, e, l in rows]
    from .leaderboard_fx import ECB_ENDPOINT
    result.append(dict(source_id="exchange-rates", display_name="ECB 参考汇率", format="ECB_FX_XML",
                       endpoint=ECB_ENDPOINT, credential_env="", credential_origin="",
                       enabled=True, refresh_mode="WEEKLY", ttl_days=7,
                       redistribution="ATTRIBUTION_AND_UPSTREAM_LICENSES"))
    return result


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def automatic_metadata_enabled() -> bool:
    # Same boundary as the desktop service: reading a test profile never
    # grants network access or enables model execution.
    return (os.environ.get("PROS_P08_TEST_FIXTURE_MODE") != "1"
            and os.environ.get("PROS_TEST_CONSOLE_AUTO_EXECUTION") != "DISABLED")


def public_preset() -> dict[str, Any]:
    return {"version": PUBLIC_PRESET_VERSION, "sha256": canonical_sha256(default_sources()),
            "credential_required": False}


def validate_source(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(default_sources()[0]):
        raise ValueError("EXTERNAL_SOURCE_FIELDS_INVALID")
    row = immutable(value)
    scan_sensitive(row)
    if row["source_id"] not in {x["source_id"] for x in default_sources()}:
        raise ValueError("EXTERNAL_SOURCE_SLOT_INVALID")
    if not isinstance(row["display_name"], str) or not 1 <= len(row["display_name"].strip()) <= 80:
        raise ValueError("EXTERNAL_SOURCE_NAME_INVALID")
    if row["format"] not in FORMATS or row["refresh_mode"] not in {"MANUAL", "WEEKLY", "DAILY"}:
        raise ValueError("EXTERNAL_SOURCE_FORMAT_INVALID")
    row["endpoint"] = normalize_external_api_base_url(row["endpoint"])
    if row["source_id"] == "exchange-rates":
        from .leaderboard_fx import ECB_ENDPOINT
        if row["format"] != "ECB_FX_XML" or row["endpoint"] != ECB_ENDPOINT or row["credential_env"] or row["credential_origin"]:
            raise ValueError("FX_SOURCE_BINDING_INVALID")
    elif row["format"] == "ECB_FX_XML":
        raise ValueError("FX_SOURCE_SLOT_INVALID")
    if not isinstance(row["enabled"], bool):
        raise ValueError("EXTERNAL_SOURCE_ENABLED_INVALID")
    if type(row["ttl_days"]) is not int or not 1 <= row["ttl_days"] <= 365:
        raise ValueError("EXTERNAL_SOURCE_TTL_INVALID")
    env = row["credential_env"]
    if not isinstance(env, str) or (env and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", env)):
        raise ValueError("EXTERNAL_SOURCE_ENV_NAME_INVALID")
    if (env and row["credential_origin"] != _origin(row["endpoint"])) or (not env and row["credential_origin"]):
        raise ValueError("EXTERNAL_SOURCE_CREDENTIAL_ORIGIN_MISMATCH")
    if row["redistribution"] not in {"LICENSE_REVIEW_REQUIRED", "ATTRIBUTION_AND_UPSTREAM_LICENSES", "PER_DATASET_LICENSE_REVIEW", "USER_LICENSE_REVIEW_REQUIRED"}:
        raise ValueError("EXTERNAL_SOURCE_LICENSE_INVALID")
    return row


def _text(value: Any, maximum: int = 256) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        return ""
    result = str(value).strip()
    if len(result) > maximum or any(ord(c) < 32 for c in result):
        raise ValueError("EXTERNAL_METADATA_TEXT_INVALID")
    return result


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("EXTERNAL_METRIC_NOT_NUMERIC")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("EXTERNAL_METRIC_NOT_FINITE")
    return result


def _record(source: Mapping[str, Any], model_id: Any, name: Any, metrics: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    model_id = _text(model_id)
    if not model_id:
        raise ValueError("EXTERNAL_MODEL_ID_MISSING")
    normalized = {_text(k, 100): _number(v) for k, v in metrics.items()}
    row = dict(schema_version=SCHEMA, source_id=source["source_id"],
               source_name=source["display_name"], upstream_source=source["source_id"],
               source_url=source["endpoint"], model_id=model_id,
               model_name=_text(name) or model_id, model_slug=model_id,
               metrics=normalized, version=None, source_as_of=None,
               scale="SOURCE_NATIVE", confidence_interval=None,
               identity_scope="MODEL_LEVEL_ONLY_NOT_EXECUTION_PROFILE",
               reference_only=True, qualification_eligible=False,
               redistribution=source["redistribution"])
    row.update(extra)
    scan_sensitive(row)
    return row


def _epoch_rows(source: Mapping[str, Any], payload: bytes) -> list[dict[str, Any]]:
    rows = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if len(infos) > 512 or sum(i.file_size for i in infos) > 64 * 1024 * 1024:
            raise ValueError("EPOCH_ARCHIVE_ENVELOPE_EXCEEDED")
        for info in infos:
            if PurePosixPath(info.filename).is_absolute() or ".." in PurePosixPath(info.filename).parts or "\\" in info.filename or info.file_size > 8 * 1024 * 1024:
                raise ValueError("EPOCH_ARCHIVE_MEMBER_INVALID")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("EPOCH_ARCHIVE_DUPLICATE_MEMBER")
        current = "epoch_capabilities_index/eci_scores.csv"
        if current in names:
            reader = csv.DictReader(io.StringIO(archive.read(current).decode("utf-8-sig")))
            if not {"Model", "eci", "date"} <= set(reader.fieldnames or []):
                raise ValueError("EPOCH_ECI_COLUMNS_CHANGED")
            for item in reader:
                if not item.get("Model") or item.get("eci") in (None, ""):
                    continue
                score = _number(item["eci"])
                low, high = _number(item.get("eci_ci_low")), _number(item.get("eci_ci_high"))
                interval = [low, high] if low is not None and high is not None and low <= score <= high else None
                rows.append(_record(source, item["Model"], item.get("Display name") or item["Model"],
                    {"eci":score}, scale="ECI_UNBOUNDED", confidence_interval=interval,
                    source_as_of=item.get("date") or None, date_semantics="MODEL_RELEASE_NOT_EVALUATION",
                    identity_scope="MODEL_CAPABILITY_UPPER_ENVELOPE", score_schema="ECI_GROUP_CSV_V2",
                    organization=_text(item.get("Organization")), model_version_mapping="GROUP_ONLY"))
            if not rows:
                raise ValueError("EPOCH_ECI_EMPTY")
            return rows
        if "epoch_capabilities_index.csv" not in names:
            raise ValueError("EPOCH_ECI_MEMBER_MISSING")
        # Read allowlisted CSV bytes in memory. Never extract archive members.
        for item in csv.DictReader(io.StringIO(archive.read("epoch_capabilities_index.csv").decode("utf-8-sig"))):
            rows.append(_record(source, item.get("Model version"), item.get("Display name") or item.get("Model name"),
                                {"eci": item.get("ECI Score")}, scale="ECI_UNBOUNDED",
                                confidence_interval=None,
                                source_auxiliary_confidence_label=_text(item.get("Confidence")) or None,
                                auxiliary_confidence_semantics="PROVIDER_METADATA_NOT_SCORE_CONFIDENCE_INTERVAL",
                                source_as_of=item.get("Release date") or None,
                                date_semantics="MODEL_RELEASE_NOT_EVALUATION", identity_scope="MODEL_CAPABILITY_UPPER_ENVELOPE"))
        # Individual metrics retain their native scale and own attribution.
        if "benchmark_metadata.csv" in archive.namelist():
            metadata = csv.DictReader(io.StringIO(archive.read("benchmark_metadata.csv").decode("utf-8-sig")))
            for spec in metadata:
                filename, column = spec.get("source_file"), spec.get("score_column")
                if filename not in archive.namelist() or not column:
                    continue
                if not any(x in str(spec.get("benchmark", "")).lower() for x in ("gpqa", "critpt", "hle", "simpleqa", "long", "context", "mmmu")):
                    continue
                for item in csv.DictReader(io.StringIO(archive.read(filename).decode("utf-8-sig"))):
                    rows.append(_record(source, item.get("Model version"), item.get("Model version"),
                                        {spec["benchmark"]: item.get(column)}, scale=_text(spec.get("scale")) or "SOURCE_NATIVE",
                                        version=_text(spec.get("benchmark")), upstream_source="epoch-ai" if not filename.endswith("_external.csv") else "epoch-external:" + spec["benchmark"]))
    return rows


def normalize_payload(source: Mapping[str, Any], payload: Any) -> list[dict[str, Any]]:
    source = validate_source(source)
    if source["format"] == "ECB_FX_XML":
        from .leaderboard_fx import parse_ecb
        fx = parse_ecb(payload)
        return [dict(schema_version="ExchangeRateReference-v1", source_id=source["source_id"],
                     model_id="USD", model_name="USD/CNY/JPY", metrics={}, reference_only=True,
                     qualification_eligible=False, fx=fx)]
    if source['format']=='LIVEBENCH_RELEASE':
        if not isinstance(payload,Mapping): raise ValueError('LIVEBENCH_BUNDLE_REQUIRED')
        result=[]
        for item in livebench_rows(payload):
            row=_record(source,item['model'],item['model'],item['metrics'],upstream_source='livebench',
                version=payload['release'],scale='PERCENT_0_100',model_slug=item['base'],
                benchmark_effort=item['effort'],complete_category_coverage=item['complete'],
                source_as_of=payload['release'],date_semantics='BENCHMARK_RELEASE_NOT_MODEL_DATE')
            result.append(row)
        return result
    if isinstance(payload, bytes) and len(payload) > 32 * 1024 * 1024:
        raise ValueError("EXTERNAL_RESPONSE_ENVELOPE_EXCEEDED")
    if source["format"] == "EPOCH_CSV_ZIP":
        if not isinstance(payload, bytes):
            raise ValueError("EPOCH_ZIP_REQUIRED")
        return _epoch_rows(source, payload)
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ValueError("EXTERNAL_RESPONSE_DATA_INVALID")
    if len(payload["data"]) > 20000:
        raise ValueError("EXTERNAL_RESPONSE_ITEM_ENVELOPE_EXCEEDED")
    result = []
    meta = payload.get("meta") or {}
    for item in payload["data"]:
        if not isinstance(item, Mapping):
            raise ValueError("EXTERNAL_MODEL_ROW_INVALID")
        version = _text(payload.get("intelligence_index_version") or meta.get("version")) or None
        if source["format"] == "ARTIFICIAL_ANALYSIS":
            evaluations = item.get("evaluations") or {}
            row = _record(source, item.get("id") or item.get("slug"), item.get("name"),
                          {k: v for k, v in evaluations.items() if k in AA_METRICS},
                          model_slug=_text(item.get("slug") or item.get("id")), version=version)
            result.append(row)
        elif source["format"] == "OPENROUTER":
            identifier = item.get("model_permaslug") or item.get("id")
            if identifier == "other":
                continue
            metrics = {}
            upstream = item.get("source") or "openrouter"
            for field in ("intelligence_index", "coding_index", "agentic_index"):
                if field in item:
                    metrics["artificial_analysis_" + field] = item[field]
            if item.get("benchmark_type") and "accuracy" in item:
                metrics[_text(item["benchmark_type"])] = item["accuracy"]
            if "total_tokens" in item:
                metrics["adoption_total_tokens"] = item["total_tokens"]
            row = _record(source, identifier, item.get("display_name") or item.get("name"), metrics,
                          upstream_source=_text(upstream), version=version,
                          source_as_of=_text(item.get("last_run_timestamp") or item.get("date") or meta.get("as_of")) or None)
            description = item.get("description")
            description = description.strip() if isinstance(description,str) and len(description) <= 8000 else ""
            description = "".join(c for c in description if ord(c) >= 32 or c in "\n\t")
            row["operational"] = {
                "description": description,
                "canonical_slug": _text(item.get("canonical_slug")),
                "alias_target": _text((item.get("alias_target") or {}).get("slug")) if isinstance(item.get("alias_target"), Mapping) else "",
                "max_output_tokens": _number((item.get("top_provider") or {}).get("max_completion_tokens")),
                "output_modalities": [_text(x) for x in (item.get("architecture") or {}).get("output_modalities", [])],
                "context_length": _number(item.get("context_length")),
                "pricing": {k: _number(v) for k, v in (item.get("pricing") or {}).items() if k in {"prompt", "completion", "request", "image", "internal_reasoning", "input_cache_read", "input_cache_write"}},
                "pricing_unit": "USD_PER_TOKEN_OR_PROVIDER_UNIT_NOT_CLI_BILLING",
                "input_modalities": [_text(x) for x in (item.get("architecture") or {}).get("input_modalities", [])],
                "supported_parameters": [_text(x) for x in item.get("supported_parameters", [])],
            }
            result.append(row)
            benchmarks = item.get("benchmarks") or {}
            aa = benchmarks.get("artificial_analysis") or benchmarks.get("artificial-analysis") or {}
            if isinstance(aa, Mapping) and aa:
                result.append(_record(source, identifier, row["model_name"],
                    {"artificial_analysis_" + k: v for k, v in aa.items() if k in {"intelligence_index", "coding_index", "agentic_index"}},
                    upstream_source="artificial-analysis", version=version))
        else:
            if payload.get("schema_version") != SCHEMA:
                raise ValueError("EXTERNAL_REFERENCE_SCHEMA_UNSUPPORTED")
            result.append(_record(source, item.get("model_id"), item.get("model_name"), item.get("metrics") or {},
                                  upstream_source="custom:" + urlsplit(source["endpoint"]).hostname,
                                  version=_text(item.get("version")) or None, source_as_of=_text(item.get("source_as_of")) or None))
    return result


_EFFORT_SUFFIX = re.compile(r"_(?:none|low|medium|high|xhigh|max|unknown|promax|proxhigh|prounknown|[0-9]+k)$", re.I)
_OFFICIAL_NAMESPACES = {"openai", "anthropic", "deepseek", "qwen", "moonshotai", "baai"}


def _base_model_id(value: Any, *, local: bool = False) -> str:
    """Only syntactic aliases; keep model version, size and fine-tune identity."""
    name = str(value or "").strip().casefold()
    name = re.sub(r"^\[本地\]\s*", "", name)
    if "/" in name and name.split("/", 1)[0] in _OFFICIAL_NAMESPACES:
        name = name.split("/", 1)[1]
    if local:
        name = name.removesuffix(".gguf")
        name = re.sub(r"[-:]q[2-8](?:_[0-9a-z]+)+$", "", name)
        name = name.removesuffix(":latest")
        name = re.sub(r":(?=\d+(?:\.\d+)?b(?:-|$))", "-", name)
    return name


def _reference_percentiles(row: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    result = {}
    for metric, raw in row["metrics"].items():
        value = _number(raw.get("value") if isinstance(raw, Mapping) else raw)
        if value is None or metric == "adoption_total_tokens":
            continue
        cohort = {}
        for other in rows:
            if any(other.get(k) != row.get(k) for k in ("source_id", "upstream_source", "version", "snapshot_sha256")):
                continue
            score = other.get("metrics", {}).get(metric)
            score = _number(score.get("value") if isinstance(score, Mapping) else score)
            identity = _base_model_id(other.get("model_id"))
            if metric == "eci" and other.get("source_id") == "epoch-ai":
                identity = _EFFORT_SUFFIX.sub("", identity)
            if score is not None and identity:
                cohort.setdefault(identity, set()).add(score)
        # ECI publishes a model upper envelope. Other conflicting duplicate
        # records are excluded, not chosen by best score or counted twice.
        values = [max(scores) for scores in cohort.values() if len(scores)==1 or metric=="eci"]
        count = len(values)
        percentile = round(100 * (sum(v < value for v in values) + .5 * sum(v == value for v in values)) / count, 2) if count > 1 else None
        result[metric] = dict(value=percentile, population_count=count,
            method="SOURCE_METRIC_MIDRANK_PERCENTILE_V1", raw_value=value,
            reference_only=True, cross_source_comparison_forbidden=True)
    return result


def _reference_matches(target: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Explicit source ID adapters, never fuzzy model/version substitution."""
    wanted = str(target.get("model_name") or "").casefold()
    if not wanted:
        return []
    effort = str(target.get("thinking_mode") or "").casefold().removeprefix("adaptive-")
    suffix = _EFFORT_SUFFIX
    exact, envelopes, catalog, livebench = [], [], [], []
    for value in rows:
        row = deepcopy(dict(value))
        names = {str(row.get(k) or "").casefold() for k in ("model_id", "model_slug", "model_name")}
        identifier = str(row.get("model_id") or "").casefold()
        if (row.get("source_id") == "epoch-ai" and "eci" in row.get("metrics", {})
                and suffix.sub("", identifier) == wanted and identifier != wanted):
            row["match_kind"] = "MODEL_ENVELOPE_REFERENCE"
            envelopes.append(row)
        elif row.get('upstream_source')=='livebench' and row.get('model_slug') == wanted:
            row['match_kind']='SOURCE_BASE_MODEL_REFERENCE'
            row['effort_mismatch']=bool(effort and row.get('benchmark_effort') and effort!=row['benchmark_effort'])
            livebench.append(row)
        elif wanted in names:
            row["match_kind"] = "EXACT_SOURCE_ID"
            exact.append(row)
        elif row.get("source_id") == "openrouter" and "/" in identifier:
            provider, model = identifier.split("/", 1)
            if provider in {"openai", "anthropic", "deepseek", "qwen", "moonshotai"} and model == wanted:
                row["match_kind"] = "OFFICIAL_PROVIDER_PREFIX"
                catalog.append(row)
    preferred = [row for row in envelopes if row["model_id"].casefold() == wanted + "_" + effort]
    if preferred:
        envelopes = preferred
    elif len({canonical_sha256(row["metrics"]) for row in envelopes}) > 1:
        envelopes = []  # Different scores with no matching effort are ambiguous.
    live_exact = [row for row in livebench if effort and row.get('benchmark_effort')==effort]
    if live_exact:
        livebench = live_exact
    elif len({canonical_sha256(row['metrics']) for row in livebench})>1:
        livebench = []  # Do not pick the best or first of differing effort variants.
    matched = preferred + exact + [r for r in envelopes if r not in preferred] + catalog + livebench
    if target.get("kind") == "LOCAL":
        # A quantized local model can borrow a *base-model reference*, never an
        # exam grade. Prefer exact records per source when available.
        base = _base_model_id(wanted, local=True)
        local_rows = []
        exact_sources = {r['source_id'] for r in matched}
        for value in rows:
            if value.get('source_id') in exact_sources:
                continue
            identifier = _base_model_id(value.get('model_slug') if value.get('upstream_source')=='livebench' else value.get('model_id'))
            if value.get('source_id') == 'epoch-ai' and 'eci' in value.get('metrics', {}):
                identifier = suffix.sub('', identifier)
            if identifier == base:
                row = deepcopy(dict(value))
                row.update(match_kind='LOCAL_BASE_MODEL_REFERENCE', local_base_model_reference=True,
                           quantized_profile_assessed=False)
                local_rows.append(row)
        # Ambiguous model variants in one source are not silently selected.
        for source in {r['source_id'] for r in local_rows}:
            candidates = [r for r in local_rows if r['source_id']==source]
            if len({canonical_sha256(r['metrics']) for r in candidates})==1:
                matched.append(candidates[0])
    return matched


def role_references(role: str, target: Mapping[str, Any], rows: list[Mapping[str, Any]], *, internal_exam_score: Any = None) -> dict[str, Any]:
    if role not in ROLE_METRICS:
        raise ValueError("EXTERNAL_REFERENCE_ROLE_UNSUPPORTED")
    matches = _reference_matches(target, rows)
    # Direct AA and OpenRouter-forwarded AA are one provenance, not two votes.
    matches.sort(key=lambda r: (r["source_id"] != r["upstream_source"], r.get("version") or "", r["source_id"]))
    seen = set()
    output = []
    for row in matches:
        kept = {}
        for metric, value in row["metrics"].items():
            key = (row["upstream_source"], metric)
            if key not in seen:
                kept[metric] = value
                seen.add(key)
        # Display each source independently while preserving one-vote lineage
        # for consumers of metrics. A forwarded score is still visibly sourced.
        row["display_metrics"] = deepcopy(row["metrics"])
        row["reference_percentiles"] = _reference_percentiles(row, rows)
        row["metrics"] = kept
        row["role_relevant_metrics"] = [k for k in kept if k in ROLE_METRICS[role]]
        if row["display_metrics"] or row.get("operational"):
            output.append(row)
    return dict(schema_version="Phase2NewRoleReference-v1", role_id=role,
                internal_exam_score=internal_exam_score, references=output,
                assessment_status="REFERENCE_ONLY" if output else "NO_MATCH",
                qualification_status="NOT_ASSESSED", reference_only=True,
                role_score_substitution_forbidden=True, cross_category_comparison_forbidden=True,
                exact_execution_profile_assessed=False)


class ExternalDataSources:
    def __init__(self, root: Path | str, *, transport: Any = None, environment: Mapping[str, str] | None = None):
        self.root = Path(root)
        self.writer = SettingsStore(self.root)
        self.path = self.root / "external_sources.json"
        self.transport = transport or PublicMetadataHttpTransport()
        self.environment = os.environ if environment is None else environment
        self.lock = RLock()
        self.activity_lock = RLock()
        self.active_sources = set()
        # A failed background fetch waits for an explicit retry or the next
        # persisted weekly window. Reopening the application is not a retry.
        self.failed_automatic_sources = set()

    @contextmanager
    def _write_lock(self, filename="external_sources.lock", *, blocking=True):
        # OS locks release on process exit, unlike stale create-only lock files.
        if not self.lock.acquire(blocking=blocking):
            raise ValueError("EXTERNAL_SOURCE_WRITE_IN_PROGRESS")
        try:
            with self._file_lock(filename):
                yield
        finally:
            self.lock.release()

    @contextmanager
    def _file_lock(self, filename):
        with (self.root / filename).open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("EXTERNAL_SOURCE_WRITE_IN_PROGRESS") from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(schema_version="ExternalDataSourceSettings-v2", revision=0, sources=default_sources())
        value = json.loads(self.path.read_text(encoding="utf-8"))
        digest = value.pop("sha256", None)
        if canonical_sha256(value) != digest or value.get("schema_version") not in {"ExternalDataSourceSettings-v1", "ExternalDataSourceSettings-v2"}:
            raise ValueError("EXTERNAL_SOURCE_SETTINGS_HASH_INVALID")
        defaults = {row["source_id"]:row for row in default_sources()}
        accepted, seen = [], set()
        for row in value["sources"]:
            if not isinstance(row, dict) or row.get("source_id") not in defaults or row["source_id"] in seen:
                raise ValueError("EXTERNAL_SOURCE_SLOT_INVALID")
            seen.add(row["source_id"])
            # Only absent fields are filled. Invalid values, unknown fields and
            # missing credential-origin bindings still fail validation.
            accepted.append(validate_source({**defaults[row["source_id"]], **row}))
        accepted.extend(deepcopy(row) for key,row in defaults.items() if key not in seen)
        value["sources"] = accepted
        return value

    def enable_weekly_updates(self):
        """One-time migration of the former manual-only product default."""
        with self._write_lock():
            state = self._load()
            legacy_aa=next((row for row in state['sources'] if row['source_id']=='artificial-analysis'
                and row['format']=='ARTIFICIAL_ANALYSIS' and row['display_name']=='Artificial Analysis'
                and row['endpoint'] in {'https://artificialanalysis.ai/api/v2/language/models/free','https://artificialanalysis.ai/api/v2/data/llms/models'}
                and row['credential_env'] in ('','ARTIFICIAL_ANALYSIS_API_KEY')),None)
            legacy_daily_fx=next((row for row in state['sources'] if row['source_id']=='exchange-rates'
                and row['format']=='ECB_FX_XML' and row['endpoint']==default_sources()[-1]['endpoint']
                and row['credential_env']=='' and row['refresh_mode']=='DAILY'),None)
            if state["schema_version"] == "ExternalDataSourceSettings-v2" and legacy_aa is None and legacy_daily_fx is None:
                return {"status":"UNCHANGED"}
            before = deepcopy(state)
            if legacy_aa is not None:
                replacement=default_sources()[0]
                for field in ("enabled", "refresh_mode", "ttl_days"):
                    replacement[field]=legacy_aa[field]
                state['sources']=[replacement if row is legacy_aa else row for row in state['sources']]
            if legacy_daily_fx is not None:
                legacy_daily_fx['refresh_mode']='WEEKLY'
            # Missing refresh_mode was supplied by _load. Preserve explicit
            # MANUAL/disabled sources; migrate the former built-in ECB daily
            # default to the product-wide weekly cadence requested by the user.
            state.update(schema_version="ExternalDataSourceSettings-v2", revision=state["revision"]+1)
            state["sha256"] = canonical_sha256(state)
            directory = self.root / "policy_migrations"
            directory.mkdir(exist_ok=True)
            # The prior record contains env names only, never credential values.
            self.writer._atomic_write(directory / (canonical_sha256(before) + ".json"), before)
            self.writer._atomic_write(self.path, state)
            return {"status":"MIGRATED", "revision":state["revision"]}

    @staticmethod
    def _source_identity(config):
        return canonical_sha256({k:v for k,v in config.items() if k not in {"refresh_mode", "ttl_days"}})

    def _legacy_identities(self, config):
        return {canonical_sha256(config), canonical_sha256({**config, "refresh_mode":"MANUAL"})}

    def save(self, source: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        from . import literature_sources
        if source.get("source_id") in {row[0] for row in literature_sources.CATALOG} or "slot_id" in source:
            return literature_sources.save(self, source, expected_revision)
        accepted = validate_source(source)
        with self._write_lock():
            state = self._load()
            if type(expected_revision) is not int or expected_revision != state["revision"]:
                raise ValueError("EXTERNAL_SOURCE_REVISION_CONFLICT")
            before = next(x for x in state["sources"] if x["source_id"] == accepted["source_id"])
            if _origin(before["endpoint"]) != _origin(accepted["endpoint"]) and accepted["credential_env"]:
                # First save the replacement with no credential. A later explicit
                # save binds a key to the newly visible origin.
                raise ValueError("EXTERNAL_SOURCE_CREDENTIAL_ORIGIN_REBIND_REQUIRED")
            state["sources"] = [accepted if x["source_id"] == accepted["source_id"] else x for x in state["sources"]]
            state["revision"] += 1
            state["sha256"] = canonical_sha256(state)
            self.writer._atomic_write(self.path, state)
        return self.get_state()

    def _latest(self, config: Mapping[str, Any]) -> dict[str, Any] | None:
        directory = self.root / "snapshots" / config["source_id"]
        # A disabled/replaced endpoint cannot keep contributing old scores.
        if not config["enabled"] or not directory.is_dir():
            return None
        identity = self._source_identity(config)
        for path in sorted(directory.glob("*.json"), reverse=True):
            value = json.loads(path.read_text(encoding="utf-8"))
            digest = value.pop("sha256", None)
            if canonical_sha256(value) != digest:
                raise ValueError("EXTERNAL_REFERENCE_CACHE_HASH_INVALID")
            if (value.get("source_identity_sha256") != identity
                    and value.get("config_sha256") not in self._legacy_identities(config)):
                continue
            value["sha256"] = digest
            return value
        return None

    def _last_refresh(self, config):
        index = self.root / "receipts" / "latest" / (config["source_id"] + ".json")
        try:
            row = json.loads(index.read_text(encoding="utf-8"))
            digest = row.pop("sha256")
            if (row.get("schema_version") == "ExternalMetadataLatestRefresh-v1"
                    and canonical_sha256(row) == digest
                    and row.get("source_id") == config["source_id"]
                    and row.get("config_sha256") in self._legacy_identities(config)):
                return {k:row.get(k) for k in (
                    "status", "reason", "requested_at", "record_count",
                    "retry_after", "external_network_calls")}
        except (ValueError, OSError, KeyError, TypeError):
            pass
        for path in sorted((self.root / "receipts").glob("*.result.json"), reverse=True):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                digest = row.pop("receipt_sha256")
                if (row.get("schema_version") == "ExternalMetadataFetchReceipt-v1"
                        and canonical_sha256(row) == digest and row.get("source_id") == config["source_id"]
                        and row.get("config_sha256") in self._legacy_identities(config)):
                    try:
                        self._store_last_refresh(config, row)
                    except OSError:
                        pass
                    return {k:row.get(k) for k in (
                        "status", "reason", "requested_at", "record_count",
                        "retry_after", "external_network_calls")}
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return None

    def _store_last_refresh(self, config, row):
        value = {k:row.get(k) for k in (
            "source_id", "config_sha256", "status", "reason", "requested_at",
            "record_count", "retry_after", "external_network_calls")}
        value["schema_version"] = "ExternalMetadataLatestRefresh-v1"
        value["sha256"] = canonical_sha256(value)
        directory = self.root / "receipts" / "latest"
        directory.mkdir(parents=True, exist_ok=True)
        self.writer._atomic_write(directory / (config["source_id"] + ".json"), value)

    def _schedule(self):
        path = self.root / "refresh_schedule.json"
        if not path.exists():
            return {"schema_version":"ExternalWeeklyRefresh-v1", "sources":{}}
        value = json.loads(path.read_text(encoding="utf-8"))
        digest = value.pop("sha256", None)
        if canonical_sha256(value) != digest:
            raise ValueError("EXTERNAL_REFRESH_SCHEDULE_INVALID")
        return value

    def _is_due(self, config, now):
        identity = self._source_identity(config)
        with self.activity_lock:
            if identity in self.failed_automatic_sources:
                return False
        times = []
        schedule = self._schedule()["sources"].get(config["source_id"], {})
        if schedule.get("identity") == identity:
            if schedule.get("status") == "RATE_LIMITED":
                try:
                    retry_at = datetime.fromisoformat(schedule["retry_after"].replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError):
                    return False
                return now >= retry_at
            if schedule.get("attempted_at"):
                # Both PASS and ERROR attempts consume the automatic weekly
                # slot. The manual refresh action remains immediately available.
                times.append(schedule["attempted_at"])
            if schedule.get("succeeded_at"):
                times.append(schedule["succeeded_at"])
            elif "status" not in schedule and (self._last_refresh(config) or {}).get("status") == "PASS":
                # Compatibility with old schedules, but never treat ERROR as PASS.
                times.append(schedule["attempted_at"])
        cached = self._latest(config)
        if cached and config['format']=='OPENROUTER' and config['endpoint']=='https://openrouter.ai/api/v1/models':
            from .reference_pricing import POLICY
            if any((row.get('operational',{}).get('regular_pricing') or {}).get('policy') != POLICY
                   for row in cached['records'] if 'operational' in row):
                return True  # Add new price evidence once; never rewrite an old snapshot.
        if cached:
            times.append(cached["retrieved_at"])
        return not times or all((now-datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() >= AUTO_REFRESH_INTERVAL_DAYS*86400 for t in times)

    def _mark_attempt(self, config, now):
        with self._write_lock():
            schedule = self._schedule()
            schedule["sources"][config["source_id"]] = {"identity":self._source_identity(config), "attempted_at":now.isoformat()}
            schedule["sha256"] = canonical_sha256(schedule)
            self.writer._atomic_write(self.root / "refresh_schedule.json", schedule)

    def _mark_outcome(self, config, now, result):
        identity = self._source_identity(config)
        with self.activity_lock:
            if result["status"] == "PASS":
                self.failed_automatic_sources.discard(identity)
            elif result["status"] == "ERROR":
                self.failed_automatic_sources.add(identity)
        with self._write_lock():
            schedule = self._schedule()
            entry = schedule["sources"].get(config["source_id"], {})
            if entry.get("identity") != identity:
                return
            entry["status"] = result["status"]
            if result["status"] == "PASS":
                entry["succeeded_at"] = now.isoformat()
                entry.pop("retry_after", None)
                entry.pop("reason", None)
            elif result["status"] == "RATE_LIMITED":
                entry["retry_after"] = result["retry_after"]
                entry["reason"] = result.get("reason", "EXTERNAL_METADATA_RATE_LIMITED")
            schedule["sha256"] = canonical_sha256(schedule)
            self.writer._atomic_write(self.root / "refresh_schedule.json", schedule)

    def refresh_activity(self):
        with self.activity_lock:
            return set(self.active_sources)

    def refresh_due(self, *, now=None, preferences=None, should_stop=None):
        now = now or datetime.now(timezone.utc)
        results = []
        if not automatic_metadata_enabled():
            return {"updated":[], "failed":[], "results":[], "external_model_calls":0}
        for config in self._load()["sources"]:
            if should_stop and should_stop():
                break
            if not config["enabled"] or config["refresh_mode"] not in {"WEEKLY", "DAILY"}:
                continue
            if config["credential_env"] and not self.environment.get(config["credential_env"]):
                continue
            result = self.refresh(config["source_id"], expected_revision=self._load()["revision"],
                preferences=preferences, _automatic=True, _now=now)
            results.append(result)
        return {"updated":[r["source_id"] for r in results if r["status"] == "PASS"],
                "failed":[r["source_id"] for r in results if r["status"] in {"ERROR", "RATE_LIMITED"}],
                "results":results, "external_model_calls":0}

    def get_state(self) -> dict[str, Any]:
        from .literature_sources import projection as literature_projection
        state = self._load()
        sources = []
        for config in state["sources"]:
            cache_error = False
            try:
                cached = self._latest(config)
            except (ValueError, OSError, TypeError, KeyError):
                cached, cache_error = None, True
            date = datetime.fromisoformat(cached["retrieved_at"].replace("Z", "+00:00")) if cached else None
            stale = bool(date and (datetime.now(timezone.utc) - date).total_seconds() > config["ttl_days"] * 86400)
            sources.append(dict(config=config, cached_model_count=len({x["model_id"] for x in cached["records"]}) if cached and config["format"] != "ECB_FX_XML" else 0,
                                cached_currency_count=3 if cached and config["format"] == "ECB_FX_XML" else 0,
                                status="CACHE_INVALID" if cache_error else "STALE" if stale else "CACHED" if cached else "NOT_FETCHED",
                                retrieved_at=cached["retrieved_at"] if cached else None,
                                credential_configured=bool(config["credential_env"] and self.environment.get(config["credential_env"])),
                                last_refresh=self._last_refresh(config)))
        return dict(schema_version=state["schema_version"], revision=state["revision"], sources=sources,
                    literature=literature_projection(self.root),
                    public_preset=public_preset(), external_model_calls=0,
                    auto_refresh=automatic_metadata_enabled(), auto_refresh_interval_days=AUTO_REFRESH_INTERVAL_DAYS)

    def refresh(self, source_id: str, *, expected_revision: int, preferences: Mapping[str, Any] | None = None,
                _automatic=False, _now=None) -> dict[str, Any]:
        if source_id not in {r["source_id"] for r in default_sources()}:
            raise ValueError("EXTERNAL_SOURCE_SLOT_INVALID")
        try:
            with self._write_lock(f"refresh_{source_id}.lock", blocking=False):
                state = self._load()
                config = next(r for r in state["sources"] if r["source_id"] == source_id)
                now = _now or datetime.now(timezone.utc)
                schedule = self._schedule()["sources"].get(source_id, {})
                if (schedule.get("identity") == self._source_identity(config)
                        and schedule.get("status") == "RATE_LIMITED"):
                    try:
                        retry_at = datetime.fromisoformat(schedule["retry_after"].replace("Z", "+00:00"))
                    except (KeyError, TypeError, ValueError):
                        retry_at = now
                    if now < retry_at:
                        return {"status":"RATE_LIMITED", "source_id":source_id,
                            "reason":"EXTERNAL_METADATA_RATE_LIMITED",
                            "retry_after":schedule["retry_after"], "external_network_calls":0,
                            "previous_snapshot_preserved":self._latest(config) is not None}
                if _automatic and not self._is_due(config, now):
                    return {"status":"NOT_DUE", "source_id":source_id, "external_network_calls":0}
                if config["enabled"] and state["revision"] == expected_revision:
                    self._mark_attempt(config, now)
                with self.activity_lock:
                    self.active_sources.add(source_id)
                try:
                    result = self._refresh_once(source_id, expected_revision=expected_revision, preferences=preferences)
                    self._mark_outcome(config, now, result)
                    return result
                finally:
                    with self.activity_lock:
                        self.active_sources.discard(source_id)
        except ValueError as error:
            if str(error) != "EXTERNAL_SOURCE_WRITE_IN_PROGRESS":
                raise
            return {"status":"BUSY", "source_id":source_id, "external_network_calls":0}

    def _refresh_once(self, source_id: str, *, expected_revision: int, preferences: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = self._load()
        if type(expected_revision) is not int or state["revision"] != expected_revision:
            raise ValueError("EXTERNAL_SOURCE_REVISION_CONFLICT")
        source = next((r for r in state["sources"] if r["source_id"] == source_id), None)
        if source is None or not source["enabled"]:
            raise ValueError("EXTERNAL_SOURCE_DISABLED_OR_MISSING")
        source = validate_source(source)
        pref = preferences or {}
        try:
            previous_snapshot = self._latest(source)
            previous_snapshot_available = previous_snapshot is not None
        except (ValueError, OSError, TypeError, KeyError):
            previous_snapshot, previous_snapshot_available = None, False
        call_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        receipt_dir = self.root / "receipts"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt = dict(schema_version="ExternalMetadataFetchReceipt-v1", request_id=call_id,
                       source_id=source_id, endpoint=source["endpoint"], config_sha256=canonical_sha256(source),
                       requested_at=utc_now(), method="GET", request_payload=None,
                       external_model_calls=0, external_network_calls=0, tokens=None, cost=None,
                       cost_semantics="METADATA_NOT_MODEL_INFERENCE", provider_received_gold=False,
                       status="PRE_SEND", settings_revision=state["revision"])
        self.writer._atomic_write(receipt_dir / f"{call_id}.presend.json", receipt)
        started = time.monotonic()
        try:
            headers = {"Accept": "application/json, application/zip, application/xml, text/xml", "User-Agent": "PR-OS-Phase2New-References/1"}
            env = source["credential_env"]
            github_live = source['format'] == 'LIVEBENCH_RELEASE' and source['endpoint'] == LIVEBENCH_ENDPOINT
            github_commit, github_files, github_paths = None, [], ['src/lib/constants.js']
            if github_live:
                if env:
                    raise ValueError('EXTERNAL_METADATA_PUBLIC_CREDENTIAL_FORBIDDEN')
                headers.update(Accept='application/vnd.github+json', **{'X-GitHub-Api-Version': GITHUB_API_VERSION})
            if env:
                secret = self.environment.get(env)
                if not secret:
                    raise ValueError("EXTERNAL_SOURCE_CREDENTIAL_NOT_CONFIGURED")
                headers["x-api-key" if source["format"] == "ARTIFICIAL_ANALYSIS" else "Authorization"] = secret if source["format"] == "ARTIFICIAL_ANALYSIS" else f"Bearer {secret}"
            route_owned = (isinstance(self.transport, PublicMetadataHttpTransport) and
                self.transport.owns_public_route(LIVEBENCH_GITHUB_API + '/commits/main' if github_live else source['endpoint']))
            if isinstance(self.transport, UrllibHttpTransport) and not route_owned:
                host = urlsplit(LIVEBENCH_GITHUB_API if github_live else source['endpoint']).hostname
                addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
                    raise ValueError("EXTERNAL_SOURCE_NONPUBLIC_ADDRESS")
            # V2 AA lists are paginated. Publish only a complete, consistent
            # snapshot; each physical metadata GET has its own pre-send receipt.
            records, page_hashes, model_ids = [], [], set()
            live_documents, live_urls, live_release = [], [], None
            page, total_pages, index_version = 1, None, None
            while True:
                split = urlsplit(source["endpoint"])
                query = [(k,v) for k,v in parse_qsl(split.query) if k != 'page']
                page_url = source["endpoint"] if page == 1 else urlunsplit(
                    (split.scheme, split.netloc, split.path, urlencode(query + [('page',str(page))]), ''))
                if live_urls: page_url=live_urls[page-1]
                github_path = None
                if github_live:
                    if page == 1:
                        page_url = LIVEBENCH_GITHUB_API + '/commits/main'
                    else:
                        github_path = github_paths[page-2]
                        page_url = LIVEBENCH_GITHUB_API + '/contents/' + github_path + '?ref=' + github_commit
                for page_attempt in range(1,4):
                    physical = dict(schema_version='ExternalMetadataPhysicalRequest-v1', request_id=f'{call_id}.page{page:04d}.a{page_attempt}',
                        source_id=source_id, endpoint=page_url, config_sha256=receipt['config_sha256'],
                        requested_at=utc_now(), method='GET', page=page, attempt=page_attempt, external_model_calls=0,
                        credential_env=env, status='PRE_SEND')
                    self.writer._atomic_write(receipt_dir / f'{call_id}.page{page:04d}.a{page_attempt}.presend.json', physical)
                    receipt['external_network_calls'] += 1
                    page_started = time.monotonic()
                    try:
                        response = self.transport.request(method='GET', url=page_url, headers=headers, body=None,
                            timeout_seconds=int(pref.get('request_timeout_seconds',60)), proxy_mode=pref.get('proxy_mode','SYSTEM'),
                            proxy_address=pref.get('proxy_address',''), max_response_bytes=32*1024*1024)
                        physical.update(http_status=response.status_code, status='RECEIVED',
                            body_sha256=hashlib.sha256(response.body).hexdigest().upper())
                        if hasattr(response, "transport_evidence"):
                            physical["transport"] = response.transport_evidence
                        limited = metadata_rate_limit(response)
                        if limited:
                            physical.update(limited)
                            raise MetadataRateLimitError(limited["retry_after"])
                        if response.status_code in (408,425,429) or 500 <= response.status_code < 600:
                            raise ValueError(f'EXTERNAL_METADATA_HTTP_{response.status_code}')
                        if github_live:
                            receipt['http_status'] = response.status_code
                            if response.status_code != 200:
                                raise ValueError(f'EXTERNAL_METADATA_HTTP_{response.status_code}')
                            if page == 1:
                                github_commit = _github_commit(response.body)
                                physical['verified_commit'] = github_commit
                            else:
                                decoded_body, file_identity = _github_content(response.body, github_path, github_commit)
                                file_identity['endpoint'] = page_url
                                github_files.append(file_identity)
                                physical['verified_file'] = file_identity
                    except Exception as error:
                        physical.update(status='ERROR', reason=metadata_error_code(error))
                        if isinstance(error, MetadataRateLimitError):
                            physical.update(status='RATE_LIMITED', retry_after=error.retry_after)
                        if page_attempt == 3 or not transient_metadata_error(physical['reason']):
                            raise
                    finally:
                        physical['duration_ms'] = round((time.monotonic()-page_started)*1000)
                        physical['receipt_sha256'] = canonical_sha256(physical)
                        self.writer._atomic_write(receipt_dir / f'{call_id}.page{page:04d}.a{page_attempt}.result.json', physical)
                    if physical['status'] != 'ERROR':
                        break
                    time.sleep(0.5 * (2 ** (page_attempt-1)))
                receipt['http_status'] = response.status_code
                if response.status_code != 200:
                    raise ValueError(f'EXTERNAL_METADATA_HTTP_{response.status_code}')
                page_hashes.append(physical['body_sha256'])
                if source['format']=='LIVEBENCH_RELEASE':
                    if github_live and page == 1:
                        page += 1
                        continue
                    live_documents.append(decoded_body if github_live else response.body)
                    if len(live_documents) == 1:
                        live_release, canonical_urls = release_urls(source['endpoint'], live_documents[0])
                        if github_live:
                            github_paths = ['src/lib/constants.js'] + [u.split('/main/',1)[1] for u in canonical_urls[1:]]
                        else:
                            live_urls = canonical_urls
                    if len(live_documents)<3: page+=1;continue
                    rows=normalize_payload(source,dict(release=live_release,
                        categories=json.loads(live_documents[1].decode('utf-8-sig')),
                        table_csv=live_documents[2].decode('utf-8-sig')))
                else:
                    rows = normalize_payload(source, response.body)
                records.extend(rows)
                if source['format'] != 'ARTIFICIAL_ANALYSIS': break
                payload = json.loads(response.body.decode('utf-8-sig'))
                pagination = payload.get('pagination')
                if pagination is None and page == 1: break  # documented legacy API
                if not isinstance(pagination,dict): raise ValueError('EXTERNAL_METADATA_PAGINATION_INVALID')
                count = pagination.get('total_pages')
                if (type(count) is not int or not 1 <= count <= 100 or pagination.get('page') != page
                        or type(pagination.get('has_more')) is not bool or pagination['has_more'] != (page < count)
                        or not payload.get('data')):
                    raise ValueError('EXTERNAL_METADATA_PAGINATION_INVALID')
                if page == 1: total_pages, index_version = count, payload.get('intelligence_index_version')
                if count != total_pages or payload.get('intelligence_index_version') != index_version:
                    raise ValueError('EXTERNAL_METADATA_CHANGED_DURING_PAGINATION')
                for item in payload['data']:
                    identity = item.get('id') or item.get('slug')
                    if not identity or identity in model_ids:
                        raise ValueError('EXTERNAL_METADATA_DUPLICATE_PAGE_MODEL')
                    model_ids.add(identity)
                if len(records) > 20000: raise ValueError('EXTERNAL_RESPONSE_ITEM_ENVELOPE_EXCEEDED')
                if page == total_pages: break
                page += 1
            if not records:
                raise ValueError("EXTERNAL_METADATA_EMPTY")
            if source['format']=='OPENROUTER' and source['endpoint']=='https://openrouter.ai/api/v1/models':
                from .reference_pricing import enrich_records
                price_attempts={}
                price_deadline=time.monotonic()+20
                def fetch_price(model_id, endpoint):
                    remaining=price_deadline-time.monotonic()
                    if remaining <= 0:
                        raise ValueError('REFERENCE_PRICE_REFRESH_BUDGET_EXHAUSTED')
                    price_attempts[model_id]=price_attempts.get(model_id,0)+1
                    attempt=price_attempts[model_id]
                    identifier=hashlib.sha256(model_id.encode()).hexdigest()[:24]+f'.attempt{attempt}'
                    physical=dict(schema_version='ExternalMetadataPhysicalRequest-v1',request_id=f'{call_id}.price.{identifier}',
                        source_id=source_id,endpoint=endpoint,model_id=model_id,attempt=attempt,config_sha256=receipt['config_sha256'],
                        requested_at=utc_now(),method='GET',external_model_calls=0,credential_env='',status='PRE_SEND')
                    try:
                        self.writer._atomic_write(receipt_dir/f'{call_id}.price.{identifier}.presend.json',physical)
                    except OSError as error:
                        raise ValueError('REFERENCE_PRICE_LOCAL_WRITE_FAILED') from error
                    begin=time.monotonic()
                    try:
                        response=self.transport.request(method='GET',url=endpoint,
                            headers={'Accept':'application/json','User-Agent':'Memo-Reference-Pricing/1'},body=None,
                            timeout_seconds=max(1,min(int(pref.get('request_timeout_seconds',60)),math.ceil(remaining))),
                            proxy_mode=pref.get('proxy_mode','SYSTEM'),
                            proxy_address=pref.get('proxy_address',''),max_response_bytes=32*1024*1024)
                        physical.update(status='RECEIVED',http_status=response.status_code,
                            body_sha256=hashlib.sha256(response.body).hexdigest().upper())
                        if hasattr(response,'transport_evidence'):physical['transport']=response.transport_evidence
                        if response.status_code!=200:raise ValueError(f'EXTERNAL_METADATA_HTTP_{response.status_code}')
                        return json.loads(response.body),dict(endpoint_url=endpoint,body_sha256=physical['body_sha256'],
                            retrieved_at=physical['requested_at'],request_id=physical['request_id'])
                    except Exception as error:
                        physical.update(status='ERROR',reason=metadata_error_code(error))
                        raise ValueError(physical['reason']) from error
                    finally:
                        physical['duration_ms']=round((time.monotonic()-begin)*1000)
                        physical['receipt_sha256']=canonical_sha256(physical)
                        try:
                            self.writer._atomic_write(receipt_dir/f'{call_id}.price.{identifier}.result.json',physical)
                        except OSError as error:
                            raise ValueError('REFERENCE_PRICE_LOCAL_WRITE_FAILED') from error
                records=enrich_records(records,fetch_price,
                    prior_records=(previous_snapshot or {}).get('records',[]),
                    max_checks=16,deadline_seconds=20,max_workers=2)
                pricing_receipts=list(receipt_dir.glob(f'{call_id}.price.*.result.json'))
                receipt['external_network_calls']+=len(pricing_receipts)
                prices=[row['operational']['regular_pricing'] for row in records if 'operational' in row]
                receipt['regular_price_summary']={'verified':sum(p['status']=='VERIFIED' for p in prices),
                    'unverified':sum(p['status']!='VERIFIED' for p in prices),
                    'endpoint_models_checked':len(price_attempts),
                    'endpoint_network_calls':len(pricing_receipts),
                    'cache_reused':sum(bool(p.get('cache_reused')) for p in prices),
                    'deferred':sum(p.get('reason')=='REFERENCE_PRICE_REFRESH_DEFERRED' for p in prices),
                    'max_endpoint_models_per_refresh':16,'deadline_seconds':20,'max_workers':2}
            with self._write_lock():
                if self._load()["revision"] != state["revision"]:
                    raise ValueError("EXTERNAL_SOURCE_CHANGED_DURING_REFRESH")
                snapshot = dict(schema_version=SCHEMA, config_sha256=receipt["config_sha256"],
                                source_identity_sha256=self._source_identity(source), retrieved_at=utc_now(),
                                body_sha256=page_hashes[0] if len(page_hashes)==1 else canonical_sha256(page_hashes),
                                page_body_sha256s=page_hashes, records=records,
                                attribution=source["display_name"] + " | " + source["endpoint"],
                                redistributed_snapshot_approved=False)
                if github_live:
                    snapshot['upstream_identity'] = dict(repository='LiveBench/new-livebench',
                        commit=github_commit, api_version=GITHUB_API_VERSION, files=github_files)
                    receipt['upstream_identity'] = snapshot['upstream_identity']
                snapshot["sha256"] = canonical_sha256(snapshot)
                directory = self.root / "snapshots" / source_id
                directory.mkdir(parents=True, exist_ok=True)
                self.writer._atomic_write(directory / f"{call_id}.json", snapshot)
            receipt.update(status="PASS", record_count=len(records), snapshot_sha256=snapshot["sha256"])
        except Exception as error:
            # Do not reflect server bodies, headers, key values, or arbitrary exceptions.
            code = metadata_error_code(error)
            receipt.update(status="RATE_LIMITED" if isinstance(error, MetadataRateLimitError) else "ERROR",
                           reason=code, previous_snapshot_preserved=previous_snapshot_available)
            if isinstance(error, MetadataRateLimitError):
                receipt["retry_after"] = error.retry_after
        finally:
            receipt["duration_ms"] = round((time.monotonic() - started) * 1000)
            receipt["receipt_sha256"] = canonical_sha256(receipt)
            self.writer._atomic_write(receipt_dir / f"{call_id}.result.json", receipt)
            try:
                self._store_last_refresh(source, receipt)
            except OSError:
                pass
        return receipt

    def public_catalog(self) -> dict[str, Any]:
        """Validated local snapshots only; never refresh, migrate or resolve keys."""
        rows, unavailable = [], []
        for source in self._load()["sources"]:
            try:
                snapshot = self._latest(source)
                if not snapshot:
                    continue
                retrieved = datetime.fromisoformat(snapshot["retrieved_at"].replace("Z", "+00:00"))
                stale = (datetime.now(timezone.utc) - retrieved).total_seconds() > source["ttl_days"] * 86400
                for item in snapshot["records"]:
                    row = {k: deepcopy(item.get(k)) for k in (
                        "source_id", "source_name", "upstream_source", "model_id", "model_name",
                        "metrics", "version", "source_as_of")}
                    row.update(snapshot_sha256=snapshot["sha256"], retrieved_at=snapshot["retrieved_at"], stale=stale)
                    rows.append(row)
            except (ValueError, OSError, TypeError, KeyError):
                unavailable.append(source["source_id"])
        return {"rows": rows, "unavailable_sources": unavailable, "external_network_calls": 0}

    def references(self, role: str, target: Mapping[str, Any], *, internal_exam_score: Any = None) -> dict[str, Any]:
        rows = []
        unavailable = []
        for source in self._load()["sources"]:
            if source["format"] == "ECB_FX_XML":
                continue
            try:
                snapshot = self._latest(source)
            except (ValueError, OSError, TypeError, KeyError):
                unavailable.append(source["source_id"])
                continue
            if snapshot:
                for item in snapshot["records"]:
                    row = deepcopy(item)
                    row["retrieved_at"] = snapshot["retrieved_at"]
                    date = datetime.fromisoformat(snapshot["retrieved_at"].replace("Z", "+00:00"))
                    row["stale"] = (datetime.now(timezone.utc) - date).total_seconds() > source["ttl_days"] * 86400
                    rows.append(row)
        result = role_references(role, target, rows, internal_exam_score=internal_exam_score)
        result['source_slots']=[{k:source[k] for k in ('source_id','display_name','format')} for source in self._load()['sources'] if source['format'] != 'ECB_FX_XML']
        result["unavailable_sources"] = unavailable
        return result
