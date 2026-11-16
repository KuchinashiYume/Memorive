"""Research Q&A: conversations about a project's material, answers that cite excerpts, answer
versions, knowledge drafts and agent handoffs.

The window reaches it through memo.* methods (ResearchWorkspace in service.py; desktop.py wires it to
the settings service). Agent tools reach it through agent_entry.py and the developer API. The steps
of answering one question are in answering.py; DEVELOPING.md follows a question from the window to
the saved answer.
"""
from memorive.research.workspace.service import ResearchWorkspace

__all__ = ['ResearchWorkspace']
