"""Contract checks for the optional custom-topic Analysis request template."""

from __future__ import annotations

import unittest
from pathlib import Path


CUSTOM_REQUEST_TEMPLATE_PATH = (
    (Path(__file__).resolve().parents[3] / "source" / "memorive" / "research" / "analysis")
    / "templates"
    / "custom_topic_analysis_request.md.tmpl"
)


class CustomTopicAnalysisRequestTemplateTests(unittest.TestCase):
    def test_template_is_optional_and_non_activating(self) -> None:
        template = CUSTOM_REQUEST_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("仅在用户提出自定义问题或专题分析时建立", template)
        self.assertIn("常规单篇 Card + Analysis 不创建本文件", template)
        self.assertIn("本文件不自动触发模型调用", template)
        self.assertIn("当前实现不提供自动 loader", template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
