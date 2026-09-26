"""Whole-document candidates for an explicitly scoped single-paper task.

Question similarity alone is inadequate for a document-level analysis. Extend
the already admitted same-paper set with its authoritative chunk sidecar. The
existing ownership and Context Pack budget gates remain in force.
"""
from dataclasses import replace
import re
from .structured_context import (
    _neighbor_candidate, _is_review_card, infer_source_role,
    is_high_confidence_bibliography,
)
from .ownership import bind_authority_projection, snapshot_fields
from .types import Candidate, CandidateSet


def bind_document_card(card, paper_id, card_sha256):
    """Use the same immutable Card authority as candidate_retrieval.

    A parsed frontmatter object's reduced hash is a different authority from
    the admitted Card file hash. Bind only absent legacy references; explicit
    ownership assertions retain their own identity and conflict checks.
    """
    declared = card.get('paper_id') or (card.get('source_anchor') or {}).get('paper_id')
    if declared != paper_id or not re.fullmatch(r'[0-9a-f]{64}', card_sha256):
        raise ValueError('DOCUMENT_CONTEXT_CARD_BINDING_MISMATCH')
    return dict(card, paper_id=paper_id,
                ownership_revision=card.get('ownership_revision', 1),
                ownership_assertion_ref=card.get('ownership_assertion_ref') or f'OS-CARD-{card_sha256[:24]}',
                ownership_basis_ref=card.get('ownership_basis_ref') or f'card-sha256:{card_sha256}')


def document_candidates_from_sidecar(chunks, card, paper_id):
    """Build the same-paper coverage set when only Chroma's HNSW reader failed."""
    if not chunks or card.get('paper_id') != paper_id:
        raise ValueError('DOCUMENT_CONTEXT_SIDECAR_BINDING_MISMATCH')
    review = _is_review_card(card)
    candidates = []
    seen = set()
    for row in chunks:
        chunk_id = row.get('chunk_id')
        if (
            row.get('paper_id') != paper_id
            or not isinstance(chunk_id, str)
            or not chunk_id.startswith(paper_id + '#')
            or chunk_id in seen
            or not str(row.get('text') or '').strip()
        ):
            raise ValueError('DOCUMENT_CONTEXT_SIDECAR_SCOPE_INVALID')
        seen.add(chunk_id)
        subject_ref = f'chunk:{paper_id}:{chunk_id}'
        ownership = bind_authority_projection(
            subject_ref=subject_ref,
            authoritative_value=card.get('data_ownership'),
            assertion_ref=card.get('ownership_assertion_ref'),
            revision=card.get('ownership_revision'),
            basis_ref=card.get('ownership_basis_ref'),
            projection_value=row.get('data_ownership'),
            projection_revision=row.get('ownership_revision'),
            projection_ref=f'chunk-sidecar:{chunk_id}',
        )
        authority = ownership.ownership_contributors[0]
        candidate = Candidate(
            chunk_id=chunk_id, paper_id=paper_id,
            text=str(row['text']), distance=1.0, admission='active',
            section_path=row.get('section_path'), block_type=row.get('block_type'),
            page_start=row.get('page_start'), page_end=row.get('page_end'),
            metadata_status='chunk_sidecar',
            retrieval_reasons=('single_paper_sidecar_hnsw_fallback',),
            source_role=row.get('source_role') or infer_source_role(
                row.get('section_path'), row.get('block_type'), row.get('text'),
                document_is_review=review,
            ),
            ownership_assertion_ref=authority.assertion_ref,
            ownership_revision=authority.revision,
            ownership_basis_ref=authority.basis_ref,
            ownership_projection_value=row.get('data_ownership'),
            ownership_projection_revision=row.get('ownership_revision'),
            **snapshot_fields(ownership),
        )
        if not is_high_confidence_bibliography(candidate):
            candidates.append(candidate)
    if not candidates:
        raise ValueError('DOCUMENT_CONTEXT_SIDECAR_EMPTY_AFTER_FILTER')
    return CandidateSet(
        candidates=tuple(candidates), target_active_count=len(candidates),
        raw_recall_count=len(chunks), filtered_non_active_count=0,
        final_active_count=len(candidates), reached_overfetch_cap=False,
        require_verified=False, scope_mode='paper_id', scope_paper_id=paper_id,
    )


def document_candidates(candidate_set, chunks, card):
    paper_id = candidate_set.scope_paper_id
    if candidate_set.scope_mode != 'paper_id' or not paper_id or not candidate_set.candidates:
        raise ValueError('DOCUMENT_CONTEXT_REQUIRES_ADMITTED_SINGLE_PAPER_SCOPE')
    seeds = {c.chunk_id:c for c in candidate_set.candidates}
    if any(c.paper_id != paper_id for c in seeds.values()):
        raise ValueError('DOCUMENT_CONTEXT_SOURCE_SCOPE_MISMATCH')
    seed = next(iter(seeds.values()))
    rows_by_id = {row.get('chunk_id'):row for row in chunks}
    if not set(seeds).issubset(rows_by_id):
        raise ValueError('DOCUMENT_CONTEXT_RECALLED_SOURCE_MISSING')
    review = _is_review_card(card)
    for row in chunks:
        if row.get('paper_id') != paper_id or not str(row.get('chunk_id','')).startswith(paper_id+'#'):
            raise ValueError('DOCUMENT_CONTEXT_SOURCE_SCOPE_MISMATCH')
        original = seeds.get(row['chunk_id'])
        # Compare the authoritative vector binding against the exact sidecar
        # projection, then use that sidecar binding for all downstream views.
        # Different vector/sidecar projection digests must not be merged as
        # duplicate authorities for one chunk.
        item = _neighbor_candidate(row, original or seed, document_is_review=review)
        # Ownership is inherited from this paper, never the seed's topical
        # field, model score, field credibility, or semantic verification.
        if original is None:
            item = replace(item, distance=1.0, field=None, credibility=None,
                           verified_by_m6=False, rerank_score=None, facet_ids=(),
                           retrieval_reasons=('single_paper_document_coverage',))
        if not is_high_confidence_bibliography(item):
            seeds[item.chunk_id] = item
        elif original is not None:
            del seeds[item.chunk_id]
    return replace(candidate_set, candidates=tuple(seeds.values()),
                   final_active_count=len(seeds), target_active_count=len(seeds))
