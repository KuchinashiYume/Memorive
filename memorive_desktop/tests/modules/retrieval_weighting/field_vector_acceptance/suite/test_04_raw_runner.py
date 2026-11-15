from __future__ import annotations

import unittest

from tests.modules.retrieval_weighting.field_vector_acceptance.suite import scenarios


class RawRunnerTests(unittest.TestCase):
    def test_A_018_raw_byte_runner_eight_paths(self):
        outcome = scenarios.evaluate_A_018()
        self.assertEqual(
            {"test_id", "expected", "observed", "result"}, set(outcome), outcome
        )
        self.assertEqual("PASS", outcome["result"], outcome)


if __name__ == "__main__":
    unittest.main()

