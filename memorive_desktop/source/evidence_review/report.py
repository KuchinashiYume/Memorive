"""EVIDENCE_REVIEW 校核报告(Verification 第 2 步)——**运行产物、库外运维区、默认不入库、不进检索**。

KNOWLEDGE_ADMISSION_log 只留 `report=<report_id>` 链过去(不展开 Problem[]);完整详情(Problem[] + 同源置信 + 升级异源待办)
在本报告。report_id 让 KNOWLEDGE_ADMISSION_log 的 report=<id> **可反查**(resolve_report)。
落点:`{OPS_ROOT}/EvidenceReview校核报告/{report_id}.json`(默认与 KNOWLEDGE_ADMISSION_log/RUNTIME_LOG 同运维区;env MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR 覆盖)。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_EVIDENCE_REVIEW_REPORT_DIR = r"G:\Memorive-运维\EvidenceReview校核报告"   # 库外运维区(与 KNOWLEDGE_ADMISSION_log / RUNTIME_LOG 日志、ServiceContracts 向量库同区)
ENV_EVIDENCE_REVIEW_REPORT_DIR = "MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR"             # 覆盖默认落点(便携 / 测试)

# 置信度(承决策②(a) / 防同源自检)
CONFIDENCE_SAME_SOURCE = "same_source_low"           # 校核模型与生成该卡的 distill_model 同供应商 / 家族
CONFIDENCE_CROSS_SOURCE = "cross_source"             # 异源(verify 槽 = Anthropic Sonnet 5 细校;deepseek 蒸馏 × anthropic 校核 → 供应商错开)


def _report_dir() -> Path:
    return Path(os.environ.get(ENV_EVIDENCE_REVIEW_REPORT_DIR) or DEFAULT_EVIDENCE_REVIEW_REPORT_DIR)


def _now_iso() -> str:
    """本地带时区、秒级——对齐 KNOWLEDGE_ADMISSION_log ts / RUNTIME_LOG _now_iso(项目一贯 +08:00)。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(s) -> str:
    """filename-safe 化(守卫①):只留字母 / 数字 / 下划线 / 短横线,其余 → _。防 paper_id 里的
    路径分隔符 / 空格 / 冒号等污染 report_id(进而污染落盘路径)。"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s)) or "unknown"


def gen_report_id(paper_id: str) -> str:
    """{safe(paper_id)}-EVIDENCE_REVIEW-{YYYYMMDDTHHMMSS}-{shorthex}。shorthex 防撞:连错重跑同秒不撞(守卫①)。"""
    ts = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    return f"{_safe(paper_id)}-EVIDENCE_REVIEW-{ts}-{uuid.uuid4().hex[:8]}"


@dataclass
class EvidenceReviewReport:
    """一次搬运类校核的完整报告(⑥ 写全)。problems 为 Problem 列表(asdict 递归序列化)。"""
    report_id: str
    paper_id: str
    card: str                          # 卡路径(字符串)
    verdict: str                       # "PASS" / "FAIL"
    problems: list                     # list[Problem]
    confidence: str                    # same_source_low / cross_source
    same_source_reason: str | None     # 同源理由(同源时填,如 "distill=deepseek / verify=deepseek、flash-pro 同家族")
    upgrade_pending: list              # 核心字段升级异源待办(如 ["key_data 数值", "author_conclusion"])
    distill_model: str | None          # 卡的 distill_model(顶层 frontmatter 键)
    verify_model: str | None           # 本次校核所用模型(verify 槽)
    checked_at: str                    # 本地带时区 ISO
    checked_count: int = 0             # 真发 prompt、真回原文核过的 subject 数(全局守卫:0=无核成,不得 admit)
    checked_locations: list = field(default_factory=list)  # 真核过的 subject.location 名单(per-entry 守卫:未在此名单的字段/key_data → unknown 非 verified)


def report_path(report_id: str) -> Path:
    return _report_dir() / f"{report_id}.json"


def write_report(report: EvidenceReviewReport) -> str:
    """落报告 json,返回 report_id。守卫③:临时文件 + os.replace **原子写入**(防半截 JSON)。"""
    d = _report_dir()
    d.mkdir(parents=True, exist_ok=True)
    final = report_path(report.report_id)
    tmp = d / f".{report.report_id}.json.tmp"
    tmp.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, final)                      # 同盘原子替换:要么旧无、要么整份新,绝不半截
    return report.report_id


def resolve_report(report_id: str) -> Path:
    """反查 report=<id> → 报告文件 Path。守卫②:拒绝 / \\ ..,且 resolve 后必须仍在报告目录内(防路径穿越)。"""
    if not isinstance(report_id, str) or not report_id.strip():
        raise ValueError(f"非法 report_id(空 / 非字符串): {report_id!r}")
    if "/" in report_id or "\\" in report_id or ".." in report_id:
        raise ValueError(f"非法 report_id(含 / \\ .. — 防路径穿越): {report_id!r}")
    base = _report_dir().resolve()
    p = (base / f"{report_id}.json").resolve()
    if not p.is_relative_to(base):              # resolve 后越出报告目录 = 路径穿越
        raise ValueError(f"report_id 解析越出报告目录(路径穿越): {report_id!r}")
    if not p.exists():
        raise FileNotFoundError(f"EVIDENCE_REVIEW 校核报告未找到: {report_id}({p})")
    return p


def read_report(report_id: str) -> dict:
    """读回报告 dict(校核视图)。"""
    return json.loads(resolve_report(report_id).read_text(encoding="utf-8"))
