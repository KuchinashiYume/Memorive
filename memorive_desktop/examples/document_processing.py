"""Document processing from a PDF to an evidence card, with a synthetic paper and a stand-in model.

    python memorive_desktop/examples/document_processing.py [work folder]

These are the first steps a dropped PDF takes in the desktop app (nodes 01 and 03 of the document
workflow, memorive/documents/workflow/executor.py), without the window and without a real model:

1. A one-page PDF of a synthetic paper is written with PyMuPDF.
2. Document processing (memorive.documents.processing.run_document_processing) reads it with the local
   PDF engine and writes the paper's folder: RawMD (the extracted text), CleanMD (cleaned, with front
   matter) and Chunks (numbered pieces of text, one JSON line each). No model is involved.
3. Card distillation (memorive.documents.card_distillation.distill) turns the chunks into an evidence
   card: research question, object, method, results, conclusion and limits, each anchored to the chunks
   it came from. It asks the model through the model gateway; here the gateway is ExampleGateway, which
   answers from the chunk text the prompt carries. In the app the gateway is CoreModelBridge, which runs
   the model configured for the node.
4. The card is printed: each field with the chunks it cites.

Everything is written under the work folder (a new temporary folder if none is given); no real data
folder is touched and no network is used. tests/test_document_processing_example.py runs this file.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "source"

TITLE = "Temperature and growth of strain K12"
PARAGRAPHS = [
    "Temperature and growth of strain K12",
    "Introduction. We asked how incubation temperature changes the growth of Escherichia coli strain K12.",
    "Methods. Strain K12 was grown in LB medium at 25 C, 30 C and 37 C for 24 hours, three replicates each.",
    "Results. Growth at 37 C was twice the growth at 25 C. Growth at 30 C lay between the two.",
    "Conclusion. Within 25 C to 37 C, warmer incubation gave faster growth of strain K12.",
    "Limitations. Only one strain and one medium were tested.",
]
CHUNK_LINE = re.compile(r"^\[(?P<id>[^\]|]+?) \| [^\]]*\] (?P<text>.*)$", re.MULTILINE)


class ExampleGateway:
    """Stands in for the model gateway. Distillation asks it three things: the core fields (each quotes
    the chunk it is about), the key data (one entry, the measured result) and the extra blocks (left
    empty: the card is written without them). Records each call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, task_type, payload, *, bypass_cache=False):
        prompt = payload["messages"][-1]["content"]
        chunks = {m["id"].strip(): m["text"].strip() for m in CHUNK_LINE.finditer(prompt)}
        if payload.get("response_contract") == "distill_aux_v1":
            kind, reply = "extra blocks", {}
        elif "stat_type" in prompt:
            kind, reply = "key data", self.key_data(chunks)
        else:
            kind, reply = "core fields", self.card_fields(chunks)
        self.calls.append(kind)
        return {"text": json.dumps(reply, ensure_ascii=False), "model": "example-model",
                "raw": {"choices": [{"finish_reason": "stop"}]}}

    @staticmethod
    def chunk_about(chunks, word):
        return next((cid for cid, text in chunks.items() if word in text), next(iter(chunks)))

    @classmethod
    def key_data(cls, chunks):
        chunk_id = cls.chunk_about(chunks, "Results")
        return {"key_data": [{
            "value": "2", "unit": "fold", "metric": "growth at 37 C relative to 25 C",
            "sample": "sample/control: 37 C vs 25 C | phase/time: 24 h | subprocess: whole culture | comparator: 25 C",
            "stat": "stat_type: ratio | n: 3 | significance: Not given",
            "quote": "Growth at 37 C was twice the growth at 25 C.", "chunk_id": chunk_id}]}

    @classmethod
    def card_fields(cls, chunks):
        def about(word):
            chunk_id = cls.chunk_about(chunks, word)
            sentence = chunks[chunk_id].split(". ", 1)[-1]
            return sentence, [{"chunk_id": chunk_id, "quote": sentence}]

        fields = {}
        for field, word, as_list in (("research_question", "Introduction", False), ("research_object", "Methods", True),
                                     ("method", "Methods", True), ("key_results", "Results", False),
                                     ("author_conclusion", "Conclusion", False),
                                     ("boundary_conditions", "Limitations", True)):
            sentence, anchors = about(word)
            fields[field] = {"value": [sentence] if as_list else sentence, "anchors": anchors}
        return fields


def write_pdf(path: Path) -> None:
    import fitz  # PyMuPDF, part of the app's dependencies

    document = fitz.open()
    page = document.new_page()
    y = 72
    for index, paragraph in enumerate(PARAGRAPHS):
        size = 16 if index == 0 else 11
        page.insert_textbox(fitz.Rect(72, y, 523, y + 60), paragraph, fontsize=size)
        y += 70 if index == 0 else 60
    document.save(path)


def main(work: Path) -> dict:
    """Run the example in work; returns the paper folder, the card and the gateway's calls."""
    work.mkdir(parents=True, exist_ok=True)
    ops = work / "ops"
    for name, value in {
        "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT": work / "library",
        "MEMORIVE_DOCUMENT_PROCESSING_VAULT_ROOT": work / "vault-unused",
        "MEMORIVE_VAULT_ROOT": work / "vault-unused",
        "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT": work / "vector-store",
        "MEMORIVE_OPS_ROOT": ops,
        "MEMORIVE_RUNTIME_LOG_LOG_DIR": ops / "runtime_log",
        "MEMORIVE_MODEL_GATEWAY_CACHE_DIR": ops / "model_gateway_cache",
        "MEMORIVE_DOCUMENT_PROCESSING_OCR_VIA_MODEL_GATEWAY_READY": "false",
    }.items():
        os.environ[name] = str(value)
    sys.path.insert(0, str(SOURCE))
    from memorive.documents.card_distillation import distill
    from memorive.documents.processing import run_document_processing
    from memorive.models.gateway.gateway import gateway_scope

    pdf = work / "K12 growth.pdf"
    write_pdf(pdf)
    processed = run_document_processing(pdf, data_ownership="self", doc_type="literature",
                                        paper_id="DOC_EXAMPLE0001", title=TITLE, target="sandbox")
    if processed.status != "completed":
        raise RuntimeError(f"document processing stopped at {processed.stopped_at}: {processed.error_message}")
    folder = next(p.parent for p in (work / "library").rglob("[[]Chunks[]]*.jsonl"))
    gateway = ExampleGateway()
    with gateway_scope(gateway):
        result = distill(folder, target="sandbox")
    if result.status != "completed":
        raise RuntimeError(f"card distillation failed: {result.error_type}: {result.error_message}")
    import yaml
    card = yaml.safe_load(Path(result.card_path).read_text(encoding="utf-8").split("---", 2)[1])
    return {"folder": folder, "card_path": Path(result.card_path), "card": card, "calls": gateway.calls}


if __name__ == "__main__":
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="memorive-document-example-"))
    outcome = main(work)
    print(f"paper folder: {outcome['folder']}")
    for path in sorted(outcome["folder"].iterdir()):
        print(f"  {path.name}")
    print(f"model calls: {', '.join(outcome['calls'])}")
    card = outcome["card"]
    for field, anchors in card["source_anchor"]["by_field"].items():
        cited = ", ".join(anchor["chunk_id"] for anchor in anchors)
        print(f"{field}: {card[field]}  [{cited}]")
    for item in card.get("key_data") or []:
        print(f"key data: {item['metric']} = {item['value']} {item['unit']}  [{item['chunk_id']}]")
