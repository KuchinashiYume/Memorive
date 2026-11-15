"""The cheaper idle-poll reads must answer exactly like the full reads they replace.

ReviewStore.states() decodes a job only when its stored revision changed since the last call;
it must stay equal to jobs() after the store's own writes, after another store object (another
thread or process) writes, and for a store opened fresh. InboxStore.list_intents(state) and
has_pending_intent(item_id) must agree with filtering the full intent list.
"""
from memorive_inbox.store import InboxStore
from memorive_review.store import ReviewStore


def _create(store, job_id, *, logic=True):
    return store.create(job_id, item_id="item-" + job_id, source_sha256="A" * 64,
                        config={"rules": ["R1"]}, logic_selected=logic, source_name=job_id + ".pdf")


def test_review_states_follow_own_writes(tmp_path):
    store = ReviewStore(tmp_path)
    assert store.states() == store.jobs() == []
    _create(store, "job-a")
    _create(store, "job-b", logic=False)
    assert store.states() == store.jobs()
    store.update("job-a", "DATA_STARTED", {"data_state": "RUNNING"})
    assert store.states() == store.jobs()
    assert [job["data_state"] for job in store.states()] == ["RUNNING", "WAITING_INPUT"]
    store.update("job-a", "DATA_DONE", {"data_state": "SUCCEEDED", "mainline_complete": True})
    store.update("job-b", "PAUSED", {"paused": True})
    assert store.states() == store.jobs()


def test_review_states_see_writes_from_another_store_object(tmp_path):
    reader, writer = ReviewStore(tmp_path), ReviewStore(tmp_path)
    _create(writer, "job-a")
    assert reader.states() == writer.jobs()          # first sight of the job
    writer.update("job-a", "DATA_STARTED", {"data_state": "RUNNING"})
    assert reader.states() == writer.jobs()          # revision moved: decoded again
    _create(writer, "job-b")
    writer.update("job-b", "CANCEL", {"cancel_requested": True})
    states = reader.states()
    assert states == writer.jobs()
    assert [job["job_id"] for job in states] == ["job-a", "job-b"]


def test_review_states_of_a_fresh_store_match_jobs(tmp_path):
    first = ReviewStore(tmp_path)
    for name in ("job-a", "job-b", "job-c"):
        _create(first, name)
    first.update("job-b", "DATA_STARTED", {"data_state": "RUNNING"})
    first.states()                                   # warm one cache, then open a new store
    fresh = ReviewStore(tmp_path)
    assert fresh.states() == fresh.jobs() == first.jobs()


def test_review_states_unchanged_jobs_are_not_reread(tmp_path):
    store = ReviewStore(tmp_path)
    _create(store, "job-a")
    _create(store, "job-b")
    before = store.states()
    store.update("job-b", "DATA_STARTED", {"data_state": "RUNNING"})
    after = store.states()
    assert after[0] is before[0]                     # job-a kept its decoded value
    assert after[1] is not before[1] and after[1]["data_state"] == "RUNNING"


def _intent(number, item_id, state="PENDING"):
    return {
        "schema_version": "InboxDispatchIntent-v1",
        "intent_id": f"intent_{number:024d}",
        "item_id": item_id,
        "idempotency_key": f"key-{number}",
        "params": {"effect": "OTHER", "n": number},
        "params_sha256": f"{number:064d}",
        "state": state,
        "created_at": "2026-11-14T00:00:00Z",
        "updated_at": "2026-11-14T00:00:00Z",
        "provider_calls": 0,
        "external_model_calls": 0,
    }


def test_pending_intents_match_the_full_list(tmp_path):
    store = InboxStore(tmp_path / "inbox.sqlite3")
    assert store.list_intents("PENDING") == [] and not store.has_pending_intent("item-1")
    for number, item_id in ((1, "item-1"), (2, "item-2"), (3, "item-1"), (4, "item-3")):
        store.enqueue_intent(_intent(number, item_id))
    store.update_intent("key-1", "DISPATCHED", {"job_id": "job-1"})
    store.update_intent("key-4", "FAILED", {"error_code": "X"})
    every = store.list_intents()
    assert [i["intent_id"] for i in every] == sorted(i["intent_id"] for i in every)
    for state in ("PENDING", "DISPATCHED", "FAILED"):
        assert store.list_intents(state) == [i for i in every if i["state"] == state]
    assert [i["item_id"] for i in store.list_intents("PENDING")] == ["item-2", "item-1"]
    assert store.has_pending_intent("item-1")        # key-3 is still pending
    assert store.has_pending_intent("item-2")
    assert not store.has_pending_intent("item-3")    # its only intent failed
    assert not store.has_pending_intent("item-unknown")
    store.update_intent("key-3", "DISPATCHED", {"job_id": "job-3"})
    assert not store.has_pending_intent("item-1")


def test_pending_intents_seen_by_another_store_object(tmp_path):
    path = tmp_path / "inbox.sqlite3"
    writer, reader = InboxStore(path), InboxStore(path)
    writer.enqueue_intent(_intent(1, "item-1"))
    assert reader.has_pending_intent("item-1") and len(reader.list_intents("PENDING")) == 1
    writer.update_intent("key-1", "DISPATCHED", {"job_id": "job-1"})
    assert not reader.has_pending_intent("item-1") and reader.list_intents("PENDING") == []
