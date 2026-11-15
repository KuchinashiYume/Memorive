"""Read-only product adapter for the reviewed artifact-location accounting panel."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

from .commerce_context import build_shared_context
from .projection import build_projection


ACCOUNTING_METHODS = frozenset({
    "accounting.get_projection_v3",
    "accounting.refresh_projection_v3",
    "models.get_commerce_context_v1",
})


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _clean(value: Any, limit: int = 192) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _timestamp(value: Any, fallback: Path) -> str:
    rendered = str(value or "").strip()
    if rendered:
        try:
            parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _decimal(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return format(parsed, "f")


class AccountingProductController:
    """Project current local settings and physical attempts without network I/O."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Mapping[str, Any]],
        local_model_registry: Any,
        leaderboard_provider: Callable[[], Mapping[str, Any]],
        exam_root: Path,
        core_root: Path,
        settings_root: Path | None = None,
    ) -> None:
        self.settings_provider = settings_provider
        self.local_model_registry = local_model_registry
        self.leaderboard_provider = leaderboard_provider
        self.exam_root = Path(exam_root).resolve(strict=False)
        self.core_root = Path(core_root).resolve(strict=False)
        self.settings_root = Path(settings_root or self.exam_root.parent / 'settings').resolve(strict=False)
        self.lock = RLock()

    @staticmethod
    def _profiles(settings_state: Mapping[str, Any], local_state: Mapping[str, Any]) -> list[dict[str, Any]]:
        settings = settings_state.get("settings") or {}
        rows: list[dict[str, Any]] = []
        for item in settings.get("model_services") or []:
            if not isinstance(item, Mapping):
                continue
            ref = _clean(item.get("config_id"))
            model = _clean(item.get("model_name"), 160)
            provider = _clean(item.get("provider"), 80)
            if not ref or not model or not provider:
                continue
            rows.append({
                "profile_ref": ref,
                "profile_kind": "API",
                "provider_label": provider,
                "model_label": _clean(item.get("display_name") or model, 96),
                "model_id": model,
                "availability": "AVAILABLE" if item.get("connection_status") == "AVAILABLE" else "UNAVAILABLE",
                "leaderboard_model_key": item.get("leaderboard_model_key"),
                "settings_profile_key": ref,
                "local_model_key": None,
            })
        for service in settings.get("cli_services") or []:
            if not isinstance(service, Mapping):
                continue
            enabled = service.get("enabled") is True
            provider = _clean(service.get("display_name") or service.get("adapter_id") or "CLI", 80)
            for item in service.get("models") or []:
                if not isinstance(item, Mapping):
                    continue
                ref = _clean(item.get("profile_ref"))
                model = _clean(item.get("model_name"), 160)
                if not ref or not model:
                    continue
                availability = "DISABLED" if not enabled else "AVAILABLE" if item.get("connection_status") == "AVAILABLE" else "UNAVAILABLE"
                rows.append({
                    "profile_ref": ref,
                    "profile_kind": "CLI",
                    "provider_label": provider,
                    "model_label": _clean(item.get("display_name") or model, 96),
                    "model_id": model,
                    "availability": availability,
                    "leaderboard_model_key": item.get("leaderboard_model_key"),
                    "settings_profile_key": ref,
                    "local_model_key": None,
                })
        for item in local_state.get("recognized_models") or []:
            if not isinstance(item, Mapping):
                continue
            ref = _clean(item.get("profile_ref"))
            model = _clean(item.get("model_name"), 160)
            provider = _clean(item.get("endpoint_name") or item.get("provider") or "本地", 80)
            if not ref or not model:
                continue
            rows.append({
                "profile_ref": ref,
                "profile_kind": "LOCAL",
                "provider_label": provider,
                "model_label": _clean(item.get("display_name") or model, 96),
                "model_id": model,
                "availability": "AVAILABLE" if item.get("execution_eligible") is True else "UNAVAILABLE",
                "leaderboard_model_key": item.get("leaderboard_model_key"),
                "settings_profile_key": None,
                "local_model_key": ref,
            })
        return rows

    @staticmethod
    def _fx(leaderboard: Mapping[str, Any]) -> dict[str, Any]:
        raw = leaderboard.get("fx")
        if not isinstance(raw, Mapping):
            return {
                "id": None, "source": None, "rate_date": None, "fetched_at": None,
                "status": "MISSING", "units_per_usd": {"USD": 1, "CNY": 1, "JPY": 1},
            }
        rates = raw.get("unitsPerUsd") or raw.get("units_per_usd") or {}
        valid = isinstance(rates, Mapping) and set(rates) >= {"USD", "CNY", "JPY"}
        normalized: dict[str, Any] = {}
        if valid:
            for code in ("USD", "CNY", "JPY"):
                value = _decimal(rates.get(code))
                if value is None or Decimal(value) <= 0:
                    valid = False
                    break
                normalized[code] = int(Decimal(value)) if Decimal(value) == int(Decimal(value)) else float(Decimal(value))
        return {
            "id": _clean(raw.get("id"), 160) or None,
            "source": _clean(raw.get("source"), 160) or None,
            "rate_date": raw.get("rateDate") or raw.get("rate_date"),
            "fetched_at": raw.get("fetchedAt") or raw.get("fetched_at"),
            "status": "INVALID" if not valid else "EXPIRED" if raw.get("refreshFailed") is True else "CACHED",
            "units_per_usd": normalized if valid else {"USD": 1, "CNY": 1, "JPY": 1},
        }

    def commerce_context(self) -> dict[str, Any]:
        settings_state = deepcopy(dict(self.settings_provider()))
        local_state = deepcopy(dict(self.local_model_registry.call("local_models.bootstrap", {})))
        leaderboard = deepcopy(dict(self.leaderboard_provider()))
        profiles = self._profiles(settings_state, local_state)
        known = {row['profile_ref'] for row in profiles}
        for _priority, path, root, _kind, _job in self._candidate_files():
            try:
                wrapper, pre, receipt = self._receipt(path, root)
                ref = _clean(wrapper.get('profile_ref') or receipt.get('profile_ref') or pre.get('profile_ref'))
                kind = wrapper.get('profile_kind') or receipt.get('profile_kind') or pre.get('profile_kind')
                model = _clean(receipt.get('requested_model') or wrapper.get('requested_model') or pre.get('requested_model') or (pre.get('model') or {}).get('model_name'),160)
                if ref and ref not in known and kind in {'API','CLI','LOCAL'} and model:
                    profiles.append({'profile_ref':ref,'profile_kind':kind,'provider_label':_clean(pre.get('provider_label') or kind,80),
                        'model_label':model[:96],'model_id':model,'availability':'UNAVAILABLE','leaderboard_model_key':None,
                        'settings_profile_key':ref if kind!='LOCAL' else None,'local_model_key':ref if kind=='LOCAL' else None})
                    known.add(ref)
            except (OSError,ValueError,TypeError,KeyError):pass
        currency = str((leaderboard.get("preferences") or {}).get("currency") or "USD").upper()
        if currency == "RMB":
            currency = "CNY"
        revision_input = {
            "settings_revision": settings_state.get("revision"),
            "local_models": local_state.get("recognized_models") or [],
            "leaderboard_revision": leaderboard.get("projection_key") or leaderboard.get("revision"),
            "profiles": profiles,
        }
        return build_shared_context(
            profiles,
            preferred_currency=currency,
            fx_snapshot=self._fx(leaderboard),
            settings_revision=settings_state.get("revision", 0),
            local_models_revision=_hash(local_state.get("recognized_models") or []),
            leaderboard_catalog_revision=leaderboard.get("projection_key") or leaderboard.get("source_revision") or leaderboard.get("revision"),
            revision=f"commerce:{_hash(revision_input)[:24]}",
        )

    @staticmethod
    def _read(path: Path, root: Path) -> dict[str, Any]:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved.stat().st_size > 5_000_000:
            raise ValueError("ACCOUNTING_RECORD_OUTSIDE_ALLOWLIST")
        value = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("ACCOUNTING_RECORD_INVALID")
        return value

    @staticmethod
    def _cost(receipt: Mapping[str, Any], profile_kind: str) -> tuple[str, str | None, str | None]:
        actual = receipt.get("actual_cost")
        currency = _clean(receipt.get("currency") or receipt.get("cost_currency"), 3).upper() or None
        if isinstance(actual, Mapping):
            currency = _clean(actual.get("currency"), 3).upper() or None
            actual = actual.get("amount")
        value = _decimal(actual)
        if value is not None and currency:
            return "ACTUAL", value, currency
        for field, code in (("actual_cost_usd", "USD"), ("actual_cost_cny", "CNY"), ("actual_cost_jpy", "JPY")):
            value = _decimal(receipt.get(field))
            if value is not None:
                return "ACTUAL", value, code
        if profile_kind == "API":
            for field, code in (("estimated_cost_cny", "CNY"), ("estimated_cost_usd", "USD"), ("estimated_cost_jpy", "JPY")):
                value = _decimal(receipt.get(field))
                if value is not None:
                    return "ESTIMATED", value, code
        return "NOT_ASSESSED", None, None

    def _candidate_files(self) -> Iterable[tuple[int, Path, Path, str, str | None]]:
        for path in (self.exam_root / 'model_call_ledger' / 'executions').glob('*.post.json'):
            yield -1, path, self.exam_root, 'ACCOUNTING_ENVELOPE', None
        for folder in ('report_profiles','research_chat_profiles','research_embedding_profiles'):
            for path in (self.settings_root / folder).glob('*.result.json'):
                yield 1, path, self.settings_root, 'ACCOUNTING_ENVELOPE', None
        exam_base = self.exam_root / "workflow_exams"
        if exam_base.is_dir():
            for path in exam_base.glob('*/round-*.result.json'):
                yield 0, path, self.exam_root, 'ACCOUNTING_ENVELOPE', path.parent.name
            for folder, priority in (("attempts", 0), ("receipts", 1)):
                for path in exam_base.glob(f"**/{folder}/*.json"):
                    if not path.name.startswith("logical_"):
                        yield priority, path, self.exam_root, "ACCOUNTING_ENVELOPE", None
        if self.core_root.is_dir():
            for path in self.core_root.glob("*/model_calls/call-*.post.json"):
                yield 0, path, self.core_root, "ACCOUNTING_ENVELOPE", path.parent.parent.name
            for path in self.core_root.glob('*/logic_calls/call-*.post.json'):
                yield 0, path, self.core_root, 'ACCOUNTING_ENVELOPE', path.parent.parent.name

    def _receipt(self,path,root):
        wrapper=self._read(path,root)
        pre_path=path.with_name(path.name.replace('.post.json','.pre.json') if path.name.endswith('.post.json') else path.name.replace('.result.json','.json'))
        pre=self._read(pre_path,root) if pre_path!=path and pre_path.is_file() else {}
        nested=wrapper.get('execution_receipt')
        return wrapper,pre,dict(nested) if isinstance(nested,Mapping) else wrapper

    def _attempts(self, context: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        profiles = {row["profile_ref"]: row for row in context.get("models") or []}
        attempts: dict[tuple[str, str], dict[str, Any]] = {}
        diagnostics = {"unreadable": 0, "unbound": 0, "unknown": 0, "usage_unknown": 0}
        for _priority, path, root, source_kind, inferred_job in sorted(self._candidate_files(), key=lambda row: (row[0], str(row[1]))):
            try:
                wrapper, pre, receipt = self._receipt(path, root)
                if str(wrapper.get('purpose') or pre.get('purpose') or '').startswith('verify_'):
                    continue
                profile_ref = _clean(wrapper.get("profile_ref") or receipt.get("profile_ref") or pre.get("profile_ref"))
                if not profile_ref:
                    diagnostics["unbound"] += 1
                    continue
                profile = profiles.get(profile_ref)
                if profile is None:
                    diagnostics["unknown"] += 1
                    continue
                physical = _clean(wrapper.get("physical_call_id") or wrapper.get("call_id") or wrapper.get("physical_index") or wrapper.get("sequence") or pre.get("sequence") or path.stem)
                if path.parent.name=='logic_calls':physical='logic:'+physical
                run_key = str(path.parents[1].resolve(strict=False))
                execution_id = receipt.get('execution_id') or wrapper.get('execution_id')
                identity = ('native', str(execution_id)) if execution_id else (run_key, physical)
                if identity in attempts:
                    continue
                usage = receipt.get("token_usage") or receipt.get("usage") or {}
                if not isinstance(usage, Mapping):
                    usage = {}
                if not usage: diagnostics['usage_unknown'] += 1
                output = _integer(usage.get("completion_tokens", usage.get("output_tokens")))
                reasoning = _integer(usage.get("reasoning_tokens", usage.get("reasoning_token_count")))
                if reasoning > output:
                    diagnostics["unreadable"] += 1
                    continue
                cost_status, cost_value, currency = self._cost(receipt, profile["profile_kind"])
                status = str(wrapper.get("status") or receipt.get("status") or "").upper()
                attempts[identity] = {
                    "attempt_id": f"attempt:{_hash(identity)[:24]}",
                    "occurred_at": _timestamp(receipt.get("finished_at") or receipt.get("requested_at") or wrapper.get("recorded_at") or wrapper.get("created_at") or pre.get("created_at"), path),
                    "job_id": _clean(wrapper.get("job_id") or receipt.get("job_id") or pre.get("job_id") or pre.get('run_id') or inferred_job) or None,
                    "profile_ref": profile_ref,
                    "profile_kind": profile["profile_kind"],
                    "provider_label": profile["provider_label"],
                    "model_label": profile["model_label"],
                    "model_id": profile["model_id"],
                    "outcome": "SUCCEEDED" if status in {"PASS", "SUCCEEDED", "COMPLETED", "SUCCESS"} else "FAILED",
                    "tokens_input": _integer(usage.get("prompt_tokens", usage.get("input_tokens"))),
                    "tokens_output": output,
                    "tokens_reasoning": reasoning,
                    "cost_status": cost_status,
                    "cost_value": cost_value,
                    "currency": currency,
                    "source_kind": source_kind,
                }
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                diagnostics["unreadable"] += 1
        return list(attempts.values()), diagnostics

    @staticmethod
    def _apply_diagnostics(projection: dict[str, Any], diagnostics: Mapping[str, int]) -> dict[str, Any]:
        mapping = {
            "unreadable": "UNREADABLE_ROWS_EXCLUDED",
            "unbound": "UNBOUND_PROFILE_ROWS_EXCLUDED",
            "unknown": "UNKNOWN_PROFILE_ROWS_EXCLUDED",
            "usage_unknown": "TOKEN_USAGE_PARTIAL",
        }
        reasons = projection["source_summary"]["reason_codes"]
        for key, code in mapping.items():
            if diagnostics.get(key) and code not in reasons:
                reasons.append(code)
        if any(diagnostics.values()):
            if projection["status"] == "READY":
                projection["status"] = "PARTIAL"
            # Unrelated legacy omissions do not hide a known current task.
        return projection

    def job_call_counts(self) -> dict[str, dict[str, Any]]:
        """Use the same deduplicated receipts as the accounting panel."""
        attempts, diagnostics = self._attempts(self.commerce_context())
        result: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            job_id = attempt.get('job_id')
            if not job_id:
                continue
            row = result.setdefault(job_id, {'model_calls':0, 'external_model_calls':0,
                'model_call_counts_complete':not any(diagnostics[k] for k in ('unreadable','unbound','unknown'))})
            row['model_calls'] += 1
            row['external_model_calls'] += int(attempt['profile_kind'] in {'API','CLI'})
        return result

    def projection(self, params: Mapping[str, Any], *, refreshed: bool) -> dict[str, Any]:
        context = self.commerce_context()
        attempts, diagnostics = self._attempts(context)
        projection = build_projection(attempts, params, context, refreshed=refreshed)
        return self._apply_diagnostics(projection, diagnostics)

    def call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if method not in ACCOUNTING_METHODS:
            raise ValueError("ACCOUNTING_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        if any(str(key).startswith("_") for key in accepted):
            raise ValueError("ACCOUNTING_PRIVATE_PARAM_FORBIDDEN")
        with self.lock:
            if method == "models.get_commerce_context_v1":
                if accepted:
                    raise ValueError("MODEL_COMMERCE_PARAMS_INVALID")
                return self.commerce_context()
            return self.projection(accepted, refreshed=method == "accounting.refresh_projection_v3")


__all__ = ["ACCOUNTING_METHODS", "AccountingProductController"]
