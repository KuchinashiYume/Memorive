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


@pytest.mark.parametrize("cell, expected", [
    ("2/5 (70.0%)", "INCONSISTENT"),
    ("2/5 (41%)", "INCONSISTENT"),
    ("1/1 (101%)", "INCONSISTENT"),
    ("0/5 (-1%)", "INCONSISTENT"),
    ("2/3 (66%)", "COMPATIBLE"),
    ("2/5 (40.0%)", "COMPATIBLE"),
    ("1/1 (100.0%)", "COMPATIBLE"),
    ("0/5 (0.0%)", "COMPATIBLE"),
    ("1/3 (33.3%)", "COMPATIBLE"),
    ("1/3 (34.0%)", "INCONSISTENT"),
])
def test_an_explicit_n_over_N_cell_uses_exact_counts(cell, expected):
    report = analyze(f"| Group | n/N (%) |\n| --- | --- |\n| A | {cell} |", source_id="sample")
    [check] = report["checks"]
    assert check["status"] == expected
    assert check["binding_source"]["header_line"] == 1
    assert check["binding_source"]["line"] == 3


@pytest.mark.parametrize("header, suffix", [
    ("n/N (weighted %)", ""),
    ("n/N (%)", "\n\nNote: percentages are survey-weighted."),
])
def test_survey_weighted_percentages_are_not_compared_to_raw_counts(header, suffix):
    report = analyze(f"| Group | {header} |\n| --- | --- |\n| A | 2/5 (70.0%) |{suffix}",
                     source_id="sample")
    assert report["findings"] == []
    assert not any(check["type"] == "percentage" for check in report["checks"])


def test_truncating_an_exact_percentage_excludes_the_next_display_step():
    binding = rule("percentage", {"count": "c", "denominator": "d", "percent": "p"},
                   unweighted_count=True, rounding="toward_zero")
    assert run(binding, records(c="3", d="4", p="74"))["status"] == "INCONSISTENT"
    assert run(binding, records(c="3", d="4", p="75"))["status"] == "COMPATIBLE"
    assert run(binding, records(c="2", d="3", p="66.6"))["status"] == "COMPATIBLE"


@pytest.mark.parametrize("mean, sd, n, expected, mismatches", [
    ("4.00", "2.00", "3", "COMPATIBLE", []),
    ("8.00", "2.00", "3", "INCONSISTENT", ["reported_mean"]),
    ("4.00", "1.63", "3", "INCONSISTENT", ["reported_sd"]),
    ("4.00", "2.00", "4", "INCONSISTENT", ["reported_n"]),
])
def test_bound_summary_is_compared_to_the_exact_complete_sample(mean, sd, n, expected, mismatches):
    binding = rule("raw_summary", {"values": ["v1", "v2", "v3"], "reported_mean": "m",
                                   "reported_sd": "s", "reported_n": "n"},
                   missing_policy="no_missing", weights="equal", sd_denominator="sample",
                   exact_complete_sample=True, rounding="nearest")
    result = run(binding, records(v1="2", v2="4", v3="6", m=mean, s=sd, n=n))
    assert result["status"] == expected
    assert result["calculation"]["mismatched_reported_fields"] == mismatches


def test_summary_does_not_compare_a_report_to_a_partial_or_rounded_sample():
    binding = rule("raw_summary", {"values": ["a", "b"], "reported_mean": "m"},
                   missing_policy="no_missing", weights="equal", sd_denominator="sample", rounding="nearest")
    result = run(binding, records(a="2", b="4", m="6"))
    assert (result["status"], result["missing"]) == (
        "INSUFFICIENT_INFORMATION", ["parameters.exact_complete_sample"])
    assert result["finding"] is None


def test_comparing_a_population_sd_preserves_distinct_equal_observations():
    binding = rule("raw_summary", {"values": ["a", "b", "c"], "reported_sd": "s"},
                   missing_policy="no_missing", weights="equal", sd_denominator="population",
                   exact_complete_sample=True, rounding="nearest")
    result = run(binding, records(a="0", b="0", c="3", s="1.41"))
    assert (result["status"], result["calculation"]["n"]) == ("COMPATIBLE", 3)
    binding["inputs"]["values"] = ["a", "a", "c"]
    result = run(binding, records(a="0", c="3", s="1.41"))
    assert result["status"] == "INPUT_ERROR" and result["finding"] is None


@pytest.mark.parametrize("lower, upper, p, expected", [
    ("0.20", "0.90", "0.01", "COMPATIBLE"),
    ("0.20", "0.90", "0.20", "INCONSISTENT"),
    ("-0.20", "0.90", "0.20", "COMPATIBLE"),
    ("-0.20", "0.90", "0.01", "INCONSISTENT"),
    ("0.00", "0.90", "0.20", "INSUFFICIENT_INFORMATION"),
    ("0.20", "0.90", "0.05", "INSUFFICIENT_INFORMATION"),
])
def test_matching_two_sided_test_and_ci_agree_on_whether_they_exclude_the_null(lower, upper, p, expected):
    binding = rule("ci_p", {"lower": "lo", "upper": "hi", "p": "p"}, alpha="0.05",
                   null_value="0", relationship="same_two_sided_test_inversion", rounding="nearest")
    result = run(binding, records(lo=lower, hi=upper, p=p))
    assert result["status"] == expected
    assert bool(result["finding"]) == (expected == "INCONSISTENT")


def test_ci_p_can_compare_a_ratio_to_one_and_use_strict_p_bounds():
    binding = rule("ci_p", {"lower": "lo", "upper": "hi", "p": "p"}, alpha="0.05",
                   null_value="1", relationship="same_two_sided_test_inversion", rounding="nearest")
    result = run(binding, records(lo="1.20", hi="1.90", p=("<", "0.05")))
    assert result["status"] == "COMPATIBLE"
    binding["parameters"]["relationship"] = "different_model_or_method"
    result = run(binding, records(lo="1.20", hi="1.90", p="0.20"))
    assert result["status"] == "NOT_APPLICABLE" and result["finding"] is None


def test_an_explicit_weighting_note_can_qualify_a_separate_count_table():
    text = ("| Unweighted count | Denominator | Percent (nearest) |\n| --- | --- | --- |\n"
            "| 2 | 5 | 70.0 |\n\nNote: Percentages were weighted.")
    assert analyze(text, source_id="sample")["findings"] == []


def test_fullwidth_percentages_in_source_preserve_the_reported_scale():
    text = "| 人数 | 计数/总数（％） |\n| --- | --- |\n| 甲 | 2/5（40.0％） |"
    report = analyze(text, source_id="sample")
    assert [c["status"] for c in report["checks"]] == ["COMPATIBLE"]
    assert report["records"][-1]["unit_literal"] == "％"


def test_duplicate_sum_binding_is_an_input_issue_but_distinct_equal_parts_are_valid():
    binding = rule("sum", {"parts": ["a", "b"], "total": "t"},
                   mutually_exclusive=True, exhaustive=True, exact_parts=True, rounding="nearest")
    assert run(binding, records(a="20", b="20", t="40"))["status"] == "COMPATIBLE"
    binding["inputs"]["parts"] = ["a", "a"]
    result = run(binding, records(a="20", t="30"))
    assert (result["status"], result["reason"], result["finding"]) == ("INPUT_ERROR", "DUPLICATE_PART_RECORD", None)


@pytest.mark.parametrize("distribution, tail, statistic, probability, df", [
    ("normal", "two_sided", "-20", "5.5072e-89", {}),
    ("normal", "lower", "-20", "2.7536e-89", {}),
    ("t", "lower", "-1", "0.25", {"df": "1"}),  # Student t_1 is Cauchy.
    ("t", "upper", "2", "0.0306", {"df": "17.6"}),  # A Welch estimate may be non-integer.
    ("chi_square", "lower", "2", "0.63212", {"df": "2"}),  # CDF = 1 - exp(-1).
    ("f", "upper", "3", "0.25", {"df1": "2", "df2": "2"}),  # Survival = 1/(1+x).
])
def test_p_rule_handles_signed_tails_fractional_df_and_very_small_p(distribution, tail, statistic, probability, df):
    binding = rule("test_p", {"statistic": "x", "p": "p"}, distribution=distribution, tail=tail,
                   adjustment="none", rounding="nearest", exact_inputs=["statistic"], **df)
    result = run(binding, records(x=statistic, p=probability))
    assert (result["status"], result["approximate"]) == ("COMPATIBLE", True), result
    assert run(binding, records(x=statistic, p="1e-40"))["status"] == "INCONSISTENT"


TABLE = "| Group | n/N (%) |\n| --- | --- |\n| A | 2/5 (70.0%) |"


@pytest.mark.parametrize("before, after, checked", [
    ("", "", True),
    ("Table 1. Percentages are survey-weighted.\n\n", "", False),
    ("", "\n\nNote: percentages are survey-weighted.", False),
    ("Supplementary Table S2. Percentages are survey-weighted.\n\n", "", False),
    ("Table 1. Counts.\n\n", "\n\nNote: Percentages are unweighted in Table 1; percentages are weighted in Table 2.", True),
    ("Table 1. Counts.\n\n", "\n\nFor Table 2, percentages were unweighted; for Table 1, percentages were weighted.", False),
    # a statement about another table, or the next table's own caption, leaves this table checked
    ("Table 1. Survey responses.\n\n", "\n\nIn Table 2, percentages are survey-weighted.", True),
    ("Table 1. Survey responses.\n\n", "\n\nIn Table 1, percentages are survey-weighted.", False),
    ("Table 1. Survey responses.\n\n", "\n\nTable 2. Percentages are survey-weighted.", True),
])
def test_a_weighting_statement_applies_to_its_own_table(before, after, checked):
    report = analyze(before + TABLE + after, source_id="sample")
    assert [c["status"] for c in report["checks"] if c["type"] == "percentage"] == (["INCONSISTENT"] if checked else [])


def test_equal_numbers_on_one_line_are_told_apart_by_their_quotes():
    summary = rule("raw_summary", {"values": [{"raw": "2", "quote": "A=2"}, {"raw": "2", "quote": "B=2"},
                                              {"raw": "5", "quote": "C=5"}],
                                   "reported_mean": {"raw": "3", "quote": "Mean=3"}},
                   missing_policy="no_missing", weights="equal", sd_denominator="sample",
                   exact_complete_sample=True, rounding="nearest")
    check = lambda: next(c for c in analyze("A=2; B=2; C=5\nMean=3", source_id="sample", rules=[summary])["checks"]
                         if c["rule_id"] == "rule-raw_summary")
    assert (check()["status"], check()["calculation"]["n"]) == ("COMPATIBLE", 3)
    # a quote holding both equal numbers names neither of them
    summary["inputs"]["values"][0]["quote"] = "A=2; B=2"
    assert (check()["status"], check()["reason"]) == ("INPUT_ERROR", "INPUT_SELECTOR_NOT_UNIQUE_OR_NOT_FOUND")


@pytest.mark.parametrize("p, rounding, expected", [
    ("0.04", "toward_zero", "INCONSISTENT"),  # p in [0.04, 0.05) is below .05, while the CI holds 0
    ("0.05", "toward_zero", "COMPATIBLE"),  # p in [0.05, 0.06)
    ("0.05", "nearest", "INSUFFICIENT_INFORMATION"),  # p in [0.045, 0.055] may fall on either side
])
def test_a_truncated_p_never_reaches_its_next_display_step(p, rounding, expected):
    binding = rule("ci_p", {"lower": "lo", "upper": "hi", "p": "p"}, alpha="0.05", null_value="0",
                   relationship="same_two_sided_test_inversion", rounding=rounding)
    assert run(binding, records(lo="-0.20", hi="0.90", p=p))["status"] == expected


def test_a_count_alone_needs_no_weights_or_sd_definition():
    binding = rule("raw_summary", {"values": ["a", "b", "c"], "reported_n": "n"},
                   missing_policy="no_missing", exact_complete_sample=True)
    result = run(binding, records(a="4", b="5", c="6", n="2"))
    assert (result["status"], result["calculation"]["mismatched_reported_fields"]) == ("INCONSISTENT", ["reported_n"])
    assert result["calculation"]["mean"] is None
    binding["inputs"]["reported_mean"] = "m"
    assert run(binding, records(a="4", b="5", c="6", n="3", m="5"))["missing"] == [
        "parameters.weights", "parameters.rounding"]
    binding["parameters"].update(weights="equal", rounding="nearest")
    assert run(binding, records(a="4", b="5", c="6", n="3", m="5"))["status"] == "COMPATIBLE"


@pytest.mark.parametrize("value, quote, expected", [
    ("12", "B=8", "INPUT_ERROR"), ("12", "A=1", "INPUT_ERROR"),
    ("12", "A=12", "COMPATIBLE"), ("12", "group A", "COMPATIBLE"),
])
def test_numeric_quote_binds_its_own_value(value, quote, expected):
    text = "group A=12; B=8\nMean=12"
    binding = rule("raw_summary", {"values": [{"raw": value, "quote": quote}],
                                   "reported_mean": {"raw": "12", "quote": "Mean=12"}},
                   missing_policy="no_missing", weights="equal", exact_complete_sample=True, rounding="nearest")
    assert analyze(text, source_id="paper", rules=[binding])["checks"][0]["status"] == expected


def test_count_only_includes_censored_observations_without_treating_bounds_as_measurements():
    binding = rule("raw_summary", {"values": ["a", "b"], "reported_n": "n"},
                   missing_policy="no_missing", exact_complete_sample=True)
    data = records(a=("<", "2"), b="8", n="2", m="5")
    check = run(binding, data)
    assert check["status"] == "COMPATIBLE"
    assert check["calculation"]["mean"] is None
    binding["inputs"]["reported_mean"] = "m"
    binding["parameters"].update(weights="equal", rounding="nearest")
    assert run(binding, data)["reason"] == "RAW_CENSORED_VALUES_NOT_SUPPORTED"
