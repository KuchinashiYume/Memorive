"""Source-syntax bindings only; no research-role, group or denominator guessing."""
from __future__ import annotations
import re
from collections import Counter
from decimal import Decimal

FORMAT_VERSION='LiteralNumericFormats-v5'
NUM=r'(?:[+−-]\s*)?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?'
EXPR=re.compile(r'(?<![\w./])('+NUM+r')\s*([+*/×÷−-])\s*('+NUM+r')\s*=\s*('+NUM+r')\s*(%)?(?!\w|\.\d)')
PAIR=re.compile(r'('+NUM+r')\s*(?:±|\\pm)\s*('+NUM+r')')
FORMULA=re.compile(r'\bSE\s*=\s*SD\s*/\s*(?:sqrt\s*\(\s*n\s*\)|√\s*n)',re.I)
DOMAIN_HEADERS={'p-value':('0','1'),'p value':('0','1'),'p值':('0','1'),
                'sd':('0',None),'se':('0',None),'sem':('0',None),
                'standard deviation':('0',None),'standard error':('0',None),'标准差':('0',None),'标准误':('0',None)}
RATIO_HEADER=re.compile(r'(?:n\s*/\s*N|count\s*/\s*total|计数\s*/\s*总数)\s*[(（]\s*[%％]\s*[)）]')
RATIO_CELL=re.compile(r'\s*(\d+)\s*/\s*(\d+)\s*[(（]\s*('+NUM+r')\s*[%％]?\s*[)）]\s*')
WEIGHTED_PERCENT_NOTE=re.compile(
    r'\bpercentages?\s+(?:are|were)\s+(?:(?:survey|sampling|design)[- ])?weighted\b'
    r'|(?:百分比|百分率)(?:为|采用|使用)(?:调查|抽样|设计)?加权|(?:割合|百分率)は(?:調査|標本)?(?:の)?重み付け',
    re.I,
)


def source_rule(ident,kind,inputs,parameters,line,quote,**extra):
    return dict(rule_id=ident,type=kind,inputs=inputs,parameters=parameters,
        binding_source=dict(kind='EXPLICIT_SOURCE_SYNTAX',line=line,quote=quote,format_version=FORMAT_VERSION,**extra))


TABLE_NAME=re.compile(r'\b(?:Table|Tab\.)\s*(S?\d+[A-Za-z]?)\b|表\s*(S?\d+)',re.I)
CAPTION=re.compile(r'\s*(?:\*\*)?\s*(?:Table|Tab\.|表)\s*S?\d',re.I)


def _table_names(text):
    return {(a or b).upper() for a, b in TABLE_NAME.findall(text)}


def table_percent_is_weighted(table, lines):
    """Whether this table's own caption (the nearest text above, when it starts "Table n") or note (the
    nearest text below) says its percentages are weighted. Text that is about another table does not
    count: "In Table 2, percentages are weighted" under a table captioned Table 1, or the caption of the
    next table ("Table 2. ...") right below this one."""
    if not table['rows']:
        return False
    caption = next((lines[i] for i in range(table['header_line']-2, -1, -1) if lines[i].strip()), '')
    caption = caption if CAPTION.match(caption) else ''
    note = next((lines[i] for i in range(table['rows'][-1]['line'], len(lines)) if lines[i].strip()), '')
    own = _table_names(caption)
    for text, is_caption in ((caption, True), (note, False)):
        if not WEIGHTED_PERCENT_NOTE.search(text):
            continue
        named = _table_names(text)
        if is_caption or own & named or not (CAPTION.match(text) or named and own):
            return True
    return False


def _ratio_rules(table, lines, by_id, bad):
    """The counts in an explicit n/N cell are exact; only the percent is rounded."""
    columns = [col for col, header in enumerate(table['headers']) if RATIO_HEADER.fullmatch(header.strip())]
    if not columns or table_percent_is_weighted(table, lines):
        return
    for row in table['rows']:
        if row['line'] in bad:
            continue
        for col in columns:
            if not RATIO_CELL.fullmatch(row['cells'][col]):
                continue
            records = [by_id[r] for r in row['record_ids'] if by_id[r]['table_column'] == col]
            if len(records) == 3:
                yield source_rule(
                    f'ratio-{row["line"]}-{col}', 'percentage',
                    dict(zip(('count', 'denominator', 'percent'), (r['record_id'] for r in records))),
                    dict(unweighted_count=True, rounding='display_precision_envelope'), row['line'],
                    records[0]['source']['quote'], header_line=table['header_line'],
                    header=table['headers'][col],
                )

def discover(text,parsed):
    by_line={}
    for r in parsed['records']:
        by_line.setdefault(r['source']['line'],[]).append(r)
    bad={x['line'] for x in parsed['input_issues']}
    lines=text.splitlines()
    rules=[]
    pairs=[]
    blocks=[]
    missing=[]
    def record(line,column):
        return next((r['record_id'] for r in by_line.get(line,[]) if r['source']['column']==column+1),None)
    for number,line in enumerate(lines,1):
        if number in bad:
            continue
        # Literal p-value spelling or conventional explicitly marked p notation.
        # A bare variable p is deliberately not classified as a probability.
        probability=re.compile(r'(?:\bp[- ]value\b|p值|_p_|\$p\$)\s*(?:<=|>=|<|>|≤|≥|=)\s*('+NUM+r')(?!\w|\.\d)',re.I)
        for m in probability.finditer(line):
            identity=record(number,m.start(1))
            if identity:
                rules.append(source_rule(f'probability-{number}-{m.start()}','numeric_domain',dict(value=identity),dict(lower='0',upper='1',domain_label='Explicitly marked p-value',unit_basis='PROBABILITY'),number,line))
        for m in EXPR.finditer(line):
            # Do not take a suffix of a longer expression as a complete equation.
            before=line[:m.start()].rstrip()
            after=line[m.end():].lstrip()
            if (before and before[-1] in '+-−*/×÷=^') or (after and after[0] in '+-−*/×÷=^('):
                continue
            ids=[record(number,m.start(i)) for i in (1,3,4)]
            if all(ids):
                rules.append(source_rule(f'expr-{number}-{m.start()}','literal_arithmetic',dict(zip(('left','right','reported'),ids)),
                    dict(operation=m.group(2),percent_result=bool(m.group(5)),rounding='display_precision_envelope'),number,line))
        for m in PAIR.finditer(line):
            ids=[record(number,m.start(i)) for i in (1,2)]
            if all(ids):
                pairs.append(dict(line=number,quote=m.group(),record_ids=ids,interpretation='Literal plus/minus pair; spread type and experimental unit are not inferred.'))
        formula=FORMULA.search(line)
        if formula:
            # A bounded paragraph is an explicit syntactic unit, not a guessed study group.
            lo=number-1
            while lo>0 and lines[lo-1].strip() and number-lo<12:
                lo-=1
            hi=number
            while hi<len(lines) and lines[hi].strip() and hi-number<12:
                hi+=1
            inputs={}
            ambiguous=[]
            for key,label in [('sd','SD'),('se','SE'),('n','n')]:
                found=[]
                pattern=re.compile(r'(?<!\w)'+label+r'\s*=\s*('+NUM+r')(?!\w|\.\d)',re.I)
                for i in range(lo,hi):
                    if i+1 in bad:
                        continue
                    for m in pattern.finditer(lines[i]):
                        identity=record(i+1,m.start(1))
                        if identity:
                            found.append(identity)
                if len(found)==1:
                    inputs[key]=found[0]
                elif len(found)>1:
                    ambiguous.append(key)
            rules.append(source_rule(f'formula-se-{number}','sd_se',inputs,
                dict(definition='explicit_SE_equals_SD_over_sqrt_n',rounding='display_precision_envelope'),number,line,
                input_labels=dict(sd='SD',se='SE',n='n'),block_start=lo+1,block_end=hi,ambiguous_labels=ambiguous))
    by_id={r['record_id']:r for r in parsed['records']}
    for table in parsed['tables']:
        rules.extend(_ratio_rules(table, lines, by_id, bad))
        for col,header in enumerate(table['headers']):
            selected=[by_id[r] for row in table['rows'] for r in row['record_ids'] if by_id[r]['table_column']==col]
            if selected:
                scalar=[r for r in selected if r['relation'] in ('','=')]
                values=[Decimal(r['value']) for r in scalar]
                blocks.append(dict(table_id=table['table_id'],header_line=table['header_line'],column=col,header_literal=header,
                    record_count=len(selected),scalar_count=len(scalar),minimum=str(min(values)) if values else None,
                    maximum=str(max(values)) if values else None,unit_literals=dict(Counter(r.get('unit_literal','') for r in selected)),
                    decimal_places=dict(Counter(r['decimal_places'] for r in selected)),
                    repeated_values=[dict(value=v,count=n) for v,n in Counter(r['value'] for r in selected).most_common(8) if n>1],
                    interpretation='Numeric tokens in this literal column only; not pooled observations or a scientific sample.'))
            bounds=DOMAIN_HEADERS.get(header.strip().casefold())
            if bounds:
                for r in selected:
                    if r['source']['line'] not in bad and r['relation'] in ('','=','<','<=','>','>='):
                        rules.append(source_rule('domain-'+r['record_id'],'numeric_domain',dict(value=r['record_id']),
                            dict(lower=bounds[0],upper=bounds[1],domain_label=header,unit_basis='PROBABILITY' if header.strip().casefold() in ('p-value','p value','p值') else 'LITERAL'),r['source']['line'],r['source']['quote']))
    return dict(rules=rules,table_columns=blocks,plus_minus_pairs=pairs,format_version=FORMAT_VERSION)
