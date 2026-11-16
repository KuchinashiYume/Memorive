"""Bibliographic identity, source connectors and novelty checks for literature discovery.

The research runtime (memorive_research_runtime) uses these when it looks for new papers:
bibliographic identity matching, the connector framework for metadata sources, the live public
metadata transport and the novelty assessment against the library. The discovery projection
module is called by other code, not by the window.
"""
