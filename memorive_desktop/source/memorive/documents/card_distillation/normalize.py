"""Normalizing a quote and its chunk before the quote is checked against the source.

Before a quote is checked as a substring of its chunk, both are normalized the same way. Only the
layout noise of PDF extraction goes; words, digits, hyphens (except at a line end), case and word
order are never touched. So any drift that changes a word (except vs after, 5.2 vs 5.3, 5.2 vs 5.20,
increased vs decreased, mg vs μg, a dropped "not" or other negation) still does not match after
normalizing and is caught.

These seven steps and nothing else (the order matters, see below):
  (1) Unicode NFKC: full width to half width, the ligatures `ﬁ ﬂ` to `fi fl`, the micro sign `µ`
      (U+00B5) to `μ` (U+03BC), superscript and subscript digits made plain, the ideographic space and
      NBSP to ` `, and so on;
  (4) curly quotes and apostrophes made straight: `‘ ’ ‛ ′` to `'`, `“ ” „ ‟ ″` to `"` (NFKC does not
      do this);
  (7) Markdown emphasis and the layout noise next to it: the paired marks of `_term_`, `*term*`,
      `**term**` and `***term***` go, then the spaces this leaves before closing punctuation and after an
      opening bracket, and the space between `<` or `>` and a digit inside brackets only. Only paired
      marks that do not touch letters or digits count, so `snake_case`, `__init__`, `2*3`, `x**2` and
      `* list item` stay;
  (6) HTML tags: `<br>`, `<br/>`, `<sub>`, `</i>` and other tags left by table or PDF extraction
      become a space (layout noise, not content; with them gone a faithful quote matches and is kept
      as evidence, which is better than dropping it). Only real tags match (`<` or `</` followed by a
      letter), so `COD < 100`, `5<10` and `x > y` are left alone;
  (3) words broken at a line end: a `-` directly followed by a line break (no space between) goes
      with the break, joining the word. Line ends only: a compound such as `trans-port` (a letter after
      the hyphen) or a range such as `5.2-5.3` (a digit after it) stays;
  (2) any run of whitespace (line breaks, tabs, any Unicode space) becomes one ASCII space;
  (5) trimmed at both ends.

The order:
  - (1) first: full width and ligatures are made plain first (a full-width `－` becomes `-`, and only
    then can (3) see a line-end break);
  - (3) and (6) before (2): (2) turns line breaks and tags into spaces; (6) turns tags into spaces for
    (2) to fold.
  - (4) and (7) do not depend on (2), (3) or (6) (they change single characters) and only need to come
    after (1).
  - (6) removes the tags only, not what they wrap (`H<sub>2</sub>O` becomes `H 2 O`). `�` (U+FFFD) is a
    lost character and cannot be removed here; such sources take the garbled-source path (the field
    drops, the card stays).

The negative cases hold because of what it does not do: no lower-casing, no dropping of trailing
zeros, no change to any letter, digit, hyphen not at a line end, or literal mark that is not emphasis.

Two deliberate limits:
  - Case is not folded. A quote capitalized at a sentence start (`The ...`) fails against a lower-case
    source; the re-anchor step handles it (the re-copy keeps the source's case). Folding case would
    hide drifts that change case.
  - NFKC flattens superscript and subscript digits: `10⁵` (U+2075) becomes `105`, `x₂` becomes `x2`, the
    unit `d⁻¹` becomes `d-1`. That is a narrow gap for numbers (`10⁵` and `105` look the same after it).
    It is not rare: in garbled sources the model often writes a unit faithfully as a superscript
    (`L d⁻¹`) while extraction garbled the source to `L d�1` (U+FFFD); NFKC turns `d⁻¹` into `d-1`,
    which is not `d�1`, so a faithful quote fails. This is one of the main reasons by_field drops a
    field instead of removing the card for a garbled source (_source_corrupted); the model made
    nothing up, it is an extraction quality debt. Whether to guard against it (finding superscripts
    before flattening) waits on how often it happens.
"""
from __future__ import annotations

import re
import unicodedata

# (4) curly quotes and apostrophes to straight ones (NFKC does not do this; includes the OCR primes).
_CURLY = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "′": "'",           # ‘ ’ ‛ ′
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',  # “ ” „ ‟ ″
})
# (7) Markdown emphasis: only paired marks go. Asterisks must close with the same count; a single
# underscore must not touch a letter, digit or underscore, so math, wildcards, lists, snake_case, dunders
# and unclosed marks are not taken for layout noise.
_MD_STAR_EMPH = re.compile(
    r"(?<![\w*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_MD_UNDERSCORE_EMPH = re.compile(
    r"(?<![\w_])_(?!_)(?=\S)(.+?)(?<=\S)(?<!_)_(?![\w_])")
_SPACE_BEFORE_CLOSING_PUNCT = re.compile(r"\s+([,.;:%)\]\}])")
_SPACE_AFTER_OPEN_PUNCT = re.compile(r"([\(\[\{])\s+")
_SPACE_AFTER_PAREN_COMPARE = re.compile(r"(?<=[\(\[])([<>])\s+(?=\d)")
# (6) HTML tags (left by table or PDF extraction, such as `<br>`) become a space. Only real tags match:
#    `<` or `</` followed by a letter, then a name or attributes (no `<>`) up to `>`. So `< 100` (a space),
#    `<10` (a digit) and `x>y` are left alone.
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*[^<>]*>")
# (3) words broken at a line end: a hyphen directly before a line break (a carriage return may come
#    between); no space between them. Only the ASCII `-` (a full-width one is `-` after NFKC); a hyphen
#    followed by a letter or digit stays (compounds, ranges).
_LINE_HYPHEN = re.compile(r"-\r?\n")
# (2) runs of whitespace (\s in a str regex covers every Unicode space).
_WS = re.compile(r"\s+")
# PDF spaces next to `·` are ignored only in products of scientific units, and at least one unit
# must have an exponent (a literal `^` from PDF or Markdown counts). So spaces in ordinary text or in a
# relation such as `A · B` are not treated as removable.
_UNIT_EXPONENT_TEXT = r"(?:\^)?[−⁻-](?:[0-9⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
_UNIT_ATOM_TEXT = rf"[A-Za-zμµ]+(?:{_UNIT_EXPONENT_TEXT})?"
_SCIENTIFIC_UNIT_PRODUCT = re.compile(
    rf"(?<![A-Za-zμµ]){_UNIT_ATOM_TEXT}(?:\s*·\s*{_UNIT_ATOM_TEXT})+(?![A-Za-zμµ])"
)
_SCIENTIFIC_UNIT_EXPONENT = re.compile(_UNIT_EXPONENT_TEXT)
# Numeric percent and per-mille tokens only; whether a space may go is decided below by
# `_cjk_percentage_pdf_whitespace_offsets` (CJK text right or left of the token). No list of papers,
# metrics or values.
_PERCENTAGE_EXPRESSION = re.compile(
    r"(?:[<>≥≤~约]\s*)?\d+(?:\.\d+)?"
    r"(?:\s*(?:±|~|～|–|—|→|-)\s*\d+(?:\.\d+)?)?"
    r"\s*(?:%|％|‰)"
)


def _is_cjk_character(char: str) -> bool:
    """CJK characters only; never used to remove PDF spaces between English words, digits or punctuation."""
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _is_non_cjk_unicode_letter_or_number(char: str) -> bool:
    """Boundaries of Unicode letter or digit tokens that sit naturally inside Chinese text.

    Only Unicode character categories are used, with no list of names, variables, papers or
    "allowed characters"; the characters themselves are never changed.
    """
    return bool(char) and not _is_cjk_character(char) and char.isalnum()


def _is_cjk_unicode_token_boundary(left: str, right: str) -> bool:
    """Whether this is a direct boundary between CJK and a Unicode letter or digit token."""
    return (
        _is_cjk_character(left)
        and _is_non_cjk_unicode_letter_or_number(right)
    ) or (
        _is_non_cjk_unicode_letter_or_number(left)
        and _is_cjk_character(right)
    )


def _cjk_percentage_pdf_whitespace_offsets(text: str) -> set[int]:
    """The PDF spaces around or inside a percentage in Chinese text that may be removed.

    Only when the text right of or left of the token is CJK (or the token has its own Chinese
    qualifier); English word boundaries, ordinary numbers and other symbols are left alone.
    """
    removable: set[int] = set()
    for match in _PERCENTAGE_EXPRESSION.finditer(text):
        left_space_start = match.start()
        while left_space_start > 0 and text[left_space_start - 1].isspace():
            left_space_start -= 1
        left = text[left_space_start - 1] if left_space_start > 0 else ""

        right_space_end = match.end()
        while right_space_end < len(text) and text[right_space_end].isspace():
            right_space_end += 1
        right = text[right_space_end] if right_space_end < len(text) else ""

        has_cjk_context = (
            _is_cjk_character(left)
            or _is_cjk_character(right)
            or any(_is_cjk_character(char) for char in match.group(0))
        )
        if not has_cjk_context:
            continue
        if _is_cjk_character(left):
            removable.update(range(left_space_start, match.start()))
        if _is_cjk_character(right):
            removable.update(range(match.end(), right_space_end))
        removable.update(
            offset
            for offset in range(match.start(), match.end())
            if text[offset].isspace()
        )
    return removable


def _normalize_cjk_pdf_whitespace(text: str) -> tuple[str, list[int]]:
    """Remove PDF spaces inside CJK words, at mixed token boundaries, and in Chinese percentages."""
    percentage_whitespace = _cjk_percentage_pdf_whitespace_offsets(text)
    normalized, offsets = [], []
    index = 0
    while index < len(text):
        if not text[index].isspace():
            normalized.append(text[index])
            offsets.append(index)
            index += 1
            continue
        run_end = index + 1
        while run_end < len(text) and text[run_end].isspace():
            run_end += 1
        left = text[index - 1] if index > 0 else ""
        right = text[run_end] if run_end < len(text) else ""
        is_cjk_word_space = _is_cjk_character(left) and _is_cjk_character(right)
        is_cjk_unicode_token_space = _is_cjk_unicode_token_boundary(left, right)
        is_cjk_percentage_space = all(
            offset in percentage_whitespace for offset in range(index, run_end)
        )
        if not (
            is_cjk_word_space
            or is_cjk_unicode_token_space
            or is_cjk_percentage_space
        ):
            for offset in range(index, run_end):
                normalized.append(text[offset])
                offsets.append(offset)
        index = run_end
    return "".join(normalized), offsets


def _scientific_unit_pdf_whitespace_offsets(text: str) -> set[int]:
    """Offsets of the removable spaces next to `·`; only products of Latin or μ units with an exponent."""
    removable: set[int] = set()
    for match in _SCIENTIFIC_UNIT_PRODUCT.finditer(text):
        if _SCIENTIFIC_UNIT_EXPONENT.search(match.group(0)) is None:
            continue
        index = match.start()
        while index < match.end():
            if not text[index].isspace():
                index += 1
                continue
            run_end = index + 1
            while run_end < match.end() and text[run_end].isspace():
                run_end += 1
            left = text[index - 1] if index > match.start() else ""
            right = text[run_end] if run_end < match.end() else ""
            if left == "·" or right == "·":
                removable.update(range(index, run_end))
            index = run_end
    return removable


def _normalize_cjk_and_scientific_unit_pdf_whitespace(text: str) -> tuple[str, list[int]]:
    """Remove PDF spaces inside CJK words, at mixed token boundaries, in percentages and in scientific units."""
    unit_whitespace = _scientific_unit_pdf_whitespace_offsets(text)
    percentage_whitespace = _cjk_percentage_pdf_whitespace_offsets(text)
    normalized, offsets = [], []
    index = 0
    while index < len(text):
        if not text[index].isspace():
            normalized.append(text[index])
            offsets.append(index)
            index += 1
            continue
        run_end = index + 1
        while run_end < len(text) and text[run_end].isspace():
            run_end += 1
        left = text[index - 1] if index > 0 else ""
        right = text[run_end] if run_end < len(text) else ""
        is_cjk_word_space = _is_cjk_character(left) and _is_cjk_character(right)
        is_cjk_unicode_token_space = _is_cjk_unicode_token_boundary(left, right)
        is_unit_dot_space = all(offset in unit_whitespace for offset in range(index, run_end))
        is_cjk_percentage_space = all(
            offset in percentage_whitespace for offset in range(index, run_end)
        )
        if not (
            is_cjk_word_space
            or is_cjk_unicode_token_space
            or is_unit_dot_space
            or is_cjk_percentage_space
        ):
            for offset in range(index, run_end):
                normalized.append(text[offset])
                offsets.append(offset)
        index = run_end
    return "".join(normalized), offsets


def recover_unique_cjk_pdf_quote(quote: str, chunk_text: str) -> str | None:
    """Map a unique match, found with spaces removed inside CJK words, at mixed token boundaries and
    in percentages, back to the source text."""
    normalized_quote, _ = _normalize_cjk_pdf_whitespace(quote)
    normalized_chunk, offsets = _normalize_cjk_pdf_whitespace(chunk_text)
    if (
        not normalized_quote
        or sum(not char.isspace() for char in normalized_quote) < 8
    ):
        return None
    match_start = normalized_chunk.find(normalized_quote)
    if match_start < 0 or normalized_chunk.find(normalized_quote, match_start + 1) >= 0:
        return None
    match_end = match_start + len(normalized_quote) - 1
    if match_end >= len(offsets):
        return None
    candidate = chunk_text[offsets[match_start]:offsets[match_end] + 1]
    candidate_normalized, _ = _normalize_cjk_pdf_whitespace(candidate)
    if candidate_normalized != normalized_quote or candidate not in chunk_text:
        return None
    return candidate


def recover_unique_scientific_unit_pdf_quote(quote: str, chunk_text: str) -> str | None:
    """When a match is unique with CJK, mixed-token and scientific-unit spaces removed, return the real
    source slice."""
    normalizer = _normalize_cjk_and_scientific_unit_pdf_whitespace
    normalized_quote, _ = normalizer(quote)
    normalized_chunk, offsets = normalizer(chunk_text)
    if (
        not normalized_quote
        or sum(not char.isspace() for char in normalized_quote) < 8
    ):
        return None
    match_start = normalized_chunk.find(normalized_quote)
    if match_start < 0 or normalized_chunk.find(normalized_quote, match_start + 1) >= 0:
        return None
    match_end = match_start + len(normalized_quote) - 1
    if match_end >= len(offsets):
        return None
    candidate = chunk_text[offsets[match_start]:offsets[match_end] + 1]
    candidate_normalized, _ = normalizer(candidate)
    if candidate_normalized != normalized_quote or candidate not in chunk_text:
        return None
    return candidate


def normalize(s: str) -> str:
    """Normalize, the same way for quote and source (the steps in the module docstring). A non-string
    counts as an empty string."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s)   # (1) NFKC, first
    s = s.translate(_CURLY)                # (4) straight quotes
    s = _MD_STAR_EMPH.sub(r"\2", s)       # (7) paired Markdown * / ** / *** emphasis
    s = _MD_UNDERSCORE_EMPH.sub(r"\1", s) # (7) paired Markdown _ emphasis
    s = _SPACE_BEFORE_CLOSING_PUNCT.sub(r"\1", s)  # (7) space before closing punctuation
    s = _SPACE_AFTER_OPEN_PUNCT.sub(r"\1", s)      # (7) space after an opening bracket
    s = _SPACE_AFTER_PAREN_COMPARE.sub(r"\1", s)   # (7) only inside brackets: > 3 to >3
    s = _LINE_HYPHEN.sub("", s)            # (3) line-end breaks, before (2)
    s = _HTML_TAG.sub(" ", s)              # (6) HTML tags to a space, before (2); real tags only, never math <>
    return _WS.sub(" ", s).strip()         # (2) whitespace runs to one ASCII space, (5) trim


def quote_in_text(quote: str, chunk_text: str) -> bool:
    """Substring check: whether the normalized quote is a substring of the normalized chunk_text.
    An empty quote (or one empty after normalizing) is always False: the empty string is a substring
    of everything, so it is refused explicitly."""
    nq = normalize(quote)
    return bool(nq) and nq in normalize(chunk_text)
