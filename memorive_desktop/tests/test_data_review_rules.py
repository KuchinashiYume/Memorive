"""The numeric rules of the data review (memorive/documents/data_review/engine.py).

evaluate() checks one rule over the records it names: whether the reported numbers can all be true
under the stated rounding and conditions (COMPATIBLE), cannot (INCONSISTENT, with a finding to clarify),
or cannot be checked (INSUFFICIENT_INFORMATION, NOT_APPLICABLE, INPUT_ERROR). analyze() reads the numbers
out of a text first and binds the rules to them by value and quote. Every number below is made up and
every expected result can be worked out by hand.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
if str(DESKTOP / "source") not in sys.path:
    sys.path.insert(0, str(DESKTOP / "source"))

from memorive.documents.data_review.engine import analyze, evaluate  # noqa: E402


def records(**values):
    """Records as the extractor writes them; a (relation, value) pair gives a bounded value such as "<30.0"."""
    out = {}
    for name, value in values.items():
        relation = ""
        if isinstance(value, tuple):
            relation, value = value
        out[name] = {"record_id": name, "raw": value, "value": value, "relation": relation,
                     "decimal_places": len(value.split(".")[1]) if "." in value else 0,
                     "source": {"kind": "RawMD", "quote": f"{name} {relation}{value}", "line": 1}}
    return out


def rule(kind, inputs, **parameters):
    return {"rule_id": f"rule-{kind}", "type": kind, "inputs": inputs, "parameters": parameters,
            "binding_source": {"kind": "TEST"}}


def run(rule_, records_):
    return evaluate(rule_, records_, checkpoint=lambda: None)


# (case, rule, records): the first value of each pair fits, the second does not.
PAIRS = [
    ("12 of 40 is 30.0 %",
     rule("percentage", {"count": "c", "denominator": "d", "percent": "p"}, unweighted_count=True, rounding="nearest"),
     {"c": "12", "d": "40"}, "p", ("30.0", "31.5")),
    ("12.5 + 20.0 + 7.5 is 40.0",
     rule("sum", {"parts": ["a", "b", "c"], "total": "t"}, mutually_exclusive=True, exhaustive=True, rounding="nearest"),
     {"a": "12.5", "b": "20.0", "c": "7.5"}, "t", ("40.0", "42.0")),
    ("SD 4.00 over n 16 gives SE 1.00",
     rule("sd_se", {"sd": "sd", "se": "se", "n": "n"}, definition="explicit_SE_equals_SD_over_sqrt_n", rounding="nearest"),
     {"sd": "4.00", "n": "16"}, "se", ("1.00", "1.50")),
    ("one quantity printed twice",
     rule("cross", {"left": "l", "right": "r"}, same_quantity=True, rounding="nearest"),
     {"l": "2.35"}, "r", ("2.35", "2.60")),
    ("18.4 - 12.1 is 6.3",
     rule("literal_arithmetic", {"left": "a", "right": "b", "reported": "x"}, operation="-", percent_result=False,
          rounding="display_precision_envelope"),
     {"a": "18.4", "b": "12.1"}, "x", ("6.3", "7.3")),
    ("a percent lies in 0..100",
     rule("numeric_domain", {"value": "v"}, lower="0", upper="100", domain_label="percent", rounding="nearest"),
     {}, "v", ("87.5", "112.0")),
    ("the mean of 4 answers on a 1-step scale is a multiple of 0.25",
     rule("grid_mean", {"mean": "m", "n": "n"}, step="1", origin="0", equal_weights=True, rounding="nearest"),
     {"n": "4"}, "m", ("4.25", "4.30")),
    ("z 1.96, two-sided, gives p 0.05",
     rule("test_p", {"statistic": "z", "p": "p"}, distribution="normal", tail="two_sided", adjustment="none",
          rounding="nearest"),
     {"z": "1.96"}, "p", ("0.05", "0.20")),
]


@pytest.mark.parametrize("case, rule_, fixed, checked, values", PAIRS, ids=[pair[0] for pair in PAIRS])
def test_each_rule_tells_fitting_numbers_from_numbers_that_cannot_all_be_true(case, rule_, fixed, checked, values):
    fitting, misfit = values
    good = run(rule_, records(**fixed, **{checked: fitting}))
    assert (good["status"], good["finding"]) == ("COMPATIBLE", None), case
    bad = run(rule_, records(**fixed, **{checked: misfit}))
    assert bad["status"] == "INCONSISTENT", case
    finding = bad["finding"]
    # A finding asks to clarify; it is never a verdict on the paper.
    assert (finding["kind"], finding["level"], finding["not_a_scientific_authenticity_verdict"]) == (
        "CONDITIONAL_NUMERIC_INCONSISTENCY", "CLARIFY", True)
    assert finding["input_ids"] == bad["input_ids"] == sorted(bad["input_ids"]) and checked in finding["input_ids"]
    assert run(rule_, records(**fixed, **{checked: misfit}))["finding"]["finding_id"] == finding["finding_id"]


def test_raw_values_are_summarized():
    result = run(rule("raw_summary", {"values": ["v1", "v2", "v3"]}, missing_policy="no_missing", weights="equal",
                      sd_denominator="sample"), records(v1="2.0", v2="4.0", v3="6.0"))
    assert result["status"] == "COMPATIBLE"
    assert (result["calculation"]["n"], result["calculation"]["mean"], result["calculation"]["sd"]) == (3, "4.0", "2.0")


def test_rules_that_cannot_be_checked_say_why():
    percentage = rule("percentage", {"count": "c", "percent": "p"}, unweighted_count=True, rounding="nearest")
    missing = run(percentage, records(c="12", p="30.0"))
    assert (missing["status"], missing["missing"]) == ("INSUFFICIENT_INFORMATION", ["inputs.denominator"])

    unknown = run(rule("median", {"values": ["v"]}), records(v="1"))
    assert (unknown["status"], unknown["reason"]) == ("NOT_APPLICABLE", "RULE_NOT_SUPPORTED")

    unbound = rule("cross", {"left": "l", "right": "r"}, same_quantity=True, rounding="nearest")
    del unbound["binding_source"]
    assert run(unbound, records(l="1", r="1"))["missing"] == ["binding_source"]

    # A bounded value ("<30.0") is not a number to compute with.
    bounded = run(rule("percentage", {"count": "c", "denominator": "d", "percent": "p"}, unweighted_count=True,
                       rounding="nearest"), records(c="12", d="40", p=("<", "30.0")))
    assert (bounded["status"], bounded["reason"]) == ("INPUT_ERROR", "SCALAR_REQUIRED")

    adjusted = run(rule("test_p", {"statistic": "z", "p": "p"}, distribution="normal", tail="two_sided",
                        adjustment="bonferroni", rounding="nearest"), records(z="1.96", p="0.05"))
    assert (adjusted["status"], adjusted["reason"]) == ("INPUT_ERROR", "ADJUSTED_P_METHOD_NOT_SUPPORTED")


def test_a_cancelled_review_stops_before_the_rule_runs():
    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        evaluate(rule("cross", {"left": "l", "right": "r"}, same_quantity=True, rounding="nearest"),
                 records(l="1", r="1"), checkpoint=cancelled)


@pytest.mark.parametrize("percent, status, color, findings", [("30.0", "COMPATIBLE", "GREEN", 0),
                                                              ("31.5", "INCONSISTENT", "YELLOW", 1)])
def test_a_text_is_read_and_checked_against_its_rules(percent, status, color, findings):
    text = f"Results\n\nOf the 40 participants, 12 ({percent}%) reported side effects.\n"
    share = {"rule_id": "share-with-side-effects", "type": "percentage", "binding_source": {"kind": "TEST"},
             "inputs": {"count": {"raw": "12", "quote": "12 ("}, "denominator": {"raw": "40", "quote": "Of the 40"},
                        "percent": {"raw": percent, "quote": f"{percent}%"}},
             "parameters": {"unweighted_count": True, "rounding": "nearest"}}
    report = analyze(text, source_id="sample", rules=[share])
    assert [record["raw"] for record in report["records"]] == ["40", "12", percent]
    assert [(check["rule_id"], check["status"]) for check in report["checks"]] == [("share-with-side-effects", status)]
    assert (report["status"], report["color"], len(report["findings"])) == ("COMPLETED", color, findings)
