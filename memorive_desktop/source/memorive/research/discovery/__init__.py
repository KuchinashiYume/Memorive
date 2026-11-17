"""Bibliographic identity, source connectors and novelty checks for literature discovery.

The research runtime (runtime.py) uses four of its modules when it looks for new
papers: bibliographic identity matching, the connector framework for metadata sources, the live
public metadata transport and the novelty assessment against the library. The discovery projection
module has no caller outside the package.

The runtime's own discovery modules are here too: desktop.py adapts identity, relations, the slate
and feedback for the desktop; directions.py (the confirmed research directions), query_plan.py,
acquisition.py (fetching within one request budget), transport.py and wos.py (public metadata),
sources.py (the literature providers and their endpoints),
metadata.py, relations.py, identity.py, slate.py (the five-channel candidate slate, with
recent_interests.py), readable.py, and work_index.py with indexing.py (the workspace's identity
index and its backfill).
"""
