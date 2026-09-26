"""Exact-claim logical omission after one producer repair and independent Delta review."""
from dataclasses import asdict, replace
from retrieval.ownership import stable_sha256
from research_analysis.revision import bind_successor
from research_analysis.analysis import _analysis_quality_metrics
from evidence_review.ownership_binding import build_ownership_review_task


def finalize_delta(previous, revised, pack, initial, receipt, delta):
    """Retain disputed content in tombstones; never convert a missing review into approval."""
    build_ownership_review_task(previous, legacy_data_ownership='self')
    build_ownership_review_task(revised, legacy_data_ownership='self')
    initial_task = build_ownership_review_task(previous, legacy_data_ownership='self')
    expected_targets = {row['i'] for row in initial.get('rulings', [])
                        if row.get('gpt_sent') is True and row.get('agrees') is False}
    target_list = receipt['target_indexes']
    targets = set(target_list)
    if (initial.get('deferred') or initial.get('drives_knowledge_admission') is not False
            or initial.get('ownership_review_task', {}).get('analysis_hash') != initial_task.analysis_hash
            or any(type(i) is not int or not 0 <= i < len(previous.literature_support) for i in target_list)
            or len(targets) != len(target_list) or targets != expected_targets
            or len(revised.literature_support) != len(previous.literature_support)):
        raise RuntimeError('ANALYSIS_INITIAL_REVIEW_OR_TARGETS_UNBOUND')
    expected_unchanged = {str(i): stable_sha256(asdict(claim))
                          for i, claim in enumerate(previous.literature_support) if i not in targets}
    if receipt.get('unchanged_claim_hashes') != expected_unchanged:
        raise RuntimeError('ANALYSIS_UNCHANGED_SET_INCOMPLETE')
    rulings = delta.get('rulings', [])
    indexes = [row.get('i') for row in rulings]
    if (pack.context_pack_hash != previous.input_context_pack_hash
            or pack.context_pack_hash != revised.input_context_pack_hash
            or not targets or delta.get('deferred') or set(indexes) != targets
            or len(indexes) != len(targets) or set(delta.get('gpt_picked', [])) != targets
            or delta.get('drives_knowledge_admission') is not False
            or delta.get('ownership_review_task', {}).get('analysis_hash') != revised.analysis_hash
            or receipt.get('base_analysis_hash') != previous.analysis_hash
            or receipt.get('successor_analysis_hash') != revised.analysis_hash
            or any(row.get('gpt_sent') is not True or type(row.get('agrees')) is not bool for row in rulings)):
        raise RuntimeError('ANALYSIS_DELTA_INCOMPLETE_OR_UNBOUND')
    unresolved = {row['i'] for row in rulings if row['agrees'] is False}
    if any(not row.get('disputes') for row in rulings if row['i'] in unresolved):
        raise RuntimeError('ANALYSIS_OMISSION_REASON_MISSING')
    kept = [i for i in range(len(revised.literature_support)) if i not in unresolved]
    if not kept:
        raise RuntimeError('ANALYSIS_OMISSION_WOULD_REMOVE_ALL_SUPPORT')
    # Do not remove the last support for any declared facet. Unknown coverage is
    # kept unknown, and omission never turns it into a completeness claim.
    lost_facets = {f for i in unresolved for f in revised.literature_support[i].facet_ids}
    kept_facets = {f for i in kept for f in revised.literature_support[i].facet_ids}
    if lost_facets - kept_facets:
        raise RuntimeError('ANALYSIS_OMISSION_WOULD_REMOVE_FACET_SUPPORT')
    unchanged = receipt['unchanged_claim_hashes']
    if any(stable_sha256(asdict(revised.literature_support[int(i)])) != h for i, h in unchanged.items()):
        raise RuntimeError('ANALYSIS_OMISSION_UNCHANGED_DRIFT')
    by_index = {row['i']: row for row in rulings}
    original_rulings = {row['i']: row for row in initial['rulings']}
    tombstones = []
    for i in sorted(unresolved):
        value = {'original_index': i, 'original_claim': asdict(previous.literature_support[i]),
                 'repaired_claim': asdict(revised.literature_support[i]),
                 'initial_disputes': original_rulings[i].get('disputes'),
                 'delta_disputes': by_index[i]['disputes'],
                 'base_analysis_hash': previous.analysis_hash, 'revised_analysis_hash': revised.analysis_hash,
                 'context_pack_hash': pack.context_pack_hash,
                 'disposition': 'WITHHELD_FROM_LITERATURE_SUPPORT_PENDING_HUMAN_REVIEW',
                 'original_and_source_immutable': True}
        value['tombstone_id'] = 'TS-' + stable_sha256(value).split(':')[-1][:24]
        tombstones.append(value)
    final = revised
    if tombstones:
        blocks = {b.source['chunk_id']: b for b in pack.blocks}
        claims = tuple(revised.literature_support[i] for i in kept)
        final = bind_successor(replace(revised, literature_support=claims,
            quality_metrics=_analysis_quality_metrics(claims, blocks, pack),
            coverage_status='not_assessed', answer_complete=False,
            coverage_assessment_ref=None, coverage_assessment_hash=None))
    task = build_ownership_review_task(final, legacy_data_ownership='self')
    mapping = {old: new for new, old in enumerate(kept)}
    retained = []
    for row in initial['rulings']:
        old = row['i']
        if old in unresolved:
            continue
        item = dict(by_index.get(old, row))
        item.update(i=mapping[old], original_index=old,
                    retained_claim_sha256=stable_sha256(asdict(final.literature_support[mapping[old]])))
        retained.append(item)
    trace = [{'i': i, 'chunk_id': c.source_id.get('chunk_id'),
              'paper_id': c.source_id.get('paper_id'), 'field': c.source_id.get('field'),
              'resolved': c.source_id.get('chunk_id') in {b.source['chunk_id'] for b in pack.blocks}}
             for i, c in enumerate(final.literature_support)]
    combined = {**initial, 'rulings': retained, 'gpt_picked': [row['i'] for row in retained if row.get('gpt_sent')],
        'trace': trace, 'trace_total': len(trace), 'trace_resolved': sum(row['resolved'] for row in trace),
        'ownership_review_task': asdict(task), 'ownership_propagation_receipt': task.ownership_propagation_receipt,
        'target': {**initial.get('target', {}), 'analysis_hash': final.analysis_hash},
        'omissions': tombstones,
        'directed_revision': {'status': 'PASS_WITH_OMISSIONS' if tombstones else 'DELTA_PASS',
            'base_analysis_hash': previous.analysis_hash, 'revised_analysis_hash': revised.analysis_hash,
            'successor_analysis_hash': final.analysis_hash, 'target_indexes': sorted(targets),
            'omitted_original_indexes': sorted(unresolved), 'index_mapping': mapping,
            'unchanged_claim_hashes': unchanged,
            'scope': 'Original representative review plus exact changed-claim Delta. Unreviewed claims remain unreviewed; no whole-analysis acceptance.'}}
    return final, combined, tombstones


def omission_notice(tombstones, language):
    if not tombstones:
        return ''
    labels = {'zh': ('审核留痕删除', '下列判断经定向修复和复审后仍未获得支持，已移出文献支持段，原文和审核理由保留供人工核对。'),
              'en': ('Claims withheld after review', 'These claims remained disputed after targeted repair and Delta review. They were removed from the literature-support section; original claims, sources, and reasons remain available for human review.'),
              'ja': ('レビュー後に保留された主張', '部分修正と再レビュー後も根拠が確認できない主張を文献根拠の節から外しました。元の主張、出典、理由は人による確認のため保持します。')}
    title, message = labels.get(language, labels['en'])
    return '\n\n## ' + title + '\n\n' + message + '\n\n' + '\n'.join(
        '- ' + row['tombstone_id'] + ': ' + row['repaired_claim']['text']
        + ' [' + str(row['repaired_claim']['source_id'].get('chunk_id')) + ']'
        for row in tombstones)
