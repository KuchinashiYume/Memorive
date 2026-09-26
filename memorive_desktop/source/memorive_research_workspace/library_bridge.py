"""Select exact existing mainline products; never scan profile or operations roots."""
from pathlib import Path

def bind(workspace,library):
    def candidates():
        library._sync_core_artifacts()
        rows=[]
        for identity,entry in library._core_entries_by_artifact_id.items():
            if Path(entry['private_path']).suffix.lower() not in {'.md','.txt','.pdf','.json'}:continue
            if entry.get('artifact_kind') not in {'CORE_CARD','CORE_ANALYSIS','CORE_RAW_DOCUMENT','CORE_CLEAN_DOCUMENT'}:continue
            rows.append({'id':identity,'title':entry['row']['display_name'],'kind':entry['artifact_kind'],
                'content_hash':entry['content_sha256'],'state':entry['row']['status'],'document_id':'doc_'+entry.get('source_content_sha256',entry['content_sha256'])})
        return {'items':rows,'source':'EXISTING_Core_BOUND_ARTIFACTS'}
    def add(project,artifacts):
        if not isinstance(artifacts,list) or not 1<=len(artifacts)<=32:raise ValueError('LIBRARY_SELECTION_REQUIRED')
        available={r['id']:r for r in candidates()['items']};rows=[];failures=[]
        for request in artifacts:
            identity=request['id'];item=available.get(identity)
            if not item or request.get('content_hash')!=item['content_hash']:raise ValueError('LIBRARY_SELECTION_STALE')
            path=library.resolve_core_private_artifact(identity)
            entry=library._core_entries_by_artifact_id[identity]
            try:
                row=workspace.index.add_library_file(project,path=path,root=entry['artifact_root'],external_id=identity,
                    expected_hash=item['content_hash'],title=item['title'],
                    document_id='doc_'+entry.get('source_content_sha256',entry['content_sha256']))
                rows.append({'id':row['id'],'title':row['title']})
            except (ValueError,OSError) as exc:failures.append({'id':identity,'code':str(exc)[:120]})
        return {'status':'PARTIAL' if failures else 'PASS','indexed':len(rows),'items':rows,'failures':failures}
    workspace.library_list=candidates;workspace.library_add=add
