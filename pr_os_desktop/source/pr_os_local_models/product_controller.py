from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pr_os_settings.model_capabilities import infer_api_model_capability


LOCAL_MODEL_METHODS = frozenset(
    {
        "local_models.bootstrap",
        "local_models.list",
        "local_models.discover",
        "local_models.endpoint_save",
        "local_models.endpoint_remove",
        "local_models.verify",
        "local_models.effect_metrics",
    }
)

_KIND_ADAPTERS = {
    "ollama": {
        "adapter_family": "OLLAMA_NATIVE",
        "model_list_path": "/api/tags",
        "known_implementations": ["Ollama"],
    },
    "openai_compatible": {
        "adapter_family": "OPENAI_COMPATIBLE",
        "model_list_path": "/v1/models",
        "known_implementations": ["LM Studio", "vLLM", "LocalAI", "Jan"],
    },
    "llama_cpp": {
        "adapter_family": "OPENAI_COMPATIBLE",
        "model_list_path": "/v1/models",
        "known_implementations": ["llama.cpp Server"],
    },
}
_KINDS = frozenset(_KIND_ADAPTERS)
_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")
_DEFAULT_ENDPOINTS = (
    ("Ollama", "ollama", "http://127.0.0.1:11434"),
    ("LM Studio / OpenAI 兼容", "openai_compatible", "http://127.0.0.1:1234"),
    ("llama.cpp Server", "llama_cpp", "http://127.0.0.1:8080"),
)


class LocalModelProductError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[override]
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    rendered = " ".join(value.replace("\x00", " ").split())
    return rendered[:limit]


class LocalModelProductController:
    """Discover and verify loopback model servers without running inference.

    Verification is deliberately limited to GET-only health/model-list endpoints.
    It never starts a process, downloads a model, sends a prompt, resolves a host
    name, or reaches a non-loopback address.
    """

    adapter_kind = "p08_local_model_loopback_read_only_v1"

    def __init__(self, state_root: Path):
        self.state_root = Path(state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_root / "local_models_v1.json"
        self._metrics = {
            "loopback_http_requests": 0,
            "external_network_calls": 0,
            "inference_calls": 0,
            "process_starts": 0,
            "model_downloads": 0,
            "credential_value_reads": 0,
        }
        if not self.state_path.exists():
            endpoints = {}
            for display_name, kind, endpoint in _DEFAULT_ENDPOINTS:
                endpoint_id = self._endpoint_id(kind, endpoint)
                endpoints[endpoint_id] = {
                    "endpoint_id": endpoint_id,
                    "display_name": display_name,
                    "kind": kind,
                    "endpoint": endpoint,
                    "user_added": False,
                    "last_verification": None,
                }
            self._save({"schema_version": "P08LocalModelState-v1", "revision": -1, "endpoints": endpoints})
        self._validate_state(self._load())

    @staticmethod
    def _endpoint_id(kind: str, endpoint: str) -> str:
        digest = hashlib.sha256(f"{kind}\x00{endpoint}".encode("utf-8")).hexdigest()[:20]
        return f"local-model-{digest}"

    @staticmethod
    def _validate_state(state: Mapping[str, Any]) -> None:
        if set(state) != {"schema_version", "revision", "endpoints"}:
            raise LocalModelProductError("LOCAL_MODEL_STATE_FIELDS_INVALID")
        if state.get("schema_version") != "P08LocalModelState-v1":
            raise LocalModelProductError("LOCAL_MODEL_STATE_SCHEMA_INVALID")
        if not isinstance(state.get("revision"), int) or not isinstance(state.get("endpoints"), dict):
            raise LocalModelProductError("LOCAL_MODEL_STATE_INVALID")

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LocalModelProductError("LOCAL_MODEL_STATE_UNAVAILABLE") from error
        self._validate_state(value)
        return value

    def _save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", -1)) + 1
        self._validate_state(state)
        temporary = self.state_path.with_suffix(".json.next")
        if temporary.exists():
            raise FileExistsError("LOCAL_MODEL_STATE_TEMP_COLLISION")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.state_path)

    @staticmethod
    def _require(params: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        keys = set(params)
        if not required <= keys or keys - required - optional:
            raise LocalModelProductError("LOCAL_MODEL_PARAMS_INVALID")

    @staticmethod
    def _validated_endpoint(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_INVALID")
        parts = urlsplit(value.strip())
        if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_NOT_LOOPBACK")
        try:
            address = ipaddress.ip_address(parts.hostname)
        except ValueError as error:
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_NOT_LOOPBACK") from error
        if not address.is_loopback or parts.query or parts.fragment:
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_NOT_LOOPBACK")
        if parts.path not in {"", "/"}:
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_BASE_PATH_INVALID")
        try:
            port = parts.port
        except ValueError as error:
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_INVALID") from error
        host = f"[{parts.hostname}]" if address.version == 6 else parts.hostname
        netloc = f"{host}:{port}" if port else host
        return urlunsplit(("http", netloc, "", "", ""))

    @staticmethod
    def _public_endpoint(row: Mapping[str, Any]) -> dict[str, Any]:
        adapter = _KIND_ADAPTERS[str(row["kind"])]
        return {
            "endpoint_id": row["endpoint_id"],
            "display_name": row["display_name"],
            "kind": row["kind"],
            "endpoint": row["endpoint"],
            "user_added": bool(row["user_added"]),
            "adapter_family": adapter["adapter_family"],
            "model_list_path": adapter["model_list_path"],
            "known_implementations": list(adapter["known_implementations"]),
            "last_verification": deepcopy(row.get("last_verification")),
        }

    @staticmethod
    def _recognized_models(endpoints: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        seen: set[str] = set()
        for endpoint in endpoints:
            verification = endpoint.get("last_verification")
            if not isinstance(verification, Mapping) or verification.get("service_reachable") is not True:
                continue
            models = verification.get("models")
            if not isinstance(models, list):
                continue
            details_by_name = {
                _safe_text(item.get("model_name"), 160): dict(item)
                for item in verification.get("model_details", [])
                if isinstance(item, Mapping) and _safe_text(item.get("model_name"), 160)
            }
            for raw_model in models:
                model_name = _safe_text(raw_model, 160)
                if not model_name:
                    continue
                model_detail = details_by_name.get(model_name, {})
                raw_digest = _safe_text(model_detail.get("model_digest"), 64)
                model_digest = raw_digest.upper() if _HEX64.fullmatch(raw_digest) else None
                identity = f"{endpoint['endpoint_id']}\x00{model_name}"
                profile_ref = f"local:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
                if profile_ref in seen:
                    continue
                seen.add(profile_ref)
                endpoint_name = str(endpoint["display_name"])
                profiles.append(
                    {
                        "profile_ref": profile_ref,
                        "endpoint_id": endpoint["endpoint_id"],
                        "endpoint_name": endpoint_name,
                        "endpoint_kind": endpoint["kind"],
                        "endpoint": endpoint["endpoint"],
                        "chat_endpoint": (
                            f"{str(endpoint['endpoint']).rstrip('/')}/api/chat"
                            if endpoint["kind"] == "ollama"
                            else None
                        ),
                        "provider": f"[本地] {endpoint_name}",
                        "display_name": f"[本地] {model_name}",
                        "model_name": model_name,
                        "model_digest": model_digest,
                        "exact_identity_available": model_digest is not None,
                        "structured_chat_adapter": (
                            "P07T09_LOCAL_STRUCTURED_CHAT_V1"
                            if endpoint["kind"] == "ollama" and model_digest is not None
                            else None
                        ),
                        "execution_eligible": endpoint["kind"] == "ollama" and model_digest is not None,
                        "capability": infer_api_model_capability(endpoint_name, model_name),
                        "connection_status": "AVAILABLE",
                        "verified_at": verification.get("verified_at"),
                    }
                )
        return sorted(profiles, key=lambda row: (row["display_name"].casefold(), row["profile_ref"]))

    @staticmethod
    def _installed_tools() -> list[dict[str, Any]]:
        candidates = (
            ("ollama", "Ollama"),
            ("lms", "LM Studio CLI"),
            ("llama-server", "llama.cpp Server"),
        )
        return [
            {
                "tool": tool,
                "display_name": display_name,
                "executable_found": bool(shutil.which(tool)),
                "executable_path_exposed": False,
            }
            for tool, display_name in candidates
        ]

    @staticmethod
    def _model_details_from_payload(kind: str, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise LocalModelProductError("LOCAL_MODEL_RESPONSE_INVALID")
        values = payload.get("models") if kind == "ollama" else payload.get("data", payload.get("models"))
        if not isinstance(values, list):
            raise LocalModelProductError("LOCAL_MODEL_RESPONSE_INVALID")
        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, Mapping):
                continue
            name = _safe_text(value.get("name") or value.get("model") or value.get("id"), 160)
            if not name or name in seen:
                continue
            seen.add(name)
            digest = _safe_text(value.get("digest"), 64) if kind == "ollama" else ""
            models.append(
                {
                    "model_name": name,
                    "model_digest": digest.upper() if _HEX64.fullmatch(digest) else None,
                }
            )
        return models

    @classmethod
    def _models_from_payload(cls, kind: str, payload: Any) -> list[str]:
        return [row["model_name"] for row in cls._model_details_from_payload(kind, payload)]

    def _verify(self, endpoint_id: str) -> dict[str, Any]:
        state = self._load()
        row = state["endpoints"].get(endpoint_id)
        if not isinstance(row, Mapping):
            raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_NOT_FOUND")
        endpoint = self._validated_endpoint(row["endpoint"])
        suffix = str(_KIND_ADAPTERS[str(row["kind"])]["model_list_path"])
        request = Request(endpoint + suffix, method="GET", headers={"Accept": "application/json"})
        self._metrics["loopback_http_requests"] += 1
        try:
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=2.5) as response:  # noqa: S310 - numeric loopback, no proxy or redirect
                if int(response.status) != 200:
                    raise LocalModelProductError("LOCAL_MODEL_SERVICE_HTTP_ERROR")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise LocalModelProductError("LOCAL_MODEL_RESPONSE_TOO_LARGE")
            payload = json.loads(raw.decode("utf-8"))
            model_details = self._model_details_from_payload(str(row["kind"]), payload)
            models = [item["model_name"] for item in model_details]
            status = "MODEL_LISTED_INFERENCE_NOT_RUN" if models else "SERVICE_REACHABLE_NO_MODELS_LISTED"
            result = {
                "endpoint_id": endpoint_id,
                "verified_at": _now(),
                "service_reachable": True,
                "model_listed": bool(models),
                "models": models,
                "model_details": model_details,
                "inference_run": False,
                "status": status,
            }
        except LocalModelProductError as error:
            result = {
                "endpoint_id": endpoint_id,
                "verified_at": _now(),
                "service_reachable": False,
                "model_listed": False,
                "models": [],
                "model_details": [],
                "inference_run": False,
                "error_code": str(error),
                "status": "SERVICE_RESPONSE_INVALID_INFERENCE_NOT_RUN",
            }
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            result = {
                "endpoint_id": endpoint_id,
                "verified_at": _now(),
                "service_reachable": False,
                "model_listed": False,
                "models": [],
                "model_details": [],
                "inference_run": False,
                "error_code": type(error).__name__,
                "status": "SERVICE_UNREACHABLE_INFERENCE_NOT_RUN",
            }
        state["endpoints"][endpoint_id]["last_verification"] = deepcopy(result)
        self._save(state)
        return result

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in LOCAL_MODEL_METHODS:
            raise LocalModelProductError("LOCAL_MODEL_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        if any(str(key).startswith("_") for key in accepted):
            raise LocalModelProductError("LOCAL_MODEL_PRIVATE_PARAM_FORBIDDEN")
        if method in {"local_models.bootstrap", "local_models.list"}:
            self._require(accepted, set())
            state = self._load()
            endpoints = [self._public_endpoint(row) for row in state["endpoints"].values()]
            endpoints.sort(key=lambda row: (not row["user_added"], row["display_name"].casefold()))
            return {
                "schema_version": "P08LocalModelsProjection-v1",
                "endpoints": endpoints,
                "recognized_models": self._recognized_models(endpoints),
                "endpoint_count": len(endpoints),
                "installed_tools": self._installed_tools(),
                "verification_boundary": "MODEL_LIST_GET_ONLY_NO_INFERENCE",
                "status": "PASS",
            }
        if method == "local_models.discover":
            self._require(accepted, set())
            state = self._load()
            endpoint_ids = sorted(state["endpoints"])
            results = [self._verify(endpoint_id) for endpoint_id in endpoint_ids]
            reachable = [row for row in results if row.get("service_reachable")]
            model_names = sorted(
                {
                    str(model)
                    for row in reachable
                    for model in row.get("models", [])
                    if str(model)
                }
            )
            return {
                "schema_version": "P08LocalModelDiscoveryReceipt-v1",
                "checked_endpoint_count": len(endpoint_ids),
                "reachable_endpoint_count": len(reachable),
                "model_count": len(model_names),
                "models": model_names,
                "results": results,
                "loopback_only": True,
                "inference_calls": 0,
                "external_network_calls": 0,
                "process_starts": 0,
                "model_downloads": 0,
                "status": "PASS" if reachable else "NO_LOCAL_SERVICE_REACHABLE",
            }
        if method == "local_models.endpoint_save":
            self._require(accepted, {"display_name", "kind", "endpoint"})
            display_name = _safe_text(accepted["display_name"], 80)
            kind = str(accepted["kind"])
            endpoint = self._validated_endpoint(accepted["endpoint"])
            if not display_name or kind not in _KINDS:
                raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_FIELDS_INVALID")
            endpoint_id = self._endpoint_id(kind, endpoint)
            state = self._load()
            state["endpoints"][endpoint_id] = {
                "endpoint_id": endpoint_id,
                "display_name": display_name,
                "kind": kind,
                "endpoint": endpoint,
                "user_added": True,
                "last_verification": None,
            }
            self._save(state)
            return {
                "schema_version": "P08LocalModelEndpointSaveReceipt-v1",
                "endpoint": self._public_endpoint(state["endpoints"][endpoint_id]),
                "status": "PASS",
            }
        if method == "local_models.endpoint_remove":
            self._require(accepted, {"endpoint_id"})
            state = self._load()
            endpoint_id = str(accepted["endpoint_id"])
            row = state["endpoints"].get(endpoint_id)
            if not isinstance(row, Mapping):
                raise LocalModelProductError("LOCAL_MODEL_ENDPOINT_NOT_FOUND")
            if not row.get("user_added"):
                raise LocalModelProductError("LOCAL_MODEL_BUILTIN_ENDPOINT_IMMUTABLE")
            del state["endpoints"][endpoint_id]
            self._save(state)
            return {"endpoint_id": endpoint_id, "removed": True, "status": "PASS"}
        if method == "local_models.verify":
            self._require(accepted, {"endpoint_id"})
            return self._verify(str(accepted["endpoint_id"]))
        self._require(accepted, set())
        return {**deepcopy(self._metrics), "status": "PASS"}


__all__ = ["LOCAL_MODEL_METHODS", "LocalModelProductController", "LocalModelProductError"]
