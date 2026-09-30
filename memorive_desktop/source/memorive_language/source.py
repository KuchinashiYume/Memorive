"""Conservative, deterministic source evidence; independent of UI and caches."""
import re
from .policy import freeze

def describe(value):
    text=value if isinstance(value,str) else '\n'.join(str(v or '') for v in value)
    # Remove identifiers and code before counting linguistic evidence.
    probe=re.sub(r'```[\s\S]*?```|https?://\S+|10\.\d{4,9}/\S+', '', text)
    han=len(re.findall(r'[\u3400-\u9fff]',probe))
    kana=len(re.findall(r'[\u3040-\u30ff]',probe))
    latin=len(re.findall(r'[A-Za-z]',probe))
    english=len(re.findall(r'\b(?:the|and|of|in|was|were|this|with|for|that|is|are|study|results|data|method|samples)\b',probe,re.I))
    total=han+kana+latin
    lang='und';basis='INSUFFICIENT_OR_UNSUPPORTED_TEXT'
    if kana>=2 and han+kana>=latin/2:
        lang='ja';basis='KANA_AND_SOURCE_SCRIPT_RATIO'
    elif han>=8 and not kana and han>=latin/2:
        lang='zh';basis='HAN_AND_SOURCE_SCRIPT_RATIO'
    elif latin>=24 and english>=2 and latin>4*(han+kana):
        lang='en';basis='LATIN_AND_ENGLISH_WORD_EVIDENCE'
    elif total>=40 and latin>=20 and han+kana>=10:
        lang='mul';basis='MIXED_SOURCE_SCRIPTS'
    return {'language':lang,'basis':basis,'method_version':'SourceEvidence-v1',
            'script_counts':{'han':han,'kana':kana,'latin':latin},'english_word_count':english}

def context(text):
    evidence=describe(text)
    return freeze(source_language=evidence['language'],source_basis=evidence['basis'])
