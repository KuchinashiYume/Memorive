from pathlib import Path
import sys, os, json, pickle, dataclasses, copy, unittest
import tempfile
_TEMP = tempfile.TemporaryDirectory(prefix='memorive-closure-regression-')
ROOT=Path(_TEMP.name)
sys.dont_write_bytecode=True
os.environ['MEMORIVE_RUNTIME_LOG_LOG_DIR']=str(ROOT/'ops/runtime_log')
from research_analysis.revision import bind_successor
from research_analysis.types import LiteratureClaim
from retrieval.ownership import stable_sha256
from memorive.documents.card_review.analysis_omission import finalize_delta, omission_notice

def read(p):return json.loads(p.read_text(encoding='utf-8'))

class ClosureTests(unittest.TestCase):
 def setUp(self):
  from tests.modules.research_analysis.test_coverage_aware_analysis_p0 import pack, block, response
  from research_analysis.analysis import analyze
  from research_analysis.revision import revise_analysis
  from memorive.documents.card_review.ownership_binding import build_ownership_review_task
  self.pack=pack(block('Paper#a',section='Results',facet_ids=(),source_role='direct_result',span_id='a'),
      block('Paper#b',section='Results',facet_ids=(),source_role='direct_result',span_id='b'),coverage_status='not_assessed')
  answer=response([{'text':'Yield increased by 80 percent.','source_chunk_id':'Paper#a'},
                   {'text':'Yield increased by 20 percent.','source_chunk_id':'Paper#b'}])
  self.prev=analyze(self.pack,'What changed?',data_ownership='self',model_call=lambda _:answer,region_check=lambda:None)
  self.initial={'rulings':[{'i':0,'gpt_sent':True,'agrees':False,'disputes':[{'detail':'Unsupported magnitude'}]}],
      'gpt_picked':[0],'drives_knowledge_admission':False,'deferred':[],
      'ownership_review_task':dataclasses.asdict(build_ownership_review_task(self.prev,legacy_data_ownership='self'))}
  output={'repairs':[{'index':0,'text':'The paper reports evidence.','source_chunk_id':'Paper#a'}]}
  self.rev,self.rec=revise_analysis(self.prev,self.pack,self.initial,model_call=lambda _:{'text':json.dumps(output)},region_check=lambda:None)
  self.delta={'rulings':[{'i':0,'gpt_sent':True,'agrees':True,'disputes':[]}],
      'gpt_picked':[0],'drives_knowledge_admission':False,'deferred':[],
      'ownership_review_task':dataclasses.asdict(build_ownership_review_task(self.rev,legacy_data_ownership='self'))}
 def run_final(self):return finalize_delta(self.prev,self.rev,self.pack,self.initial,self.rec,self.delta)
 def reject(self):
  self.delta['rulings'][0].update(agrees=False,disputes=[{'type':'unsupported','detail':'OFFLINE CONTROLLED UNRESOLVED TEST','quote':''}])
 def test_success_path_uses_bound_synthetic_delta(self):
  a,b,t=self.run_final();self.assertEqual(a,self.rev);self.assertEqual(t,[]);self.assertEqual(b['directed_revision']['status'],'DELTA_PASS')
 def test_omission_retains_original_and_only_removes_target(self):
  self.reject();before=pickle.dumps((self.prev,self.rev,self.delta));a,b,t=self.run_final()
  self.assertEqual(a.literature_support,self.rev.literature_support[1:]);self.assertEqual(len(t),1)
  self.assertEqual(t[0]['original_claim'],dataclasses.asdict(self.prev.literature_support[0]))
  self.assertEqual(b['ownership_review_task']['analysis_hash'],a.analysis_hash)
  self.assertEqual(b['trace_total'],1);self.assertFalse(a.answer_complete)
  self.assertEqual(before,pickle.dumps((self.prev,self.rev,self.delta)))
  self.assertIn(t[0]['tombstone_id'],omission_notice(t,'en'))
  for field in ('question','ai_general_knowledge','risks_and_uncertainties','recheck_locations','flagged_unsourced'):
   self.assertEqual(getattr(a,field),getattr(self.rev,field))
 def test_missing_delta_is_not_omission(self):
  self.delta['rulings']=[]
  with self.assertRaisesRegex(RuntimeError,'INCOMPLETE'):self.run_final()
 def test_deferred_delta_is_not_omission(self):
  self.delta['deferred']=['unavailable']
  with self.assertRaisesRegex(RuntimeError,'INCOMPLETE'):self.run_final()
 def test_stale_delta_is_not_omission(self):
  self.delta['ownership_review_task']['analysis_hash']='sha256:wrong'
  with self.assertRaisesRegex(RuntimeError,'UNBOUND'):self.run_final()
 def test_no_remaining_support_blocks(self):
  self.reject();self.prev=bind_successor(dataclasses.replace(self.prev,literature_support=self.prev.literature_support[:1]))
  self.rev=bind_successor(dataclasses.replace(self.rev,literature_support=self.rev.literature_support[:1]))
  self.rec.update(base_analysis_hash=self.prev.analysis_hash,successor_analysis_hash=self.rev.analysis_hash,unchanged_claim_hashes={})
  self.delta['ownership_review_task']['analysis_hash']=self.rev.analysis_hash
  self.initial['ownership_review_task']['analysis_hash']=self.prev.analysis_hash
  with self.assertRaisesRegex(RuntimeError,'REMOVE_ALL'):self.run_final()
 def test_last_facet_support_blocks(self):
  self.reject();claims=list(self.rev.literature_support);claims[0]=dataclasses.replace(claims[0],facet_ids=('essential',))
  self.rev=bind_successor(dataclasses.replace(self.rev,literature_support=tuple(claims)))
  self.rec['successor_analysis_hash']=self.rev.analysis_hash;self.delta['ownership_review_task']['analysis_hash']=self.rev.analysis_hash
  with self.assertRaisesRegex(RuntimeError,'FACET_SUPPORT'):self.run_final()

 def test_initial_review_mismatch_blocks(self):
  self.initial['ownership_review_task']['analysis_hash']='sha256:stale'
  with self.assertRaisesRegex(RuntimeError,'UNBOUND'):self.run_final()
 def test_missing_unchanged_binding_blocks(self):
  self.rec['unchanged_claim_hashes']={}
  with self.assertRaisesRegex(RuntimeError,'UNCHANGED_SET'):self.run_final()

 def test_complete_revision_replays_checkpoint_without_model_calls(self):
  from memorive.documents.card_review.analysis_revision import complete_analysis_revision
  from memorive.documents.card_review.judgment import VerifierCandidate
  import types
  calls=[]
  def revise(payload):
   calls.append('repair');return {'text':json.dumps({'repairs':[{'index':0,'text':'The paper reports evidence.','source_chunk_id':'Paper#a'}]})}
  def verify(role,payload):
   calls.append('delta');return json.dumps({'agrees':True,'disputes':[]})
  bridge=types.SimpleNamespace(select_judgment_candidates=lambda _:('anthropic',[VerifierCandidate('verify_judgment','openai','test-verifier',True)]))
  folder=Path(tempfile.mkdtemp(dir=ROOT))
  kwargs=dict(bridge=bridge,evidence_root=folder,output_language='en',model_call=revise,verifier_call=verify,region_check=lambda:None)
  chunks={b.source['chunk_id']:b.content for b in self.pack.blocks}
  first=complete_analysis_revision(self.prev,self.pack,self.initial,chunks,**kwargs)
  second=complete_analysis_revision(self.prev,self.pack,self.initial,chunks,**kwargs)
  self.assertEqual(calls,['repair','delta']);self.assertEqual(first[0],second[0])
  self.assertEqual(json.loads(json.dumps(first[1])),second[1])
  self.assertEqual(first[1]['directed_revision']['status'],'DELTA_PASS')
  self.assertTrue((folder/'tombstones.json').is_file())
  (folder/'analysis.pkl').write_bytes(b'corrupt')
  with self.assertRaisesRegex(RuntimeError,'TAMPERED'):
   complete_analysis_revision(self.prev,self.pack,self.initial,chunks,**kwargs)

if __name__=='__main__' :unittest.main(verbosity=2)
