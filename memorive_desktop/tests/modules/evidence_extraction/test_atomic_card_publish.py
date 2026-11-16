from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memorive.documents.card_distillation.segmented_distill.publish import (
    publish_bytes_no_clobber,
    publish_staged_card_no_clobber,
    visibility_state,
    write_staged_bytes,
)


def test_staged_write_is_create_only(tmp_path: Path) -> None:
    staged = tmp_path / "candidate" / "card.json"
    write_staged_bytes(staged, b"complete-card")
    with pytest.raises(FileExistsError):
        write_staged_bytes(staged, b"replacement")
    assert staged.read_bytes() == b"complete-card"


def test_card_is_last_atomically_visible_and_no_clobber(tmp_path: Path) -> None:
    staged = write_staged_bytes(tmp_path / "candidate" / "card.json", b"complete-card")
    target = tmp_path / "canonical" / "card.json"
    assert not target.exists()
    publish_staged_card_no_clobber(staged, target)
    assert target.read_bytes() == b"complete-card"
    with pytest.raises(FileExistsError):
        publish_staged_card_no_clobber(staged, target)
    assert target.read_bytes() == b"complete-card"


def test_two_concurrent_writers_only_one_wins(tmp_path: Path) -> None:
    target = tmp_path / "canonical" / "card.json"

    def publish(index: int) -> tuple[str, bytes]:
        payload = f"candidate-{index}".encode()
        try:
            publish_bytes_no_clobber(target, payload)
            return "won", payload
        except FileExistsError:
            return "blocked", payload

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(publish, range(8)))
    winners = [payload for state, payload in results if state == "won"]
    assert len(winners) == 1
    assert sum(state == "blocked" for state, _ in results) == 7
    assert target.read_bytes() == winners[0]


def test_crash_before_promotion_is_resumable(tmp_path: Path) -> None:
    target = tmp_path / "canonical" / "card.json"
    write_staged_bytes(tmp_path / "candidate" / "sidecar.json", b"sidecar")
    assert visibility_state(target, admission_complete=False) == "RESUMABLE_CARD_NOT_VISIBLE"


def test_published_pending_card_is_not_m8_consumable(tmp_path: Path) -> None:
    target = tmp_path / "canonical" / "card.json"
    publish_bytes_no_clobber(target, b"complete-card")
    assert visibility_state(target, admission_complete=False) == "PENDING_NOT_CONSUMABLE_BY_KNOWLEDGE_ADMISSION"
    assert visibility_state(target, admission_complete=True) == "ADMITTED"


def test_existing_target_bytes_are_never_replaced(tmp_path: Path) -> None:
    target = tmp_path / "card.json"
    target.write_bytes(b"user-existing-card")
    with pytest.raises(FileExistsError):
        publish_bytes_no_clobber(target, b"new-card")
    assert target.read_bytes() == b"user-existing-card"
