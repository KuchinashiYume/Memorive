"""Building and writing a card.

The model's JSON is only an intermediate; this module turns the checked fields into [Card] front
matter that follows the card schema exactly (the {value, anchors} shape is never written as a card).
It covers the 7 core fields with each field's by_field anchors (a list of anchor objects
[{chunk_id, quote, section_page}]; high-risk fields carry a verbatim quote, combined fields may have a
null quote) and the bookkeeping fields; the key_data block (each entry {value, unit, metric, sample,
stat, quote, chunk_id, needs_review}, with chunk_id but no section_page written; each quote is
verbatim from the source, checked as a substring during distillation, and a failure refuses the whole
card); and the extra blocks (comparison context, limitations and outlook, terms and citations).
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from memorive.documents.card_distillation.config import CARD_SCHEMA_VERSION
from memorive.documents.card_distillation.errors import CardExistsError

# -- credibility (graded admission) ---------------------------------------------------
# One for each field and each key_data entry: no review finding = verified, a probable anchor
# misplacement = anchor_uncertain, a real violation = flagged; not reviewed yet = unknown, which is how a
# card starts. Older v1 to v4 cards without field_credibility read as unknown (read_field_credibility).
CRED_VERIFIED = "verified"
CRED_ANCHOR_UNCERTAIN = "anchor_uncertain"
CRED_FLAGGED = "flagged"
CRED_UNKNOWN = "unknown"
CREDIBILITIES = frozenset({CRED_VERIFIED, CRED_ANCHOR_UNCERTAIN, CRED_FLAGGED, CRED_UNKNOWN})
# The "not checked" mark is on a card from the start, so no way of admitting it (verify_and_drive,
# manual_approve or placeholder, besides grade_and_admit) puts an unknown entry into active without a
# mark; only verified has danger=None. grading._mark uses these two constants for unknown, so the text
# is the same either way.
DANGER_UNKNOWN_KD = "⚠ 数值 EVIDENCE_REVIEW 未核(未收为异源核对象 / 锚点缺失 / 缺 chunk)·勿直接引·须核原文"
DANGER_UNKNOWN_FIELD = "⚠ 该字段 EVIDENCE_REVIEW 未核(未收为异源核对象 / 锚点缺失 / 缺 chunk)·勿直接引·须核原文"
_FENCE_RE = re.compile(r"(?m)^---[ \t]*\r?$")   # CRLF-safe front matter fence (the same as in memorive/documents/card_states/card_io)

# The six core content fields (the 7th, source_anchor, anchors all of them and is built apart).
CONTENT_FIELDS = ["research_question", "research_object", "method",
                  "key_results", "author_conclusion", "boundary_conditions"]
# List fields and string fields: a list field is a list of strings, the rest are strings.
LIST_FIELDS = {"research_object", "method", "boundary_conditions"}
FINAL_COMPLETION_STATUSES = frozenset({"complete", "passed_with_omissions"})

# -- rough main language of a card (only for system labels: the section_page default and the body comment) --
# The prompt keeps the 7 fields in the source's language, but a few texts the system writes and the
# prompt does not reach (defaults, the body HTML comment, model fallback) would put Chinese into an
# English card, so they follow the card's language. Field content is never changed.
_HAN_RE = re.compile(r"[一-鿿]")        # Han characters
_KANA_RE = re.compile(r"[぀-ヿ]")       # Japanese kana (hiragana and katakana)


def detect_card_lang(field_values) -> str:
    """Rough main language of a card: 'ja', 'zh' or 'en'. It only picks the language of system labels
    and never changes field content.
    Kana present: 'ja'; otherwise Han characters at 20% or more: 'zh'; anything else (mostly English,
    a few Han characters kept out by the threshold): 'en'. So an English card never gets Chinese labels."""
    parts = []
    for v in field_values:
        parts.extend(v if isinstance(v, list) else [v])
    s = "".join(str(x) for x in parts)
    if _KANA_RE.search(s):
        return "ja"
    if not s:
        return "en"
    return "zh" if len(_HAN_RE.findall(s)) / len(s) >= 0.20 else "en"


# -- key_data template labels (system scaffolding chosen by card_lang; an English card never gets Chinese labels) --
# The four sample slots hold R4's scope, the three stat slots R3's statistics. The labels are the
# system's, the content is in the source's language. The model fills in content and uses the given
# labels verbatim; the validator accepts the label set of card_lang (distill._validate_key_data).
KEY_DATA_SAMPLE_SLOTS = ("group", "phase_time", "subprocess", "comparator")
KEY_DATA_STAT_SLOTS = ("stat_type", "n", "significance")
_KEY_DATA_LABELS = {
    "ja": {"group":"標本/対照群","phase_time":"段階/時点","subprocess":"部分過程","comparator":"比較対象","stat_type":"統計量の種類","n":"n","significance":"有意性","not_given":"記載なし"},
    "zh": {"group": "样本/对照组", "phase_time": "阶段·时段", "subprocess": "子过程",
           "comparator": "对照", "stat_type": "统计类型", "n": "n", "significance": "显著性",
           "not_given": "未给"},
    "en": {"group": "sample/control", "phase_time": "phase/time", "subprocess": "subprocess",
           "comparator": "comparator", "stat_type": "stat_type", "n": "n",
           "significance": "significance", "not_given": "Not given"},
}


def key_data_labels(lang: str) -> dict:
    """The key_data template labels for a card's language (system labels are set by the system and
    never mixed; a language without its own set gets English)."""
    return _KEY_DATA_LABELS.get(lang, _KEY_DATA_LABELS["en"])


# The body's placeholder comment, in the card's language; written by the system, not paper content.
_BODY_COMMENT = {
    "ja": "<!-- 本文: 原文に忠実な要約。構造化フィールド、key_data、比較・限界・用語の補助情報は frontmatter に保存。個人的な分析は KNOWLEDGE_FEEDBACK に属します。 -->\n",
    "zh": ("<!-- 正文区:纯人读忠实提要(结构化字段 + key_data + 三附属[比较上下文/局限展望/术语引用] 均在 frontmatter;"
           "三附属 Core 只抽存不投入使用)。个人分析归 KNOWLEDGE_FEEDBACK、不入卡。 -->\n"),
    "en": ("<!-- Body: human-readable faithful summary (structured fields + key_data + 3 auxiliaries "
           "[comparison / limitations-outlook / terms-citations] all in frontmatter; auxiliaries stored-only in Core). "
           "Personal analysis goes to KNOWLEDGE_FEEDBACK, not the card. -->\n"),
}


class _CardDumper(yaml.SafeDumper):
    """Writes long text (with line breaks) as a `|` block scalar, the schema's form for long text."""


def _str_representer(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_CardDumper.add_representer(str, _str_representer)


def build_card(*, paper_id: str, title: str, data_ownership: str,
               fields: dict, by_field: dict, distill_model: str, distilled_at: str,
               schema_version: int, key_data: list | None = None,
               aux: dict | None = None) -> dict:
    """Put the checked fields into a front matter dict in the schema's order.
    fields: {field: value}; by_field: {field: [{chunk_id, quote, section_page}, ...]} (one object per
      anchor; quote may be null);
    key_data: the checked key_data entries (each {value, unit, metric, sample, stat, quote, chunk_id,
      needs_review}), a required block. Only chunk_id is written, not section_page (the page is looked
      up from chunk_id for display, logs and the review view); quote is verbatim from the source
      (checked as a substring during distillation).
    aux: the extra blocks:
      - a dict (success): the four blocks are written (empty lists too);
      - None (failure): none of the four is written (a pending card without them; their failure does
        not affect the core fields and key_data).
      (The runtime log has aux_call / aux_finished / aux_failed to tell "not run", "failed" and
      "succeeded but empty" apart.)"""
    card_language = detect_card_lang(fields.get(f) for f in CONTENT_FIELDS)
    chinese = card_language == "zh"
    unknown_number = DANGER_UNKNOWN_KD if chinese else "Unverified value; check the source before citing."
    unknown_field = DANGER_UNKNOWN_FIELD if chinese else "Unverified field; check the source before citing."
    if card_language == 'ja':
        unknown_number = '未検証の数値です。引用前に原文を確認してください。'
        unknown_field = '未検証の項目です。引用前に原文を確認してください。'
    fm: dict = {"title": title}
    for f in CONTENT_FIELDS:                       # the six content fields
        fm[f] = fields[f]
    fm["source_anchor"] = {                        # the 7th field, which anchors all the others (by_field: a list of anchor objects per field)
        "paper_id": paper_id,
        "section_page": None,                      # the old whole-card anchor: not the main anchor since v2, kept for v1 cards
        "chunk_id": None,
        # each field: a list of anchor objects [{chunk_id, quote, section_page}, ...] (quote may be null)
        "by_field": {f: [dict(a) for a in by_field[f]] for f in CONTENT_FIELDS},
    }
    # A required block (an empty list is fine for a purely qualitative paper; checked in distill).
    # Each entry starts unknown with the not-checked mark; grade_and_admit sets verified (no mark),
    # anchor_uncertain or flagged (each with a mark).
    fm["key_data"] = [{**dict(e), "credibility": CRED_UNKNOWN, "credibility_note": None, "danger": unknown_number}
                      for e in (key_data or [])]
    if aux is not None:                            # extra blocks, stored only: all four when aux succeeded (empty lists too), none after a failure
        fm["comparison_context"] = list(aux.get("comparison_context") or [])
        fm["author_limitations_outlook"] = (aux.get("author_limitations_outlook")
                                            or {"limitations": [], "outlook": []})
        fm["terms"] = list(aux.get("terms") or [])
        fm["citations"] = list(aux.get("citations") or [])
    # top-level credibility of the six core fields: unknown with the not-checked mark until grade_and_admit
    fm["field_credibility"] = {f: {"credibility": CRED_UNKNOWN, "note": None, "danger": unknown_field}
                               for f in CONTENT_FIELDS}
    fm["completion_status"] = "pending_review"
    fm["omissions"] = []
    fm["review_status"] = "pending"                # not active yet; the card state step admits it
    fm["is_derived"] = False                       # always false for a paper card; derived entries belong to knowledge feedback
    fm["data_ownership"] = data_ownership          # from document intake
    fm["schema_version"] = schema_version
    fm["distill_model"] = distill_model            # the model the gateway reports it used
    fm["distilled_at"] = distilled_at              # local time with offset, to the minute; a redo writes a new one
    return fm


def write_card(card_path: Path, fm: dict, *, overwrite: bool = False) -> Path:
    """Write [Card].md: the front matter (schema order, long text as | blocks) and an empty body with a
    placeholder comment in the card's language.
    No overwrite by default: when the target exists and overwrite is false, CardExistsError (stored
    data is never overwritten; pass overwrite=True to replace it)."""
    if card_path.exists() and not overwrite:
        raise CardExistsError(f"[Card] 已存在、默认不覆盖: {card_path}(重蒸馏要覆盖须显式 overwrite=True)。")
    front = yaml.dump(fm, Dumper=_CardDumper, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, width=10_000)
    lang = detect_card_lang([fm.get(f) for f in CONTENT_FIELDS])   # the placeholder comment follows the card's language
    body = "---\n" + front + "---\n\n" + _BODY_COMMENT[lang]
    if overwrite:
        fd, temporary = tempfile.mkstemp(dir=card_path.parent, prefix=".card_publish_", suffix=".tmp")
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, card_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    else:
        from memorive.documents.card_distillation.segmented_distill.publish import publish_bytes_no_clobber
        try:
            publish_bytes_no_clobber(card_path, body.encode("utf-8"))
        except FileExistsError as exc:
            raise CardExistsError(f"[Card] concurrently created: {card_path}") from exc
    return card_path


def read_field_credibility(fm: dict) -> dict:
    """The top-level field_credibility; an older v1 to v4 card without it gives unknown for all six
    core fields (neither taken as checked nor an error)."""
    fc = fm.get("field_credibility")
    if isinstance(fc, dict):
        return fc
    return {f: {"credibility": CRED_UNKNOWN, "note": None, "danger": DANGER_UNKNOWN_FIELD} for f in CONTENT_FIELDS}


def apply_field_credibility(card_path: Path, field_cred: dict, key_data_cred: list) -> Path:
    """Write the review's credibilities into an existing card (called by grade_and_admit): the top-level
    field_credibility and each key_data entry's credibility, credibility_note and danger. The rest of
    the front matter and the body stay as they are (the YAML is read, changed and dumped again with
    the same _CardDumper; review_status is not touched, the card state step moves it). Written
    atomically through a temporary file and os.replace; CRLF-safe.

    field_cred: {field: {credibility, note}} (six core fields); key_data_cred: [{credibility, note,
    danger}] in key_data order.
    """
    text = card_path.read_bytes().decode("utf-8")
    fences = list(_FENCE_RE.finditer(text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise ValueError(f"卡片 frontmatter 非法(需独占一行 --- 开头 + 闭合): {card_path}")
    front = text[fences[0].end():fences[1].start()]
    body_from_2nd_fence = text[fences[1].start():]        # "---\n\n<body...>" kept as it is
    fm = yaml.safe_load(front)
    if not isinstance(fm, dict):
        raise ValueError(f"卡片 frontmatter 解析非 dict: {card_path}")
    fm["field_credibility"] = field_cred                 # replaces the unknown set when the card was made
    kd = fm.get("key_data")
    if isinstance(kd, list):
        for i, e in enumerate(kd):
            if not isinstance(e, dict):
                continue
            if i < len(key_data_cred):                 # normal case: the grade at this index (only verified has danger=None)
                c = key_data_cred[i]
                e["credibility"] = c.get("credibility", CRED_UNKNOWN)
                e["credibility_note"] = c.get("note")
                e["danger"] = c.get("danger")
            else:                                      # out of range (should not happen): unknown with the mark, never unknown without one
                e["credibility"] = CRED_UNKNOWN
                e["credibility_note"] = None
                e["danger"] = DANGER_UNKNOWN_KD
    new_front = yaml.dump(fm, Dumper=_CardDumper, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=10_000)
    new_text = "---\n" + new_front + body_from_2nd_fence
    fd, tmp = tempfile.mkstemp(dir=card_path.parent, prefix=".cred_tmp_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_text.encode("utf-8"))
        os.replace(tmp, card_path)                       # an atomic replace on the same disk: the old file or the whole new one
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return card_path


def finalize_card_after_repair(
    card_path: Path,
    *,
    fields: dict,
    by_field: dict,
    key_data: list,
    field_credibility: dict,
    omissions: list,
    completion_status: str,
) -> Path:
    """Atomically write the authoritative post-EVIDENCE_REVIEW Card v6.

    The initial Card and every EVIDENCE_REVIEW report remain recoverable through the omission
    records.  This writer only accepts a complete, internally consistent final
    payload; validation failure leaves the existing Card byte-for-byte intact.
    """
    card_path = Path(card_path)
    if completion_status not in FINAL_COMPLETION_STATUSES:
        raise ValueError(f"unknown completion_status: {completion_status!r}")
    if not isinstance(omissions, list):
        raise ValueError("omissions must be a list")
    if (completion_status == "passed_with_omissions") != bool(omissions):
        raise ValueError("completion_status must match whether omissions exist")
    if not isinstance(fields, dict) or set(CONTENT_FIELDS) - set(fields):
        raise ValueError("fields must contain all core content fields")
    if not isinstance(by_field, dict) or set(CONTENT_FIELDS) - set(by_field):
        raise ValueError("by_field must contain all core content fields")
    if not isinstance(key_data, list):
        raise ValueError("key_data must be a list")
    if not isinstance(field_credibility, dict) or set(CONTENT_FIELDS) - set(field_credibility):
        raise ValueError("field_credibility must contain all core content fields")

    missing_locations = set()
    for field in CONTENT_FIELDS:
        value = fields[field]
        if field in LIST_FIELDS:
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                raise ValueError(f"final field {field} must be null or a non-empty-string list")
            if value is None or value == []:
                missing_locations.add(f"by_field.{field}")
        else:
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"final field {field} must be null or a non-empty string")
            if value is None:
                missing_locations.add(f"by_field.{field}")
        anchors = by_field[field]
        if not isinstance(anchors, list) or not all(isinstance(anchor, dict) for anchor in anchors):
            raise ValueError(f"final anchors for {field} must be an object list")

    omission_keys = set()
    omission_locations = set()
    required_omission_keys = {
        "paper_id",
        "location",
        "item_index",
        "disposition",
        "removed_value",
        "related_anchors",
        "attempts",
        "initial_report_id",
        "final_report_id",
    }
    for index, omission in enumerate(omissions):
        if not isinstance(omission, dict) or required_omission_keys - set(omission):
            raise ValueError(f"omissions[{index}] is missing required audit keys")
        location = omission["location"]
        item_index = omission["item_index"]
        if not isinstance(location, str) or not location:
            raise ValueError(f"omissions[{index}].location must be non-empty")
        if not isinstance(item_index, int) or isinstance(item_index, bool) or item_index < 0:
            raise ValueError(f"omissions[{index}].item_index must be a non-negative integer")
        if not isinstance(omission["paper_id"], str) or not omission["paper_id"].strip():
            raise ValueError(f"omissions[{index}].paper_id must be non-empty")
        anchors = omission["related_anchors"]
        if not isinstance(anchors, list) or any(not isinstance(anchor, dict) for anchor in anchors):
            raise ValueError(f"omissions[{index}].related_anchors must be an object list")
        attempts = omission["attempts"]
        # New records describe roles. Accept exact historical shapes without
        # rewriting their counters or claiming that a specific model was used.
        accepted_attempt_keys = (
            {"initial_review_calls", "repair_calls", "targeted_review_calls"},
            {"initial_review_calls", "deepseek_repair_calls", "targeted_review_calls"},
            {"sonnet_initial_review_calls", "deepseek_repair_calls", "sonnet_targeted_review_calls"},
        )
        if not isinstance(attempts, dict) or set(attempts) not in accepted_attempt_keys:
            raise ValueError(f"omissions[{index}].attempts has an invalid shape")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in attempts.values()
        ):
            raise ValueError(f"omissions[{index}].attempts values must be non-negative integers")
        key = (location, item_index)
        if key in omission_keys:
            raise ValueError(f"duplicate omission target: {key}")
        omission_keys.add(key)
        omission_locations.add(location)
    if not missing_locations.issubset(omission_locations):
        raise ValueError("every fully missing core field must have an omission tombstone")

    text = card_path.read_bytes().decode("utf-8")
    fences = list(_FENCE_RE.finditer(text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise ValueError(f"卡片 frontmatter 非法(需独占一行 --- 开头 + 闭合): {card_path}")
    front = text[fences[0].end():fences[1].start()]
    body_from_2nd_fence = text[fences[1].start():]
    fm = yaml.safe_load(front)
    if not isinstance(fm, dict):
        raise ValueError(f"卡片 frontmatter 解析非 dict: {card_path}")

    for field in CONTENT_FIELDS:
        fm[field] = fields[field]
    source_anchor = fm.get("source_anchor")
    if not isinstance(source_anchor, dict):
        raise ValueError("source_anchor must be an object")
    source_anchor["by_field"] = {field: [dict(anchor) for anchor in by_field[field]] for field in CONTENT_FIELDS}
    fm["key_data"] = [dict(item) for item in key_data]
    fm["field_credibility"] = field_credibility
    fm["completion_status"] = completion_status
    fm["omissions"] = omissions
    fm["schema_version"] = CARD_SCHEMA_VERSION

    new_front = yaml.dump(
        fm,
        Dumper=_CardDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10_000,
    )
    new_text = "---\n" + new_front + body_from_2nd_fence
    fd, tmp = tempfile.mkstemp(dir=card_path.parent, prefix=".repair_tmp_", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(new_text.encode("utf-8"))
        tmp_text = tmp_path.read_bytes().decode("utf-8")
        tmp_fences = list(_FENCE_RE.finditer(tmp_text))
        if len(tmp_fences) < 2:
            raise ValueError("temporary repaired Card lost frontmatter fences")
        tmp_fm = yaml.safe_load(tmp_text[tmp_fences[0].end():tmp_fences[1].start()])
        if not isinstance(tmp_fm, dict) or tmp_fm.get("completion_status") != completion_status:
            raise ValueError("temporary repaired Card failed post-write verification")
        os.replace(tmp_path, card_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return card_path
