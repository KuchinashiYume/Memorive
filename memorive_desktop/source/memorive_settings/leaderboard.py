"""Leaderboard projection and user preferences; no inference, qualification or billing writes."""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
from threading import RLock, Thread

from .contracts import canonical_sha256, utc_now
from .leaderboard_pricing import select_leaderboard_price
from .leaderboard_price_registry import alias_model_id, official_catalog_models, public_route_price

SCHEMA = "ModelLeaderboardProjection-v4"
PREF_SCHEMA = "ModelLeaderboardPreferences-v1"
DEFAULTS = {"currency": "USD", "weights": {"artificial-analysis": 100, "epoch-ai": 0},
            "preset": "general", "ability_weight": 70, "cost_weight": 30,
            "input_ratio": 0.8, "stars": []}
CATEGORIES = ("reasoning", "coding", "agentic_coding", "math", "data_analysis", "language", "instruction_following")
PRESETS = {"general": (1, 1, 1, 1, 1, 1, 1),
           "research": (20, 5, 5, 10, 20, 20, 20),
           "coding": (15, 30, 30, 10, 5, 5, 5)}
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


def validate_preferences(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("LEADERBOARD_PREFERENCES_INVALID")
    result = deepcopy(value)
    if result["currency"] == "RMB":
        result["currency"] = "CNY"
    if result["currency"] not in {"USD", "CNY", "JPY"} or result["preset"] not in PRESETS:
        raise ValueError("LEADERBOARD_PREFERENCES_INVALID")
    weights = result["weights"]
    if not isinstance(weights, dict) or not 1 <= len(weights) <= 16 or any(
        not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", k) or number(v) is None or not 0 <= v <= 1000
        for k, v in weights.items()
    ) or sum(weights.values()) <= 0:
        raise ValueError("LEADERBOARD_WEIGHTS_INVALID")
    a, c = result["ability_weight"], result["cost_weight"]
    if any(number(v) is None or not 0 <= v <= 1000 for v in (a, c)) or a + c == 0:
        raise ValueError("LEADERBOARD_WEIGHTS_INVALID")
    if number(result["input_ratio"]) is None or not 0 <= result["input_ratio"] <= 1:
        raise ValueError("LEADERBOARD_RATIO_INVALID")
    stars = result["stars"]
    if not isinstance(stars, list) or len(stars) > 20000 or any(
        not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in stars
    ) or len(stars) != len(set(stars)):
        raise ValueError("LEADERBOARD_STARS_INVALID")
    result["stars"] = sorted(stars)
    return result


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


def display_name(name, base, vendor):
    # Display formatting only. Identity/version/effort keys and persisted stars never change.
    value = text(name) or base
    prefixes = {vendor, VENDORS.get(vendor, ("",""))[0], "MoonshotAI" if vendor=="moonshotai" else ""}
    if vendor == "x-ai":
        prefixes.add("SpaceXAI")
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix and value.casefold().startswith(prefix.casefold()+":"):
            value = value[len(prefix)+1:].strip()
            break
    if value == base and re.fullmatch(r"[a-z0-9._-]+", value, re.I):
        value = re.sub(r"(?<=\d)-(?=\d(?:$|[^\d]))", ".", value)
        words = value.replace("-", " ").split()
        casing = {"gpt":"GPT","claude":"Claude","fable":"Fable","sonnet":"Sonnet","opus":"Opus",
                  "gemini":"Gemini","grok":"Grok","deepseek":"DeepSeek","qwen":"Qwen","kimi":"Kimi",
                  "glm":"GLM","minimax":"MiniMax","sol":"Sol","terra":"Terra","luna":"Luna","astra":"Astra",
                  "max":"Max","pro":"Pro","flash":"Flash","plus":"Plus"}
        value = " ".join(casing.get(word.lower(),word) for word in words)
    return value


def variant_id(key):
    return hashlib.sha256(json.dumps(list(key), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def source_quality(record, preset):
    metrics = record.get("metrics") or {}
    if record.get("upstream_source") == "livebench":
        pairs = [(number(metrics.get("livebench_" + k)), w) for k, w in zip(CATEGORIES, PRESETS[preset]) if w]
        return sum(v * w for v, w in pairs) / sum(w for _, w in pairs) if all(
            v is not None and 0 <= v <= 100 for v, _ in pairs) else None
    if record.get("source_id") == "epoch-ai":
        return number(metrics.get("eci"))
    if record.get("upstream_source") == "artificial-analysis":
        return number(metrics.get("artificial_analysis_intelligence_index"))
    return None


def effective_preferences(preferences, sources):
    """Project enabled scoring slots without rewriting saved user choices."""
    result = deepcopy(preferences)
    result["preset"] = "general"  # Retired UI setting; keep the legacy field readable.
    active = [s["id"] for s in sources if s.get("enabled", True)
              and s["format"] not in {"ECB_FX_XML", "OPENROUTER"}]
    weights = {key: preferences["weights"].get(key, 0) for key in active}
    if active and not any(weights.values()):
        weights[active[0]] = 100
    result["weights"] = weights
    return result


def project_rows(sources, preferences):
    # The service resolves enabled/default selections once; this pure scorer
    # respects the caller's explicit source weights.
    preferences = {**preferences, "preset": "general"}
    groups, prices, references, conflicts, aliases = {}, {}, {}, set(), {}
    prices_by_model_id = {}
    for source in sources:
        if source["format"] == "ECB_FX_XML":
            continue
        for record in source["records"]:
            key = identity(record)
            alias = (key[0], join_base(key[1]), key[2])
            key = aliases.setdefault(alias, key)
            vendor, base, effort = key
            model = groups.setdefault(key, {
                "id": variant_id(key), "name": text(record.get("model_name")) or base,
                "base_model": base, "version": base, "effort": effort,
                "vendor_id": vendor, "vendor": VENDORS.get(vendor, ("未标注", "未标注"))[0],
                "region": VENDORS.get(vendor, ("未标注", "未标注"))[1],
                "quality": {}, "details": [], "price": None, "availability": "REFERENCE_ONLY"})
            metrics = {text(k, 100): number(v) for k, v in (record.get("metrics") or {}).items() if text(k, 100)}
            detail = {"source_id": source["id"], "source_name": source["name"],
                      "model_id": text(record.get("model_id")), "metrics": metrics,
                      "benchmark_effort": effort, "scope": record.get("identity_scope"),
                      "benchmark_version": text(record.get("version")), "as_of": text(record.get("source_as_of")),
                      "date_semantics": text(record.get("date_semantics")), "stale": source["status"] != "CACHED",
                      "score_eligible": False}
            quality = source_quality(record, preferences["preset"]) if source["status"] == "CACHED" else None
            if quality is not None:
                existing = model["quality"].get(source["id"])
                conflict_key = (key, source["id"])
                if source["id"] in model["quality"] and existing != quality:
                    conflicts.add(conflict_key)
                # Conflicting duplicate evidence is unusable, never 'take best'.
                model["quality"][source["id"]] = None if conflict_key in conflicts else quality
                detail["score_eligible"] = True
            model["details"].append(detail)
            if source['id'] == 'epoch-ai' and number(metrics.get('eci')) is not None:
                detail['scope'] = 'MODEL_CAPABILITY_UPPER_ENVELOPE' if effort == 'reference' else 'EXACT_MODEL_EFFORT'
            if source["format"] == "OPENROUTER" and record.get("operational") is not None:
                op = record["operational"]
                pricing = op.get("pricing") or {}
                inp, out = number(pricing.get("prompt")), number(pricing.get("completion"))
                usable = source["status"] == "CACHED" and inp is not None and out is not None and inp >= 0 and out >= 0 and number(inp * 1000000) is not None and number(out * 1000000) is not None
                price = {"input": inp * 1000000, "output": out * 1000000, "currency": "USD",
                         "unit": "PER_MILLION_TOKENS", "source_id": source["id"],
                         "source_name": text(source.get("name")),
                         "source_url": text(record.get("source_url"), 2048),
                         "retrieved_at": text(source.get("retrieved_at")),
                         "model_id": text(record.get("model_id")),
                         "channel": "OPENROUTER_CATALOG",
                         "scope": "API_SKU_UNIT_PRICE_NOT_TASK_COST"} if usable else None
                price, price_evidence = select_leaderboard_price(
                    price, pricing, op, text(record.get("model_id"))
                )
                description = op.get("description")
                description = description if isinstance(description,str) and len(description)<=8000 else ""
                params = op.get("supported_parameters")
                capabilities = None if not isinstance(params,list) else {
                    "tools": "tools" in params,
                    "structured_output": any(x in params for x in ("response_format","structured_outputs")),
                    "reasoning": "reasoning" in params}
                product = {"display_name": text(record.get("model_name")),
                           **price_evidence,
                           "description": description, "max_output_tokens": number(op.get("max_output_tokens")),
                           "output_modalities": [text(v,80) for v in op.get("output_modalities",[]) if text(v,80)],
                           "capabilities": capabilities,
                           "context_length": number(op.get("context_length")),
                           "input_modalities": [text(v, 80) for v in op.get("input_modalities", []) if text(v, 80)],
                           "supported_parameters": [text(v, 80) for v in op.get("supported_parameters", []) if text(v, 80)]}
                entry = (price, product, source["status"])
                # Duplicate prices from different SKUs are ambiguous.
                price_key = (vendor, join_base(base))
                existing = prices.get(price_key)
                prices[price_key] = entry if existing is None else (entry if existing == entry else (None, {}, "AMBIGUOUS"))
                provider_model_id = text(record.get("model_id"))
                if provider_model_id:
                    previous = prices_by_model_id.get(provider_model_id)
                    prices_by_model_id[provider_model_id] = entry if previous is None or previous == entry else (None, {}, "AMBIGUOUS")
                model["availability"] = "IN_CATALOG" if source["status"] == "CACHED" else "STALE"
    for release in official_catalog_models():
        vendor, base = release["vendor"], release["base_model"]
        matching = [key for key in groups
                    if key[0] == vendor and join_base(key[1]) == join_base(base)]
        if not matching:
            key = (vendor, base, None)
            groups[key] = {
                "id": variant_id(key), "name": release["display_name"],
                "base_model": base, "version": base, "effort": None,
                "vendor_id": vendor, "vendor": VENDORS.get(vendor, ("未标注", "未标注"))[0],
                "region": VENDORS.get(vendor, ("未标注", "未标注"))[1],
                "quality": {}, "details": [], "price": None,
                "availability": "OFFICIAL_RELEASE",
            }
            matching = [key]
        for key in matching:
            model = groups[key]
            model["official_release"] = {
                "model_id": release["model_id"], "released_on": release["released_on"],
                "source_as_of": release["source_as_of"], "source_name": release["source_name"],
                "source_url": release["source_url"], "display_name": release["display_name"],
            }
            model["official_product"] = {
                "display_name": release["display_name"], "description": release["description"],
                "context_length": release["context_length"],
                "max_output_tokens": release["max_output_tokens"],
            }
            model["details"].append({
                "source_id": "official-release", "source_name": release["source_name"],
                "model_id": release["model_id"], "metrics": {}, "benchmark_effort": key[2],
                "scope": "OFFICIAL_MODEL_RELEASE_NO_BENCHMARK_SCORE",
                "benchmark_version": release["released_on"], "as_of": release["source_as_of"],
                "date_semantics": "MODEL_RELEASE_NOT_EVALUATION", "stale": False,
                "score_eligible": False,
            })
    rows = []
    variants = {(v, join_base(b)) for v, b, e in groups if e is not None}
    for (vendor, base, effort), model in groups.items():
        if (effort is None and (vendor, join_base(base)) in variants
                and not model["quality"] and not model.get("official_release")):
            continue  # A price-only placeholder is represented by its explicit effort rows.
        price, product, status = prices.get((vendor, join_base(base)), (None, {}, "MISSING"))
        if price is None:
            provider_model_id = alias_model_id(vendor, base)
            alias_entry = prices_by_model_id.get(provider_model_id) if provider_model_id else None
            if alias_entry and alias_entry[0] is not None:
                price, product, status = deepcopy(alias_entry)
                product = {**product, "price_identity_binding":"EXPLICIT_OPENROUTER_MODEL_ALIAS",
                           "price_identity_model_id":provider_model_id}
        if price is None:
            fallback_price, fallback_evidence = public_route_price(vendor, join_base(base))
            if fallback_price is not None:
                price, status = fallback_price, "REFERENCE_ROUTE"
                product = {**product, **fallback_evidence,
                           "price_identity_binding":"EXPLICIT_SAME_CREATOR_PUBLIC_ROUTE"}
        model["price"] = deepcopy(price)
        model["product"] = {**deepcopy(model.pop("official_product", {})), **deepcopy(product)}
        model["channel_price"] = deepcopy(product.get("channel_quote"))
        model["price_status"] = product.get("regular_price_status", "NO_CURRENT_PUBLIC_ROUTE")
        model["price_reason"] = product.get("regular_price_reason", "NO_EXACT_PROVIDER_ROUTE_IN_CURRENT_PUBLIC_CATALOGS")
        official_name = (model.get("official_release") or {}).get("display_name")
        model["name"] = official_name or display_name(product.get("display_name"), base, vendor)
        if status == "CACHED":
            model["availability"] = "IN_CATALOG"
        for detail in references.get((vendor, base), []):
            if detail not in model["details"]:
                model["details"].append(deepcopy(detail))
        rows.append(model)
    weights = {k: v for k, v in preferences["weights"].items() if v > 0}
    # Use the union of selected-source coverage, not a mandatory intersection.
    # Missing sources never become zero scores; effective shares are disclosed.
    cohort = [m for m in rows if m['vendor_id'] in VENDORS and any(number(m['quality'].get(k)) is not None for k in weights)]
    distributions = {k: sorted(m['quality'][k] for m in cohort if number(m['quality'].get(k)) is not None) for k in weights}
    costs, cost_keys = [], set()
    ratio = preferences["input_ratio"]
    for model in rows:
        price = model["price"]
        model["mixed_usd"] = ratio * price["input"] + (1 - ratio) * price["output"] if price else None
        price_key = (model['vendor_id'], join_base(model['base_model']))
        if model["mixed_usd"] is not None and model["mixed_usd"] > 0 and price_key not in cost_keys:
            cost_keys.add(price_key)
            costs.append(model["mixed_usd"])
    reference_cost = median(costs) if len(costs) >= 3 else None
    cohort_ids = {m["id"] for m in cohort}
    a, c = preferences["ability_weight"], preferences["cost_weight"]
    for model in rows:
        ability = None
        breakdown = {}
        available = {k:w for k,w in weights.items() if number(model['quality'].get(k)) is not None and len(distributions[k]) >= 10}
        for key, weight in preferences["weights"].items():
            raw = number(model["quality"].get(key))
            included = key in available and model['id'] in cohort_ids
            values = distributions.get(key, [])
            percentile = 100 * (bisect_left(values, raw) + bisect_right(values, raw)) / (2*len(values)) if included and raw is not None else None
            share = 100*weight/sum(available.values()) if included else 0
            breakdown[key] = {"raw":raw,"percentile":percentile,"weight_percent":share,
                              "contribution":percentile*share/100 if percentile is not None else None,
                              "status":"INCLUDED" if included else "NOT_SELECTED" if weight==0 else
                                       "SOURCE_COVERAGE_MISSING" if raw is None else "COHORT_TOO_SMALL"}
        model["score_breakdown"] = breakdown
        model["catalog_eligible"] = model["vendor_id"] in VENDORS and model["id"] in cohort_ids
        model["catalog_visible"] = model["catalog_eligible"] or bool(model.get("official_release"))
        model['source_coverage'] = {'used':len(available),'selected':len(weights),'weight_percent':sum(available.values())/sum(weights.values())*100 if weights else 0}
        if available and model["id"] in cohort_ids:
            total = 0
            for key, weight in available.items():
                values, value = distributions[key], model["quality"][key]
                percentile = 100 * (bisect_left(values, value) + bisect_right(values, value)) / (2 * len(values))
                total += percentile * weight
            ability = total / sum(available.values())
        cost = model["mixed_usd"]
        economy = 100 / (1 + cost / reference_cost) if cost is not None and cost > 0 and reference_cost else None
        value = None
        if (a == 0 or ability is not None) and (c == 0 or economy is not None):
            value = economy if a == 0 else ability if c == 0 else 100 * (ability / 100) ** (a / (a + c)) * (economy / 100) ** (c / (a + c))
        model.update(ability=ability, economy=economy, value=value, starred=model["id"] in preferences["stars"],
                     score_status="ASSESSED" if ability is not None else "COHORT_TOO_SMALL" if len(cohort) < 10 else "SOURCE_COVERAGE_MISSING")
    return sorted(rows, key=lambda m: m["id"]), {
        "id": "SOURCE_COVERAGE_MIDRANK_GEOMETRIC_V2", "cohort_size": len(cohort), "minimum_cohort": 10,
        "source_sample_sizes": {k:len(v) for k,v in distributions.items()},
        "missing_source_policy": "RENORMALIZE_AVAILABLE_WITH_COVERAGE",
        "reference_cost_usd": reference_cost, "cost_sample_size": len(costs),
        "weights": weights, "ability_weight": a, "cost_weight": c, "input_ratio": ratio,
        "cohort_scope": "FULL_CURRENT_CATALOG_BEFORE_UI_FILTERS", "currency_changes_rank": False,
        "quality_scope": "EXACT_EFFORT_OR_SEPARATE_MODEL_REFERENCE",
        "price_scope": "CHANNEL_CATALOG_QUOTE_WITH_VERIFIED_PROMOTIONS_REMOVED_NOT_ACTUAL_TASK_COST"}


class LeaderboardService:
    def __init__(self, sources, profile_root, *, network_preferences=None, clock=None):
        self.sources = sources
        self.path = Path(profile_root) / "leaderboard_preferences.json"
        self.network_preferences = network_preferences or (lambda: {})
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = RLock()
        self.refresh_state = {"status": "IDLE", "results": []}

    def _load(self):
        if not self.path.exists():
            return {"schema_version": PREF_SCHEMA, "revision": 0, "preferences": deepcopy(DEFAULTS)}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        digest = value.pop("sha256", None)
        if digest != canonical_sha256(value) or value.get("schema_version") != PREF_SCHEMA or type(value.get("revision")) is not int:
            raise ValueError("LEADERBOARD_PREFERENCES_CORRUPT")
        preferences = value["preferences"]
        if not isinstance(preferences, dict):
            raise ValueError("LEADERBOARD_PREFERENCES_INVALID")
        value["preferences"] = validate_preferences({**deepcopy(DEFAULTS), **preferences})
        return value

    def save(self, *, expected_revision, changes):
        if type(expected_revision) is not int or not isinstance(changes, dict) or not changes or not set(changes) <= set(DEFAULTS):
            raise ValueError("LEADERBOARD_SAVE_INVALID")
        with self.lock, self.sources._file_lock("leaderboard_preferences.lock"):
            state = self._load()
            if state["revision"] != expected_revision:
                raise ValueError("LEADERBOARD_REVISION_CONFLICT")
            candidate = validate_preferences({**state["preferences"], **changes})
            candidate["preset"] = "general"
            known = {s["source_id"] for s in self.sources._load()["sources"] if s["format"] not in {"ECB_FX_XML", "OPENROUTER"}}
            if "weights" in changes and any(weight and key not in known for key, weight in candidate["weights"].items()):
                raise ValueError("LEADERBOARD_SCORE_SOURCE_INVALID")
            state.update(revision=state["revision"] + 1, preferences=candidate)
            state["sha256"] = canonical_sha256(state)
            self.sources.writer._atomic_write(self.path, state)
        return self.get()

    def _snapshots(self):
        result = []
        # Settings and immutable snapshots are published with atomic replace.
        # Never acquire the source's network-spanning write lock for a UI read.
        state = self.sources._load()
        active = self.sources.refresh_activity()
        for config in state["sources"]:
            last_refresh = self.sources._last_refresh(config)
            row = {"id": config["source_id"], "name": config["display_name"], "format": config["format"],
                   "enabled": config["enabled"], "status": "DISABLED" if not config["enabled"] else "NOT_FETCHED",
                   "retrieved_at": None, "records": [], "snapshot_id": None,
                   "last_refresh": last_refresh, "fetch_status": "RUNNING" if config["source_id"] in active
                   else (last_refresh or {}).get("status", "NOT_FETCHED")}
            try:
                snapshot = self.sources._latest(config)
                if snapshot:
                    stamp = datetime.fromisoformat(snapshot["retrieved_at"].replace("Z", "+00:00"))
                    age = (self.clock() - stamp).total_seconds()
                    row.update(retrieved_at=snapshot["retrieved_at"], records=snapshot["records"],
                               snapshot_id=snapshot["sha256"],
                               status="DISABLED" if not config['enabled'] else "CACHED" if 0 <= age <= config["ttl_days"] * 86400 else "STALE")
            except (OSError, ValueError, TypeError, KeyError):
                row["status"] = "CACHE_INVALID"
            result.append(row)
        return state["revision"], result

    def get(self):
        with self.lock:
            user = self._load()
            refresh_state = deepcopy(self.refresh_state)
        revision, snapshots = self._snapshots()
        from .external_sources import automatic_metadata_enabled, public_preset
        enabled = [s for s in snapshots if s["enabled"]]
        if refresh_state["status"] != "RUNNING":
            results = [{"source_id":s["id"], **s["last_refresh"]} for s in enabled if s["last_refresh"]]
            failed = any(r["status"] in {"ERROR", "RATE_LIMITED"} for r in results)
            cached = any(s["records"] for s in enabled)
            refresh_state.update(results=results, status=(
                "NO_ENABLED_SOURCES" if not enabled else
                "RUNNING" if any(s["fetch_status"] == "RUNNING" for s in enabled) else
                "PARTIAL" if failed and cached else "ERROR" if failed else
                "PASS" if cached else "NOT_FETCHED"))
        refresh_state["automatic_enabled"] = automatic_metadata_enabled()
        preferences = effective_preferences(user["preferences"], snapshots)
        rows, algorithm = project_rows(snapshots, preferences)
        fx = None
        for source in snapshots:
            if source["format"] == "ECB_FX_XML" and source["records"]:
                rate = source["records"][0].get("fx")
                if rate:
                    fx = {**deepcopy(rate), "id": source["snapshot_id"], "source": source["name"],
                          "fetchedAt": source["retrieved_at"], "refreshFailed": source["status"] != "CACHED" or any(
                              r["source_id"] == source["id"] and r["status"] != "PASS" for r in refresh_state["results"])}
        catalog_sources = [s for s in snapshots if s["enabled"] and s["format"] != "ECB_FX_XML"]
        dates = [s["retrieved_at"] for s in catalog_sources if s["retrieved_at"]]
        updated = min(dates) if dates else None
        source_projection = []
        for source in snapshots:
            row = {k: v for k, v in source.items() if k != "records"}
            row["scored_count"] = sum(number(m["quality"].get(source["id"])) is not None for m in rows)
            row["role"] = "FX" if source["format"] == "ECB_FX_XML" else "PRICE" if source["format"] == "OPENROUTER" else "SCORE"
            source_projection.append(row)
        projection_key = canonical_sha256({"preferences": user["revision"], "source_revision": revision,
            "snapshots": [(s["id"], s["snapshot_id"], s["status"], s["fetch_status"], s["last_refresh"]) for s in snapshots],
            "refresh": refresh_state, "day": self.clock().date().isoformat()})
        return {**user, "preferences": preferences, "schema_version": SCHEMA, "source_revision": revision, "projection_key": projection_key,
                "public_preset": public_preset(),
                "rows": rows, "sources": source_projection, "fx": fx, "algorithm": algorithm,
                "updated_at": updated, "observed_at": self.clock().isoformat(),
                "refresh": refresh_state, "missing_star_count": len(set(user["preferences"]["stars"]) - {r["id"] for r in rows}),
                "catalog_policy":{"id":"SELECTED_SCORE_VENDOR_V2",
                                  "default_count":sum(m["catalog_visible"] for m in rows),
                                  "suppressed_count":sum(not m["catalog_visible"] for m in rows),
                                  "official_unscored_count":sum(
                                      bool(m.get("official_release")) and not m["catalog_eligible"] for m in rows),
                                  "starred_incomplete_visible":True},
                "qualification_eligible": False}

    def refresh(self, *, automatic=False, should_stop=None):
        from .external_sources import automatic_metadata_enabled
        if automatic and not automatic_metadata_enabled():
            return {"status": "DISABLED"}
        with self.lock:
            if self.refresh_state["status"] == "RUNNING":
                return {"status": "BUSY"}
            state = self.sources._load()
            configs = [s for s in state["sources"] if s["enabled"]]
            if automatic:
                configs = [s for s in configs if s["refresh_mode"] in {"WEEKLY", "DAILY"}
                           and (not s["credential_env"] or self.sources.environment.get(s["credential_env"]))
                           and self.sources._is_due(s, self.clock())]
                if not configs:
                    return {"status": "NOT_DUE"}
            configs.sort(key=lambda s: s['format'] != 'ECB_FX_XML')
            prefs = deepcopy(self.network_preferences())
            self.refresh_state = {"status": "RUNNING", "started_at": utc_now(), "results": []}
        def work():
            results = []
            for config in configs:
                if should_stop and should_stop():
                    break
                try:
                    receipt = self.sources.refresh(config["source_id"], expected_revision=state["revision"],
                                                   preferences=prefs, _automatic=automatic)
                    item = {k: receipt.get(k) for k in (
                        "source_id", "status", "reason", "external_network_calls",
                        "retry_after", "previous_snapshot_preserved")}
                except Exception:
                    item = {"source_id": config["source_id"], "status": "ERROR", "reason": "SOURCE_REFRESH_FAILED"}
                results.append(item)
                with self.lock:
                    self.refresh_state["results"] = deepcopy(results)
            passed = sum(r["status"] == "PASS" for r in results)
            with self.lock:
                self.refresh_state.update(status="NO_ENABLED_SOURCES" if not results else "PASS" if passed == len(results) else "PARTIAL" if passed else "ERROR",
                                          finished_at=utc_now())
        Thread(target=work, name="memorive-leaderboard-metadata-refresh", daemon=True).start()
        return {"status": "RUNNING"}
