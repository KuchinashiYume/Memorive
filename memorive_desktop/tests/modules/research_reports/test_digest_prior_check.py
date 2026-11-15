"""A corrected report (revision 2 onward) must name the frozen report it replaces.

`_validate_prior` runs before a digest is built: the first revision stands alone; a correction
needs the predecessor's id, kind, revision and both hashes, and the predecessor's common object
must still hash to what was recorded.
"""
from __future__ import annotations

import copy

import pytest

from research_reports.canonical import typed_payload_hash
from research_reports.digest import DigestAuthorityError, _validate_prior

PRIOR_ID = "digest-daily-2026-11-02-r1"


def _prior(**changes):
    payload = {"object_id": PRIOR_ID, "schema_version": "2.0", "revision": 1, "summary": "daily change log"}
    payload["content_hash"] = typed_payload_hash(payload)
    value = {"digest_id": PRIOR_ID, "digest_kind": "daily", "revision": 1, "raw_sha256": "A" * 64,
             "common_payload_hash": payload["content_hash"]["value"],
             "common_object": {"object_type": "ChangeDigest", "payload": payload}}
    value.update(changes)
    return value


def _check(revision=2, supersedes=PRIOR_ID, prior=None, kind="daily"):
    return _validate_prior(digest_kind=kind, revision=revision, supersedes_digest_id=supersedes,
                           prior_digest=_prior() if prior is None else prior)


def test_a_first_revision_stands_alone():
    assert _validate_prior(digest_kind="daily", revision=1, supersedes_digest_id=None, prior_digest=None) == (None, None)


def test_a_correction_binds_to_its_frozen_predecessor():
    prior_id, binding = _check()
    assert prior_id == PRIOR_ID
    assert binding == {"digest_id": PRIOR_ID, "revision": 1, "raw_sha256": "A" * 64,
                       "common_payload_hash": _prior()["common_payload_hash"]}


@pytest.mark.parametrize("revision", [0, -1, True, 1.0, "2"])
def test_a_revision_must_be_a_positive_integer(revision):
    with pytest.raises(DigestAuthorityError, match="positive integer"):
        _validate_prior(digest_kind="daily", revision=revision, supersedes_digest_id=None, prior_digest=None)


@pytest.mark.parametrize("supersedes, prior", [(PRIOR_ID, None), (None, "prior")])
def test_a_first_revision_cannot_claim_a_predecessor(supersedes, prior):
    with pytest.raises(DigestAuthorityError, match="base digest"):
        _validate_prior(digest_kind="daily", revision=1, supersedes_digest_id=supersedes,
                        prior_digest=_prior() if prior else None)


def _stale_common():
    value = _prior()
    value["common_object"]["payload"]["summary"] = "edited after freezing"
    return value


def _rehashed(field, item):
    value = _prior()
    payload = value["common_object"]["payload"]
    payload[field] = item
    payload["content_hash"] = typed_payload_hash(payload)
    value["common_payload_hash"] = payload["content_hash"]["value"]
    return value


@pytest.mark.parametrize("kwargs, message", [
    (dict(prior="not a dict"), "frozen prior-digest authority object"),
    (dict(prior={k: v for k, v in _prior().items() if k != "raw_sha256"}), "fields mismatch"),
    (dict(prior=dict(_prior(), extra=1)), "fields mismatch"),
    (dict(kind="weekly"), "kind differs"),
    (dict(supersedes=None), "supersedes ID"),
    (dict(supersedes="digest-other"), "supersedes ID"),
    (dict(prior=_prior(digest_id="")), "supersedes ID"),
    (dict(supersedes="", prior=_prior(digest_id="")), "supersedes ID"),
    (dict(revision=3), "prior revision plus one"),
    (dict(prior=_prior(revision=True)), "prior revision plus one"),
    (dict(prior=_prior(raw_sha256="a" * 64)), "invalid prior raw_sha256"),
    (dict(prior=_prior(raw_sha256="A" * 63)), "invalid prior raw_sha256"),
    (dict(prior=_prior(common_payload_hash="0" * 64)), "prior common payload hash is stale"),
    (dict(prior=_prior(common_payload_hash="x" * 64)), "invalid prior common_payload_hash"),
    (dict(prior=_prior(common_object={"object_type": "Report"})), "prior common object is missing"),
    (dict(prior=_prior(common_object=None)), "prior common object is missing"),
    (dict(prior=_rehashed("object_id", "digest-other")), "prior common identity/revision mismatch"),
    (dict(prior=_rehashed("revision", 2)), "prior common identity/revision mismatch"),
    (dict(prior=_stale_common()), "prior common payload hash is stale"),
])
def test_a_correction_without_a_matching_frozen_predecessor_is_refused(kwargs, message):
    with pytest.raises(DigestAuthorityError, match=message):
        _check(**kwargs)


def test_the_predecessor_given_is_not_changed():
    prior = _prior()
    before = copy.deepcopy(prior)
    _check(prior=prior)
    assert prior == before
