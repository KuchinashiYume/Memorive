"""Sending model requests. Every model request of the app goes through LiveModelValidationRunner
(runner.py): chats over an API, a command-line tool or a local model, embeddings, OCR and reranking.

  runner.py           the runners: the interface, the one that refuses every request, the live one
  api_route.py        the API route: request body, reading the reply (also streamed), failure evidence
  cli_route.py        the command-line route: argument lists, Codex's schema and events, diagnostics
  structured.py       the prompt that asks for a JSON answer, and finding that answer in a reply
  usage.py            token counts and cost estimates read from a reply
  catalog.py          a provider's model list, and suggestions when a model is not on it
  embeddings.py       embedding requests
  ocr_and_rerank.py   OCR and reranking requests
  transport.py        how bytes go out and come back: HTTP and process transports and their results
"""
