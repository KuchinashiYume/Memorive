"""Daily, weekly and monthly reports as Markdown that a person reads from the top.

render_markdown(digest) writes a report from the change digest (digest.py) alone;
render_markdown_with_narrative(digest, narrative, metadata) adds the summary a model wrote, which
api_briefing.py has checked: every sentence names the records it rests on. Both fill
templates/report.md.tmpl and are @localized (zh-CN, en-US, ja-JP; the fixed text is translated in
memorive.platform.language.reports).

A report reads, in this order: the period and its record counts in one line; the model's summary, if
any, each sentence followed by the numbers of the records it rests on (［1］); what needs attention;
the progress of the period, one line per paper or conversation; the counts; and an appendix with every
record (number, time, text, link), whether the records are complete, what the report does not assess,
and where the report comes from. The digest decides what is in a report; this module only lays it out.

Dynamic text (paper names, model sentences) is escaped where Markdown would read it as markup, and
refused if it holds control characters or conclusion phrases (canonical.py), so a file name or a model
sentence cannot add links, images or headings, or claim a finding.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from memorive.platform.language.reports import localized, template as localized_template, tr
from memorive.research.reports.canonical import forbidden_phrase, require_safe_text
from memorive.research.reports.policy import ZONE

TEMPLATE = Path(__file__).resolve().parent / "templates" / "report.md.tmpl"
TOKEN_PATTERN = re.compile(r"{{([^{}]+)}}")
# Characters Markdown reads as markup inside a line (a value never holds a line break).
# What lets a value add something to the page: links and images ([ ] !), HTML (< >), code (`), table
# cells (|) and escapes (\). Underscores and asterisks only make italics; left as they are, file names
# stay readable where the report is shown as plain text.
MARKUP = frozenset("\\`[]<>!|")
# A value at the start of a line must not start a heading, a list or a quote.
LINE_START = re.compile(r"^(?:[#>*+-]|\d+[.)])")
NAMES = {"daily": "日报", "weekly": "周报", "monthly": "月报"}
SUMMARY_FROM = 4   # records in a progress section from which its summary sentence is shown
PROGRESS_HEADING = {"daily": "这一天的进展", "weekly": "这一周的进展", "monthly": "这个月的进展"}
LEAD = {"daily": "这一天的记录：{v0}。", "weekly": "这一周的记录：{v0}。", "monthly": "这个月的记录：{v0}。"}
NOTHING = {"daily": "这一天没有新的记录。", "weekly": "这一周没有新的记录。", "monthly": "这个月没有新的记录。"}
# The digest sections (digest.SECTION_TITLES) that hold what needs attention, and the progress.
ATTENTION = {"daily": ("失败", "待审"), "weekly": ("风险与待办", "未验证项"),
             "monthly": ("风险与未评估维度", "长期待办")}
PROGRESS = {"daily": ("新增", "修改"), "weekly": ("研究进展", "明确标记的重要变化", "明确来源的潜在张力"),
            "monthly": ("知识库结构变化", "明确记录的研究方向变化")}
NOT_ASSESSED = {
    "semantic_priority_quality": "- 哪些变化更重要：记录里没有重要性的标注，报告按时间排列，不排先后。",
    "potential_tensions": "- 文献之间的分歧：这一时段没有研究机会的记录。",
    "research_direction_changes": "- 研究方向的变化：这一时段没有记录方向决定。",
    "optional_source_coverage": "- 部分可选来源的记录没能读取，上面的数字可能不全。",
    "late_event_coverage": "- 有迟到的记录无法确认属于哪个时段。",
}


def _text(value: object, field: str) -> str:
    text = require_safe_text(str(value), field, allow_empty=True)
    hit = forbidden_phrase(text)
    if hit:
        raise ValueError(f"forbidden report phrase detected in {field}: {hit}")
    text = "".join("\\" + char if char in MARKUP else char for char in text)
    return "\\" + text if LINE_START.match(text) else text


def _local(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(ZONE)


def _when(stamp: str, kind: str) -> str:
    return _local(stamp).strftime("%H:%M" if kind == "daily" else "%m-%d %H:%M")


def _sections(digest: dict) -> dict[str, list[dict]]:
    return {section["title"]: section["items"] for section in digest["sections"]}


def _numbered(digest: dict) -> tuple[list[dict], dict[str, int]]:
    """Every record once, oldest first (the late ones too), and its number."""
    seen: dict[str, dict] = {}
    for section in digest["sections"]:
        for item in section["items"]:
            seen.setdefault(item["event_id"], item)
    for item in digest["late_arrivals"]:
        seen.setdefault(item["event_id"], dict(item, late=True))
    ordered = sorted(seen.values(), key=lambda item: (item["occurred_at"], item["event_id"]))
    return ordered, {item["event_id"]: number for number, item in enumerate(ordered, 1)}


def _refs(event_ids: list[str], numbers: dict[str, int]) -> str:
    """［1–3, 5］ for the records a sentence rests on."""
    values = sorted({numbers[event_id] for event_id in event_ids if event_id in numbers})
    if not values:
        return ""
    runs, start = [], values[0]
    for previous, value in zip(values, values[1:] + [None]):
        if value != previous + 1:
            runs.append(str(start) if start == previous else f"{start}–{previous}")
            start = value
    return "［" + ", ".join(runs) + "］"


def _item_line(item: dict, kind: str, numbers: dict[str, int]) -> str:
    return f"- {_when(item['occurred_at'], kind)} · {_text(item['text'], 'text')}{_refs([item['event_id']], numbers)}"


def _title(digest: dict) -> str:
    kind = digest["digest_kind"]
    start, end = _local(digest["window_start"]), _local(digest["window_end"]) - timedelta(seconds=1)
    span = {"daily": f"{start:%Y-%m-%d}", "weekly": f"{start:%Y-%m-%d} — {end:%Y-%m-%d}",
            "monthly": f"{start:%Y-%m}"}[kind]
    return f"{tr(NAMES[kind])} · {span}"


def _lead(digest: dict) -> str:
    kind = digest["digest_kind"]
    counts = [(section["title"], len(section["items"])) for section in digest["sections"] if section["items"]]
    if not counts:
        return tr(NOTHING[kind])
    return tr(LEAD[kind], tr("、").join(tr("{v0} {v1} 条", tr(title), count) for title, count in counts))


def _summary(narrative: dict | None, numbers: dict[str, int]) -> str:
    if not narrative:
        return ""
    return "\n\n".join(_text(row["text"], "summary_text") + _refs(row["event_ids"], numbers)
                       for row in narrative["period_summary"])


def _attention(digest: dict, narrative: dict | None, numbers: dict[str, int]) -> str:
    kind = digest["digest_kind"]
    lines, cited = [], set()
    for row in (narrative or {}).get("priority_items", []):
        lines.append("- " + _text(row["text"], "priority_text") + _refs(row["event_ids"], numbers))
        cited.update(row["event_ids"])
    for conflict in digest["conflicts"]:
        lines.append(tr("- {v0} 的内容互相冲突，报告没有替你选择，见附录“记录是否完整”。", _text(conflict["event_id"], "event_id")))
    for issue in digest["unparseable_events"]:
        if issue["blocking_status"] == "blocking":
            lines.append(tr("- 有记录无法解析（{v0}），报告可能不完整，见附录。", _text(issue["error_code"], "error_code")))
    sections = _sections(digest)
    shown = set()
    for title in ATTENTION[kind]:
        for item in sections.get(title, []):
            if item["event_id"] in cited or item["event_id"] in shown:
                continue
            shown.add(item["event_id"])
            lines.append(f"- {tr(title)}：{_when(item['occurred_at'], kind)} · {_text(item['text'], 'text')}"
                         f"{_refs([item['event_id']], numbers)}")
    return "\n".join(lines) or tr("- 没有需要马上处理的事。")


def _progress(digest: dict, narrative: dict | None, numbers: dict[str, int]) -> str:
    kind = digest["digest_kind"]
    sections = _sections(digest)
    summaries = {row["section_title"]: row for row in (narrative or {}).get("section_summaries", [])}
    blocks = []
    for title in PROGRESS[kind]:
        items = sections.get(title, [])
        if not items:
            continue
        block = [f"### {tr(title)}", ""]
        summary = summaries.get(title)
        # With a few records the lines below say it all; a summary sentence only repeats them.
        if summary and summary["text"] != "NONE" and len(items) >= SUMMARY_FROM:
            block += [_text(summary["text"], "section_summary") + _refs(summary["event_ids"], numbers), ""]
        block += [_item_line(item, kind, numbers) for item in items]
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) or tr("这一时段没有新的进展记录。")


def _numbers(digest: dict) -> str:
    rows = [tr("| 类别 | 数量 |"), "|---|---:|"]
    rows += [f"| {tr(section['title'])} | {len(section['items'])} |" for section in digest["sections"]]
    for label, values in (("| 迟到 | {v0} |", digest["late_arrivals"]), ("| 冲突 | {v0} |", digest["conflicts"]),
                          ("| 不可解析 | {v0} |", digest["unparseable_events"])):
        if values:
            rows.append(tr(label, len(values)))
    return "\n".join(rows)


def _records(ordered: list[dict], numbers: dict[str, int]) -> str:
    if not ordered:
        return tr("- 没有记录。")
    return "\n".join(
        tr("{v0}. {v1} · {v2}{v3} · {v4} · 编号 {v5}", numbers[item["event_id"]],
           _local(item["occurred_at"]).strftime("%Y-%m-%d %H:%M"), _text(item["text"], "text"),
           tr("（迟到）") if item.get("late") else "", _text(item["source_record_ref"], "source_record_ref"),
           _text(item["event_id"][:11], "event_id"))
        for item in ordered)


def _quality(digest: dict) -> str:
    late, conflicts, issues = digest["late_arrivals"], digest["conflicts"], digest["unparseable_events"]
    if digest["report_status"] == "complete" and not (late or conflicts or issues):
        return tr("记录完整：没有迟到、冲突或无法解析的记录。")
    lines = [] if digest["report_status"] == "complete" else [tr("记录不完整（{v0}）。", _text(digest["report_status"], "report_status"))]
    if late:
        lines.append(tr("- 迟到 {v0} 条：发生在这一时段，但在上一份报告截止后才记录，已列在记录清单里，标“迟到”。", len(late)))
    for conflict in conflicts:
        lines.append(tr("- {v0}：内容互相冲突，报告没有替你选择；来源 {v1}。", _text(conflict["event_id"], "event_id"),
                        "、".join(_text(ref, "source_record_ref") for ref in conflict["source_record_refs"])))
    for issue in issues:
        effect = tr("影响报告完整性") if issue["blocking_status"] == "blocking" else tr("不影响报告完整性")
        lines.append(tr("- {v0}：无法解析（{v1}），{v2}；来源 {v3}。", _text(issue["issue_id"], "issue_id"),
                        _text(issue["error_code"], "error_code"), effect, _text(issue["source_record_ref"], "source_record_ref")))
    return "\n".join(lines)


def _not_assessed(digest: dict) -> str:
    lines = [tr(NOT_ASSESSED[value["dimension"]]) if value["dimension"] in NOT_ASSESSED
             else f"- {_text(value['dimension'], 'dimension')}（{_text(value['reason_code'], 'reason_code')}）"
             for value in digest["not_assessed_dimensions"]]
    return "\n".join(lines) or tr("- 没有。")


def _about(digest: dict, ordered: list[dict], metadata: dict | None) -> str:
    lines = [
        tr("- 统计时段：{v0} 至 {v1}（{v2}，含开始、不含结束）", _local(digest["window_start"]).strftime("%Y-%m-%d %H:%M"),
           _local(digest["window_end"]).strftime("%Y-%m-%d %H:%M"), _text(digest["report_timezone"], "report_timezone")),
        tr("- 生成时间：{v0}", _local(digest["generated_at"]).strftime("%Y-%m-%d %H:%M") if digest.get("generated_at") else "—"),
        tr("- 记录 {v0} 条；来源清单校验值 {v1}", len(ordered), _text(digest["input_manifest_sha256"][:16], "input_manifest_sha256")),
        tr("- 摘要：由 {v0} 根据上面的记录撰写，每句后的编号是它依据的记录。", _text(metadata["model"], "model"))
        if metadata else tr("- 摘要：这份报告没有模型摘要。"),
        tr("- 报告编号：{v0}，第 {v1} 版", _text(digest["digest_id"], "digest_id"), digest["revision"])
        + (tr("，取代 {v0}", _text(digest["supersedes_digest_id"], "supersedes_digest_id")) if digest.get("supersedes_digest_id") else ""),
    ]
    return "\n".join(lines)


def _render(digest: dict, narrative: dict | None, metadata: dict | None) -> str:
    kind = digest["digest_kind"]
    if kind not in NAMES:
        raise ValueError(f"unsupported digest template kind: {kind}")
    ordered, numbers = _numbered(digest)
    values = {
        "title": _title(digest),
        "lead": _lead(digest),
        "summary": _summary(narrative, numbers),
        "attention": _attention(digest, narrative, numbers),
        "progress_heading": tr(PROGRESS_HEADING[kind]),
        "progress": _progress(digest, narrative, numbers),
        "numbers": _numbers(digest),
        "records": _records(ordered, numbers),
        "quality": _quality(digest),
        "not_assessed": _not_assessed(digest),
        "about": _about(digest, ordered, metadata),
    }
    page = localized_template(TEMPLATE.read_text(encoding="utf-8"))
    for match in TOKEN_PATTERN.finditer(page):
        if match.group(1) not in values:
            raise ValueError(f"unknown report template placeholder: {match.group(1)}")
    page = TOKEN_PATTERN.sub(lambda match: values[match.group(1)], page)
    hit = forbidden_phrase(page)
    if hit:
        raise ValueError(f"forbidden report phrase detected after normalized scan: {hit}")
    return re.sub(r"\n{3,}", "\n\n", page).rstrip() + "\n"


@localized
def render_markdown(digest: dict) -> str:
    """The report from the digest alone, without a model summary."""
    return _render(digest, None, None)


@localized
def render_markdown_with_narrative(digest: dict, narrative: dict, metadata: dict) -> str:
    """The report with the model summary api_briefing validated; metadata names the model."""
    return _render(digest, narrative, metadata)
