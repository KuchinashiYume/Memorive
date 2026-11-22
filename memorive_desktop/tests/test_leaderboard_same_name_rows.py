"""Rows of the model leaderboard that would show the same name name their version.

Epoch AI lists dated releases of a model (Gemini 2.5 Flash of April, May, June...). Matched to one catalogue
entry, they took its display name, so the list showed several identical "Gemini 2.5 Flash" rows with different
scores. Each such row now names its version; a name shown once is unchanged.
"""
from __future__ import annotations

from memorive.desktop.settings import leaderboard


def _record(model_id, eci):
    return {"model_id": model_id, "model_name": model_id, "metrics": {"eci": eci},
            "identity_scope": "MODEL", "version": "", "source_as_of": "2026-11-01"}


def test_rows_with_one_display_name_name_their_versions(monkeypatch):
    # The catalogue's display name for every Gemini 2.5 Flash release, as the acceptance data had it.
    real = leaderboard.display_name
    monkeypatch.setattr(leaderboard, "display_name",
                        lambda name, base, vendor: "Gemini 2.5 Flash" if base.startswith("gemini-2.5-flash")
                        else real(name, base, vendor))
    source = {"id": "epoch-ai", "name": "Epoch AI", "format": "EPOCH_CSV_ZIP", "status": "CACHED", "records": [
        _record("gemini-2.5-flash-preview-04-17", 140.7), _record("gemini-2.5-flash-preview-05-20", 141.9),
        _record("gpt-5.6-sol", 150.1)]}
    rows, _summary = leaderboard.project_rows([source], {**leaderboard.DEFAULTS, "weights": {"epoch-ai": 100}})
    names = sorted(row["name"] for row in rows)
    assert len(set(names)) == len(names)
    assert [name for name in names if name.startswith("Gemini")] == [
        "Gemini 2.5 Flash (gemini-2.5-flash-preview-04-17)", "Gemini 2.5 Flash (gemini-2.5-flash-preview-05-20)"]
    assert any(name.startswith("GPT") and "(" not in name for name in names)
