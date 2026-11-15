"""Knowledge drafts (knowledge_feedback/research_ingest.py): a draft that cites excerpts waits for a
person; accepting it writes a knowledge note that can be cited like any other material.

A draft is proposed by the desktop or by an agent. Only the desktop reviews it. Accepting writes
the note as a Markdown file in the workspace, registers it with the artifact registry and adds it
to the evidence index. A revision is a new draft; accepting it retires the version it replaces.
Everything here is written in a temporary folder.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from artifact_registry.registry_v2 import ArtifactRegistry
from memorive_language import freeze
from memorive_research_workspace.service import ResearchWorkspace

PAPER = "\n".join(f"{n:03d} Growth of strain K12 was measured every hour at 37 C in the same medium." for n in range(1, 41))


@pytest.fixture
def workspace(tmp_path):
    opened = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [])
    yield opened
    opened.close()


def add_paper(workspace, folder, name="k12.md", text=PAPER):
    folder.mkdir(exist_ok=True)
    paper = folder / name
    paper.write_bytes(text.encode("utf-8"))
    return workspace.index.add_library_file(
        "default", path=paper, root=folder, external_id=name,
        expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")


def excerpt_ids(workspace, artifact_id):
    with workspace.store.tx() as db:
        rows = db.execute("SELECT id FROM chunks WHERE artifact_id=? ORDER BY line_start", (artifact_id,)).fetchall()
    return [row["id"] for row in rows]


@pytest.fixture
def paper(workspace, tmp_path):
    return add_paper(workspace, tmp_path / "library")


@pytest.fixture
def cited(workspace, paper):
    return excerpt_ids(workspace, paper["id"])[:2]


def propose(workspace, cited_ids, **changes):
    fields = dict(project="default", title="K12 doubles at 37 C", claim="Growth doubled after 20 minutes.",
                  scope="Strain K12 in one medium.", limitations="One laboratory, three runs.", evidence_ids=cited_ids)
    fields.update(changes)
    return workspace.feedback.propose(**fields)


def review(workspace, draft, decision="accept", **changes):
    arguments = dict(expected_revision=draft["revision"], proposal_hash=draft["proposal_hash"], decision=decision, confirmed=True)
    arguments.update(changes)
    return workspace.feedback.review(draft["id"], **arguments)


def retract(workspace, note, confirmed=True):
    return workspace.feedback.retract(note["id"], expected_revision=note["revision"], confirmed=confirmed)


def events(workspace, name):
    with workspace.store.tx() as db:
        return [dict(row) for row in db.execute("SELECT * FROM events WHERE event=? ORDER BY seq", (name,))]


def registry_entries(workspace):
    events = ArtifactRegistry(workspace.store.root / "artifact_registry_registry").read_events()
    return {event["payload"]["artifact_id"]: event["payload"] for event in events
            if event["event_type"] == "artifact_registered"}


def note_files(workspace):
    folder = workspace.store.root / "feedback_artifacts"
    return sorted(folder.glob("*.md")) if folder.exists() else []


# Proposing

def test_a_draft_waits_for_review_and_names_the_excerpts_it_cites(workspace, paper, cited):
    draft = propose(workspace, cited)
    assert draft["state"] == "review_pending" and draft["human_confirmed"] is False
    assert draft["origin"] == "desktop" and draft["derived_type"] == "synthesis"
    assert draft["parents"] == [{"id": i, "artifact_id": paper["id"], "content_hash": paper["content_hash"]} for i in sorted(cited)]
    assert [e["object_id"] for e in events(workspace, "KNOWLEDGE_FEEDBACK_PROPOSED")] == [draft["id"]]


def test_the_same_draft_twice_is_kept_once(workspace, cited):
    first = propose(workspace, cited)
    again = propose(workspace, list(reversed(cited)) + cited[:1], title="  K12 doubles at 37 C  ")
    assert again["id"] == first["id"]
    assert len(workspace.store.list("feedback", "default")) == 1


@pytest.mark.parametrize("changes, error", [
    ({"title": ""}, "FEEDBACK_FIELDS_REQUIRED"),
    ({"scope": "   "}, "FEEDBACK_FIELDS_REQUIRED"),
    ({"claim": "x" * 16001}, "FEEDBACK_FIELDS_REQUIRED"),
    ({"limitations": None}, "FEEDBACK_FIELDS_REQUIRED"),
    ({"evidence_ids": []}, "FEEDBACK_EVIDENCE_REQUIRED"),
    ({"evidence_ids": "ev_1"}, "FEEDBACK_EVIDENCE_REQUIRED"),
    ({"evidence_ids": ["ev_missing"]}, "EVIDENCE_NOT_FOUND"),
    ({"derived_type": "opinion"}, "DERIVED_TYPE_INVALID"),
    ({"language_context": {"policy_version": "x"}}, "LANGUAGE_CONTEXT_FIELDS_INVALID"),
])
def test_a_draft_needs_every_field_and_real_excerpts(workspace, cited, changes, error):
    with pytest.raises(ValueError, match=error):
        propose(workspace, cited, **changes)
    assert workspace.store.list("feedback", "default") == []


def test_a_draft_cites_at_most_24_excerpts(workspace, tmp_path, cited):
    long_paper = add_paper(workspace, tmp_path / "long", text="\n".join([PAPER] * 20))
    many = excerpt_ids(workspace, long_paper["id"])
    assert len(many) > 24
    with pytest.raises(ValueError, match="FEEDBACK_EVIDENCE_REQUIRED"):
        propose(workspace, many[:25])
    assert len(propose(workspace, many[:24])["parents"]) == 24


# Accepting and rejecting

def test_accepting_a_draft_makes_a_knowledge_note_that_can_be_cited(workspace, paper, cited):
    draft = propose(workspace, cited)
    note = review(workspace, draft)
    assert note["state"] == "active" and note["human_confirmed"] is True and note["reviewed_at"]
    artifact = workspace.store.get("artifact", note["artifact_id"])
    assert artifact["kind"] == "derived" and artifact["state"] == "active" and artifact["feedback_id"] == draft["id"]
    text = Path(artifact["path"]).read_text(encoding="utf-8")
    assert text.startswith("# K12 doubles at 37 C\n\nGrowth doubled after 20 minutes.\n\n## Scope\nStrain K12 in one medium.")
    assert "## Limitations\nOne laboratory, three runs." in text
    assert text.endswith("## Evidence\n" + "".join(f"- {i} · K12\n" for i in sorted(cited)))
    read = workspace.index.read(note["evidence_id"], "default")
    assert read["text"] == text and read["knowledge_review"]["admission_state"] == "active"
    entries = registry_entries(workspace)
    links = entries[note["artifact_id"]]["parent_artifacts"]
    assert sorted(link["evidence_basis"]["evidence_ref"] for link in links) == sorted(cited)
    [snapshot_id] = {link["parent_artifact_id"] for link in links}
    snapshot = entries[snapshot_id]
    assert snapshot["artifact_type"] == "research_source_snapshot"
    assert Path(snapshot["locator"]["path"]).read_bytes() == PAPER.encode("utf-8")
    assert [e["object_id"] for e in events(workspace, "KNOWLEDGE_ADMISSION_FEEDBACK_ACTIVE")] == [draft["id"]]


def test_repeating_an_accepted_review_changes_nothing(workspace, cited):
    draft = propose(workspace, cited)
    note = review(workspace, draft)
    assert review(workspace, draft) == note
    assert len(events(workspace, "KNOWLEDGE_ADMISSION_FEEDBACK_ACTIVE")) == 1


def test_rejecting_a_draft_makes_no_note(workspace, cited):
    draft = propose(workspace, cited)
    rejected = review(workspace, draft, decision="reject")
    assert rejected["state"] == "rejected" and rejected["human_confirmed"] is True
    assert "artifact_id" not in rejected and note_files(workspace) == []
    assert review(workspace, draft, decision="reject") == rejected
    with pytest.raises(ValueError, match="FEEDBACK_TRANSITION_INVALID"):
        review(workspace, draft, decision="accept", expected_revision=rejected["revision"])


@pytest.mark.parametrize("changes, error", [
    ({"confirmed": False}, "HUMAN_REVIEW_REQUIRED"),
    ({"confirmed": "yes"}, "HUMAN_REVIEW_REQUIRED"),
    ({"decision": "maybe"}, "HUMAN_REVIEW_REQUIRED"),
    ({"expected_revision": 99}, "REVISION_CONFLICT"),
    ({"proposal_hash": "0" * 64}, "REVISION_CONFLICT"),
])
def test_a_review_needs_confirmation_and_the_current_version(workspace, cited, changes, error):
    draft = propose(workspace, cited)
    with pytest.raises(ValueError, match=error):
        review(workspace, draft, **changes)
    assert workspace.store.get("feedback", draft["id"])["state"] == "review_pending"


def test_an_unknown_draft_cannot_be_reviewed(workspace, cited):
    draft = dict(propose(workspace, cited), id="fb_missing")
    with pytest.raises(ValueError, match="FEEDBACK_NOT_FOUND"):
        review(workspace, draft)


def test_a_draft_whose_source_changed_cannot_be_accepted(workspace, paper, cited):
    draft = propose(workspace, cited)
    Path(paper["path"]).write_bytes(PAPER.replace("37 C", "30 C").encode("utf-8"))
    with pytest.raises(ValueError, match="EVIDENCE_STALE"):
        review(workspace, draft)
    assert workspace.store.get("feedback", draft["id"])["state"] == "review_pending"
    assert note_files(workspace) == []


def test_the_note_headings_follow_the_draft_language(workspace, cited):
    draft = propose(workspace, cited, language_context=freeze(explicit_locale="zh-CN"))
    note = review(workspace, draft)
    text = Path(workspace.store.get("artifact", note["artifact_id"])["path"]).read_text(encoding="utf-8")
    assert "## 适用范围\n" in text and "## 局限\n" in text and "## 证据\n" in text


def test_a_note_can_cite_an_accepted_note(workspace, cited):
    first = review(workspace, propose(workspace, cited))
    second = review(workspace, propose(workspace, [first["evidence_id"]], title="Built on the first note"))
    entries = registry_entries(workspace)
    assert {p["parent_artifact_id"] for p in entries[second["artifact_id"]]["parent_artifacts"]} == {first["artifact_id"]}
    assert workspace.index.read(second["evidence_id"], "default")["kind"] == "derived"


# Retracting

def test_retracting_a_note_takes_it_out_of_the_evidence(workspace, cited):
    note = review(workspace, propose(workspace, cited))
    retracted = retract(workspace, note)
    assert retracted["state"] == "retracted"
    assert workspace.store.get("artifact", note["artifact_id"])["state"] == "retracted"
    with pytest.raises(ValueError, match="EVIDENCE_INACTIVE"):
        workspace.index.read(note["evidence_id"], "default")
    assert [e["object_id"] for e in events(workspace, "KNOWLEDGE_ADMISSION_FEEDBACK_RETRACTED")] == [note["id"]]


def test_retracting_needs_an_accepted_note_confirmation_and_the_current_version(workspace, cited):
    draft = propose(workspace, cited)
    with pytest.raises(ValueError, match="FEEDBACK_TRANSITION_INVALID"):
        retract(workspace, draft, confirmed=False)
    note = review(workspace, draft)
    with pytest.raises(ValueError, match="HUMAN_REVIEW_REQUIRED"):
        retract(workspace, note, confirmed=False)
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        retract(workspace, dict(note, revision=note["revision"] + 1))
    with pytest.raises(ValueError, match="FEEDBACK_NOT_FOUND"):
        retract(workspace, dict(note, id="fb_missing"))
    assert workspace.store.get("feedback", note["id"])["state"] == "active"


# Revisions

def revise(workspace, draft, **changes):
    evidence_ids = changes.pop("evidence_ids", None)
    return workspace.feedback_revise(draft["id"], draft["revision"], changes, evidence_ids)


def test_a_revision_replaces_the_accepted_note_once_accepted(workspace, cited):
    note = review(workspace, propose(workspace, cited))
    revision = revise(workspace, note, claim="Growth doubled after 25 minutes.")
    assert revision["state"] == "review_pending" and revision["predecessor"] == note["id"]
    assert revision["origin"] == "desktop_revision" and revision["title"] == note["title"]
    assert workspace.store.get("feedback", note["id"])["state"] == "active"
    accepted = review(workspace, revision)
    old = workspace.store.get("feedback", note["id"])
    assert accepted["state"] == "active"
    assert old["state"] == "retracted" and old["superseded_by"] == revision["id"]
    assert workspace.store.get("artifact", note["artifact_id"])["state"] == "retracted"
    assert workspace.index.read(accepted["evidence_id"], "default")["kind"] == "derived"


def test_a_revision_of_a_pending_draft_closes_the_draft_when_accepted(workspace, cited):
    draft = propose(workspace, cited)
    revision = revise(workspace, draft, title="A better title")
    review(workspace, revision)
    old = workspace.store.get("feedback", draft["id"])
    assert old["state"] == "rejected" and old["superseded_by"] == revision["id"]
    assert [e["object_id"] for e in events(workspace, "KNOWLEDGE_ADMISSION_FEEDBACK_SUPERSEDED")] == [draft["id"]]


def test_only_one_revision_of_a_note_can_be_accepted(workspace, cited):
    note = review(workspace, propose(workspace, cited))
    one = revise(workspace, note, claim="Version one.")
    two = revise(workspace, note, claim="Version two.")
    review(workspace, one)
    with pytest.raises(ValueError, match="FEEDBACK_REVISION_SUPERSEDED"):
        review(workspace, two)


def test_a_revision_cannot_cite_the_note_it_replaces(workspace, cited):
    note = review(workspace, propose(workspace, cited))
    revision = revise(workspace, note, evidence_ids=[note["evidence_id"]])
    with pytest.raises(ValueError, match="FEEDBACK_REVISION_PARENT_CONFLICT"):
        review(workspace, revision)
    assert workspace.store.get("feedback", note["id"])["state"] == "active"


def test_a_revision_keeps_only_the_fields_a_person_may_edit(workspace, cited):
    draft = propose(workspace, cited)
    with pytest.raises(ValueError, match="FEEDBACK_FIELDS_INVALID"):
        revise(workspace, draft, origin="desktop")
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        workspace.feedback_revise(draft["id"], draft["revision"] + 1, {"title": "x"})


# Drafts that answer an agent hand-off

@pytest.fixture
def handed_off(workspace, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("Samples were incubated at 37 C.\nGrowth doubled after 20 minutes.\n", encoding="utf-8")
    thread = workspace.thread_create()
    workspace.attachments.attach(thread["id"], [str(note)])
    for future in list(workspace.futures.values()):
        future.result(timeout=60)
    [artifact] = workspace.store.list("artifact", "default")
    pack = workspace.handoffs.create(thread["id"], "What changed?")
    draft = workspace.submit_draft("default", "From the agent", "Growth doubled.", "This note.", "One source.",
                                   excerpt_ids(workspace, artifact["id"]), handoff_id=pack["id"],
                                   handoff_hash=pack["content_hash"], request_id="request-0001")
    return thread, pack, draft


def test_a_handoff_draft_is_bound_to_its_pack(workspace, handed_off):
    thread, pack, draft = handed_off
    assert draft["origin"] == "external_agent"
    assert draft["handoff_binding"] == {"handoff_id": pack["id"], "handoff_hash": pack["content_hash"],
                                        "version_binding": pack["version_binding"]}
    assert review(workspace, draft)["state"] == "active"


def test_a_handoff_draft_cannot_be_accepted_once_the_conversation_changed(workspace, handed_off):
    thread, pack, draft = handed_off
    current = workspace.thread_get(thread["id"])
    workspace.attachments.configure(current["id"], current["revision"], {"scope": "conversation"})
    with pytest.raises(ValueError):
        review(workspace, draft)
    assert workspace.store.get("feedback", draft["id"])["state"] == "review_pending"
    assert review(workspace, draft, decision="reject")["state"] == "rejected"
