from __future__ import annotations

import unittest

from tests.modules.retrieval_weighting.field_vector_acceptance.suite import scenarios


class ContractAndFixtureTests(unittest.TestCase):
    def _assert_pass(self, evaluator):
        outcome = evaluator()
        self.assertEqual(
            {"test_id", "expected", "observed", "result"}, set(outcome), outcome
        )
        self.assertEqual("PASS", outcome["result"], outcome)

    def test_A_009_doc_type_missing_unknown_and_conflict_fail_closed(self):
        self._assert_pass(scenarios.evaluate_A_009)

    def test_A_015_schema_and_runtime_mutation_suite(self):
        self._assert_pass(scenarios.evaluate_A_015)


if __name__ == "__main__":
    unittest.main()

