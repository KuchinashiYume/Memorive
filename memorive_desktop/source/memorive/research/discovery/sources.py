"""The literature metadata sources research discovery can search: PRESETS maps each provider to its
display name, endpoint and the environment variable that holds its key (only Web of Science needs one);
CATALOG is the list the settings page shows; SELECTABLE the providers a user may select. Research
discovery (transport.py) checks requests against PRESETS; the settings' literature source slots
(memorive/desktop/settings/literature_sources.py) are built from all three.
"""
PRESETS = {
    'arxiv': ('arXiv', 'https://export.arxiv.org/api/query', ''),
    'crossref': ('Crossref', 'https://api.crossref.org/works', ''),
    'web-of-science': ('Web of Science Starter', 'https://api.clarivate.com/apis/wos-starter/v1/documents', 'WOS_API_KEY'),
}
CATALOG = tuple((p, v[0], '官方文献元数据', 'CONNECTOR_BUILT', 'memorive.research.discovery.transport')
                for p,v in PRESETS.items())
SELECTABLE = frozenset(PRESETS)
