"""Starter API metadata adapter; official OpenAPI fields, no full-text acquisition."""
import json
from urllib.parse import urlsplit

ENDPOINT = 'https://api.clarivate.com/apis/wos-starter/v1/documents'

def parse(body):
    value=json.loads(body)
    if not isinstance(value,dict) or not isinstance(value.get('hits'),list):
        raise ValueError('WOS_RESPONSE_SCHEMA_INVALID')
    records=[];skipped=[]
    for i,item in enumerate(value['hits']):
        if not isinstance(item,dict):raise ValueError('WOS_DOCUMENT_INVALID')
        uid=item.get('uid');title=item.get('title')
        if not isinstance(uid,str) or not uid or not isinstance(title,str) or not title.strip():
            skipped.append({'item_index':i,'reason_codes':['CORE_IDENTIFIER_OR_TITLE_MISSING']});continue
        identifiers=item.get('identifiers') or {}
        names=item.get('names') or {}
        authors=[{'display_name':a.get('displayName') or a.get('wosStandard'),'given':None,'family':None}
                 for a in names.get('authors',[]) if isinstance(a,dict) and (a.get('displayName') or a.get('wosStandard'))]
        locations=[]
        locator=(item.get('links') or {}).get('record')
        if isinstance(locator,str) and urlsplit(locator).scheme=='https' and not urlsplit(locator).username:
            locations.append({'kind':'METADATA_RECORD','locator':locator,'materialized':False,'rights_status':'LOCATION_CLAIM_ONLY'})
        # Starter exposes year/month, not necessarily an exact publication day.
        # Do not fabricate January 1 or a precision the provider did not supply.
        partial=['bibliographic.published_date','bibliographic.updated_date','bibliographic.abstract']
        if not authors:partial.append('bibliographic.authors')
        records.append({'source_record_id':uid,
            'identifiers':{'arxiv_id':None,'doi':identifiers.get('doi'),'pmid':identifiers.get('pmid'),'source_record_id':uid},
            'bibliographic':{'title':title,'authors':authors,'published_date':None,'updated_date':None,
                             'abstract':None,'record_type':'; '.join(item.get('types') or ['unknown'])},
            'location_claims':locations,'partial_fields':partial})
    return {'records':records,'skipped_records':skipped}
