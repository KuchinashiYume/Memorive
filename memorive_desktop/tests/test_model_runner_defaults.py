from __future__ import annotations

from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_settings import model_validation


def test_codex_resolver_prefers_real_exe_over_cmd_wrapper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wrapper = tmp_path / "codex.cmd"
    executable = tmp_path / "codex.exe"
    wrapper.write_bytes(b"wrapper")
    executable.write_bytes(b"binary")

    def which(name: str):
        return str(executable if name == "codex.exe" else wrapper)

    monkeypatch.setattr(model_validation.shutil, "which", which)
    resolved = model_validation.default_executable_resolver("codex")
    assert resolved == str(executable.resolve())


def test_structured_chat_exam_timeout_allows_profile_specific_deadline() -> None:
    assert model_validation._structured_chat_timeout_limit(
        "workflow_model_exam"
    ) == 3_600
    assert model_validation._structured_chat_timeout_limit(
        "core_document_processing"
    ) == 3_600
    assert model_validation._structured_chat_timeout_limit(
        "conversation_refinement"
    ) == 600
