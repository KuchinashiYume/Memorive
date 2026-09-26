"""Provider model IDs are public protocol values, not application code names."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
from memorive_settings.model_capabilities import infer_api_model_capability,workflow_node_accepts_capability
from model_gateway.gateway import _canonical_embed_model

class ExternalModelIdentityTests(unittest.TestCase):
 def test_local_and_cloud_embedding_identity(self):
  for name in ('bge-m3','bge-m3:latest','BAAI/bge-m3','Pro/BAAI/bge-m3'):
   with self.subTest(model=name):
    self.assertEqual(infer_api_model_capability('provider',name),'EMBEDDING')
    self.assertTrue(workflow_node_accepts_capability('chunk_embedding',infer_api_model_capability('provider',name)))
    self.assertEqual(_canonical_embed_model(name),'bge-m3')
 def test_reranker_is_not_embedding(self):
  self.assertEqual(infer_api_model_capability('provider','BAAI/bge-reranker-v2-m3'),'RERANKER')
  self.assertFalse(workflow_node_accepts_capability('chunk_embedding','RERANKER'))

if __name__=='__main__':unittest.main()
