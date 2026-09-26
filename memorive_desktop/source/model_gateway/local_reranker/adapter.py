"""Execution ExecutionCore-backed local Qwen3 reranker adapter for RETRIEVAL-CALIBRATION.

The parent side uses :class:`JobWorkspace` and :class:`IsolatedJobRunner`.
The same file also contains the deliberately dependency-light worker entrypoint
so the frozen model virtualenv does not need the repository test dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

try:  # Package import in the parent; sibling import in the isolated worker.
    from .contracts import (
        LocalRerankerViolation,
        RESULT_VERSION,
        WORKER_VERSION,
        build_request,
        bytes_sha256,
        canonical_json_bytes,
        canonical_sha256,
        validate_compatibility_manifest,
        validate_worker_result,
    )
except ImportError:  # pragma: no cover - exercised by the real worker process
    from contracts import (  # type: ignore[no-redef]
        LocalRerankerViolation,
        RESULT_VERSION,
        WORKER_VERSION,
        build_request,
        bytes_sha256,
        canonical_json_bytes,
        canonical_sha256,
        validate_compatibility_manifest,
        validate_worker_result,
    )


ADAPTER_ID = "retrieval_calibration_local_reranker"
DEFAULT_RAW_CAP_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class LocalRerankerOutcome:
    success: bool
    error_code: str | None
    score_batches: tuple[dict[str, Any], ...]
    workspace_manifest: dict[str, Any] | None
    process_receipt: dict[str, Any] | None
    resource_receipt: dict[str, Any] | None
    worker_result: dict[str, Any] | None
    physical_starts: int


def _environment(manifest: Mapping[str, Any], workspace: Any) -> dict[str, str]:
    configured = dict(manifest["runtime"].get("offline_environment") or {})
    environment = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "TORCHINDUCTOR_CACHE_DIR": str(workspace.scratch_root / "torchinductor"),
        "TORCH_EXTENSIONS_DIR": str(workspace.scratch_root / "torch_extensions"),
        "CUDA_VISIBLE_DEVICES": str(manifest["device"]["index"]),
        # A fixed non-personal value prevents getpass from falling through to
        # the POSIX-only ``pwd`` module in the bundled Windows runtime.
        "USERNAME": "MEMORIVE_Installation_LOCAL",
    }
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "TOKENIZERS_PARALLELISM"):
        if name in configured:
            environment[name] = str(configured[name])
    if os.name == "nt":
        environment.update(
            {
                "SYSTEMROOT": os.environ["SYSTEMROOT"],
                "TEMP": str(workspace.scratch_root),
                "TMP": str(workspace.scratch_root),
            }
        )
        if "PATH" in os.environ:
            environment["PATH"] = os.environ["PATH"]
    return environment


def _map_process_error(receipt: Mapping[str, Any], stderr: bytes) -> str:
    error = receipt.get("error_code")
    rendered = stderr.decode("utf-8", errors="replace").lower()
    if error == "PROCESS_TIMEOUT":
        return "TIMEOUT"
    if "out of memory" in rendered or "cuda oom" in rendered:
        return "OOM"
    if "no such file" in rendered or "filenotfounderror" in rendered:
        return "MODEL_MISSING"
    if "safetensor" in rendered or "checksum" in rendered or "model_corrupt" in rendered:
        return "MODEL_CORRUPT"
    return "LOCAL_PROCESS_ERROR"


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


class LocalRerankerAdapter:
    """One-process, local-only reranker with atomic semantic validation."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        *,
        worker_script: str | Path | None = None,
        python_executable: str | Path | None = None,
        clock: Any | None = None,
    ) -> None:
        self._core_schema = dict(core_schema)
        self._worker_script = Path(worker_script or __file__).resolve()
        self._python_executable = Path(python_executable).resolve() if python_executable else None
        self._clock = clock

    def execute(
        self,
        batches: Sequence[Mapping[str, Any]],
        compatibility_manifest: Mapping[str, Any],
        *,
        workspace_parent: str | Path,
        protected_roots: Sequence[str | Path],
        job_id: str,
        attempt_id: str,
        timeout_seconds: float = 900.0,
        raw_cap_bytes: int = DEFAULT_RAW_CAP_BYTES,
    ) -> LocalRerankerOutcome:
        manifest = validate_compatibility_manifest(compatibility_manifest)
        request = build_request(batches, manifest)
        request_bytes = canonical_json_bytes(request)
        python_executable = self._python_executable or Path(manifest["runtime"]["python_executable"]).resolve()
        model_root = Path(manifest["model_root"]).resolve()
        if not python_executable.is_file():
            raise LocalRerankerViolation("RUNTIME_EXECUTABLE_MISSING")
        if not model_root.is_dir():
            raise LocalRerankerViolation("MODEL_MISSING")
        if not self._worker_script.is_file():
            raise LocalRerankerViolation("WORKER_SCRIPT_MISSING")

        # Lazy imports keep the frozen worker virtualenv independent of jsonschema.
        from model_gateway.execution_core.runner import IsolatedJobRunner, ProcessSpec
        from model_gateway.execution_core.workspace import JobWorkspace

        environment_names = [
            "CUDA_VISIBLE_DEVICES",
            "DO_NOT_TRACK",
            "HF_HUB_DISABLE_TELEMETRY",
            "HF_HUB_OFFLINE",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONIOENCODING",
            "TOKENIZERS_PARALLELISM",
            "TORCHINDUCTOR_CACHE_DIR",
            "TORCH_EXTENSIONS_DIR",
            "TRANSFORMERS_OFFLINE",
            "USERNAME",
        ]
        if os.name == "nt":
            environment_names.extend(["PATH", "SYSTEMROOT", "TEMP", "TMP"])
        workspace = JobWorkspace.create(
            workspace_parent,
            job_id,
            self._core_schema,
            process_allowlist=(str(python_executable),),
            environment_allowlist=tuple(environment_names),
            extra_read_roots=(model_root, self._worker_script),
            protected_roots=protected_roots,
            network_allowlist=(),
            input_hashes=(
                {
                    "relative_path": "request.json",
                    "sha256": bytes_sha256(request_bytes),
                    "bytes": len(request_bytes),
                },
            ),
            selected_adapter_id=ADAPTER_ID,
            selected_manifest_hash=canonical_sha256(manifest),
            expected_attempt_id=attempt_id,
            contract_timeout_seconds=timeout_seconds,
            raw_cap_bytes=raw_cap_bytes,
            **({"clock": self._clock} if self._clock is not None else {}),
        )
        workspace.stage_input("request.json", request_bytes)
        output_path = workspace.output_root / "result.json"
        environment = _environment(manifest, workspace)
        spec = ProcessSpec(
            adapter_id=ADAPTER_ID,
            attempt_id=attempt_id,
            argv=(
                str(python_executable),
                str(self._worker_script),
                "--worker",
                "--input",
                str(workspace.input_root / "request.json"),
                "--output",
                str(output_path),
            ),
            cwd=str(workspace.scratch_root),
            environment=environment,
            environment_allowlist=tuple(sorted(environment_names)),
            timeout_seconds=timeout_seconds,
            raw_cap_bytes=raw_cap_bytes,
            declared_read_paths=(
                str(self._worker_script),
                str(workspace.input_root / "request.json"),
                str(model_root),
            ),
            declared_write_paths=(str(output_path),),
        )
        runner = IsolatedJobRunner(
            self._core_schema,
            **({"clock": self._clock} if self._clock is not None else {}),
        )
        process = runner.run(workspace, spec)
        if process.receipt.get("error_code") is not None:
            return LocalRerankerOutcome(
                False,
                _map_process_error(process.receipt, process.stderr),
                (),
                workspace.manifest,
                process.receipt,
                None,
                None,
                1 if process.receipt.get("process_started") else 0,
            )
        try:
            raw_result = json.loads(output_path.read_text(encoding="utf-8"))
            result = validate_worker_result(raw_result, request, manifest)
        except (OSError, json.JSONDecodeError, LocalRerankerViolation) as exc:
            code = exc.code if isinstance(exc, LocalRerankerViolation) else "WORKER_RESULT_INVALID"
            return LocalRerankerOutcome(
                False,
                code,
                (),
                workspace.manifest,
                process.receipt,
                None,
                None,
                1,
            )
        resource_receipt = {
            "schema_version": "RetrievalCalibrationLocalRerankerResourceReceipt-v1",
            "job_id": job_id,
            "attempt_id": attempt_id,
            "model_id": manifest["model_id"],
            "canonical_revision": manifest["canonical_revision"],
            "request_sha256": request["request_sha256"],
            "result_sha256": result["result_sha256"],
            "resources": result["resources"],
            "accounting": {
                "evidence_class": "local_compute",
                "provider_tokens": "N/A_LOCAL_COMPUTE",
                "api_cost_cny": 0.0,
                "local_electricity_or_depreciation_cost": "NOT_MONETIZED",
            },
            "side_effects": {
                **result["side_effects"],
                "production_route_mutations": 0,
                "production_index_writes": 0,
                "formal_runtime_log_artifact_registry_writes": 0,
            },
        }
        resource_receipt["receipt_sha256"] = canonical_sha256(resource_receipt)
        return LocalRerankerOutcome(
            True,
            None,
            tuple(dict(batch) for batch in result["batches"]),
            workspace.manifest,
            process.receipt,
            resource_receipt,
            result,
            1,
        )


def _worker_main(input_path: Path, output_path: Path) -> int:
    """Load the frozen model once and score every query batch offline."""

    network_connect_attempts = 0

    def deny_network(event: str, _args: Any) -> None:
        nonlocal network_connect_attempts
        if event == "socket.connect":
            network_connect_attempts += 1
            raise RuntimeError("NETWORK_CONNECT_FORBIDDEN")

    sys.addaudithook(deny_network)
    request = json.loads(input_path.read_text(encoding="utf-8"))
    if request.get("worker_version") != WORKER_VERSION or request.get("gold_or_scorer_present") is not False:
        raise RuntimeError("WORKER_REQUEST_INVALID")
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        if os.environ.get(name) != "1":
            raise RuntimeError("OFFLINE_ENVIRONMENT_INVALID")

    import psutil
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_binding = request["model"]
    model_root = Path(model_binding["model_root"])
    device_binding = request["device"]
    if device_binding["kind"] == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_DEVICE_UNAVAILABLE")
        torch.cuda.set_device(0)
        device = torch.device("cuda")
        # Torch 2.12 on the bundled Windows runtime rejects a torch.device
        # argument here; the already-selected current device is unambiguous.
        torch.cuda.reset_peak_memory_stats()
    else:
        device = torch.device("cpu")
    dtype = torch.bfloat16 if device_binding.get("dtype") == "bfloat16" else torch.float32
    process = psutil.Process(os.getpid())
    rss_before = process.memory_info().rss
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    load_started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        str(model_root),
        padding_side="left",
        local_files_only=True,
        trust_remote_code=False,
    )
    model = AutoModelForCausalLM.from_pretrained(
        str(model_root),
        torch_dtype=dtype,
        local_files_only=True,
        trust_remote_code=False,
        low_cpu_mem_usage=False,
    ).to(device).eval()
    load_seconds = time.perf_counter() - load_started
    token_false_id = tokenizer.convert_tokens_to_ids("no")
    token_true_id = tokenizer.convert_tokens_to_ids("yes")
    if not isinstance(token_false_id, int) or not isinstance(token_true_id, int) or token_false_id == token_true_id:
        raise RuntimeError("YES_NO_TOKEN_ID_INVALID")
    prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    prefix_tokens = tokenizer.encode(prefix, add_special_tokens=False)
    suffix_tokens = tokenizer.encode(suffix, add_special_tokens=False)
    max_length = int(request["limits"]["max_input_tokens"])
    batch_size = int(request["limits"]["batch_size"])
    query_latencies: list[float] = []
    sub_batch_latencies: list[float] = []
    token_count_estimates: list[int] = []
    result_batches: list[dict[str, Any]] = []

    @torch.no_grad()
    def compute_scores(pairs: Sequence[str]) -> list[float]:
        encoded = tokenizer(
            list(pairs),
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=max_length - len(prefix_tokens) - len(suffix_tokens),
        )
        for index, token_ids in enumerate(encoded["input_ids"]):
            encoded["input_ids"][index] = prefix_tokens + token_ids + suffix_tokens
            token_count_estimates.append(len(encoded["input_ids"][index]))
        inputs = tokenizer.pad(encoded, padding=True, return_tensors="pt", max_length=max_length)
        inputs = {key: value.to(device) for key, value in inputs.items()}
        logits = model(**inputs).logits[:, -1, :]
        yes_logits = logits[:, token_true_id]
        no_logits = logits[:, token_false_id]
        probabilities = torch.softmax(torch.stack([no_logits, yes_logits], dim=1), dim=1)[:, 1]
        return [float(value) for value in probabilities.detach().cpu().tolist()]

    for batch in request["batches"]:
        query_started = time.perf_counter()
        scores: list[float] = []
        documents = batch["documents"]
        formatted = [
            f"<Instruct>: {request['instruction']}\n<Query>: {batch['query']}\n<Document>: {document['model_input']}"
            for document in documents
        ]
        for offset in range(0, len(formatted), batch_size):
            batch_started = time.perf_counter()
            scores.extend(compute_scores(formatted[offset : offset + batch_size]))
            if device.type == "cuda":
                torch.cuda.synchronize()
            sub_batch_latencies.append(time.perf_counter() - batch_started)
        query_latencies.append(time.perf_counter() - query_started)
        result_batches.append(
            {
                "query_id": batch["query_id"],
                "scores": [
                    {
                        "index": document["index"],
                        "candidate_id": document["candidate_id"],
                        "text_sha256": document["text_sha256"],
                        "provenance_sha256": document["provenance_sha256"],
                        "model_input_sha256": document["model_input_sha256"],
                        "score": score,
                    }
                    for document, score in zip(documents, scores)
                ],
            }
        )

    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    rss_after_inference = process.memory_info().rss
    vram_peak = torch.cuda.max_memory_allocated() if device.type == "cuda" else None
    vram_reserved_peak = torch.cuda.max_memory_reserved() if device.type == "cuda" else None
    actual_name = torch.cuda.get_device_name() if device.type == "cuda" else "CPU"
    resources = {
        "cold_load_seconds": load_seconds,
        "warm_query_seconds_p50": statistics.median(query_latencies) if query_latencies else None,
        "warm_query_seconds_p95": _percentile(query_latencies, 0.95),
        "sub_batch_seconds_p50": statistics.median(sub_batch_latencies) if sub_batch_latencies else None,
        "sub_batch_seconds_p95": _percentile(sub_batch_latencies, 0.95),
        "peak_rss_bytes_sampled": max(rss_before, rss_after_inference),
        "peak_vram_allocated_bytes": vram_peak,
        "peak_vram_reserved_bytes": vram_reserved_peak,
        "cpu_percent_process_time_over_wall": (cpu_seconds / wall_seconds * 100.0) if wall_seconds > 0 else None,
        "gpu_utilization_percent": None,
        "gpu_utilization_status": "NOT_MEASURED",
        "device_name": actual_name,
        "query_count": len(query_latencies),
        "document_count": sum(len(batch["documents"]) for batch in request["batches"]),
        "input_token_estimate_max": max(token_count_estimates) if token_count_estimates else None,
        "timeout_count": 0,
        "oom_count": 0,
        "physical_starts": 1,
        "resource_sampling_scope": "WORKER_PROCESS_AND_TORCH_CUDA_ALLOCATOR",
    }
    result = {
        "schema_version": RESULT_VERSION,
        "worker_version": WORKER_VERSION,
        "request_sha256": request["request_sha256"],
        "identity": {
            "model_id": model_binding["model_id"],
            "canonical_revision": model_binding["canonical_revision"],
            "model_sha256sums_sha256": model_binding["model_sha256sums_sha256"],
            "weights_exact_set_sha256": model_binding["weights_exact_set_sha256"],
            "runtime_sha256sums_sha256": model_binding["runtime_sha256sums_sha256"],
            "transformers_version": __import__("transformers").__version__,
            "torch_version": torch.__version__,
            "dtype": str(dtype),
            "device": str(device),
        },
        "batches": result_batches,
        "resources": resources,
        "side_effects": {
            "external_requests": 0,
            "network_connect_attempts": network_connect_attempts,
            "cloud_fallback_started": False,
            "document_mutations": 0,
        },
    }
    result["result_sha256"] = canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.worker:
        parser.error("--worker is required")
    return _worker_main(args.input, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
