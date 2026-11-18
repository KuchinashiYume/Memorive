"""Which fetched papers literature discovery counts as related to a research direction
(memorive/research/discovery/query_plan.py, assess).

A candidate is related when its title and abstract share enough of the direction's query terms (half of
them, at least two, at most three) and, when the direction lists keywords, at least one keyword. The
records below are made up; the first two follow a real run, where a review of a drug for Cushing syndrome
matched a direction on citation evidence in RAG through the words "citation" and "evidence" alone.
"""
from memorive.research.discovery.query_plan import assess, request_options

RAG = {"id": "direction-rag-evidence", "revision": 1, "name": "Evidence attribution in RAG",
       "query": "retrieval-augmented generation citation evidence attribution",
       "keywords": ["retrieval-augmented generation", "citation", "attribution"]}


def record(title, abstract=None, published=None):
    return {"bibliographic": {"title": title, "abstract": abstract, "published_date": published}}


def related(paper, direction=RAG, objective="related"):
    return assess(paper, direction, [], request_options(objective, direction["id"], 90, as_of="2026-11-18"))


def test_two_everyday_words_from_another_field_are_not_enough():
    review = record("Use and toxicity of a steroid inhibitor in Cushing syndrome: a systematic review and evidence map",
                    "We searched four databases and ran backward and forward citation searching. Remission rates "
                    "varied between 40 and 70 percent across case series.")
    result = related(review)
    assert result["matched_query_terms"] == ["citation", "evidence"]
    assert result["matched_keywords"] == ["citation"]
    assert result["supported"] is False


def test_a_paper_on_the_topic_is_related_and_says_why():
    paper = record("Passage attribution for evidence-grounded retrieval-augmented generation",
                   "We attribute each generated sentence to the retrieved passage that supports it.")
    result = related(paper)
    assert result["supported"] is True
    assert {"retrieval", "augmented", "generation", "attribution", "evidence"} <= set(result["matched_query_terms"])
    assert "retrieval-augmented generation" in result["matched_keywords"]
    assert "Evidence attribution in RAG" in result["recommendation_reason"]
    assert result["evidence"] and result["evidence"][0]["field"] in {"title", "abstract"}


def test_three_terms_and_a_keyword_are_enough_for_a_long_query():
    long_query = dict(RAG, query="retrieval augmented generation citation evidence attribution "
                                 "faithfulness grounding verification hallucination")
    paper = record("Citation checks for retrieval augmented answers",
                   "Each answer is compared with the passages it cites.")
    result = related(paper, long_query)
    assert len(result["matched_query_terms"]) == 3
    assert result["supported"] is True


def test_listed_keywords_must_appear():
    paper = record("Evidence retrieval and augmented generation for legal drafting",
                   "We combine retrieval with generation.")
    result = related(paper)
    assert len(result["matched_query_terms"]) >= 3
    assert result["matched_keywords"] == []
    assert result["supported"] is False


def test_a_two_word_query_needs_both_words():
    direction = {"id": "direction-sleep", "revision": 1, "name": "Sleep surveys", "query": "sleep survey",
                 "keywords": []}
    assert related(record("A national sleep survey of adults"), direction)["supported"] is True
    assert related(record("Sleep and memory in older adults"), direction)["supported"] is False


def test_latest_keeps_only_papers_inside_the_window():
    paper_text = ("Citation attribution in retrieval-augmented generation",
                  "We study evidence attribution for generated citations.")
    inside = related(record(*paper_text, published="2026-10-01"), objective="latest")
    outside = related(record(*paper_text, published="2025-01-01"), objective="latest")
    assert inside["supported"] and inside["publication_eligible"]
    assert outside["supported"] and not outside["publication_eligible"]
