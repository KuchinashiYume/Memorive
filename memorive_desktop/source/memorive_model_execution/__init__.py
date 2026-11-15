"""Running the model chosen in Settings for a feature of the app.

Research Q&A, reports, the AI briefing, retrieval embeddings and picture transcription each run a
model in two steps, two calls to the settings service:

  freeze(run_id, profile_ref)   the chosen model profile is copied into <snapshot id>.json, named by the
                                hash of its content, so that later changes in Settings do not change a
                                run in progress
  execute(snapshot_id, ...)     the request is sent once: <snapshot id>.attempt.json is created first and
                                never removed, so a snapshot is never sent twice, not even after a crash;
                                the call is counted in the call ledger (memorive_settings/call_ledger.py);
                                the result is kept as <snapshot id>.result.json

Where things are:
  execution.py   the kinds of run: reports, research questions (tried again after temporary
                 failures), pictures, embeddings, the AI briefing (which can be cancelled)
  retry.py       which failures are tried again, how often, and how long to wait in between; the
                 workflow's model calls (memorive_workflow/model_bridge.py) follow the same rules

The settings controller (memorive_settings/controller.py) makes one object of each kind and serves
them as settings.research_chat_freeze, settings.research_chat_execute and the like. The request
itself is sent by the model validation runner the controller holds (memorive_settings/model_validation.py).
"""
from .execution import AIBriefingExecution, ReportProfileExecution, ResearchChatExecution, ResearchEmbeddingExecution

__all__ = ["AIBriefingExecution", "ReportProfileExecution", "ResearchChatExecution", "ResearchEmbeddingExecution"]
