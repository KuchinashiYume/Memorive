from __future__ import annotations

import json
from pathlib import Path

from manager import LOCAL_PROFILE_FILES, seed_local_profile


def write_json(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return payload


def test_local_preferences_seed_is_an_exact_configuration_allowlist(tmp_path):
    source = tmp_path / "user-profile"
    session = tmp_path / "session"
    session.mkdir()
    expected = {}
    for index, relative in enumerate(LOCAL_PROFILE_FILES):
        expected[relative] = write_json(source / relative, {"fixture": index, "relative": relative})

    write_json(source / "messages" / "message_projection.json", {"private": "message"})
    write_json(source / "inbox" / "inbox.json", {"private": "inbox"})
    write_json(source / "settings" / "workflow_exam_jobs" / "job.json", {"private": "job"})
    write_json(source / "ui" / "control_store.json", {"private": "transient-ui"})

    receipt = seed_local_profile(session, source)

    assert receipt["status"] == "SEEDED"
    assert receipt["copied_file_count"] == len(expected)
    assert receipt["automatic_execution"] is False
    assert receipt["source_profile_mutated"] is False
    for relative, payload in expected.items():
        assert (session / "state" / "profile" / relative).read_bytes() == payload
    assert not (session / "state" / "profile" / "messages").exists()
    assert not (session / "state" / "profile" / "inbox").exists()
    assert not (session / "state" / "profile" / "ui" / "control_store.json").exists()
    assert not (session / "state" / "profile" / "settings" / "workflow_exam_jobs").exists()


def test_missing_local_profile_keeps_clean_defaults(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    receipt = seed_local_profile(session, tmp_path / "missing")
    assert receipt["status"] == "NOT_FOUND"
    assert receipt["copied_file_count"] == 0
    assert receipt["automatic_execution"] is False
