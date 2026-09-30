"""Configurable desktop successor; historical Research frozen policies stay immutable."""
import math,copy,re
from datetime import datetime,timezone
from memorive_research_workspace.store import digest,now,packed

WEIGHTS={'Similarity':.45,'Semantic':.20,'Manual':.15,'Rule':.10,'Note':.07,'Citation':.03}
RELEVANCE=('Similarity','Semantic');AUTHORITY=('Manual','Rule','Note','Citation')
PENALTIES={'synthesis':.65,'concept':.8,'annotation':.95}
WEIGHT_LIMITS={'Similarity':[30,60],'Semantic':[10,30],'Manual':[5,25],'Rule':[5,20],'Note':[0,15],'Citation':[0,10]}
RELEVANCE_LIMITS=[60,85]
PRESETS=[
    {'id':'balanced','label':'均衡研究','description':'兼顾问题匹配与已有积累','weights':[45,20,15,10,7,3]},
    {'id':'locate','label':'原文定位','description':'优先查找术语与原文片段','weights':[60,15,5,10,7,3]},
    {'id':'compare','label':'跨篇比较','description':'关注不同表述之间的联系','weights':[40,30,10,10,5,5]},
    {'id':'reuse','label':'积累复用','description':'增加已标记资料的参考作用','weights':[35,25,20,10,7,3]},
]
DEFAULT={'weights':WEIGHTS,'penalties':PENALTIES,'semantic_profile_ref':None,'embedding_profile_ref':None,'semantic_enabled':True,
         'review_days':180,'interest_days':30,'interest_half_life':7,'interest_boost':.08,'keep_counterevidence':True,'rule_enrichment':True,'rule_credential_env':'OPENALEX_API_KEY'}

def validate(config,*,enforce_limits=True):
    if isinstance(config,dict):config={'rule_enrichment':True,'rule_credential_env':'OPENALEX_API_KEY',**config}
    if not isinstance(config,dict) or set(config)!=set(DEFAULT):raise ValueError('WEIGHT_SETTINGS_INVALID')
    if not isinstance(config['weights'],dict) or not isinstance(config['penalties'],dict) or set(config['weights'])!=set(WEIGHTS) or set(config['penalties'])!=set(PENALTIES):raise ValueError('WEIGHT_SETTINGS_INVALID')
    def number(v):return type(v) in (int,float) and math.isfinite(v)
    if any(not number(v) or not 0<=v<=1 for v in config['weights'].values()) or abs(sum(config['weights'].values())-1)>1e-6:raise ValueError('WEIGHT_TOTAL_REQUIRED')
    if sum(config['weights'][p] for p in RELEVANCE)<=0:raise ValueError('WEIGHT_RELEVANCE_REQUIRED')
    if enforce_limits and any(not low-1e-7<=config['weights'][key]*100<=high+1e-7 for key,(low,high) in WEIGHT_LIMITS.items()):raise ValueError('WEIGHT_RANGE_INVALID')
    relevance=sum(config['weights'][p]*100 for p in RELEVANCE)
    if enforce_limits and not RELEVANCE_LIMITS[0]-1e-7<=relevance<=RELEVANCE_LIMITS[1]+1e-7:raise ValueError('WEIGHT_RELEVANCE_RANGE_INVALID')
    if any(not number(v) or not 0<=v<=1 for v in config['penalties'].values()):raise ValueError('WEIGHT_PENALTY_INVALID')
    for key in ('embedding_profile_ref','semantic_profile_ref'):
        if config[key] is not None and (not isinstance(config[key],str) or not 1<=len(config[key])<=500):raise ValueError('WEIGHT_PROFILE_INVALID')
    for key in ('semantic_enabled','keep_counterevidence','rule_enrichment'):
        if type(config[key]) is not bool:raise ValueError('WEIGHT_SETTINGS_INVALID')
    if not isinstance(config['rule_credential_env'],str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}',config['rule_credential_env']):raise ValueError('WEIGHT_SETTINGS_INVALID')
    for key,low,high in [('review_days',7,730),('interest_days',7,90),('interest_half_life',1,60)]:
        if type(config[key]) is not int or not low<=config[key]<=high:raise ValueError('WEIGHT_SETTINGS_INVALID')
    if not number(config['interest_boost']) or not 0<=config['interest_boost']<=.2:raise ValueError('WEIGHT_SETTINGS_INVALID')
    return copy.deepcopy(config)

def aggregate(signals,config,penalty=1):
    """Normalize missing signals inside their group, never across authority/relevance."""
    weights=config['weights'];report={};sums={}
    for group,providers in [('relevance',RELEVANCE),('authority',AUTHORITY)]:
        mass=sum(weights[p] for p in providers)
        observed={p:signals.get(p) for p in providers if signals.get(p) is not None}
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in observed.values()):raise ValueError('WEIGHT_SIGNAL_INVALID')
        available_mass=sum(weights[p] for p in observed)
        # No authority evidence is unknown, not a declaration of poor quality.
        prior=.5 if group=='authority' and not available_mass else 0
        value=mass*sum(weights[p]*v for p,v in observed.items())/available_mass if available_mass else mass*prior
        sums[group]=value*(penalty if group=='authority' else 1)
        for p in providers:
            effective=mass*weights[p]/available_mass if p in observed and available_mass else 0
            report[p]={'value':observed.get(p),'state':'observed' if p in observed else 'unknown','weight':weights[p],
                       'effective_weight':effective,'contribution':effective*observed.get(p,0)*(penalty if group=='authority' else 1)}
    return {'score':round(sum(sums.values()),7),'signals':report,'relevance_score':sums['relevance'],'authority_score':sums['authority'],
            'derived_penalty':penalty,'missing_policy':'renormalize_within_group; no_authority=neutral_prior'}

class ResearchPolicy:
    def __init__(self,store):self.store=store

    def settings(self,project=None):
        with self.store.tx() as db:
            if project is not None and not self.store.get('project',project,db=db):raise ValueError('PROJECT_NOT_FOUND')
            global_row=self.store.get('weight_policy','global',db=db)
            local=self.store.get('weight_policy',project,db=db) if project else None
            row=local if local and not local.get('inherit') else global_row
            config=copy.deepcopy((row or {}).get('config',DEFAULT))
            config.setdefault('rule_enrichment',True);config.setdefault('rule_credential_env','OPENALEX_API_KEY')
            return {'config':config,'revision':(local if project else global_row or {}).get('revision',0) if (local if project else global_row) else 0,
                    'global_revision':(global_row or {}).get('revision',0),'project':project,'inherited':bool(project and (not local or local.get('inherit'))),
                    'policy_hash':digest(config),'defaults':copy.deepcopy(DEFAULT),'limits':copy.deepcopy(WEIGHT_LIMITS),
                    'relevance_limits':list(RELEVANCE_LIMITS),'presets':copy.deepcopy(PRESETS)}

    def save(self,config,expected_revision,project=None,inherit=False):
        if type(inherit) is not bool or (inherit and not project):raise ValueError('WEIGHT_SETTINGS_INVALID')
        config=validate(config,enforce_limits=not inherit)
        with self.store.tx() as db:
            if project is not None and not self.store.get('project',project,db=db):raise ValueError('PROJECT_NOT_FOUND')
            key=project or 'global';old=self.store.get('weight_policy',key,db=db)
            value={'config':config,'inherit':inherit,'changed_at':now(),'policy_hash':digest(config)}
            row=self.store.put('weight_policy',key,project or '',value,expected=expected_revision,db=db)
            self.store.put('weight_policy_history',key+':'+str(row['revision']),project or '',dict(value,previous_hash=(old or {}).get('policy_hash')),expected=0,db=db)
            self.store.event('DECISION_LOG_WEIGHT_POLICY_CHANGED',key,{'old_hash':(old or {}).get('policy_hash'),'new_hash':value['policy_hash'],'revision':row['revision']},db)
        return self.settings(project)

    def metadata(self,project,artifact_id):
        settings=self.settings(project)
        with self.store.tx() as db:
            a=self.store.get('artifact',artifact_id,db=db)
            if not a or a['project']!=project:raise ValueError('MATERIAL_OUTSIDE_PROJECT')
            doc=a.get('document_id') or a.get('source_binding',{}).get('document_id') or artifact_id
            row=self.store.get('research_metadata',project+':'+doc,db=db)
            return {'artifact_id':artifact_id,'document_id':doc,'project':project,'revision':(row or {}).get('revision',0),
                    'bibliography':self.store.get('research_rule',project+':'+doc,db=db),'manual_score':(row or {}).get('manual_score'),'manual_reason':(row or {}).get('manual_reason',''),
                    'annotations':(row or {}).get('annotations',[]),'citations':(row or {}).get('citations',[]),'rule':(row or {}).get('rule',{}),
                    'history':[r for r in self.store.list('research_metadata_history',project,db=db) if r.get('document_id')==doc],
                    'effective_penalty':settings['config']['penalties'].get(a.get('derived_type','synthesis'),.65) if a['kind']=='derived' else 1,
                    'penalty_override':(row or {}).get('penalty_override'), 'penalty_reason':(row or {}).get('penalty_reason','')}

    def metadata_save(self,project,artifact_id,expected_revision,changes):
        allowed={'manual_score','manual_reason','annotations','citations','rule','penalty_override','penalty_reason'}
        if not isinstance(changes,dict) or not changes or set(changes)-allowed:raise ValueError('WEIGHT_METADATA_INVALID')
        value=self.metadata(project,artifact_id);value.pop('history',None);value.pop('bibliography',None);value.pop('effective_penalty',None);value.update(changes)
        if value['manual_score'] is not None:
            if type(value['manual_score']) not in (int,float) or not math.isfinite(value['manual_score']) or not 0<=value['manual_score']<=100:raise ValueError('MANUAL_WEIGHT_INVALID')
            if not isinstance(value['manual_reason'],str) or not value['manual_reason'].strip():raise ValueError('MANUAL_REASON_REQUIRED')
        if value['penalty_override'] is not None:
            if type(value['penalty_override']) not in (int,float) or not 0<=value['penalty_override']<=1:raise ValueError('WEIGHT_PENALTY_INVALID')
            if not isinstance(value['penalty_reason'],str) or not value['penalty_reason'].strip():raise ValueError('PENALTY_REASON_REQUIRED')
        for key in ('manual_reason','penalty_reason'):
            if not isinstance(value[key],str) or len(value[key])>4000:raise ValueError('WEIGHT_METADATA_INVALID')
        for key in ('annotations','citations'):
            if not isinstance(value[key],list) or len(value[key])>500:raise ValueError('WEIGHT_METADATA_INVALID')
            for item in value[key]:
                if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or len(packed(item))>16000:raise ValueError('WEIGHT_METADATA_INVALID')
            value[key]=list({item['id']:item for item in value[key]}.values())
        rule=value['rule']
        if not isinstance(rule,dict) or set(rule)-{'score','source','observed_at','year','journal','doc_type','journal_metric','author_metrics','cited_by'}:raise ValueError('RULE_METADATA_INVALID')
        if rule and not (isinstance(rule.get('source'),str) and rule['source'].strip() and isinstance(rule.get('observed_at'),str)):raise ValueError('RULE_SOURCE_REQUIRED')
        if rule:
            try:datetime.fromisoformat(rule['observed_at'].replace('Z','+00:00'))
            except ValueError:raise ValueError('RULE_SOURCE_REQUIRED')
        metric=rule.get('journal_metric')
        if metric is not None:
            if not isinstance(metric,dict) or metric.get('kind') not in {'jcr_jif','openalex_2yr_mean_citedness'} or type(metric.get('value')) not in (int,float) or not math.isfinite(metric['value']) or not 0<=metric['value']<=10000 or type(metric.get('year')) is not int or not 1900<=metric['year']<=datetime.now(timezone.utc).year or not metric.get('source_id'):raise ValueError('RULE_METRIC_INVALID')
        if 'author_metrics' in rule:
            authors=rule['author_metrics']
            if not isinstance(authors,dict) or not authors.get('provider') or not isinstance(authors.get('authors'),list) or not 1<=len(authors['authors'])<=100:raise ValueError('RULE_METRIC_INVALID')
            for author in authors['authors']:
                if not isinstance(author,dict) or type(author.get('h_index')) is not int or not 0<=author['h_index']<=10000 or not author.get('id'):raise ValueError('RULE_METRIC_INVALID')
        if rule.get('score') is not None:
            if type(rule['score']) not in (int,float) or not math.isfinite(rule['score']) or not 0<=rule['score']<=1 or not rule.get('source') or not rule.get('observed_at'):raise ValueError('RULE_SOURCE_REQUIRED')
        with self.store.tx() as db:
            doc=value['document_id'];value['changed_at']=now();value['actor']='local_user';row=self.store.put('research_metadata',project+':'+doc,project,value,expected=expected_revision,db=db)
            self.store.put('research_metadata_history',project+':'+doc+':'+str(row['revision']),project,dict(value,metadata_revision=row['revision']),expected=0,db=db)
            self.store.event('DECISION_LOG_RESEARCH_METADATA_CHANGED',doc,{'revision':row['revision'],'fields':sorted(changes),'reason_hash':digest([value['manual_reason'],value['penalty_reason']])},db)
            if 'penalty_override' in changes:self.store.event('KNOWLEDGE_ADMISSION_DERIVED_PENALTY_CHANGED',artifact_id,{'metadata_revision':row['revision'],'decision_ref':doc+':'+str(row['revision'])},db)
        return self.metadata(project,artifact_id)

    def signals(self,artifact,lexical,semantic,config,db):
        doc=artifact.get('document_id') or artifact.get('source_binding',{}).get('document_id') or artifact['id']
        m=self.store.get('research_metadata',artifact['project']+':'+doc,db=db) or {}
        annotations=m.get('annotations',[]);citations=m.get('citations',[])
        if artifact.get('source_binding'):
            source=self.store.get('connector_document',artifact['source_binding'].get('source_id',''),db=db) or {}
            annotations=annotations+source.get('annotations',[])
        distinct={digest(a.get('text',a)) for a in annotations if a}
        own_citations={c['id']:c for c in citations if c.get('type') in {'data','method','argument'}}
        cite=sum({'data':1,'method':1.5,'argument':2}[c['type']] for c in own_citations.values())
        from .research_rule import score as rule_score
        observation=self.store.get('research_rule',artifact['project']+':'+doc,db=db) or {}
        merged_fields={**observation.get('fields',{}),**{k:v for k,v in m.get('rule',{}).items() if k not in {'score','source','observed_at'}}}
        automatic=rule_score({'fields':merged_fields}) if observation.get('status')=='observed' or m.get('rule') else None
        signals={'Similarity':lexical,'Semantic':semantic,'Manual':m['manual_score']/100 if m.get('manual_score') is not None else None,
                 'Rule':m.get('rule',{}).get('score',automatic),'Note':min(1,math.log1p(len(distinct))/math.log(21)) if distinct else None,
                 'Citation':min(1,math.log1p(cite)/math.log(21)) if own_citations else None}
        penalty=1
        if artifact['kind']=='derived':
            penalty=config['penalties'].get(artifact.get('derived_type','synthesis'),.65)
            if m.get('penalty_override') is not None:penalty=m['penalty_override']
        return aggregate(signals,config,penalty)
