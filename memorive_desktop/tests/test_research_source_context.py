"""Regression: source identity and locators must reach the model unchanged."""
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
from memorive_research_workspace.service import ResearchWorkspace

class ResearchSourceContextTests(unittest.TestCase):
    def test_pdf_locators_survive_model_context(self):
        with tempfile.TemporaryDirectory() as root:
            workspace=ResearchWorkspace(Path(root))
            try:
                ref={'id':'evidence','artifact_id':'article-a','document_id':'paper-a',
                     'title':'Article A','text':'Observed result','content_hash':'a'*64,
                     'page':3,'line_start':12,'line_end':18}
                thread={'id':'chat','temporary':True,'messages':[],'project':'default'}
                context,_=workspace._context(thread,'Explain the result',[ref],
                    {'memory_enabled':False,'recent_messages':8,'context_chars':24000})
                self.assertEqual(context['evidence'],[ref])
            finally:workspace.close()

if __name__=='__main__':unittest.main()
