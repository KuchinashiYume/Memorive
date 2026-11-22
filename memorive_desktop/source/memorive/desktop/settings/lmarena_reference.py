"""LMArena (formerly Chatbot Arena) as a leaderboard source: the text arena's overall board with style control, from
the public dataset lmarena-ai/leaderboard-dataset (CC BY 4.0) through the Hugging Face rows API. Data only; the
ratings are human-preference Elo scores, not benchmark percentages.

external_sources.py fetches the pages (page_url) and turns the rows into reference records (normalized_rows);
metadata_transport.py pins the page URLs. A model listed at several efforts or modes ("Claude Opus 5.5 (High)",
"(Max)", "(Thinking)") is one model here, rated by its best variant: the model's upper envelope, as Epoch AI's ECI,
so the leaderboard puts both sources on one row.
"""
import re
from urllib.parse import urlencode

DATASET = 'lmarena-ai/leaderboard-dataset'
CONFIG = 'text_style_control'
CATEGORY = 'overall'
ENDPOINT = 'https://huggingface.co/datasets/' + DATASET  # the source's identity in the settings
ROWS_API = 'https://datasets-server.huggingface.co/filter'
PAGE_SIZE = 100
MAX_ROWS = 5000
METRIC = 'lmarena_text'

# The organisation LMArena gives a model -> the vendor namespace of memorive/models/identity.py.
VENDORS = {'openai': 'openai', 'anthropic': 'anthropic', 'google': 'google', 'xai': 'x-ai', 'meta': 'meta-llama',
           'mistral': 'mistralai', 'deepseek': 'deepseek', 'alibaba': 'qwen', 'moonshot': 'moonshotai', 'zai': 'z-ai',
           'minimax': 'minimax'}
# Bracketed words that name an effort or mode, not a different model: dropped from the model's name.
_MODE = re.compile(r'(?:x?high|max|medium|low|minimal|thinking(?:\s+\d+k)?|non-thinking|reasoning|\d+k)', re.I)


def page_url(offset):
    """The rows API page from offset: the overall category of the latest text board, PAGE_SIZE rows."""
    return ROWS_API + '?' + urlencode({'dataset': DATASET, 'config': CONFIG, 'split': 'latest',
                                       'where': f"\"category\"='{CATEGORY}'", 'offset': int(offset),
                                       'length': PAGE_SIZE})


def is_page_url(url):
    """A page URL page_url made (any offset below MAX_ROWS), nothing else."""
    return isinstance(url, str) and any(url == page_url(offset) for offset in range(0, MAX_ROWS, PAGE_SIZE))


def _model_name(name):
    """A board name without its effort and mode words: the name and the bracketed words that stay."""
    match = re.fullmatch(r'(.*?)\s*\(([^()]*)\)\s*', name)
    base, extra = (match[1], [t.strip() for t in match[2].split(',')]) if match else (name, [])
    return base.strip(), [t for t in extra if t and not _MODE.fullmatch(t)]


def model_slug(name):
    """The model a board name stands for: lower case, words joined by '-', effort and mode words dropped, dates
    and versions in brackets kept ("GPT-6.2 Sol (Max)" -> gpt-6.2-sol, "Qwen3 (2507)" -> qwen3-2507)."""
    base, kept = _model_name(name)
    return re.sub(r'[\s_]+', '-', ' '.join([base, *kept]).strip()).casefold()


def display_name(name):
    """The model's name to show ("Claude Opus 5.5 (High)" -> Claude Opus 5.5, "Qwen3 (2507)" stays)."""
    base, kept = _model_name(name)
    return f'{base} ({", ".join(kept)})' if kept else base


def normalized_rows(page_rows):
    """One record per model, its best variant: {model_id, slug, name, rating, rating_lower, rating_upper, votes,
    published}. The model id carries the vendor namespace when LMArena's organisation is a known vendor."""
    best = {}
    published = {row.get('leaderboard_publish_date') for row in page_rows}
    if len(published) != 1 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(next(iter(published)))):
        raise ValueError('LMARENA_PUBLISH_DATE_INVALID')
    for row in page_rows:
        if row.get('category') != CATEGORY or not isinstance(row.get('model_name'), str) or not row['model_name'].strip():
            raise ValueError('LMARENA_ROW_INVALID')
        rating = row.get('rating')
        if type(rating) not in (int, float) or not 0 < rating < 5000:
            raise ValueError('LMARENA_RATING_INVALID')
        slug = model_slug(row['model_name'])
        vendor = VENDORS.get(str(row.get('organization') or '').casefold())
        model_id = f'{vendor}/{slug}' if vendor else slug
        if model_id not in best or rating > best[model_id]['rating']:
            best[model_id] = dict(model_id=model_id, slug=slug, name=display_name(row['model_name']), rating=float(rating),
                                  rating_lower=row.get('rating_lower'), rating_upper=row.get('rating_upper'),
                                  votes=row.get('vote_count'), published=next(iter(published)))
    if not best:
        raise ValueError('LMARENA_EMPTY')
    return list(best.values())
