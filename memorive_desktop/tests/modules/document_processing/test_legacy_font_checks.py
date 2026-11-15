"""Checks that guard the recovery of text from older PDFs set in legacy fonts (C-number, YMath, symbol).

Recovery runs only when the embedded font programs are exactly the audited ones; anything else is
left to the ordinary text path. These tests drive the checks with synthetic inputs, in the style of
the other legacy-font tests next to this file.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from document_processing.engines import legacy_cfont, legacy_cfont_layout, legacy_symbol_layout, legacy_ymath


@pytest.mark.parametrize("module, error", [
    (legacy_cfont, legacy_cfont.LegacyCFontRecoveryError),
    (legacy_ymath, legacy_ymath.LegacyYMathRecoveryError),
])
class TestAuditedFontSet:
    def test_no_audited_font_means_no_recovery(self, module, error):
        assert module._validate_font_program_hashes({}) is False

    def test_the_exact_audited_set_is_accepted(self, module, error):
        assert module._validate_font_program_hashes(dict(module._FONT_SHA256)) is True

    def test_a_missing_font_is_refused(self, module, error):
        fonts = dict(module._FONT_SHA256)
        dropped = sorted(fonts)[0]
        del fonts[dropped]
        with pytest.raises(error, match=f"missing=\\['{dropped}'\\]"):
            module._validate_font_program_hashes(fonts)

    def test_an_extra_font_is_refused(self, module, error):
        with pytest.raises(error, match="extra=\\['NotAudited'\\]"):
            module._validate_font_program_hashes(dict(module._FONT_SHA256, NotAudited="0" * 64))

    def test_a_different_font_program_is_refused(self, module, error):
        fonts = dict(module._FONT_SHA256)
        changed = sorted(fonts)[-1]
        fonts[changed] = "0" * 64
        with pytest.raises(error, match="hash mismatch"):
            module._validate_font_program_hashes(fonts)


def test_c_font_trace_relations_other_than_the_audited_ones_are_refused():
    with pytest.raises(legacy_cfont.LegacyCFontRecoveryError, match="relation hash mismatch"):
        legacy_cfont._validate_trace_relations(set())
    with pytest.raises(legacy_cfont.LegacyCFontRecoveryError, match="relation hash mismatch"):
        legacy_cfont._validate_trace_relations({("C1", 65, 1, 2)})


def _parsed(*pages):
    return SimpleNamespace(pages=[SimpleNamespace(boxes=[
        SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1, boxclass=kind) for kind, (x0, y0, x1, y1) in page
    ]) for page in pages])


def _baseline(*pages):
    return [{"page_boxes": [{"index": i, "class": kind, "bbox": list(box)} for i, (kind, box) in enumerate(page)]}
            for page in pages]


PAGE = [("text", (10, 20, 300, 40)), ("picture", (10, 50, 200, 150))]


def test_layout_boxes_matching_the_production_page_chunks_pass():
    legacy_cfont_layout._validate_page_boxes(_parsed(PAGE, PAGE[:1]), _baseline(PAGE, PAGE[:1]))


def test_layout_boxes_use_the_outer_integer_rectangle():
    legacy_cfont_layout._validate_page_boxes(_parsed([("text", (10.2, 20.7, 299.5, 39.1))]),
                                             _baseline([("text", (10, 20, 300, 40))]))


@pytest.mark.parametrize("parsed, baseline, message", [
    (_parsed(PAGE), _baseline(PAGE, PAGE), "page count differs"),
    (_parsed(PAGE), _baseline(PAGE[:1]), "box count differs"),
    (_parsed([("table", (10, 20, 300, 40))]), _baseline([("text", (10, 20, 300, 40))]), "Layout identity differs"),
    (_parsed([("text", (10, 20, 300, 41))]), _baseline([("text", (10, 20, 300, 40))]), "Layout identity differs"),
    (_parsed(PAGE), [{"page_boxes": [{"index": 1, "class": "text", "bbox": [10, 20, 300, 40]},
                                     {"index": 1, "class": "picture", "bbox": [10, 50, 200, 150]}]}],
     "box 0: Layout identity differs"),
])
def test_layout_boxes_that_differ_from_the_production_page_chunks_are_refused(parsed, baseline, message):
    with pytest.raises(legacy_cfont_layout.LegacyCFontLayoutRecoveryError, match=message):
        legacy_cfont_layout._validate_page_boxes(parsed, baseline)


class _Document:
    """Pages list fonts as PyMuPDF does: (xref, ext, type, basefont, ...); extract_font returns data at [3]."""

    def __init__(self, fonts, programs):
        self.pages = [SimpleNamespace(get_fonts=lambda full, page=page: page) for page in fonts]
        self.programs = programs

    def __iter__(self):
        return iter(self.pages)

    def extract_font(self, xref):
        return ("name", "ttf", "TrueType", self.programs.get(xref, b""))


@pytest.fixture
def audited_symbol_font(monkeypatch):
    program = b"symbol font program"
    monkeypatch.setitem(legacy_symbol_layout._AUDITED_FONT_SHA256, "TestSymbol", hashlib.sha256(program).hexdigest())
    return program


def test_symbol_fonts_are_kept_only_when_their_program_is_the_audited_one(audited_symbol_font):
    document = _Document([[(7, "ttf", "TrueType", "ABCDEF+TestSymbol"), (8, "ttf", "TrueType", "ABCDEF+Arial")]],
                         {7: audited_symbol_font, 8: b"ordinary"})
    assert legacy_symbol_layout._exact_font_programs(document) == {
        "TestSymbol": hashlib.sha256(audited_symbol_font).hexdigest()}
    other = _Document([[(7, "ttf", "TrueType", "XYZ+TestSymbol")]], {7: b"a different program"})
    assert legacy_symbol_layout._exact_font_programs(other) == {}


def test_a_symbol_font_without_an_embedded_program_is_refused(audited_symbol_font):
    document = _Document([[(7, "ttf", "TrueType", "ABCDEF+TestSymbol")]], {})
    with pytest.raises(legacy_symbol_layout.LegacySymbolLayoutRecoveryError, match="cannot be extracted"):
        legacy_symbol_layout._exact_font_programs(document)


def test_a_symbol_font_embedded_twice_with_different_programs_is_refused(audited_symbol_font):
    document = _Document([[(7, "ttf", "TrueType", "A+TestSymbol")], [(9, "ttf", "TrueType", "B+TestSymbol")]],
                         {7: audited_symbol_font, 9: b"another copy"})
    with pytest.raises(legacy_symbol_layout.LegacySymbolLayoutRecoveryError, match="conflicting embedded program"):
        legacy_symbol_layout._exact_font_programs(document)
