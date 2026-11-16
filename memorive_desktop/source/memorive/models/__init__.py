"""Model requests: who a model is, what a call may ask for, how calls are sent and recorded.

  validation/          LiveModelValidationRunner: every model request of the app is sent here
                       (API, command-line tools, local models, embeddings, OCR, reranking)
  execution/           a feature's model run: the profile frozen once, the request sent once
                       (runs.py), and when a failed call is tried again (retry.py)
  calls/               the call ledger (ledger.py), cancellable HTTP reads, proxy and TLS settings
  cli/                 command-line tools: finding them on Windows, user templates, running them,
                       verifying them, pinning the Codex executable used for pictures
  scheduling.py        which background task may run now, and how many command-line calls at once
  capabilities.py      the kind of model (chat, embedding, reranker, OCR) told from provider and
                       name, and which workflow nodes accept which kind
  endpoints.py         API protocols, the base-URL rule, model-list URLs; kimi_endpoint.py for Kimi
  request_options.py   saved options (reasoning effort and the like) turned into provider parameters
  identity.py          the vendor and base model behind a model id
  pricing.py           a call's price estimate; price_registry.py and .json hold public prices,
                       provider_aliases.py the provider's own compatibility names
  output_limits.py     how large a reply may be
  gateway/             model transport for the document and research work: providers, routing,
                       prompts and their files, caches, cost, the local model components
  local/               local model servers (Ollama and the like) found and checked for the window
  channels/            receipts of model channel attempts and their append-only store
  cost_routing/        cost routing advice: its ledger of rejections, historical evidence, decision
                       previews

The settings service (memorive/desktop/settings) holds the runner and serves these as settings.* calls.
scheduling.py reads the workflow's worker count (memorive/documents/workflow/contracts.py,
CORE_WORKER_CAPACITY). Places where gateway/ still reaches into other areas:
research_prompt_cache.py reads the answer styles of memorive/desktop/settings, routing_advisory.py uses
the exam scoring (memorive.exams.evaluation) and the hashing helpers of the research ranking
contracts (memorive/research/ranking/contracts.py).
"""
