"""The audited legacy PDF fonts keep their real names.

Text recovery for PDFs that use these fonts requires the font names found in the document to
equal this set exactly. A release-time renaming once turned AdvPSMSAM10 into
AdvPSMSAKNOWLEDGE_FEEDBACK, and recovery failed for every such PDF from v1.01 until the fix.
The names below are the ones in the original development source.
"""
from document_processing.engines import legacy_cfont

REAL_FONT_NAMES = {
    "AdvP4C4E51", "AdvP4C4E74", "AdvPSMP1", "AdvPSMP10", "AdvPSMPi6", "AdvPSMSAM10",
    "AdvPSSym", "AdvPSTim", "AdvPSTim-B", "AdvPSTim-I", "AdvSPSMI",
}


def test_audited_font_names_are_the_real_ones():
    assert set(legacy_cfont._FONT_SHA256) == REAL_FONT_NAMES
    assert legacy_cfont._AUDITED_FONT_NAMES == REAL_FONT_NAMES
