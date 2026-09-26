"""Read-only bottom-panel projections. Never send requests, grade or write history.

Ranks have a source/role/version cohort, not a cross-role intelligence score.
Billing reads physical receipts only; estimates, unknown amounts and currencies
stay separate. No prompts, outputs, Gold, credentials or raw paths leave here.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping
from .external_sources import _reference_percentiles

ROLE_NAMES = {"ingest": "文档处理", "card_distill": "生成卡片", "judgment_review": "核对分析",
    "document_processing": "文档处理", "ocr": "文档处理", "chunk_embedding": "向量化",
    "embedding": "向量化", "card_distiller": "生成卡片", "card": "生成卡片",
    "transport_review": "核对卡片", "card_reviewer": "核对卡片", "context_pack": "整理分析材料",
    "reranker": "整理分析材料", "analysis_primary": "分析", "analysis": "分析",
    "analysis_reviewer": "核对分析"}
METRICS = {"eci": "ECI", "livebench_global": "LiveBench", "artificial_analysis_intelligence_index": "Intelligence Index",
    "intelligence_index": "Intelligence Index"}
CHANNELS = {"API", "CLI", "LOCAL"}

def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

def text(value, limit=240):
    return str(value or "").replace("\x00", "")[:limit]

def key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]

def ranking_projection(catalog, history, external):
    groups = {}
    def group(identity, **props):
        gid = key(identity)
        if gid not in groups: groups[gid] = dict(id=gid, rows=[], **props)
        return groups[gid]
    for origin, entries in (("考试", catalog.get("entries", [])), ("本地实考", history)):
        for item in entries:
            score = number(item.get("score_exact", item.get("score")))
            if score is None or score > 100: continue
            role = text(item.get("node_id") or item.get("role_id"))
            cohort = text(item.get("comparison_cohort_id"), 1024)
            eligible = bool(cohort and item.get("horizontal_comparison_eligible") is True and origin == "考试")
            identity = (origin, role, cohort if eligible else "unranked")
            g = group(identity, source=origin, role=ROLE_NAMES.get(role, role) or "—",
                cohort=cohort if eligible else "", version=text(item.get("sample_revision")),
                metric="实考", ranked=eligible, reference_only=False)
            effort = item.get("thinking_mode") or item.get("tier")
            if not isinstance(effort, str):
                effort = item.get("thinking") if isinstance(item.get("thinking"), str) else ("思考" if item.get("thinking") is True else "")
            g["rows"].append(dict(model=text(item.get("model_name")), channel=text(item.get("profile_kind")),
                thinking=text(effort),
                score=score, raw_score=score, percent=False, rank=None,
                recorded_at=text(item.get("recorded_at") or item.get("source_as_of")),
                profile_identity=text(item.get("target_sha256") or item.get("model_digest")),
                stale=False))
    for item in external:
        for metric, label in METRICS.items():
            raw = item.get("metrics", {}).get(metric)
            score = raw.get("value") if isinstance(raw, Mapping) else raw
            if type(score) not in (int, float) or not math.isfinite(score): continue
            percent = _reference_percentiles(item, external).get(metric, {}).get("value")
            identity = ("external", item.get("source_id"), item.get("upstream_source"), metric,
                        item.get("version"), item.get("snapshot_sha256"))
            g = group(identity, source=text(item.get("source_name") or item.get("source_id")),
                role="", cohort=text(item.get("snapshot_sha256")), version=text(item.get("version")),
                metric=label, ranked=True, reference_only=True)
            g["rows"].append(dict(model=text(item.get("model_name") or item.get("model_id")),
                channel="", thinking="", score=percent, raw_score=score, percent=True, rank=None,
                recorded_at=text(item.get("source_as_of") or item.get("retrieved_at")),
                profile_identity=text(item.get("model_id")), stale=bool(item.get("stale"))))
    for g in groups.values():
        # Only exact duplicate projections collapse. Different execution profiles
        # remain separate, and conflicting duplicate scores are never "best picked".
        distinct = {}
        for row in g["rows"]: distinct.setdefault(key(row), row)
        g["rows"] = list(distinct.values())
        conflicts = defaultdict(set)
        for row in g["rows"]:
            conflicts[(row["model"], row["channel"], row["thinking"], row["profile_identity"])].add(row["raw_score"])
        for row in g["rows"]:
            row["conflict"] = len(conflicts[(row["model"],row["channel"],row["thinking"],row["profile_identity"])]) > 1
        g["rows"].sort(key=lambda row: (-row["raw_score"] if g["ranked"] and not row["conflict"] else 0, row["model"], row["thinking"]))
        ranked = [row for row in g["rows"] if not row["conflict"]]
        for row in ranked:
            if g["ranked"]: row["rank"] = 1 + sum(x["raw_score"] > row["raw_score"] for x in ranked)
    return dict(groups=list(groups.values()), reference_only=True, cross_source_comparison_forbidden=True,
                cross_role_comparison_forbidden=True, external_model_calls=0)

def _read(path, base):
    if not path.resolve().is_relative_to(base.resolve()): raise ValueError("OUTSIDE_RECEIPT_ROOT")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError("NOT_OBJECT")
    return value

def _amount(receipt):
    actual = receipt.get("actual_cost")
    currency = text(receipt.get("currency") or receipt.get("cost_currency")).upper()
    if isinstance(actual, Mapping):
        currency = text(actual.get("currency")).upper()
        actual = actual.get("amount")
    cost = number(actual)
    if cost is not None and currency in {"USD", "CNY", "EUR", "JPY", "GBP"}:
        return cost, currency, "ACTUAL"
    for field, unit in (("estimated_cost_cny", "CNY"), ("estimated_cost_usd", "USD")):
        cost = number(receipt.get(field))
        if cost is not None: return cost, unit, "ESTIMATED"
    return None, "", "UNKNOWN"

def billing_projection(exam_root, phase1_root):
    exam_root, phase1_root = Path(exam_root), Path(phase1_root)
    files = []
    # Explicit physical metadata locations; never recurse through arbitrary files.
    for parent in ("*/", "*/*/"):
        for folder in ("attempts", "receipts"):
            for path in (exam_root / "workflow_exams").glob(parent + folder + "/*.json"):
                if not path.name.startswith("logical_"):
                    files.append((path, exam_root, "考试"))
    for path in phase1_root.glob("*/model_calls/call-*.post.json"):
        files.append((path, phase1_root, "文献"))
    slots, unreadable, conflicts = {}, 0, 0
    # A matching attempt is preferred: it also preserves failed requests.
    files.sort(key=lambda item: (item[0].parent.name != "attempts", str(item[0])))
    for path, base, origin in files:
        try:
            wrapper = _read(path, base)
            nested = wrapper.get("execution_receipt")
            receipt = dict(nested) if isinstance(nested, dict) else wrapper
            pre = {}
            if origin == "文献":
                pre_path = path.with_name(path.name.replace(".post.json", ".pre.json"))
                if pre_path.is_file(): pre = _read(pre_path, base)
            model = text(receipt.get("requested_model") or wrapper.get("requested_model") or pre.get("requested_model")
                         or receipt.get("returned_model") or wrapper.get("returned_model"))
            channel = text(receipt.get("profile_kind") or wrapper.get("profile_kind") or pre.get("profile_kind")).upper()
            usage = receipt.get("token_usage") or receipt.get("usage") or {}
            if not isinstance(usage, Mapping): usage = {}
            cost, currency, kind = _amount(receipt)
            physical = wrapper.get("physical_index") or wrapper.get("call_id") or path.stem
            binding = text(wrapper.get("pre_send_claim_sha256") or receipt.get("pre_send_claim_sha256"))
            # Same run+stage+physical call is one attempt; never deduplicate by usage.
            identity = (str(path.parent.parent.resolve()), binding or str(physical))
            row = dict(id=key(identity), model=model, channel=channel if channel in CHANNELS else "",
                status=text(wrapper.get("status") or receipt.get("status")).upper(),
                input_tokens=number(usage.get("prompt_tokens", usage.get("input_tokens"))),
                output_tokens=number(usage.get("completion_tokens", usage.get("output_tokens"))),
                cost=cost, currency=currency, cost_kind=kind, origin=origin,
                recorded_at=text(receipt.get("finished_at") or receipt.get("requested_at") or wrapper.get("recorded_at")),
                observed_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                timestamp_is_file_time=not bool(receipt.get("finished_at") or receipt.get("requested_at") or wrapper.get("recorded_at")))
            if identity in slots:
                previous = slots[identity]
                if previous["cost"] is not None and cost is not None and (previous["cost"],previous["currency"]) != (cost,currency):
                    previous.update(cost=None,currency="",cost_kind="CONFLICT")
                    conflicts += 1
                continue
            slots[identity] = row
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            unreadable += 1
    rows = sorted(slots.values(), key=lambda row: row["recorded_at"] or row["observed_at"], reverse=True)
    totals = {}
    for row in rows:
        if row["cost"] is None: continue
        total = totals.setdefault(row["currency"], dict(currency=row["currency"],actual=None,estimated=None))
        field = "actual" if row["cost_kind"] == "ACTUAL" else "estimated"
        total[field] = float(Decimal(str(total[field] or 0)) + Decimal(str(row["cost"])))
    return dict(rows=rows, totals=list(totals.values()), unknown_cost_calls=sum(row["cost"] is None for row in rows),
        failed_calls=sum(row["status"] in {"FAIL","FAILED","ERROR"} for row in rows),
        unreadable_records=unreadable, conflicting_records=conflicts, external_model_calls=0,
        scope="CURRENT_PROFILE_PHASE1_AND_EXAM_PHYSICAL_RECEIPTS", complete_account_invoice=False)
