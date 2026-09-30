"""Budget-aware selection of intact, already-authorized research excerpts.

Section, outcome and qualification signals are lexical hints, never judgments
of scientific relevance, contradiction, independence or sufficiency. No model,
translation, source expansion, text rewriting or citation repair happens here.
"""
import collections
import math
import re
import unicodedata
from .answer_coverage import document_key, pack_order
from .evidence import tokens

REVISION = 'source-result-constraint-design-budget-v3'
HEADING = re.compile(
    r'^\s*(?:#{1,6}\s*)?(?:\d+(?:\.\d+)*\.?\s*)?'
    r'(results?(?:\s+and\s+discussion)?|discussion|conclusions?|'
    r'materials?\s+and\s+methods?|methods|introduction|abstract|'
    r'references|acknowledg(?:e)?ments?|author contributions|'
    r'结果(?:与讨论)?|讨论|结论|材料与方法|方法|引言|摘要|参考文献)\s*:?\s*$', re.I)
OUTCOMES = re.compile(r'\b(?:observ\w*|yield\w*|increas\w*|decreas\w*|improv\w*|'
    r'reduc\w*|enhanc\w*|produc\w*|recover\w*|higher|lower|difference\w*)\b|'
    r'观察|提高|增加|降低|减少|改善|产量|差异', re.I)
QUANTITY = re.compile(r'\d[\d.,]*\s*(?:%|±)|±\s*\d|\bp\s*[<=>]\s*0?[.]\d|百分之', re.I)
COMPARATORS = re.compile(r'\b(?:compared\s+(?:with|to)|relative\s+to|versus|'
    r'in\s+contrast|controls?|absence\s+of|without)\b|相对|对照|相比', re.I)
QUALIFICATION = re.compile(r'\b(?:no\s+(?:statistically\s+)?significant|'
    r'not\s+(?:statistically\s+)?significant|non[- ]significant|'
    r'did\s+not|cannot|could\s+not|uncertain\w*|limitations?|'
    r'not\s+(?:prove|establish|demonstrate)|limited\s+(?:to|by)|'
    r'no\s+difference)\b|不显著|未达显著|未能|不能证明|不确定|局限|仅限', re.I)
GENERIC_CONTRAST = re.compile(r'\b(?:however|except|contrary|footnote)\b|然而|但是|脚注', re.I)
TITLE_STOPWORDS = frozenset('a an the of in on at to for from with within and or by as is are study studies'.split())
METHOD_QUESTION = re.compile(r'\b(?:methods?|protocols?|procedures?|sampling|sample size|'
    r'dos(?:e|age)|temperature|experimental design)\b|方法|步骤|实验设计|样本量|重复数|剂量|温度|手順',re.I)
OUTCOME_QUESTION = re.compile(r'\b(?:results?|outcomes?|effects?|yields?|conclusions?|'
    r'increase|decrease|improvement)\b|结果|效果|效应|产量|速率|结论|提高|提升|降低|增加|减少',re.I)
DESIGN_CONTEXT = tuple(re.compile(pattern,re.I) for pattern in (
    r'\b(?:reactors?|membrane|aerobic|anaerobic|batch|continuous)\b|反应器|厌氧|好氧|批次',
    r'\b(?:controls?|treatments?|untreated)\b|对照|处理组|未处理',
    r'\b(?:temperature|dosage|dose|pH|HRT|SRT)\b|温度|剂量|停留时间',
    r'\b(?:independent|replicat\w*|random\w*)\b|独立|重复|随机'))

def _scoring_text(text):
    # Normalize only the scoring view. Supplied text and all source locators
    # remain the original objects, including PDF extraction peculiarities.
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'(?<=\w)[-\u00ad]\s*\n\s*(?=\w)', '', text)
    return re.sub(r'\s+', ' ', text.replace('\u00ad', ''))

def signals(refs, question):
    """Return bounded selection hints with section state confined to one source."""
    groups = collections.defaultdict(list)
    for ref in refs:groups[document_key(ref)].append(ref)
    sections = {};preamble=set()
    for rows in groups.values():
        section = 'unknown'
        # Without page/line metadata, do not invent order or propagate headings.
        located = all(r.get('page') is not None and r.get('line_start') is not None for r in rows)
        ordered = sorted(rows, key=lambda r:(r['page'],r['line_start'],r['id'])) if located else rows
        def compact(text):return re.sub(r'\W+','',unicodedata.normalize('NFKC',text)).lower()
        title_starts=[i for i,r in enumerate(ordered) if len(compact(r.get('title','')))>24
                      and compact(r['title']) in compact(r['text'])]
        if located and title_starts:preamble.update(r['id'] for r in ordered[:title_starts[0]])
        for i,ref in enumerate(ordered):
            if i in title_starts:section='unknown'
            headings = []
            title=compact(ref.get('title',''));prefix=''
            for line in ref['text'].splitlines():
                prefix=(prefix+compact(line))[-max(len(title)*2,200):]
                if len(title)>24 and title in prefix:
                    # A bound document title after a preceding article's
                    # references resets section state inside a mixed PDF.
                    section='unknown';headings=[];prefix=''
                if (m := HEADING.fullmatch(unicodedata.normalize('NFKC',line))):headings.append(m.group(1).lower())
            if not located:section = 'unknown'
            result_here = any(h.startswith(('result','结果')) for h in headings)
            if headings:section = headings[-1]
            sections[ref['id']] = 'results' if result_here else section
    terms = set(tokens(question));vocab = {r['id']:set(tokens(r['text'])) for r in refs}
    frequency = collections.Counter(t for words in vocab.values() for t in words & terms)
    lexical = {r['id']:sum(math.log(1+len(refs)/(1+frequency[t])) for t in vocab[r['id']] & terms) for r in refs}
    method_focus = bool(METHOD_QUESTION.search(question) and not OUTCOME_QUESTION.search(question))
    out = {}
    for rows in groups.values():
        maximum = max((lexical[r['id']] for r in rows), default=0)
        title_terms = set().union(*(set(tokens(r.get('title','')))-TITLE_STOPWORDS for r in rows))
        affinity = {r['id']:len(vocab[r['id']] & title_terms) for r in rows}
        title_max = max(affinity.values(),default=0)
        for ref in rows:
            text = _scoring_text(ref['text']);section = sections[ref['id']]
            results = section.startswith(('result','结果'))
            backmatter = section.startswith(('references','acknowledg','author contributions','参考文献'))
            methods = section.startswith(('material','method','材料','方法'))
            background = section.startswith(('introduction','abstract','引言','摘要'))
            outcome = min(3,len(set(m.lower() for m in OUTCOMES.findall(text))))
            quantified = min(3,len(QUANTITY.findall(text)))
            comparisons = min(2,len(set(m.lower() for m in COMPARATORS.findall(text))))
            qualification = min(3,len(set(m.lower() for m in QUALIFICATION.findall(text))))
            relevance = lexical[ref['id']]/maximum if maximum else 0
            # Quantified comparisons in body results compete with query hits;
            # repeated background words alone cannot consume every source slot.
            # PDFs can contain adjacent articles. A document-title affinity is
            # a generic source-focus hint, not a whitelist of publication names.
            focus = affinity[ref['id']]/title_max if title_max else 0
            if method_focus:
                # An explicit methods/conditions question must not be displaced
                # just because outcome paragraphs contain more numeric effects.
                primary = 2*relevance + (4 if methods else 0) - (8 if backmatter else 0)
            else:
                primary = 2*relevance + (3 if results else 0) + outcome + min(1,quantified) + comparisons
                primary -= 8 if backmatter else 3 if methods else 2 if background else 0
            # Use title affinity only to demote a complete focus miss. Rewarding
            # repeated title words would again prefer introductions over results.
            if title_max and not affinity[ref['id']]:primary-=2
            if ref['id'] in preamble:primary-=8
            constraint = 2*qualification + min(1,outcome) + min(1,quantified) + relevance if qualification else 0
            if not constraint and GENERIC_CONTRAST.search(text):constraint = .25
            if backmatter:constraint = 0
            if methods or background:constraint = max(0,constraint-2)
            if ref['id'] in preamble:constraint=0
            design = sum(bool(pattern.search(text)) for pattern in DESIGN_CONTEXT)
            if backmatter or ref['id'] in preamble:design=0
            out[ref['id']] = dict(primary=primary,focus=focus,constraint=constraint,section=section,design=design)
    return out

def select(refs, question, fits):
    """Admit complete refs using the caller's exact serialized context cost.

    Try one useful member per source before extras. An oversized candidate
    cannot evict later, fitting sources. Then consider source-local limitations.
    If the source heads leave no room for any substantive qualification, try
    one source-preserving exchange while retaining another primary excerpt.
    Remaining evidence uses page diversity. This is deliberately a bounded
    heuristic; missing coverage remains visible in the normal record.
    """
    ordered = pack_order(refs,question);hints = signals(ordered,question)
    groups = {}
    for ref in ordered:groups.setdefault(document_key(ref),[]).append(ref)
    for key, rows in groups.items():
        groups[key] = sorted(rows,key=lambda r:-hints[r['id']]['primary'])
    chosen=[];seen=set()
    def admit(ref):
        if ref['id'] in seen:return False
        if not fits(chosen+[ref]):return False
        chosen.append(ref);seen.add(ref['id']);return True
    for rows in groups.values():
        for ref in rows:
            if admit(ref):break
    # One strongest remaining qualification per source is considered before
    # generic contrasts and repeated result pages. A matched word is no verdict.
    constraints=[]
    for rows in groups.values():
        candidates=[r for r in rows if r['id'] not in seen and hints[r['id']]['constraint']>0]
        if candidates:
            constraints.append(max(candidates,key=lambda r:(hints[r['id']]['constraint'],hints[r['id']]['primary'])))
    for ref in sorted(constraints,key=lambda r:(-hints[r['id']]['constraint'],-hints[r['id']]['primary'])):admit(ref)
    # Greedy source heads can fill the budget with positive findings only.
    # A complete qualification from the same source can replace one head;
    # never lose source coverage, replace the sole primary, or do this for a
    # methods-only question. Generic "however"/footnote hints (.25) alone do
    # not justify evicting a primary. These remain lexical hints, not verdicts.
    method_focus = bool(METHOD_QUESTION.search(question) and not OUTCOME_QUESTION.search(question))
    if len(chosen)>1 and not method_focus and not any(hints[r['id']]['constraint']>.25 for r in chosen):
        exchanges=[]
        for ref in ordered:
            if ref['id'] in seen or hints[ref['id']]['constraint']<=.25:continue
            key=document_key(ref)
            if not any(document_key(r)!=key and hints[r['id']]['primary']>0 for r in chosen):continue
            for i,old in enumerate(chosen):
                if document_key(old)!=key:continue
                trial=chosen[:i]+[ref]+chosen[i+1:]
                if fits(trial):
                    exchanges.append((ref,i,hints[old['id']]['primary']-hints[ref['id']]['primary']))
        if exchanges:
            ref,i,_=min(exchanges,key=lambda item:(-hints[item[0]['id']]['constraint'],item[2],-hints[item[0]['id']]['primary']))
            seen.remove(chosen[i]['id']);chosen[i]=ref;seen.add(ref['id'])
    # A result-only excerpt can lose the experimental system/control while
    # keeping an impressive number. Prefer one intact source-local design
    # companion before repeated result pages. Lexical hints do not establish
    # that a design is complete, and cannot cross a document boundary.
    if len(groups)>1 or METHOD_QUESTION.search(question):
        for key,rows in groups.items():
            source_chosen=[r for r in chosen if document_key(r)==key]
            if not source_chosen or any(hints[r['id']]['design']>=2 for r in source_chosen):continue
            companions=[r for r in rows if r['id'] not in seen and hints[r['id']]['design']>=2]
            for ref in sorted(companions,key=lambda r:(-hints[r['id']]['design'],-hints[r['id']]['primary'])):
                if admit(ref):break
    used_pages={(document_key(r),r.get('page')) for r in chosen if r.get('page') is not None}
    remaining=[]
    for key,rows in groups.items():
        fresh=[];repeat=[]
        for ref in rows:
            if ref['id'] in seen:continue
            page=ref.get('page');place=(key,page)
            if page is None or place not in used_pages:fresh.append(ref);used_pages.add(place)
            else:repeat.append(ref)
        remaining.append(fresh+repeat)
    for i in range(max((len(rows) for rows in remaining),default=0)):
        for rows in remaining:
            if i<len(rows):admit(rows[i])
    return chosen
