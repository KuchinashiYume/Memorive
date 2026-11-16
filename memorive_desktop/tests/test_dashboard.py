"""Offline dashboard regression: no providers, real profile or source content."""
import json
from pathlib import Path
import tempfile
import unittest
from memorive.desktop.settings.model_dashboard import ranking_projection, billing_projection

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exam, self.jobs = self.root / "exams", self.root / "jobs"
    def tearDown(self): self.temp.cleanup()
    def write(self, path, row):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row), encoding="utf-8")
    def bill(self): return billing_projection(self.exam, self.jobs)
    def call(self, name="call_01", where="attempts", **receipt):
        self.write(self.exam / "workflow_exams" / "one" / where / (name+".json"),
                   {"execution_receipt": receipt, "physical_model_calls": 1})
    def ranks(self, rows): return ranking_projection({"entries": rows}, [], [])
    def test_empty_is_unknown_not_zero(self):
        self.assertEqual(self.bill()["totals"], [])
        self.assertFalse(self.exam.exists())
    def test_failed_attempt_duplicate_and_reasoning_subset(self):
        for where in ("attempts", "receipts"):
            self.call(where=where, requested_model="m", status="FAILED", estimated_cost_cny=.2,
                      token_usage={"prompt_tokens":20,"completion_tokens":30,"reasoning_tokens":25})
        out=self.bill()
        self.assertEqual(len(out["rows"]),1)
        self.assertEqual(out["totals"][0]["estimated"],.2)
        self.assertEqual(out["rows"][0]["output_tokens"],30)
        self.assertEqual(out["failed_calls"],1)
    def test_identical_distinct_calls_not_deduplicated(self):
        for n in ("call_01","call_02"): self.call(n, requested_model="m",estimated_cost_cny=.1)
        self.assertEqual(len(self.bill()["rows"]),2)
    def test_currencies_separate_actual_not_added_to_own_estimate(self):
        self.call(actual_cost={"amount":.1,"currency":"USD"}, estimated_cost_cny=1)
        self.call("call_02",estimated_cost_cny=.5)
        totals={x["currency"]:x for x in self.bill()["totals"]}
        self.assertEqual(totals["USD"]["actual"],.1)
        self.assertEqual(totals["CNY"]["estimated"],.5)
    def test_bare_actual_no_assumed_currency(self):
        self.call(actual_cost=3)
        self.assertEqual(self.bill()["unknown_cost_calls"],1)
    def test_cli_missing_is_not_free(self):
        self.call(requested_model="gpt",profile_kind="CLI",actual_cost=None)
        self.assertIsNone(self.bill()["rows"][0]["cost"])
    def test_small_positive_cost_not_rounded_to_free(self):
        self.call(estimated_cost_cny=.00000000001)
        self.assertGreater(self.bill()["totals"][0]["estimated"],0)
    def test_negative_eci_is_valid_reference(self):
        rows=[dict(source_id="epoch-ai",model_id="x",metrics={"eci":-10})]
        self.assertEqual(ranking_projection({"entries":[]},[],rows)["groups"][0]["rows"][0]["raw_score"],-10)
    def test_phase1_pre_binding(self):
        p=self.jobs/"job-one"/"model_calls"
        self.write(p/"call-000001.pre.json",{"profile_kind":"LOCAL","requested_model":"qwen"})
        self.write(p/"call-000001.post.json",{"status":"PASS","execution_receipt":{"estimated_cost_cny":0}})
        row=self.bill()["rows"][0]
        self.assertEqual((row["model"],row["channel"],row["cost"]),("qwen","LOCAL",0))
    def test_legacy_stages_distinct(self):
        for stage in ("low_cost","high_cost"): self.call(where=stage+"/receipts",requested_model=stage,estimated_cost_cny=.1)
        self.assertEqual(len(self.bill()["rows"]),2)
    def test_ignore_gold_content_aggregate(self):
        self.write(self.exam/"workflow_exams"/"one"/"gold"/"secret.json",{"actual_cost":999})
        self.write(self.exam/"workflow_exams"/"one"/"exam_result.json",{"estimated_cost_cny":999})
        self.call(requested_model="m",estimated_cost_cny=.1,prompt="PRIVATE",credential="PRIVATE")
        bill=self.bill();out=json.dumps(bill)
        for text in ("PRIVATE",str(self.root)): self.assertNotIn(text,out)
        def numbers(value):
            if isinstance(value,dict):
                for item in value.values(): yield from numbers(item)
            elif isinstance(value,list):
                for item in value: yield from numbers(item)
            elif isinstance(value,(int,float)) and not isinstance(value,bool): yield value
        self.assertNotIn(999,list(numbers(bill)))  # ids may contain the digits 999 by chance
    def test_bad_json_and_numbers(self):
        self.call(estimated_cost_cny=-1,token_usage={"prompt_tokens":True,"completion_tokens":float("nan")})
        bad=self.exam/"workflow_exams"/"one"/"receipts"/"bad.json"
        bad.parent.mkdir(exist_ok=True);bad.write_text("{",encoding="utf-8")
        out=self.bill()
        self.assertEqual(out["unreadable_records"],1)
        self.assertEqual(out["unknown_cost_calls"],1)
        self.assertIsNone(out["rows"][0]["input_tokens"])
    def test_no_cross_role_or_cohort_ranking(self):
        rows=[dict(model_name="m"+str(i),node_id=role,score=90-i,comparison_cohort_id=cohort,
              horizontal_comparison_eligible=True) for i,(role,cohort) in enumerate(
              [("analysis","a"),("analysis","b"),("card","a")])]
        self.assertEqual(len(self.ranks(rows)["groups"]),3)
    def test_ties_and_profiles_preserved(self):
        rows=[dict(model_name="same",node_id="card",score=90,comparison_cohort_id="c",
              profile_kind=kind,thinking_mode=mode,horizontal_comparison_eligible=True)
              for kind,mode in [("API","high"),("CLI","max")]]
        self.assertEqual([x["rank"] for x in self.ranks(rows)["groups"][0]["rows"]],[1,1])
    def test_missing_cohort_never_ranked(self):
        self.assertIsNone(self.ranks([dict(model_name="x",node_id="card",score=90)])["groups"][0]["rows"][0]["rank"])
    def test_historical_not_current(self):
        self.assertEqual(ranking_projection({"entries":[],"historical_entries":[{"model_name":"old","score":99}]},[],[])["groups"],[])
    def test_local_history_separate(self):
        out=ranking_projection({"entries":[dict(model_name="api",node_id="card",score=90,
          comparison_cohort_id="c",horizontal_comparison_eligible=True)]},
          [dict(model_name="local",node_id="card",score=97,profile_kind="LOCAL")],[])
        self.assertEqual(len(out["groups"]),2)
        self.assertIsNone(out["groups"][1]["rows"][0]["rank"])
    def test_external_versions_separate(self):
        rows=[dict(source_id="epoch-ai",source_name="Epoch AI",model_id="model",model_name="Model",
              metrics={"eci":162},version=v,snapshot_sha256=v) for v in ("v1","v2")]
        groups=ranking_projection({"entries":[]},[],rows)["groups"]
        self.assertEqual(len(groups),2)
        self.assertEqual(groups[0]["rows"][0]["raw_score"],162)
    def test_price_and_context_not_capability(self):
        rows=[dict(source_id="openrouter",model_id="x",metrics={"context_length":1000000,"price_input":.1})]
        self.assertEqual(ranking_projection({"entries":[]},[],rows)["groups"],[])
    def test_no_private_fields_or_qualification(self):
        out=self.ranks([dict(model_name="m",node_id="analysis",score=95,prompt="PRIVATE")])
        self.assertTrue(out["reference_only"])
        self.assertNotIn("PRIVATE",json.dumps(out))

if __name__ == "__main__": unittest.main()
