"""Model identity: which vendor and base model a model id names, and the reasoning effort when the
id or its ranking record carries one.

identity() turns a model id, or a ranking record (Artificial Analysis, Epoch AI, LiveBench rows),
into (vendor, base_model, effort); join_base() makes version punctuation comparable. The model
leaderboard in Settings (memorive_settings/leaderboard.py) groups ranking rows by these keys, and
the price registry (memorive/models/price_registry.json) is keyed by them, so the
call ledger's price estimate (memorive/models/pricing.py) resolves a request's model the
same way. number() and text() read a finite number and a short printable string; the leaderboard
uses them throughout.
"""
import math
import re

EFFORT = re.compile(r"[-_](none|low|medium|high|xhigh|max|ultra)(?:-effort)?$", re.I)
# Identity hints only; these do not generate score/price/effort capabilities.
VENDORS = {"openai": ("OpenAI", "海外"), "anthropic": ("Anthropic", "海外"),
           "google": ("Google", "海外"), "x-ai": ("xAI", "海外"), "meta-llama": ("Meta", "海外"),
           "mistralai": ("Mistral", "海外"), "deepseek": ("DeepSeek", "国产"),
           "qwen": ("Qwen", "国产"), "moonshotai": ("Moonshot", "国产"),
           "z-ai": ("Z.ai", "国产"), "minimax": ("MiniMax", "国产")}
PREFIXES = (("gpt-", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
            ("claude-", "anthropic"), ("gemini-", "google"), ("grok-", "x-ai"),
            ("deepseek-", "deepseek"), ("qwen", "qwen"), ("kimi-", "moonshotai"),
            ("glm-", "z-ai"), ("minimax-", "minimax"))


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def text(value, limit=256):
    return value.strip() if isinstance(value, str) and len(value) <= limit and all(ord(c) >= 32 for c in value) else ""


def identity(record):
    raw_id = text(record.get("model_id")).casefold()
    if not raw_id:
        raise ValueError("LEADERBOARD_MODEL_ID_INVALID")
    source = text(record.get("source_id"))
    base, effort = raw_id, None
    # ECI upper-envelope exports may repeat a group score under effort aliases.
    # A suffix alone cannot turn that model-level score into an effort result.
    if record.get('source_id') == 'epoch-ai' and number((record.get('metrics') or {}).get('eci')) is not None:
        base = re.sub(r'\s+', '-', base)
        match = EFFORT.search(base)
        if match and record.get('identity_scope') == 'EXACT_MODEL_EFFORT':
            base, effort = base[:match.start()], match[1].lower()
        else:
            if match:
                base = base[:match.start()]
            base = re.sub(r'_unknown$', '', base)
            effort = 'reference'
    if record.get("upstream_source") == "livebench":
        match = EFFORT.search(base)
        effort = record.get("benchmark_effort")
        if effort is not None and effort not in {"none", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("LEADERBOARD_EFFORT_INVALID")
        if match:
            if effort is not None and effort != match[1].lower():
                raise ValueError("LEADERBOARD_EFFORT_CONFLICT")
            base, effort = base[:match.start()], match[1].lower()
    elif record.get("identity_scope") == "EXACT_MODEL_EFFORT":
        effort = record.get("benchmark_effort")
        if effort not in {"none", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("LEADERBOARD_EFFORT_INVALID")
        match = EFFORT.search(base)
        if match:
            if match[1].lower() != effort:
                raise ValueError('LEADERBOARD_EFFORT_CONFLICT')
            base = base[:match.start()]
    vendor = ""
    if "/" in base:
        namespace, rest = base.split("/", 1)
        if namespace in VENDORS:
            vendor, base = namespace, rest
    if not vendor:
        vendor = next((vendor for prefix, vendor in PREFIXES if base.startswith(prefix)), "")
    # Unknown namespaces never cross-join vendors based on a coincidental name.
    if not vendor:
        vendor = "source:" + source
    return vendor, base, effort


def join_base(base):
    # Version punctuation only; preserve dates, batch/free SKUs and effort.
    return re.sub(r'(?<=\d)\.(?=\d)', '-', base)
