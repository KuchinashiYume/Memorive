"""Deterministic data processing. No semantic inference, model or network calls.

All rule bindings must name source records and supply their mathematical
preconditions. Number-pattern descriptions never become scientific findings.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Mapping

ENGINE_VERSION = 'MemoriveDataEngine-v7'
RULE_VERSION = 'ExplicitNumericRules-v5'
MAX_BYTES = 4_000_000
MAX_RECORDS = 20_000
MAX_RULES = 200
# A sentence period may follow a complete numeric token. A further decimal
# component may not: never backtrack away from a percent sign to satisfy it.
NUMBER = re.compile(r'(?:(?P<relation><=|>=|<|>|≤|≥|=)\s*|(?<![\w.]))(?P<value>(?:[+−-]\s*)?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?)(?P<percent>\s*%)?(?!\w|\.\d)')
AMBIGUOUS = re.compile(r'(?<!\w)(?:\d+(?:\.\d+){2,}|\d+,\d+|[0-9]*[OlI][0-9]*\.[0-9]+|\d+\.[0-9]*[OlI][0-9]*)(?!\w)')
UNITS = re.compile(r'^\s*([%‰‱％]|(?:µ|μ|u|m|k)?(?:g|L|l|mol|M|m|s|Hz|Pa)(?:/(?:mL|ml|L|l|kg|g|s))?)(?!\w)')

def _normalization(record, basis):
    """Declared rule basis only. Never mutate source values or infer SI conversion."""
    unit=record.get('unit_literal','')
    scale=Decimal(1)
    if basis=='PROBABILITY':
        if unit not in ('','%','％'):
            raise ValueError('PROBABILITY_UNIT_NOT_SUPPORTED')
        if unit in ('%','％'):
            scale=Decimal('0.01')
    elif basis=='PERCENT_LITERAL':
        if unit not in ('','%','％'):
            raise ValueError('PERCENT_LITERAL_UNIT_NOT_SUPPORTED')
    elif basis=='DIMENSIONLESS':
        if unit:
            raise ValueError('DIMENSIONLESS_UNIT_REQUIRED')
    elif basis=='LITERAL':
        if unit and (not UNITS.fullmatch(unit) or unit in ('‰','‱','％')):
            raise ValueError('LITERAL_UNIT_NOT_SUPPORTED')
    else:
        raise ValueError('UNIT_BASIS_NOT_SUPPORTED')
    return dict(record_id=record['record_id'],reported_value=record['value'],unit_literal=unit,
                scale=str(scale),unit_basis=basis,normalized_value=str(decimal(record['value'])*scale))

def _rule_units(kind,resolved,parameters):
    """Bounded unit policy for existing formulas; mismatches are input limits."""
    bases={key:'LITERAL' for key in resolved}
    same=[]
    if kind=='test_p':
        bases.update(statistic='DIMENSIONLESS',p='PROBABILITY')
    elif kind=='ci_p':
        bases['p']='PROBABILITY'
        same=['lower','upper']
    elif kind=='numeric_domain':
        bases['value']=parameters.get('unit_basis','LITERAL')
    elif kind=='percentage':
        bases.update(count='DIMENSIONLESS',denominator='DIMENSIONLESS',percent='PERCENT_LITERAL')
    elif kind=='literal_arithmetic':
        if parameters['percent_result']:
            bases.update(left='DIMENSIONLESS',right='DIMENSIONLESS',reported='PERCENT_LITERAL')
        elif parameters['operation'] in ('+','-','−'):
            same=['left','right','reported']
        else:
            bases.update(left='DIMENSIONLESS',right='DIMENSIONLESS',reported='DIMENSIONLESS')
    elif kind=='sum':
        same=['parts','total']
    elif kind=='sd_se':
        bases['n']='DIMENSIONLESS'
        same=['sd','se']
    elif kind=='cross':
        same=['left','right']
    elif kind=='grid_mean':
        bases.update(mean='DIMENSIONLESS',n='DIMENSIONLESS')
    elif kind=='raw_summary':
        same=[key for key in ('values','reported_mean','reported_sd') if key in resolved]
        if 'reported_n' in resolved:
            bases['reported_n']='DIMENSIONLESS'
    rows=lambda key:resolved[key] if isinstance(resolved[key],list) else [resolved[key]]
    normalized={key:[_normalization(row,bases[key]) for row in rows(key)] for key in resolved}
    if same and len({row.get('unit_literal','') for key in same for row in rows(key)})>1:
        raise ValueError('UNIT_MISMATCH_CONVERSION_NOT_DECLARED')
    return normalized

def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest().upper()

def decimal(value: Any) -> Decimal:
    if isinstance(value,bool) or not isinstance(value,(str,int,Decimal)):
        raise ValueError('DECIMAL_STRING_REQUIRED')
    result=Decimal(re.sub(r'^([+−-])\s+',r'\1',str(value)).replace('−','-'))
    if not result.is_finite() or abs(result.adjusted()) > 100 or len(result.as_tuple().digits) > 100:
        raise ValueError('NUMBER_OUTSIDE_SUPPORTED_RANGE')
    return result

def interval(record: Mapping[str,Any], *, rounding: str = 'nearest', exact: bool = False):
    value=decimal(record['value'])
    relation=record.get('relation','')
    if relation not in ('','='):
        raise ValueError('SCALAR_REQUIRED')
    if exact:
        return (value,value)
    step=Decimal(1).scaleb(value.as_tuple().exponent)
    if rounding=='display_precision_envelope':
        return value-step,value+step
    if rounding=='nearest':
        return value-step/2,value+step/2
    if rounding=='toward_zero':
        return (value,value+step) if value>0 else (value-step,value) if value<0 else (-step,step)
    raise ValueError('ROUNDING_RULE_REQUIRED')

def _range(pair):return [str(v) for v in pair]
def _overlap(a,b):return a[0]<=b[1] and b[0]<=a[1]


def _contains_exact(value, bounds, record, *, rounding, exact=False, squared=False):
    """Check an exact rational or variance against the stated display precision."""
    lo, hi = map(Fraction, bounds)
    reported = decimal(record['value'])
    truncated = rounding == 'toward_zero' and not exact
    open_lo, open_hi = truncated and reported <= 0, truncated and reported >= 0
    if squared:
        if lo < 0:
            lo, open_lo = Fraction(0), False
        lo, hi = lo**2, hi**2
    return (lo < value if open_lo else lo <= value) and (value < hi if open_hi else value <= hi)


def _integer(v,name):
    x=decimal(v)
    if x!=x.to_integral_value() or x<1:
        raise ValueError(name+'_POSITIVE_INTEGER_REQUIRED')
    return int(x)

def extract(text: str, *, source_sha256: str, checkpoint: Callable[[],None]) -> dict:
    records=[]
    issues=[]
    tables=[]
    table=None
    previous=None
    from memorive.platform.progress import scope
    with scope('RAW_EXTRACTION', range(1,len(text.splitlines())+1), 'PROCESSED_ITEMS') as node_units:
        for line_no,line in enumerate(text.splitlines(),1):
            if line_no%128==0:
                checkpoint()
            cells=[]
            if '|' in line:
                for i,match in enumerate(re.finditer(r'(?<=\|)[^|]*(?=\|)|^[^|]+(?=\|)|(?<=\|)[^|]+$',line)):
                    cells.append((match.start(),match.end(),match.group().strip()))
            separator=bool(cells and all(re.fullmatch(r':?-{3,}:?',c[2]) for c in cells))
            if separator and previous:
                table={'table_id':'table-'+str(line_no-1),'header_line':line_no-1,'headers':[c[2] for c in previous], 'rows':[]}
                tables.append(table)
            elif not cells:
                table=None
            spans=[(m.start(),m.end()) for m in AMBIGUOUS.finditer(line)]
            if spans:
                issues.append({'kind':'AMBIGUOUS_NUMBER_FORMAT','line':line_no,'quote':line,'spans':spans})
            row_ids=[]
            for match in NUMBER.finditer(line):
                if any(a<match.end() and match.start()<b for a,b in spans):
                    continue
                if len(records)>=MAX_RECORDS:
                    return dict(records=records,input_issues=issues,tables=tables,limited=True,processed_lines=line_no)
                raw=match.group('value')
                value_start=match.start('value')
                # A sign following a written numeric operand is an arithmetic/range
                # separator. Keep a genuinely unary spaced sign (e.g. r = − 0.200).
                previous_text=line[:value_start].rstrip()
                if raw[:1] in ('+','-','−') and previous_text and previous_text[-1] in '0123456789)]':
                    skip=re.match(r'[+−-]\s*',raw).end()
                    raw=raw[skip:]
                    value_start+=skip
                try:
                    value=decimal(raw)
                except (ValueError,InvalidOperation):
                    issues.append({'kind':'UNSUPPORTED_NUMBER','line':line_no,'quote':match.group()})
                    continue
                column=next((i for i,c in enumerate(cells) if c[0]<=match.start('value')<c[1]),None)
                unit=UNITS.match(line[match.end('value'):])
                unit=unit.group(1) if unit else ''
                record={'record_id':f'L{line_no}:C{value_start+1}', 'raw':raw,'value':str(value),
                        'relation':(match.group('relation') or '').replace('≤','<=').replace('≥','>='),
                        'unit_literal':'%' if match.group('percent') else unit,
                        'decimal_places':max(0,-value.as_tuple().exponent),'semantic_role':'UNBOUND',
                        'source':{'kind':'RawMD','sha256':source_sha256,'line':line_no,'column':value_start+1,'quote':line},
                        'table_id':table['table_id'] if table and not separator else None,'table_column':column,
                        'label_literal':table['headers'][column] if table and column is not None and column<len(table['headers']) else None}
                records.append(record)
                row_ids.append(record['record_id'])
            if table and not separator and cells:
                if len(cells)!=len(table['headers']):
                    issues.append({'kind':'TABLE_COLUMN_MISMATCH','line':line_no,'quote':line})
                else:
                    table['rows'].append({'line':line_no,'cells':[c[2] for c in cells],'record_ids':row_ids})
            previous=cells if cells and not separator else None
            node_units.complete(line_no)
    return dict(records=records,input_issues=issues,tables=tables,limited=False,processed_lines=len(text.splitlines()))

def table_bindings(parsed: dict, lines: list[str]) -> list[dict]:
    """Only exact supported count/denominator/percentage labels on one row.

    No bare n is attached to SD/SE, no free text is interpreted as a method.
    """
    from memorive.documents.data_review.formats import table_percent_is_weighted
    labels={'unweighted count':'count','未加权计数':'count','denominator':'denominator','分母':'denominator',
            'percent (nearest)':'percent','百分比（四舍五入）':'percent'}
    by_id={r['record_id']:r for r in parsed['records']}
    rules=[]
    bad={i['line'] for i in parsed['input_issues']}
    for table in parsed['tables']:
        headers=[labels.get(h.strip().casefold()) for h in table['headers']]
        if any(headers.count(k)!=1 for k in ('count','denominator','percent')) or table_percent_is_weighted(table,lines):
            continue
        for row in table['rows']:
            if row['line'] in bad:
                continue
            found={k:[by_id[r] for r in row['record_ids'] if by_id[r]['table_column']==headers.index(k)] for k in ('count','denominator','percent')}
            if not all(len(x)==1 and x[0]['relation'] in ('','=') for x in found.values()):
                continue
            rules.append({'rule_id':f'row-percent-{row["line"]}','type':'percentage',
                          'inputs':{k:x[0]['record_id'] for k,x in found.items()},
                          'parameters':{'unweighted_count':True,'rounding':'nearest'},
                          'binding_source':{'kind':'SUPPORTED_TABLE_LABELS','line':table['header_line'],'headers':table['headers']}})
    return rules

REQUIRED = {
    'literal_arithmetic':(['left','right','reported'],['operation','percent_result','rounding']),
    'numeric_domain':(['value'],['lower','upper','domain_label']),
    'percentage':(['count','denominator','percent'],['unweighted_count','rounding']),
    'sum':(['parts','total'],['mutually_exclusive','exhaustive','rounding']),
    'sd_se':(['sd','se','n'],['definition','rounding']),
    'cross':(['left','right'],['same_quantity','rounding']),
    'grid_mean':(['mean','n'],['step','origin','equal_weights','rounding']),
    'test_p':(['statistic','p'],['distribution','tail','adjustment','rounding']),
    'ci_p':(['lower','upper','p'],['relationship','alpha','null_value','rounding']),
    'raw_summary':(['values'],['missing_policy','weights','sd_denominator']),
}
REPORTED_SUMMARY_FIELDS = ('reported_n', 'reported_mean', 'reported_sd')

def _tail(distribution, statistic, parameters):
    # mpmath is already a pinned runtime dependency. Numerical approximations
    # are labeled as such, not advertised as a rigorous interval proof.
    from mpmath import mp
    ctx=mp.clone()
    ctx.dps=45
    x=ctx.mpf(str(statistic))
    tail=parameters['tail']
    if tail not in ('upper','lower','two_sided') or (tail=='two_sided' and distribution not in ('normal','t')):
        raise ValueError('TAIL_NOT_SUPPORTED')
    if distribution=='normal':
        if tail=='two_sided':
            value=ctx.erfc(abs(x)/ctx.sqrt(2))
        else:
            value=ctx.erfc((x if tail=='upper' else -x)/ctx.sqrt(2))/2
    elif distribution=='t':
        df=ctx.mpf(str(decimal(parameters['df'])))
        if df<=0:
            raise ValueError('DF_POSITIVE_REQUIRED')
        half=ctx.betainc(df/2,ctx.mpf('.5'),0,df/(df+x*x),regularized=True)/2
        value=2*half if tail=='two_sided' else half if (tail=='upper')==(x>=0) else 1-half
    elif distribution=='chi_square':
        df=ctx.mpf(str(decimal(parameters['df'])))
        if df<=0 or x<0:
            raise ValueError('DISTRIBUTION_DOMAIN_INVALID')
        bounds=(x/2,ctx.inf) if tail=='upper' else (0,x/2)
        value=ctx.gammainc(df/2,*bounds,regularized=True)
    elif distribution=='f':
        a=ctx.mpf(str(decimal(parameters['df1'])))
        b=ctx.mpf(str(decimal(parameters['df2'])))
        if a<=0 or b<=0 or x<0:
            raise ValueError('DISTRIBUTION_DOMAIN_INVALID')
        if tail=='upper':
            value=ctx.betainc(b/2,a/2,0,b/(b+a*x),regularized=True)
        else:
            value=ctx.betainc(a/2,b/2,0,a*x/(b+a*x),regularized=True)
    else:
        raise ValueError('DISTRIBUTION_NOT_SUPPORTED')
    return Decimal(str(value))

def _resolve_rule_inputs(inputs, records, result):
    """Each input of the rule as its record (or list of records): a record id, or the exact raw value and
    source quote of one record. The ids used are added to result['input_ids']."""
    resolved={}
    for key,ids in inputs.items():
        sequence=ids if isinstance(ids,list) else [ids]
        selected=[]
        for selector in sequence:
            if isinstance(selector,Mapping):
                if not isinstance(selector.get('raw'),str) or not isinstance(selector.get('quote'),str) or not selector['quote'].strip():
                    raise ValueError('EXACT_VALUE_AND_SOURCE_QUOTE_REQUIRED')
                candidates=[row for row in records.values() if row['raw']==selector['raw'] and selector['quote'] in row['source'].get('quote','')
                    and ('line' not in selector or selector['line']==row['source'].get('line'))]
                if 'occurrence' in selector:
                    ordinal=selector['occurrence']
                    if isinstance(ordinal,bool) or not isinstance(ordinal,int) or not 1<=ordinal<=len(candidates):
                        raise ValueError('SOURCE_OCCURRENCE_INVALID')
                    candidates=[candidates[ordinal-1]]
                if len(candidates)!=1:
                    raise ValueError('INPUT_SELECTOR_NOT_UNIQUE_OR_NOT_FOUND')
                selected.append(candidates[0]['record_id'])
            else:
                selected.append(selector)
        if not selected or any(not isinstance(i,str) or i not in records for i in selected):
            raise ValueError('INPUT_RECORD_NOT_FOUND')
        result['input_ids'].extend(selected)
        resolved[key]=[records[i] for i in selected] if isinstance(ids,list) else records[selected[0]]
    result['input_ids']=sorted(set(result['input_ids']))
    return resolved


def _check_literal_arithmetic(p, scalar, resolved):
    """A printed arithmetic relation (left op right = reported), each number allowed one last-place unit."""
    if p['rounding']!='display_precision_envelope':
        raise ValueError('EXPLICIT_ARITHMETIC_PRECISION_REQUIRED')
    def envelope(key):
        v=scalar(key)
        step=Decimal(1).scaleb(decimal(resolved[key]['raw']).as_tuple().exponent)
        return v-step,v+step
    left,right=envelope('left'),envelope('right')
    op=p['operation']
    if op=='+':
        expected=(left[0]+right[0],left[1]+right[1])
    elif op in ('-','−'):
        expected=(left[0]-right[1],left[1]-right[0])
    elif op in ('*','×'):
        products=[x*y for x in left for y in right]
        expected=min(products),max(products)
    elif op in ('/','÷'):
        if right[0]<=0<=right[1]:
            raise ValueError('DIVISOR_PRECISION_INTERVAL_INCLUDES_ZERO')
        ratios=[x/y for x in left for y in right]
        expected=min(ratios),max(ratios)
    else:
        raise ValueError('LITERAL_OPERATOR_NOT_SUPPORTED')
    if p['percent_result']:
        expected=expected[0]*100,expected[1]*100
    observed=envelope('reported')
    compatible=_overlap(expected,observed)
    calculation={'formula':'literal left '+op+' right'+(' × 100' if p['percent_result'] else ''),
        'expected_interval':_range(expected),'reported_interval':_range(observed),
        'condition':'Only the explicitly printed arithmetic relation; each operand and result allows one last-place unit for nearest/truncation. No scientific or group interpretation.'}
    return compatible, calculation


def _check_numeric_domain(resolved, units, p):
    """A value (or bound) lies in the rule's named numeric domain."""
    record=resolved['value']
    v=decimal(record['value'])
    relation=record['relation']
    normalization=units['value'][0]
    v=decimal(normalization['normalized_value'])
    lo=decimal(p['lower']) if p['lower'] is not None else None
    hi=decimal(p['upper']) if p['upper'] is not None else None
    if relation in ('','='):
        compatible=(lo is None or v>=lo) and (hi is None or v<=hi)
    elif relation in ('<','<='):
        compatible=lo is None or (v>lo if relation=='<' else v>=lo)
    elif relation in ('>','>='):
        compatible=hi is None or (v<hi if relation=='>' else v<=hi)
    else:
        raise ValueError('DOMAIN_RELATION_NOT_SUPPORTED')
    calculation={'formula':'nonempty intersection with literal named numeric domain','domain_label':p['domain_label'],'value':str(v),'relation':relation,'lower':p['lower'],'upper':p['upper'],'does_not_validate_statistical_method':True,
                 'normalization':normalization}
    return compatible, calculation


def _check_percentage(p, scalar, integer, r, resolved):
    """A reported percentage is 100 x count / denominator within its rounding."""
    if p['unweighted_count'] is not True:
        raise ValueError('UNWEIGHTED_COUNT_REQUIRED')
    count=scalar('count')
    denominator=integer('denominator')
    if count!=count.to_integral_value() or not 0<=count<=denominator:
        raise ValueError('COUNT_INVALID')
    exact_ratio=Fraction(int(count)*100,denominator)
    observed=r('percent')
    compatible=_contains_exact(exact_ratio,observed,resolved['percent'],rounding=p['rounding'],
                               exact='percent' in p.get('exact_inputs',[]))
    calculation={'formula':'100 × count / denominator','expected':str(count*100/denominator),
                 'exact_ratio':str(exact_ratio),'reported_interval':_range(observed)}
    return compatible, calculation


def _check_sum(p, resolved, r):
    """Disjoint parts that cover the whole add up to the reported total within rounding."""
    if p['mutually_exclusive'] is not True or p['exhaustive'] is not True:
        raise ValueError('EXHAUSTIVE_DISJOINT_PARTS_REQUIRED')
    if len({row['record_id'] for row in resolved['parts']}) != len(resolved['parts']):
        raise ValueError('DUPLICATE_PART_RECORD')
    parts=[interval(row,rounding=p['rounding'],exact=p.get('exact_parts') is True) for row in resolved['parts']]
    expected=(sum(a for a,b in parts),sum(b for a,b in parts))
    observed=r('total')
    compatible=_overlap(expected,observed)
    calculation={'formula':'sum(parts)','expected_interval':_range(expected),'reported_interval':_range(observed)}
    return compatible, calculation


def _check_sd_se(p, integer, r, scalar):
    """A standard error and standard deviation agree with n: SD squared = n x SE squared, within rounding."""
    if p['definition'] not in ('independent_equal_weight_mean','explicit_SE_equals_SD_over_sqrt_n'):
        raise ValueError('SE_DEFINITION_NOT_SUPPORTED')
    n=integer('n')
    sd=r('sd')
    se=r('se')
    if scalar('sd')<0 or scalar('se')<0:
        raise ValueError('SD_SE_NONNEGATIVE_REQUIRED')
    sd=(max(Decimal(0),sd[0]),sd[1])
    se=(max(Decimal(0),se[0]),se[1])
    # Compare squared positive intervals, avoiding sqrt rounding.
    expected=(sd[0]*sd[0],sd[1]*sd[1])
    observed=(se[0]*se[0]*n,se[1]*se[1]*n)
    compatible=_overlap(expected,observed)
    calculation={'formula':'SD² = n × SE²','sd_squared_interval':_range(expected),'n_se_squared_interval':_range(observed),'n':n}
    return compatible, calculation


def _check_cross(p, r):
    """Two reports of the same quantity overlap within rounding."""
    if p['same_quantity'] is not True:
        raise ValueError('SAME_QUANTITY_BINDING_REQUIRED')
    a,b=r('left'),r('right')
    compatible=_overlap(a,b)
    calculation={'left_interval':_range(a),'right_interval':_range(b),'formula':'intersection of reported rounding intervals'}
    return compatible, calculation


def _check_grid_mean(p, integer, r):
    """A mean of n values on a grid (origin + step x integer) can be the reported mean."""
    if p['equal_weights'] is not True:
        raise ValueError('EQUAL_WEIGHTS_REQUIRED')
    n=integer('n')
    step=Fraction(decimal(p['step']))
    origin=Fraction(decimal(p['origin']))
    if step<=0:
        raise ValueError('GRID_STEP_POSITIVE_REQUIRED')
    lo,hi=map(Fraction,r('mean'))
    lower=math.ceil((lo-origin)*n/step)
    upper=math.floor((hi-origin)*n/step)
    if 'min_value' in p:
        lower=max(lower,n*math.ceil((Fraction(decimal(p['min_value']))-origin)/step))
    if 'max_value' in p:
        upper=min(upper,n*math.floor((Fraction(decimal(p['max_value']))-origin)/step))
    compatible=lower<=upper
    calculation={'formula':'mean = origin + step × integer_sum / n','integer_sum_min':lower,'integer_sum_max':upper,
                 'proof':'exact rational interval; boundary endpoints conservatively included','joint_mean_sd_tested':False}
    return compatible, calculation


def _check_test_p(r, p, scalar, resolved, units):
    """A reported p value agrees with the tail probability of the reported statistic under the rule's
    distribution (a numerical approximation)."""
    a,b=r('statistic')
    reported_interval=(a,b)
    if p['distribution'] in ('chi_square','f'):
        # Validate the observation before intersecting its rounding
        # interval. An invalid negative statistic is never clamped.
        if scalar('statistic')<0:
            raise ValueError('DISTRIBUTION_DOMAIN_INVALID')
        a=max(Decimal(0),a)
        if a>b:
            raise ValueError('STATISTIC_DOMAIN_INTERSECTION_EMPTY')
    points=[a,b]+([Decimal(0)] if a<=0<=b else [])
    values=[_tail(p['distribution'],v,p) for v in points]
    # An absolute band would treat all sufficiently small p values as equivalent.
    epsilon=Decimal('1e-30')
    expected=(max(Decimal(0),min(values)*(1-epsilon)),min(Decimal(1),max(values)*(1+epsilon)))
    normalization=units['p'][0]
    observed, relation = _reported_probability(resolved['p'], normalization, r)
    compatible=_overlap(expected,observed)
    approximate=True
    if relation=='<':
        compatible=compatible and expected[0]<observed[1]
    elif relation=='>':
        compatible=compatible and expected[1]>observed[0]
    calculation={'formula':'specified distribution tail probability','expected_interval':_range(expected),
                 'reported_interval':_range(observed),'algorithm':'mpmath 45 decimal digits; numerical approximation',
                 'normalization':normalization,'reported_relation':relation,
                 'strict_interval_proof':False,'df_parameters':{k:p[k] for k in ('df','df1','df2') if k in p},
                 'reported_statistic_interval':_range(reported_interval),'effective_statistic_interval':_range((a,b))}
    return compatible, approximate, calculation


def _reported_probability(record, normalization, r):
    v = decimal(normalization['normalized_value'])
    if not 0 <= v <= 1:
        raise ValueError('P_RANGE_INVALID')
    relation = record['relation']
    if relation in ('<', '<='):
        return (Decimal(0), v), relation
    if relation in ('>', '>='):
        return (v, Decimal(1)), relation
    if relation in ('', '='):
        lo, hi = (x * decimal(normalization['scale']) for x in r('p'))
        return (max(Decimal(0), lo), min(Decimal(1), hi)), relation
    raise ValueError('P_RELATION_NOT_SUPPORTED')


def _check_ci_p(p, r, resolved, units):
    """Compare decisions for a CI formed by inverting the very same two-sided test."""
    alpha, null = decimal(p['alpha']), decimal(p['null_value'])
    if not 0 < alpha < 1:
        raise ValueError('ALPHA_OUTSIDE_OPEN_UNIT_INTERVAL')
    lower, upper = r('lower'), r('upper')
    observed, relation = _reported_probability(resolved['p'], units['p'][0], r)
    if (relation == '<' and observed[1] == 0) or (relation == '>' and observed[0] == 1):
        raise ValueError('EMPTY_P_BOUND')
    if lower[0] > upper[1]:
        return False, {'formula': 'CI lower endpoint ≤ upper endpoint',
                       'lower_interval': _range(lower), 'upper_interval': _range(upper)}
    ci_decision = None
    if null < lower[0] or null > upper[1]:
        ci_decision = 'REJECT'
    elif lower[1] <= null <= upper[0]:
        ci_decision = 'RETAIN'
    p_decision = None
    if observed[1] < alpha or (observed[1] == alpha and relation == '<'):
        p_decision = 'REJECT'
    elif observed[0] >= alpha:
        p_decision = 'RETAIN'
    calculation = {'formula': 'A CI from the same test excludes the null iff p < alpha',
                   'alpha': str(alpha), 'null_value': str(null), 'lower_interval': _range(lower),
                   'upper_interval': _range(upper), 'reported_p_interval': _range(observed),
                   'reported_p_relation': relation, 'ci_decision': ci_decision, 'p_decision': p_decision}
    return (ci_decision == p_decision if ci_decision and p_decision else None), calculation


def _raw_summary(p, resolved, r):
    """The mean, variance and standard deviation of raw values, for the record."""
    if p['missing_policy']!='no_missing' or p['weights']!='equal' or p['sd_denominator'] not in ('sample','population'):
        raise ValueError('RAW_SUMMARY_METHOD_NOT_SUPPORTED')
    rows=resolved['values']
    if len({row['record_id'] for row in rows}) != len(rows):
        raise ValueError('DUPLICATE_OBSERVATION_RECORD')
    values=[decimal(v['value']) for v in rows]
    if any(v['relation'] not in ('','=') for v in rows):
        raise ValueError('RAW_CENSORED_VALUES_NOT_SUPPORTED')
    n=len(values)
    mean=sum(values)/n
    denominator=n-1 if p['sd_denominator']=='sample' else n
    compared=[key for key in REPORTED_SUMMARY_FIELDS if key in resolved]
    if denominator<=0 and (not compared or 'reported_sd' in compared):
        raise ValueError('SD_REQUIRES_MORE_VALUES')
    variance=sum((x-mean)**2 for x in values)/denominator if denominator else None
    calculation={'n':n,'mean':str(mean),'variance':str(variance) if variance is not None else None,
                 'sd':str(variance.sqrt()) if variance is not None else None,'definition':p['sd_denominator']}
    if not compared:
        return True, calculation
    rational_values = list(map(Fraction, values))
    exact_mean = sum(rational_values) / n
    compared_fields = {}
    mismatches = []
    for key in compared:
        record = resolved[key]
        bounds = r(key)
        if key == 'reported_n':
            matches = n == _integer(bounds[0], 'REPORTED_N')
        else:
            if key == 'reported_sd' and decimal(record['value']) < 0:
                raise ValueError('SD_NONNEGATIVE_REQUIRED')
            expected = (sum((v-exact_mean)**2 for v in rational_values)/denominator
                        if key == 'reported_sd' else exact_mean)
            matches = _contains_exact(expected, bounds, record, rounding=p['rounding'],
                                      exact=key in p.get('exact_inputs',[]), squared=key=='reported_sd')
        compared_fields[key] = {'reported_interval': _range(bounds), 'compatible': matches}
        if not matches:
            mismatches.append(key)
    calculation.update(formula='Recompute n, arithmetic mean and SD from the bound complete sample',
                       compared_fields=compared_fields, mismatched_reported_fields=mismatches)
    return not mismatches, calculation


def evaluate(rule: Mapping[str,Any], records: dict[str,dict], *, checkpoint:Callable[[],None]) -> dict:
    checkpoint()
    result={'rule_id':str(rule.get('rule_id','')),'type':rule.get('type'),'rule_version':RULE_VERSION,
            'status':'INSUFFICIENT_INFORMATION','missing':[],'input_ids':[], 'binding_source':rule.get('binding_source'),
            'parameters':rule.get('parameters',{}),'calculation':{},'finding':None}
    kind=rule.get('type')
    inputs=rule.get('inputs',{})
    p=rule.get('parameters',{})
    if kind not in REQUIRED:
        return {**result,'status':'NOT_APPLICABLE','reason':'RULE_NOT_SUPPORTED'}
    needed,parameters=REQUIRED[kind]
    if kind=='raw_summary' and any(key in inputs for key in REPORTED_SUMMARY_FIELDS):
        parameters=[*parameters, 'exact_complete_sample']
        if 'reported_mean' in inputs or 'reported_sd' in inputs:
            parameters.append('rounding')
    result['missing']=[f'inputs.{key}' for key in needed if key not in inputs]+[f'parameters.{key}' for key in parameters if key not in p]
    if not rule.get('binding_source'):
        result['missing'].append('binding_source')
    if result['missing']:
        return result
    if kind=='raw_summary' and 'exact_complete_sample' in parameters and p['exact_complete_sample'] is not True:
        return {**result,'status':'NOT_APPLICABLE','reason':'EXACT_COMPLETE_SAMPLE_REQUIRED'}
    if kind=='ci_p' and p['relationship']!='same_two_sided_test_inversion':
        return {**result,'status':'NOT_APPLICABLE','reason':'CI_P_SAME_TWO_SIDED_TEST_REQUIRED'}
    try:
        resolved = _resolve_rule_inputs(inputs, records, result)
        r=lambda k:interval(resolved[k],rounding=p.get('rounding','nearest'),
                            exact=k=='reported_n' or k in p.get('exact_inputs',[]))
        def scalar(k):
            if resolved[k]['relation'] not in ('','='):
                raise ValueError('SCALAR_REQUIRED')
            return decimal(resolved[k]['value'])
        integer=lambda k:_integer(scalar(k),k.upper())
        compatible=True
        calculation={}
        approximate=False
        with localcontext() as ctx:
            ctx.prec=256
            units=_rule_units(kind,resolved,p)
            if kind=='literal_arithmetic':
                compatible, calculation = _check_literal_arithmetic(p, scalar, resolved)
            elif kind=='numeric_domain':
                compatible, calculation = _check_numeric_domain(resolved, units, p)
            elif kind=='percentage':
                compatible, calculation = _check_percentage(p, scalar, integer, r, resolved)
            elif kind=='sum':
                compatible, calculation = _check_sum(p, resolved, r)
            elif kind=='sd_se':
                compatible, calculation = _check_sd_se(p, integer, r, scalar)
            elif kind=='cross':
                compatible, calculation = _check_cross(p, r)
            elif kind=='grid_mean':
                compatible, calculation = _check_grid_mean(p, integer, r)
            elif kind=='test_p':
                if p['adjustment']!='none':
                    raise ValueError('ADJUSTED_P_METHOD_NOT_SUPPORTED')
                missing=[key for key in (['df'] if p['distribution'] in ('t','chi_square') else ['df1','df2'] if p['distribution']=='f' else []) if key not in p]
                if missing:
                    return {**result,'missing':['parameters.'+key for key in missing]}
                compatible, approximate, calculation = _check_test_p(r, p, scalar, resolved, units)
            elif kind=='ci_p':
                compatible, calculation = _check_ci_p(p, r, resolved, units)
                if compatible is None:
                    return {**result,'calculation':calculation,'reason':'CI_P_DECISION_UNRESOLVED_AT_REPORTED_PRECISION'}
            elif kind=='raw_summary':
                compatible, calculation = _raw_summary(p, resolved, r)
            calculation['unit_normalizations']=units
            result.update(status='COMPATIBLE' if compatible else 'INCONSISTENT',calculation=calculation,approximate=approximate)
            if not compatible:
                result['finding']={'finding_id':'data-'+digest({'rule':rule,'inputs':result['input_ids']})[:24],
                    'root_cause_key':digest({'inputs':result['input_ids'],'quantity':p.get('quantity_binding')}),
                    'kind':'CONDITIONAL_NUMERIC_INCONSISTENCY','level':'CLARIFY','impact':'UNKNOWN',
                    'source_aligned':True,'conditions_explicit':True,'alternative_explanation':'Check conversion, labels and the supplied method binding.',
                    'input_ids':result['input_ids'],'rule_id':result['rule_id'],'statement':'Reported values do not overlap under the supplied conditions.',
                    'not_a_scientific_authenticity_verdict':True}
    except (KeyError,ValueError,TypeError,InvalidOperation,ArithmeticError) as exc:
        result.update(status='INPUT_ERROR',reason=str(exc)[:180])
    return result

def analyze(text:str, *, source_id:str, source_sha256:str|None=None, rules:list|None=None, supplemental_records:list|None=None,
            checkpoint:Callable[[],None]=lambda:None, max_seconds:float=20) -> dict:
    if not isinstance(text,str):
        raise ValueError('RAW_MD_TEXT_REQUIRED')
    raw=text.encode('utf-8')
    actual=digest(raw)
    if source_sha256 and source_sha256.upper()!=actual:
        raise ValueError('RAW_MD_HASH_MISMATCH')
    if len(raw)>MAX_BYTES:
        raise ValueError('RAW_MD_SIZE_BUDGET_EXCEEDED')
    if rules is not None and (not isinstance(rules,list) or len(rules)>MAX_RULES):
        raise ValueError('RULE_BINDING_BUDGET_EXCEEDED')
    start=time.monotonic()
    def check():
        checkpoint()
        if time.monotonic()-start>max_seconds:
            raise TimeoutError('DATA_COMPUTE_TIME_BUDGET_EXCEEDED')
    parsed=extract(text,source_sha256=actual,checkpoint=check)
    from memorive.documents.data_review.formats import discover
    formats=discover(text,parsed)
    rows=parsed['records']
    by_id={r['record_id']:r for r in rows}
    automatic=[*table_bindings(parsed,text.splitlines()),*formats['rules']]
    provided=rules or []
    provided_ids={r.get('rule_id') for r in provided}
    automatic=[r for r in automatic if r.get('rule_id') not in provided_ids]
    auto_limited=len(automatic)>MAX_RULES-len(provided)
    bound=[*automatic[:MAX_RULES-len(provided)],*provided]
    for record in supplemental_records or []:
        if record['record_id'] in by_id:
            raise ValueError('SUPPLEMENTAL_RECORD_ID_CONFLICT')
        if record.get('source',{}).get('kind') not in {'rawPDF','RawMD_CARD_ANCHOR','CONFIRMED_STRUCTURE_CELL'}:
            raise ValueError('SUPPLEMENTAL_SOURCE_REQUIRED')
        if record['source']['kind']=='CONFIRMED_STRUCTURE_CELL':
            source=record['source']
            confirmation=source.get('review_receipt',{})
            if not source.get('quote') or not confirmation.get('note') or any(
                source.get(k)!=confirmation.get(k if k!='sha256' else 'source_sha256')
                for k in ('structure_id','block_id','sha256')):raise ValueError('STRUCTURE_CONFIRMATION_BINDING_REQUIRED')
        decimal(record['value'])
        by_id[record['record_id']]=record
    if len(bound)>MAX_RULES:
        raise ValueError('RULE_BINDING_BUDGET_EXCEEDED')
    if len({r.get('rule_id') for r in bound})!=len(bound):
        raise ValueError('RULE_ID_DUPLICATE')
    from memorive.platform.progress import scope
    checks=[]
    with scope('RULE_EVALUATION', [r['rule_id'] for r in bound], 'PROCESSED_ITEMS') as node_units:
        for rule in bound:
            result=evaluate(rule,by_id,checkpoint=check)
            checks.append(result)
            node_units.complete(rule['rule_id'], limited=result['status'] not in ('COMPATIBLE','INCONSISTENT'))
    values=[decimal(r['value']) for r in rows if r['relation'] in ('','=')]
    frequency=Counter(str(v.normalize()) for v in values)
    digits=Counter(re.sub(r'\D','',r['raw'].split('e')[0].split('E')[0])[-1:] for r in rows)
    descriptions={'record_count':len(rows),'scalar_count':len(values),'table_count':len(parsed['tables']),
                  'decimal_places':dict(sorted(Counter(r['decimal_places'] for r in rows).items())),
                  'last_digit_counts':dict(sorted(digits.items())),
                  'repeated_values':[{'value':v,'count':n} for v,n in frequency.most_common(30) if n>1],
                  'minimum':str(min(values)) if values else None,'maximum':str(max(values)) if values else None,
                  'interpretation':'Descriptive only; mixed numeric objects are not a research sample; no uniform-digit assumption.'}
    findings=[r['finding'] for r in checks if r.get('finding')]
    groups={}
    for finding in findings:
        groups.setdefault(finding['root_cause_key'],[]).append(finding['finding_id'])
    completed=sum(r['status'] in ('COMPATIBLE','INCONSISTENT') for r in checks)
    limited=parsed['limited'] or auto_limited or bool(parsed['input_issues']) or completed<len(checks)
    color='YELLOW' if findings else 'GREEN' if completed and not limited else None
    report={'schema_version':'LiteratureDataReport-v1','engine_version':ENGINE_VERSION,'rule_version':RULE_VERSION,
            'source_id':source_id,'source_sha256':actual,'binding_sha256':digest(bound),
            'status':'COMPLETED_LIMITED' if limited or not completed else 'COMPLETED','kind':'DATA',
            'color':color,'color_scope':'BOUND_CHECKS_ONLY' if color else 'NO_EFFECTIVE_CHECK_VERDICT',
            'scope':{'source':'RawMD','processed_lines':parsed['processed_lines'],'numeric_objects':len(rows),'planned_checks':len(checks),
                     'completed_checks':completed,'automatic_rule_limit_reached':auto_limited,
                     'completed_check_types':dict(Counter(r['type'] for r in checks if r['status'] in ('COMPATIBLE','INCONSISTENT'))),
                     'missing_parameter_count':sum(len(r.get('missing',[])) for r in checks),
                     'not_run':['scientific meaning','images','unloaded supplements','whole-paper authenticity','digit-frequency hypothesis tests']},
            'descriptions':descriptions,'records':rows,'tables':parsed['tables'],'input_issues':parsed['input_issues'],
            'checks':checks,'rule_bindings':bound,'numeric_blocks':{'table_columns':formats['table_columns'],'plus_minus_pairs':formats['plus_minus_pairs'],'format_version':formats['format_version']},
            'supplemental_records':supplemental_records or [],'findings':findings,'finding_groups':[{'root_cause_key':key,'finding_ids':ids} for key,ids in groups.items()],
            'missing_parameters':[] if bound else ['No explicit rule bindings; numeric descriptions are available.'],
            'external_model_calls':0,'derived_opinion':True,'raw_evidence_eligible':False}
    report['report_id']='data-'+digest(report)[:32]
    report['report_sha256']=digest(report)
    return report
