"""Adding processed papers from the library to a research project.

Document processing leaves a card, an analysis, the original and the cleaned text of each paper in
the library. library_list shows those files; library_add puts the chosen ones into the project's
search index (workspace.index.add_library_file). The files come from the library controller
(memorive/documents/library/product_controller.py: core_artifacts and resolve_core_private_artifact); no
folder is scanned.
"""
from pathlib import Path

# The products offered as research material, and what each is called in the index.
MATERIAL_LAYERS = {'CORE_CARD': 'card', 'CORE_ANALYSIS': 'analysis', 'CORE_RAW_DOCUMENT': 'original',
                   'CORE_CLEAN_DOCUMENT': 'cleaned'}
READABLE_SUFFIXES = {'.md', '.txt', '.pdf', '.json'}
MAX_FILES_PER_ADD = 32


def bind(workspace, library):
    """Give the workspace library_list and library_add."""

    def offered_files():
        return {artifact_id: record for artifact_id, record in library.core_artifacts().items()
                if record['artifact_kind'] in MATERIAL_LAYERS
                and Path(record['private_path']).suffix.lower() in READABLE_SUFFIXES}

    def library_list():
        items = [{'id': artifact_id, 'title': record['row']['display_name'], 'kind': record['artifact_kind'],
                  'content_hash': record['content_sha256'], 'state': record['row']['status'],
                  'document_id': 'doc_' + document_hash(record)}
                 for artifact_id, record in offered_files().items()]
        return {'items': items, 'source': 'EXISTING_Core_BOUND_ARTIFACTS'}

    def library_add(project, artifacts):
        """Add the chosen files. Each choice names a file id and the content hash shown in the list."""
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_FILES_PER_ADD:
            raise ValueError('LIBRARY_SELECTION_REQUIRED')
        offered = offered_files()
        added, failures = [], []
        for choice in artifacts:
            artifact_id = choice['id']
            record = offered.get(artifact_id)
            # The file changed or went away after the list was shown: the user chooses again.
            if record is None or choice.get('content_hash') != record['content_sha256']:
                raise ValueError('LIBRARY_SELECTION_STALE')
            path = library.resolve_core_private_artifact(artifact_id)
            try:
                row = workspace.index.add_library_file(
                    project, path=path, root=record['artifact_root'], external_id=artifact_id,
                    expected_hash=record['content_sha256'], title=record['row']['display_name'],
                    document_id='doc_' + document_hash(record), source_content_hash=document_hash(record),
                    material_layer=MATERIAL_LAYERS[record['artifact_kind']],
                    structure_bundle=page_layout(record, path))
            except (ValueError, OSError) as exc:
                failures.append({'id': artifact_id, 'code': str(exc)[:120]})
                continue
            added.append({'id': row['id'], 'title': row['title']})
        return {'status': 'PARTIAL' if failures else 'PASS', 'indexed': len(added), 'items': added,
                'failures': failures}

    workspace.library_list = library_list
    workspace.library_add = library_add


def document_hash(record):
    """The hash of the paper a file was made from; it groups a paper's files into one document."""
    return record.get('source_content_sha256', record['content_sha256'])


def page_layout(record, path):
    """The page layout saved next to an original document; the index uses it to cite pages."""
    folder = Path(path).parent / 'structure'
    if record['artifact_kind'] == 'CORE_RAW_DOCUMENT' and (folder / 'bundle.json').is_file():
        return folder
    return None
