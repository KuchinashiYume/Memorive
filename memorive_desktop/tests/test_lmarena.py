"""LMArena as the leaderboard's first scoring source (since 2026-11-22, in place of LiveBench, which stopped at its
2026-06-25 release): reading the text board, one row per model next to Epoch AI, the pinned page addresses, the
paged refresh, and moving saved settings off LiveBench. Made-up boards only; no network."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from leaderboard_test_support import LIVEBENCH_SOURCE  # noqa: F401  (puts source/ on the path)
from memorive.desktop.settings import lmarena_reference as lm
from memorive.desktop.settings.external_sources import ExternalDataSources, default_sources, normalize_payload
from memorive.desktop.settings.leaderboard import (DEFAULTS, FORMER_DEFAULT_WEIGHTS, FORMER_PREF_SCHEMA, PREF_SCHEMA,
                                                   LeaderboardService, project_rows)
from memorive.desktop.settings.livebench_reference import DEFAULT_ENDPOINT as LIVEBENCH_ENDPOINT
from memorive.desktop.settings.metadata_transport import is_builtin_public_metadata
from memorive.models.identity import identity
from memorive.models.validation.transport import HttpResponse
from memorive.platform.json_files import canonical_sha256


def row(name, organization, rating, date="2026-11-19"):
    return {"model_name": name, "organization": organization, "license": "Proprietary", "rating": rating,
            "rating_lower": rating - 5, "rating_upper": rating + 5, "variance": 4.0, "vote_count": 1000.0,
            "rank": 1, "category": "overall", "leaderboard_publish_date": date}


BOARD = [row("Claude Opus 5.5 (High)", "anthropic", 1506), row("Claude Opus 5.5 (Max)", "anthropic", 1510),
         row("GPT-6.2 Sol (Max)", "openai", 1465), row("Qwen3-235B-A22B-Instruct (2507)", "alibaba", 1420),
         row("Dolly 2.0 12B", "", 983)]


def test_the_default_first_slot_is_lmarena_without_credentials():
    source = default_sources()[0]
    assert (source["source_id"], source["display_name"], source["format"]) == ("artificial-analysis", "LMArena", "LMARENA_LEADERBOARD")
    assert source["endpoint"] == lm.ENDPOINT and not source["credential_env"]
    assert source["redistribution"] == "ATTRIBUTION_AND_UPSTREAM_LICENSES"  # CC BY 4.0
    assert DEFAULTS["weights"] == {"artificial-analysis": 50, "epoch-ai": 50}


@pytest.mark.parametrize(("name", "slug", "shown"), [
    ("Claude Opus 5.5 (High)", "claude-opus-5.5", "Claude Opus 5.5"),
    ("GPT-6.2 Sol (Max)", "gpt-6.2-sol", "GPT-6.2 Sol"),
    ("Grok 4.1 (Thinking)", "grok-4.1", "Grok 4.1"),
    ("Qwen3-235B-A22B-Instruct (2507)", "qwen3-235b-a22b-instruct-2507", "Qwen3-235B-A22B-Instruct (2507)"),
    ("Claude Sonnet 4.5 (Thinking 16K, 20250805)", "claude-sonnet-4.5-20250805", "Claude Sonnet 4.5 (20250805)"),
    ("GPT-5.4", "gpt-5.4", "GPT-5.4"),
])
def test_effort_and_mode_words_leave_the_name_dates_stay(name, slug, shown):
    assert (lm.model_slug(name), lm.display_name(name)) == (slug, shown)


def test_a_model_is_rated_by_its_best_variant_under_its_vendor():
    models = {item["model_id"]: item for item in lm.normalized_rows(BOARD)}
    assert set(models) == {"anthropic/claude-opus-5.5", "openai/gpt-6.2-sol", "qwen/qwen3-235b-a22b-instruct-2507",
                           "dolly-2.0-12b"}
    assert models["anthropic/claude-opus-5.5"]["rating"] == 1510
    assert models["anthropic/claude-opus-5.5"]["name"] == "Claude Opus 5.5"


@pytest.mark.parametrize("change", [
    lambda rows: rows[0].update(rating=float("nan")),
    lambda rows: rows[0].update(rating=-3),
    lambda rows: rows[0].update(category="coding"),
    lambda rows: rows[0].update(leaderboard_publish_date="2026-11-12"),
    lambda rows: rows.clear(),
])
def test_a_board_that_does_not_fit_is_refused(change):
    rows = deepcopy(BOARD)
    change(rows)
    with pytest.raises(ValueError):
        lm.normalized_rows(rows)


def test_lmarena_and_epoch_score_the_same_row():
    records = normalize_payload(default_sources()[0], {"rows": BOARD})
    opus = next(r for r in records if r["model_id"] == "anthropic/claude-opus-5.5")
    assert identity(opus) == ("anthropic", "claude-opus-5.5", "reference")
    assert opus["metrics"] == {"lmarena_text": 1510.0} and opus["version"] == "2026-11-19"
    epoch = [{"model_id": f"Claude Opus 5.{i}", "model_name": f"Claude Opus 5.{i}", "source_id": "epoch-ai",
              "identity_scope": "MODEL_CAPABILITY_UPPER_ENVELOPE", "metrics": {"eci": 150 + i}} for i in range(12)]
    arena = [*records, *normalize_payload(default_sources()[0], {"rows": [
        row(f"Claude Opus 5.{i} (High)", "anthropic", 1400 + i) for i in range(12) if i != 5]})]
    rows, algorithm = project_rows([
        {"id": "artificial-analysis", "name": "LMArena", "format": "LMARENA_LEADERBOARD", "status": "CACHED", "records": arena},
        {"id": "epoch-ai", "name": "Epoch AI", "format": "EPOCH_CSV_ZIP", "status": "CACHED", "records": epoch}],
        deepcopy(DEFAULTS))
    [row55] = [r for r in rows if r["base_model"] == "claude-opus-5.5"]
    assert {k: v["status"] for k, v in row55["score_breakdown"].items()} == {"artificial-analysis": "INCLUDED", "epoch-ai": "INCLUDED"}
    assert row55["ability"] is not None and algorithm["weights"] == {"artificial-analysis": 50, "epoch-ai": 50}


def test_only_the_board_pages_are_pinned():
    assert is_builtin_public_metadata(lm.page_url(0)) and is_builtin_public_metadata(lm.page_url(400))
    assert not is_builtin_public_metadata(lm.page_url(50))
    assert not is_builtin_public_metadata(lm.page_url(0) + "&x=1")
    assert not is_builtin_public_metadata(lm.page_url(0).replace("text_style_control", "text"))


class Board:
    """The rows API in pages of 100 rows."""

    def __init__(self, rows, total=None):
        self.rows, self.total, self.urls = rows, total, []

    def request(self, **kwargs):
        self.urls.append(kwargs["url"])
        offset = next(o for o in range(0, lm.MAX_ROWS, lm.PAGE_SIZE) if kwargs["url"] == lm.page_url(o))
        page = [{"row_idx": offset + i, "row": item, "truncated_cells": []}
                for i, item in enumerate(self.rows[offset:offset + lm.PAGE_SIZE])]
        total = self.total(offset) if callable(self.total) else len(self.rows)
        return HttpResponse(200, json.dumps({"rows": page, "num_rows_total": total}).encode())


def board(count):
    return [row(f"GPT-6.{i} Sol (Max)", "openai", 1300 + i) for i in range(count)]


def test_a_refresh_reads_every_page_of_the_board(tmp_path):
    transport = Board(board(150))
    sources = ExternalDataSources(tmp_path, transport=transport, environment={})
    result = sources.refresh("artificial-analysis", expected_revision=0)
    assert result["status"] == "PASS" and result["record_count"] == 150
    assert transport.urls == [lm.page_url(0), lm.page_url(100)]


def test_a_board_that_changes_between_pages_is_not_kept(tmp_path):
    transport = Board(board(150), total=lambda offset: 150 if offset == 0 else 151)
    sources = ExternalDataSources(tmp_path, transport=transport, environment={})
    result = sources.refresh("artificial-analysis", expected_revision=0)
    assert result["status"] != "PASS"
    assert sources.get_state()["sources"][0]["status"] == "NOT_FETCHED"


def saved(tmp_path, first):
    state = {"schema_version": "ExternalDataSourceSettings-v2", "revision": 3, "sources": [first, *default_sources()[1:]]}
    state["sha256"] = canonical_sha256(state)
    (tmp_path / "external_sources.json").write_text(json.dumps(state), encoding="utf-8")
    return ExternalDataSources(tmp_path, environment={}).get_state()["sources"][0]["config"]


def test_the_former_livebench_default_becomes_lmarena_keeping_its_schedule(tmp_path):
    former = {**default_sources()[0], "display_name": "LiveBench", "format": "LIVEBENCH_RELEASE",
              "endpoint": LIVEBENCH_ENDPOINT, "redistribution": "LICENSE_REVIEW_REQUIRED",
              "refresh_mode": "MANUAL", "enabled": False}
    config = saved(tmp_path, former)
    assert (config["format"], config["display_name"], config["refresh_mode"], config["enabled"]) == (
        "LMARENA_LEADERBOARD", "LMArena", "MANUAL", False)


def test_a_renamed_livebench_slot_is_kept(tmp_path):
    assert saved(tmp_path, LIVEBENCH_SOURCE)["format"] == "LIVEBENCH_RELEASE"


@pytest.mark.parametrize(("schema", "weights", "expected"), [
    (FORMER_PREF_SCHEMA, FORMER_DEFAULT_WEIGHTS, DEFAULTS["weights"]),  # saved while LiveBench held the slot
    (FORMER_PREF_SCHEMA, {"artificial-analysis": 30, "epoch-ai": 70}, {"artificial-analysis": 30, "epoch-ai": 70}),
    (PREF_SCHEMA, FORMER_DEFAULT_WEIGHTS, FORMER_DEFAULT_WEIGHTS),  # LMArena only, chosen after the change
])
def test_weights_left_at_the_former_default_follow_the_new_one(tmp_path, schema, weights, expected):
    value = {"schema_version": schema, "revision": 2, "preferences": {**deepcopy(DEFAULTS), "weights": weights}}
    value["sha256"] = canonical_sha256(value)
    (tmp_path / "leaderboard_preferences.json").write_text(json.dumps(value), encoding="utf-8")
    service = LeaderboardService(ExternalDataSources(tmp_path / "external", environment={}), tmp_path)
    assert service._load()["preferences"]["weights"] == expected


def test_lmarena_only_stays_after_a_save(tmp_path):
    service = LeaderboardService(ExternalDataSources(tmp_path / "external", environment={}), tmp_path)
    service.save(expected_revision=0, changes={"weights": {"artificial-analysis": 100, "epoch-ai": 0}})
    stored = json.loads((tmp_path / "leaderboard_preferences.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == PREF_SCHEMA
    assert service._load()["preferences"]["weights"] == {"artificial-analysis": 100, "epoch-ai": 0}


def test_a_former_file_is_written_back_as_v2_on_the_next_save(tmp_path):
    value = {"schema_version": FORMER_PREF_SCHEMA, "revision": 4,
             "preferences": {**deepcopy(DEFAULTS), "weights": FORMER_DEFAULT_WEIGHTS}}
    value["sha256"] = canonical_sha256(value)
    (tmp_path / "leaderboard_preferences.json").write_text(json.dumps(value), encoding="utf-8")
    service = LeaderboardService(ExternalDataSources(tmp_path / "external", environment={}), tmp_path)
    service.save(expected_revision=4, changes={"currency": "CNY"})
    stored = json.loads((tmp_path / "leaderboard_preferences.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == PREF_SCHEMA and stored["preferences"]["weights"] == DEFAULTS["weights"]
