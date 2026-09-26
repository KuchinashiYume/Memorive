"""EVIDENCE_EXTRACTION 卡片装配 + 落盘。

**模型 JSON 只是中间产物;本模块把校验后的字段转成严格符合 D1 card_schema(v4)的 [Card] frontmatter**
(承约束:不得把 {value,anchors} 私自当卡片格式落盘)。
范围:核心 7 字段 + 每字段 by_field 锚点(**v4 第二刀:per-锚点对象列表 [{chunk_id, quote, section_page}]**,高风险字段
挂逐字 quote、综合字段 quote 可 null)+ 机制字段(Step 1);key_data 关键数据块(Step 2:每条
{value,unit,metric,sample,stat,quote,chunk_id,needs_review}、只挂 chunk_id 不落盘 section_page;**v3 第一刀起每条挂逐字原文
quote**、蒸馏当场 substring 验真、不过整卡 fail-closed,承之八);比较上下文/局限展望/术语引用 三附属留 Step 3。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from .config import CARD_SCHEMA_VERSION
from .errors import CardExistsError

# ── 分级信任信用档(v5·上半;承 v6.4 之四)──────────────────────────────
# 每字段 / 每 key_data 一个:EVIDENCE_REVIEW 无 flag=verified / EVIDENCE_REVIEW 疑锚点归位=anchor_uncertain / EVIDENCE_REVIEW 真违规=flagged;
# EVIDENCE_EXTRACTION 建卡时未跑 EVIDENCE_REVIEW=unknown。旧 v1–v4 卡缺 field_credibility → 读端当 unknown(read_field_credibility 兜底)。
CRED_VERIFIED = "verified"
CRED_ANCHOR_UNCERTAIN = "anchor_uncertain"
CRED_FLAGGED = "flagged"
CRED_UNKNOWN = "unknown"
CREDIBILITIES = frozenset({CRED_VERIFIED, CRED_ANCHOR_UNCERTAIN, CRED_FLAGGED, CRED_UNKNOWN})
# 未核默认危险标(承外审第三轮 [中·跨路径]):**从建卡起就带**——任何 admit 路径(grade_and_admit 之外的
# verify_and_drive / manual_approve / placeholder)都不产出「unknown 却无标」进 active。verified 才 danger=None。
# grading._mark 的 unknown 分支复用这两个常量(建卡时标 = 落档时标,逐字一致)。
DANGER_UNKNOWN_KD = "⚠ 数值 EVIDENCE_REVIEW 未核(未收为异源核对象 / 锚点缺失 / 缺 chunk)·勿直接引·须核原文"
DANGER_UNKNOWN_FIELD = "⚠ 该字段 EVIDENCE_REVIEW 未核(未收为异源核对象 / 锚点缺失 / 缺 chunk)·勿直接引·须核原文"
_FENCE_RE = re.compile(r"(?m)^---[ \t]*\r?$")   # CRLF 安全 frontmatter fence(与 knowledge_admission/card_io 同款)

# 6 个核心内容字段(第 7 字段 source_anchor 是贯穿性锚点,单独装配)。
CONTENT_FIELDS = ["research_question", "research_object", "method",
                  "key_results", "author_conclusion", "boundary_conditions"]
# 列表型 vs 标量型(承 D1 第一节类型):列表=字符串列表,其余=字符串。
LIST_FIELDS = {"research_object", "method", "boundary_conditions"}
FINAL_COMPLETION_STATUSES = frozenset({"complete", "passed_with_omissions"})

# ── 卡片主语言粗判(仅用于系统标签本地化:section_page 缺省 / 正文注释;绝不碰字段内容)──
# 承 v6.3 之七「数据层不残留异语」:Prompt v4 让 7 字段随原文语言,但少数**系统生成**、Prompt
# 覆盖不到的死角(缺省占位、正文 HTML 注释、model 兜底)会把中文漏进英文卡,须跟随卡片语言。
_HAN_RE = re.compile(r"[一-鿿]")        # 汉字
_KANA_RE = re.compile(r"[぀-ヿ]")       # 日文假名(平/片)


def detect_card_lang(field_values) -> str:
    """粗判卡片主语言 → 'zh' | 'en',仅决定系统标签语言、**不改任何字段内容**。
    判据:含假名 → 'en'(日文等走中性英文,绝不给非中文卡错挂中文);否则汉字占比 ≥ 20% → 'zh';
    其余(英文为主、偶发汉字被占比阈值挡)→ 'en'。→ **英文卡永不误挂中文标签**。"""
    parts = []
    for v in field_values:
        parts.extend(v if isinstance(v, list) else [v])
    s = "".join(str(x) for x in parts)
    if not s or _KANA_RE.search(s):
        return "en"
    return "zh" if len(_HAN_RE.findall(s)) / len(s) >= 0.20 else "en"


# ── key_data 结构化模板标签(系统脚手架:随 card_lang 由系统定,承之七英文卡永不挂中文标签)──
# sample 四槽 = R4 作用域落点;stat 三槽 = R3 统计语义落点。**标签由系统定、内容随原文语言**。
# 模型只填内容 + 逐字照用注入的标签;validator 按 card_lang 认对应标签集(见 distill._validate_key_data)。
KEY_DATA_SAMPLE_SLOTS = ("group", "phase_time", "subprocess", "comparator")
KEY_DATA_STAT_SLOTS = ("stat_type", "n", "significance")
_KEY_DATA_LABELS = {
    "zh": {"group": "样本/对照组", "phase_time": "阶段·时段", "subprocess": "子过程",
           "comparator": "对照", "stat_type": "统计类型", "n": "n", "significance": "显著性",
           "not_given": "未给"},
    "en": {"group": "sample/control", "phase_time": "phase/time", "subprocess": "subprocess",
           "comparator": "comparator", "stat_type": "stat_type", "n": "n",
           "significance": "significance", "not_given": "Not given"},
}


def key_data_labels(lang: str) -> dict:
    """按卡片语言取 key_data 模板标签集(承之七:系统标签由系统定、不混语;非 zh 一律走中性英文)。"""
    return _KEY_DATA_LABELS.get(lang, _KEY_DATA_LABELS["en"])


# 正文区占位注释(随卡片语言;承之七:系统标签不残留异语)。系统生成、非文献内容。
_BODY_COMMENT = {
    "zh": ("<!-- 正文区:纯人读忠实提要(结构化字段 + key_data + 三附属[比较上下文/局限展望/术语引用] 均在 frontmatter;"
           "三附属 Core 只抽存不投入使用)。个人分析归 KNOWLEDGE_FEEDBACK、不入卡。 -->\n"),
    "en": ("<!-- Body: human-readable faithful summary (structured fields + key_data + 3 auxiliaries "
           "[comparison / limitations-outlook / terms-citations] all in frontmatter; auxiliaries stored-only in Core). "
           "Personal analysis goes to KNOWLEDGE_FEEDBACK, not the card. -->\n"),
}


class _CardDumper(yaml.SafeDumper):
    """长文本(含换行)用 `|` 块标量落盘(承 D1「长文本默认进 frontmatter 的 | 多行块」)。"""


def _str_representer(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_CardDumper.add_representer(str, _str_representer)


def build_card(*, paper_id: str, title: str, data_ownership: str,
               fields: dict, by_field: dict, distill_model: str, distilled_at: str,
               schema_version: int, key_data: list | None = None,
               aux: dict | None = None) -> dict:
    """把校验后的字段组装成严格 D1 序的 frontmatter dict。
    fields: {字段: value};by_field: {字段: [{chunk_id, quote, section_page}, …]}(v4 第二刀 per-锚点;quote 可 null);
    key_data: 已校验的关键数据条目列表(每条 {value,unit,metric,sample,stat,quote,chunk_id,needs_review});
      附属块·必含(承 card_schema §三①);**只挂 chunk_id、不落盘 section_page**(页码由 chunk_id 反查、
      仅渲染/日志/校核视图派生显示,非 schema 变更);**quote 为逐字原文**(v3 第一刀,蒸馏当场过 substring 验真、承之八)。
    aux: 三附属抽取结果(Configuration 步3)——**落盘三态**:
      · aux=dict(成功)→ 四块按 card_schema §三②③④ 落(某块空列表也落);
      · aux=None(整体失败)→ 四块**全不落**(不带 aux 的 pending 卡,承决策:低风险不拖垮核心+key_data)。
      (RUNTIME_LOG 另有 aux_call/aux_finished/aux_failed 区分「未运行 / 失败 / 成功但空」。)"""
    chinese = detect_card_lang(fields.get(f) for f in CONTENT_FIELDS) == "zh"
    unknown_number = DANGER_UNKNOWN_KD if chinese else "Unverified value; check the source before citing."
    unknown_field = DANGER_UNKNOWN_FIELD if chinese else "Unverified field; check the source before citing."
    fm: dict = {"title": title}
    for f in CONTENT_FIELDS:                       # 6 内容字段(D1 类型)
        fm[f] = fields[f]
    fm["source_anchor"] = {                        # 第 7 字段:贯穿性锚点(v4 by_field:per-锚点对象列表)
        "paper_id": paper_id,
        "section_page": None,                      # 旧版整卡级锚点:v2 起不作 Configuration 主锚点、留 v1 兼容
        "chunk_id": None,
        # v4(source_anchor v3 第二刀):每字段 = 锚点对象列表 [{chunk_id, quote, section_page}, …](quote 可 null)
        "by_field": {f: [dict(a) for a in by_field[f]] for f in CONTENT_FIELDS},
    }
    # 附属块·必含(空列表合法:纯定性论文;承决策③校验在 distill)。
    # v5 分级信任:每条加信用档位(EVIDENCE_EXTRACTION 建卡未跑 EVIDENCE_REVIEW → unknown + **⚠未核标**;grade_and_admit 落 verified〔无标〕/anchor/flagged〔各带标〕)。
    fm["key_data"] = [{**dict(e), "credibility": CRED_UNKNOWN, "credibility_note": None, "danger": unknown_number}
                      for e in (key_data or [])]
    if aux is not None:                            # 三附属·仅存储/可选:aux 成功才落(四块;空列表也落);失败→四块全不落(三态)
        fm["comparison_context"] = list(aux.get("comparison_context") or [])
        fm["author_limitations_outlook"] = (aux.get("author_limitations_outlook")
                                            or {"limitations": [], "outlook": []})
        fm["terms"] = list(aux.get("terms") or [])
        fm["citations"] = list(aux.get("citations") or [])
    # v5 分级信任·上半:顶层 per-字段信用档(6 核心字段;EVIDENCE_EXTRACTION 建卡 unknown + **⚠未核标**、EVIDENCE_REVIEW grade_and_admit 落档)。
    fm["field_credibility"] = {f: {"credibility": CRED_UNKNOWN, "note": None, "danger": unknown_field}
                               for f in CONTENT_FIELDS}
    fm["completion_status"] = "pending_review"
    fm["omissions"] = []
    fm["review_status"] = "pending"                # 前向依赖过渡:不放行 Active(KNOWLEDGE_ADMISSION 待 Intake)
    fm["is_derived"] = False                       # 文献卡恒 false;衍生条目归 KNOWLEDGE_FEEDBACK
    fm["data_ownership"] = data_ownership          # 承 DocumentIngest/D0
    fm["schema_version"] = schema_version
    fm["distill_model"] = distill_model            # 留痕:MODEL_GATEWAY 回显实际档
    fm["distilled_at"] = distilled_at              # 留痕:带时区到分钟,只增不改随重做另起
    return fm


def write_card(card_path: Path, fm: dict, *, overwrite: bool = False) -> Path:
    """落 [Card].md:frontmatter(严格序、长文本 | 块)+ 空正文占位(占位注释随卡片语言,承之七)。
    **默认不覆盖**:目标已存在且 not overwrite → `CardExistsError`(承 ARTIFACT_REGISTRY 数据永不覆盖;要覆盖须显式 overwrite=True)。"""
    if card_path.exists() and not overwrite:
        raise CardExistsError(f"[Card] 已存在、默认不覆盖: {card_path}(重蒸馏要覆盖须显式 overwrite=True)。")
    front = yaml.dump(fm, Dumper=_CardDumper, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, width=10_000)
    lang = detect_card_lang([fm.get(f) for f in CONTENT_FIELDS])   # 占位注释随卡片语言(之七:系统标签不残留异语)
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
        from .segmented_distill.publish import publish_bytes_no_clobber
        try:
            publish_bytes_no_clobber(card_path, body.encode("utf-8"))
        except FileExistsError as exc:
            raise CardExistsError(f"[Card] concurrently created: {card_path}") from exc
    return card_path


def read_field_credibility(fm: dict) -> dict:
    """读端兼容:取顶层 field_credibility;旧 v1–v4 卡缺该键 → 6 核心字段全 `unknown`(不臆断已校验、也不报错)。
    承 v5 分级信任「旧卡缺 credibility 当 unknown」。"""
    fc = fm.get("field_credibility")
    if isinstance(fc, dict):
        return fc
    return {f: {"credibility": CRED_UNKNOWN, "note": None, "danger": unknown_field} for f in CONTENT_FIELDS}


def apply_field_credibility(card_path: Path, field_cred: dict, key_data_cred: list) -> Path:
    """把 EVIDENCE_REVIEW 分级信任档写进**已有卡**(grade_and_admit 调):更新顶层 field_credibility + 每条 key_data 的
    credibility/credibility_note/danger;**其余 frontmatter 与正文逐字保留**(读 yaml 改档再同款 _CardDumper 重转、
    review_status 不动、由 KNOWLEDGE_ADMISSION 另行转移);经 tmp+os.replace **原子写**。CRLF 安全。

    field_cred:{字段: {credibility, note}}(6 核心字段);key_data_cred:与 key_data 同序 [{credibility, note, danger}]。
    """
    text = card_path.read_bytes().decode("utf-8")
    fences = list(_FENCE_RE.finditer(text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise ValueError(f"卡片 frontmatter 非法(需独占一行 --- 开头 + 闭合): {card_path}")
    front = text[fences[0].end():fences[1].start()]
    body_from_2nd_fence = text[fences[1].start():]        # "---\n\n<正文…>" 逐字保留
    fm = yaml.safe_load(front)
    if not isinstance(fm, dict):
        raise ValueError(f"卡片 frontmatter 解析非 dict: {card_path}")
    fm["field_credibility"] = field_cred                 # 覆盖(EVIDENCE_EXTRACTION 建卡的 unknown → EVIDENCE_REVIEW 落档)
    kd = fm.get("key_data")
    if isinstance(kd, list):
        for i, e in enumerate(kd):
            if not isinstance(e, dict):
                continue
            if i < len(key_data_cred):                 # 正常:按 index 落 grade 档(verified 才 danger=None)
                c = key_data_cred[i]
                e["credibility"] = c.get("credibility", CRED_UNKNOWN)
                e["credibility_note"] = c.get("note")
                e["danger"] = c.get("danger")
            else:                                      # 越界兜底(不该发生;承外审 [低]:绝不留 unknown 无标)→ unknown + ⚠
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
        os.replace(tmp, card_path)                       # 同盘原子替换:要么旧、要么整份新
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
