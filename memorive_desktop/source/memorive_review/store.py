"""Durable review state and immutable report publication beside Core jobs."""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from .engine import digest

def now():return datetime.now(timezone.utc).isoformat()
def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def atomic(path:Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    data=value if isinstance(value,bytes) else (packed(value)+'\n').encode('utf-8')
    with temporary.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

TERMINAL={'COMPLETED','COMPLETED_LIMITED','SKIPPED','CANCELLED','FAILED','UNCERTAIN'}
def initial_state(selected:bool):return 'WAITING_INPUT' if selected else 'SKIPPED'

class ReviewStore:
    def __init__(self,core_root):
        self.root=Path(core_root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'literature_review.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS review_jobs(job_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS review_reports(report_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS review_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS review_dispositions(operation_id TEXT PRIMARY KEY,report_id TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS review_cache(cache_key TEXT PRIMARY KEY,payload TEXT NOT NULL);''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row;db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:yield db
        finally:db.close()
    def create(self,job_id,*,item_id,source_sha256,config,logic_selected,source_name=''):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,191}',job_id):raise ValueError('REVIEW_JOB_ID_INVALID')
        value=dict(schema_version='LiteratureReviewJob-v1',job_id=job_id,item_id=item_id,source_sha256=source_sha256,source_name=source_name,
                   config=deepcopy(config),logic_selected=bool(logic_selected),data_state='WAITING_INPUT',logic_state=initial_state(logic_selected),
                   mainline_complete=False,mainline_receipt=None,logic_attempt=1,paused=False,cancel_requested=False,
                   data_report_id=None,logic_report_id=None,created_at=now(),updated_at=now(),revision=0)
        value['intent_sha256']=digest({k:value[k] for k in ('item_id','source_sha256','config','logic_selected')})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM review_jobs WHERE job_id=?',(job_id,)).fetchone()
            if row:
                old=json.loads(row['payload'])
                if old['intent_sha256']!=value['intent_sha256']:raise ValueError('REVIEW_SNAPSHOT_REPLAY_CONFLICT')
                return old
            db.execute('INSERT INTO review_jobs VALUES(?,?,?)',(job_id,0,packed(value)))
            self._event(db,job_id,'CREATED',{'logic_selected':logic_selected,'intent_sha256':value['intent_sha256']})
        return value
    @staticmethod
    def _event(db,job_id,kind,value):
        db.execute('INSERT INTO review_events(job_id,kind,created_at,payload) VALUES(?,?,?,?)',(job_id,kind,now(),packed(value)))
    def get(self,job_id):
        with self.connect() as db:row=db.execute('SELECT payload FROM review_jobs WHERE job_id=?',(job_id,)).fetchone()
        return json.loads(row['payload']) if row else None
    def jobs(self):
        with self.connect() as db:rows=db.execute('SELECT payload FROM review_jobs ORDER BY rowid').fetchall()
        return [json.loads(r['payload']) for r in rows]
    def update(self,job_id,event,changes,*,expected_revision=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM review_jobs WHERE job_id=?',(job_id,)).fetchone()
            if not row:raise ValueError('REVIEW_JOB_NOT_FOUND')
            value=json.loads(row['payload'])
            if expected_revision is not None and value['revision']!=expected_revision:raise ValueError('REVIEW_VERSION_CONFLICT')
            if set(changes)&{'job_id','item_id','source_sha256','source_name','intent_sha256','config','logic_selected','created_at'}:raise ValueError('REVIEW_SNAPSHOT_IMMUTABLE')
            value.update(deepcopy(changes));value['revision']+=1;value['updated_at']=now()
            db.execute('UPDATE review_jobs SET revision=?,payload=? WHERE job_id=?',(value['revision'],packed(value),job_id))
            self._event(db,job_id,event,{'revision':value['revision'],'changes':changes})
        return value
    def cache(self,key,value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR IGNORE INTO review_cache VALUES(?,?)',(key,packed(value)))
            row=db.execute('SELECT payload FROM review_cache WHERE cache_key=?',(key,)).fetchone()
        return json.loads(row['payload']) if row else None
    def publish(self,job_id,report,*,binder):
        from memorive_workflow.contracts import CoreArtifact
        value=deepcopy(report);report_id=value['report_id'];kind=value['kind']
        if kind not in {'DATA','LOGIC'} or not re.fullmatch(r'[a-z]+-[A-Fa-f0-9]{24,64}',report_id):raise ValueError('REVIEW_REPORT_ID_INVALID')
        if value.get('report_sha256')!=digest({k:v for k,v in value.items() if k!='report_sha256'}):raise ValueError('REVIEW_REPORT_HASH_INVALID')
        root=self.root/job_id/'artifacts';root.mkdir(parents=True,exist_ok=True)
        state=self.get(job_id)
        if state.get('source_name'):
            from memorive_folder_management.policy import document_folder
            root=document_folder(root,paper_id='DOC_'+state['source_sha256'][:16].upper(),title=Path(state['source_name']).stem)
            root.mkdir(parents=True,exist_ok=True)
        prefix='core-data-review' if kind=='DATA' else 'core-logic-review'
        paths=[(prefix+'-'+report_id,root/(report_id+'.md'),'CORE_'+kind+'_REVIEW',render(report).encode('utf-8')),
               (prefix+'-record-'+report_id,root/(report_id+'.json'),'CORE_'+kind+'_REVIEW_RECORD',(packed(report)+'\n').encode('utf-8'))]
        with self.connect() as db:
            old=db.execute('SELECT payload FROM review_reports WHERE report_id=?',(report_id,)).fetchone()
            if old and json.loads(old['payload'])!=value:raise ValueError('REVIEW_REPORT_IMMUTABLE_CONFLICT')
            db.execute('INSERT OR IGNORE INTO review_reports VALUES(?,?,?,?,?)',(report_id,job_id,kind,'STAGING',packed(value)))
        # Re-entering after failure only publishes already saved bytes; no inference.
        for artifact_id,path,artifact_kind,raw in paths:
            if path.exists():
                if path.read_bytes()!=raw:raise ValueError('REVIEW_REPORT_FILE_CONFLICT')
            else:atomic(path,raw)
            display_name=render(value).splitlines()[0].removeprefix('# ') if value.get('language_context') else ('数据分析摘要' if kind=='DATA' else '逻辑分析报告')
            binder(CoreArtifact(artifact_id,artifact_kind,path,digest(raw),display_name))
        with self.connect() as db:
            db.execute("UPDATE review_reports SET state='PUBLISHED' WHERE report_id=?",(report_id,))
            self._event(db,job_id,'REPORT_PUBLISHED',{'report_id':report_id,'kind':kind,'sha256':value['report_sha256']})
        return self.update(job_id,'REPORT_READY',{('data_report_id' if kind=='DATA' else 'logic_report_id'):report_id,
                                               ('data_state' if kind=='DATA' else 'logic_state'):value['status']})
    def report(self,report_id):
        with self.connect() as db:row=db.execute("SELECT payload FROM review_reports WHERE report_id=? AND state='PUBLISHED'",(report_id,)).fetchone()
        if not row:raise ValueError('REVIEW_REPORT_NOT_PUBLISHED')
        return json.loads(row['payload'])
    def dispositions(self,report_id):
        with self.connect() as db:rows=db.execute('SELECT payload FROM review_dispositions WHERE report_id=? ORDER BY created_at,operation_id',(report_id,)).fetchall()
        return [json.loads(row['payload']) for row in rows]
    def source_status(self,report):
        state=self.get(report['source_id']) or {}
        latest=state.get('data_report_id' if report['kind']=='DATA' else 'logic_report_id')
        data_changed=report['kind']=='LOGIC' and report.get('data_report_id')!=state.get('data_report_id')
        return {'stale':bool(state.get('stale') or (latest and latest!=report['report_id']) or data_changed),
                'data_version_superseded':bool(data_changed),'latest_report_id':latest}
    def disposition(self,report_id,*,finding_id,action,reason,evidence,operation_id):
        report=self.report(report_id)
        if finding_id not in {f['finding_id'] for f in report['findings']}:raise ValueError('REVIEW_FINDING_NOT_FOUND')
        if action not in {'EXPLAINED','CONFIRMED_REPORT_ISSUE','NEEDS_MATERIAL','DEFERRED','REOPENED'}:raise ValueError('REVIEW_DISPOSITION_INVALID')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>4000:raise ValueError('REVIEW_REASON_REQUIRED')
        if not isinstance(evidence,str) or len(evidence)>8000 or (action=='EXPLAINED' and not evidence.strip()):raise ValueError('REVIEW_EXPLANATION_EVIDENCE_REQUIRED')
        value=dict(report_id=report_id,report_sha256=report['report_sha256'],finding_id=finding_id,action=action,reason=reason,evidence=evidence,operation_id=operation_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');prior=db.execute('SELECT payload FROM review_dispositions WHERE operation_id=?',(operation_id,)).fetchone()
            if prior:
                previous=json.loads(prior['payload'])
                if any(previous[k]!=v for k,v in value.items()):raise ValueError('REVIEW_DISPOSITION_IDEMPOTENCY_CONFLICT')
                return previous
            value['created_at']=now();db.execute('INSERT INTO review_dispositions VALUES(?,?,?,?)',(operation_id,report_id,value['created_at'],packed(value)))
        return value
    def public(self,job_id):
        value=self.get(job_id)
        if not value:return None
        public_keys={'schema_version','job_id','item_id','source_sha256','logic_selected','data_state','logic_state',
            'mainline_complete','logic_attempt','paused','cancel_requested','data_report_id','logic_report_id',
            'created_at','updated_at','revision','segment_count','completed_segments','reason_code','data_reason_code','stale'}
        result={k:v for k,v in value.items() if k in public_keys}
        model=value.get('config',{}).get('nodes',{}).get('E2_LOGIC_REVIEW',{}).get('execution_profile',{}).get('model',{})
        result['model_label']=' · '.join(str(model[k]) for k in ('model_name','thinking_mode') if model.get(k))
        for kind in ('data','logic'):
            report_id=value.get(kind+'_report_id')
            if report_id:
                report=self.report(report_id);changes=self.dispositions(report_id)
                latest={r['finding_id']:r for r in changes};open_findings=[f for f in report['findings'] if latest.get(f['finding_id'],{}).get('action')!='EXPLAINED']
                result[kind+'_report']={k:report.get(k) for k in ('report_id','kind','status','color','scope','report_sha256')}
                result[kind+'_report'].update(human_disposition_count=len(changes),unresolved_findings=len(open_findings),
                    source_stale=bool(value.get('stale')),stale=bool(value.get('stale')),machine_color_preserved=True)
                result[kind+'_report'].update(self.source_status(report))
        return result

def render(report):
    if report.get("language_context"):
        from memorive_language.source_reports import render as render_source_report
        return render_source_report(report)
    return render_legacy(report)

def render_legacy(report):
    heading='数据分析摘要' if report['kind']=='DATA' else '逻辑分析报告'
    colors={'GREEN':'已检查范围未见重要疑点','YELLOW':'存在需要澄清的疑点','RED':'存在重要疑点，建议优先复核',None:'核查受限；没有风险色结论'}
    lines=[f'# {heading}','',colors[report.get('color')],f'状态：{report["status"]}',f'报告：{report["report_id"]}',
           f'来源 SHA-256：{report["source_sha256"]}','','## 已检查范围','',json.dumps(report['scope'],ensure_ascii=False,sort_keys=True,indent=2),'',
           '本报告为派生复核意见。统计相容不证明实验真实发生，不相容不判断作者动机。最终科研判断由人完成。','']
    if report.get('descriptions'):
        d=report['descriptions'];lines+=['## 数值描述','',f'共提取 {d["record_count"]} 个数值对象、{d["table_count"]} 个表格；最小值 {d["minimum"]}，最大值 {d["maximum"]}。',
            '这些对象可能来自不同研究量，不代表同一研究样本。重复值与末位分布仅作描述。','',
            '| 原样数值 | 原文位置 | 单位字面值 |','| --- | --- | --- |']
        for r in report['records']:lines.append(f'| {r["raw"]} | L{r["source"]["line"]}:C{r["source"]["column"]} | {r["unit_literal"]} |')
        lines+=['']
    if report.get('numeric_blocks'):
        lines+=['## 数值列与格式摘要','','以下仅按原文列分组，不推断同一研究样本或合并实验单位。','']
        for column in report['numeric_blocks']['table_columns']:
            lines+=[f'- L{column["header_line"]} / {column["header_literal"]}：{column["record_count"]} 个数值；范围 {column["minimum"]} — {column["maximum"]}。']
        lines+=['',f'字面加减对：{len(report["numeric_blocks"]["plus_minus_pairs"])}；未据此推断 SD/SE 类型。','']
    global_review=report.get('scope',{}).get('global_relationship_check')
    if global_review:
        lines+=['## 跨章节关系检查','',{'FULL_SOURCE_CHECKED':'全文原文已在同一次请求联合检查。','CHECKED':'分段后已另行核验跨段原文关系。','LIMITED':'全局关系检查受限。','NOT_RUN':'全局关系检查未完成。'}.get(global_review['status'],global_review['status']),'']
        for relation in global_review.get('checks',[]):
            lines+=[relation['relation'],relation['reasoning'],'']
            for citation in relation['citations']:lines+=[f'> L{citation["line"]}：{citation["quote"]}','']
    labels={'COMPATIBLE':'给定条件下相容','INCONSISTENT':'给定条件下不相容','INSUFFICIENT_INFORMATION':'信息不足','INPUT_ERROR':'输入问题','NOT_APPLICABLE':'不适用'}
    for check in report.get('checks',[]):
        lines += [f'### {check["rule_id"]} · {labels.get(check["status"],check["status"])}','']
        if check.get('calculation',{}).get('formula'):lines+=['计算：'+check['calculation']['formula'],'']
        if check.get('missing'):lines+=['缺少参数：'+', '.join(check['missing']),'']
        if check.get('reason'):lines+=['输入说明：'+check['reason'],'']
        lines+=['```json',json.dumps(check,ensure_ascii=False,sort_keys=True,indent=2),'```','']
    for finding in report.get('findings',[]):
        lines += [f'### {finding.get("claim") or finding["finding_id"]}','',finding.get('reasoning') or finding.get('statement',''),'']
        if finding.get('alternative_explanation'):lines+=['解释与反例：'+finding['alternative_explanation'],'']
        if report.get('decision_policy_version') and finding.get('escalation_limited'):lines+=['分级受限说明：'+finding['escalation_limited'],'']
        for reference in finding.get('citations',[]):lines += [f'> L{reference["line"]}：{reference["quote"]}','']
    if report.get('missing_parameters'):lines+=['## 缺少参数','',json.dumps(report['missing_parameters'],ensure_ascii=False,sort_keys=True,indent=2)]
    return '\n'.join(lines)+'\n'
