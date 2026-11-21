"""The AI briefing's report as the model returns it (research/briefing/jobs.py, mapped_report).

The report has a main line, items and a trend; the main line and the trend are statements with their own
sources. A model wrote them as text and gave their sources once, at the top level; the whole report was refused
(AI_REPORT_SCHEMA_INVALID) though its items were valid and the call was paid for.
"""
from __future__ import annotations

import pytest

from memorive.research.briefing.jobs import mapped_report

ROW = {"source_id": "src-1", "body": "Model release notes.", "source": "openai_news", "source_name": "OpenAI",
       "url": "https://openai.com/news/a", "title": "A release", "published_at": "2026-11-20",
       "retrieved_at": "2026-11-21T08:00:00Z", "body_extent": "feed_summary"}
COLLECTION = {"materials": [ROW], "sources": [], "failures": [], "unknown_date_count": 0}
WINDOW = {"start": "2026-11-20", "end": "2026-11-21", "zone": "Asia/Tokyo"}
ITEM = {"title": "A release", "what": "A model was released.", "why": "It changes the default.", "source_ids": ["src-1"]}


def report(response):
    return mapped_report(response, COLLECTION, WINDOW, "brief")


def test_a_main_line_and_trend_written_as_text_with_sources_at_the_top_are_kept():
    result = report({"mainline": "Releases continued.", "items": [ITEM], "trend": "More releases.",
                     "source_ids": ["src-1"]})
    assert result["mainline"] == {"text": "Releases continued.", "source_ids": ["src-1"]}
    assert result["trend"]["source_ids"] == ["src-1"] and "Releases continued." in result["markdown"]


def test_sources_at_the_top_are_held_to_the_collection():
    with pytest.raises(ValueError, match="AI_REPORT_CITATION_INVALID"):
        report({"mainline": "Releases continued.", "items": [ITEM], "trend": None, "source_ids": ["src-unknown"]})
