"""Bounded, local interpretation of explicit follow-ups; never invokes a model."""
import re

def language(text):
    if re.search(r'[\u3040-\u30ff]',text):return 'ja'
    if re.search(r'[\u3400-\u9fff]',text):return 'zh'
    return 'en'

def say(lang,zh,en,ja):return {'zh':zh,'en':en,'ja':ja}[lang]

def target(text):
    match=re.search(r'第?\s*([一二三四五六七八九十百零两\d]+)\s*(?:条|项|句|番目)|(?:claim|statement|conclusion)\s*#?\s*(\d+)',text,re.I)
    if match:
        value=match[1] or match[2]
        if value.isdigit():n=int(value)
        else:
            digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits['两']=2
            if '百' in value:return {'ambiguous':True}
            if '十' in value:
                left,right=value.split('十',1);n=10*digits.get(left,1)+digits.get(right,0)
            else:n=digits.get(value,0)
        return {'index':n-1}
    match=re.search(r'[“「"]([^”」"]{4,})[”」"]',text)
    return {'quote':match[1]} if match else {}


QUOTED=re.compile(r'```[\s\S]*?```|`[^`]*`|“[^”]*”|「[^」]*」|『[^』]*』|"(?:\\.|[^"\\])*"|‘[^’]*’|(?<!\w)\x27[^\x27\n]*\x27(?!\w)')


NUMBER=r'[一二三四五六七八九十百零两\d]+'
TOKEN=r'@q\d+@'
ZH_SELECTION=rf'(?:第?\s*{NUMBER}\s*(?:条|项|句)(?:结论|主张|内容)?|{TOKEN}|这句(?:话)?)'
EN_SELECTION=rf'(?:(?:claim|statement|conclusion)\s*#?\s*\d+|{TOKEN}|this sentence)'
JA_SELECTION=rf'(?:{NUMBER}番目(?:の結論)?|{TOKEN}|この文)'
ZH_PREVIOUS=r'(?:你(?:的)?|您(?:的)?)?(?:上一(?:个|条|次)?回答|上次回答|上一段(?:回答)?|刚才(?:的)?(?:回答|结论)?|先前(?:的)?回答)'
EN_PREVIOUS=r'(?:(?:the|your|our)\s+)?(?:previous|last|earlier)\s+(?:answer|response|reply)'
JA_PREVIOUS=r'(?:前の回答|先ほどの回答|直前の回答)'
ZH_PREFIX=r'(?:(?:请帮我|请(?:你|您)?|麻烦(?:你|您)?|帮我)\s*)?(?:(?:分别|逐条|重新|再次|现在|直接|先)\s*)*'
EN_PREFIX=r'(?:(?:please|(?:can|could|would|will)\s+you(?:\s+please)?|let[\x27’]s|i\s+(?:want|would\s+like)\s+you\s+to)\s+)?'


def _trim(text):return text.strip().rstrip('。.!?！？').strip()


def _lex(text):
    # Quoted operands retain their bytes and cannot supply command verbs.
    if re.search(TOKEN,text):return None
    quotes={}
    def replace(match):
        token='@q'+str(len(quotes))+'@';quotes[token]=match.group();return token
    control=QUOTED.sub(replace,text)
    if re.search(r'[“”「」『』"\x60]',control):return None
    return control,quotes


def _unquote(text):
    if text.startswith(chr(96)*3):return text[3:-3]
    return text[1:-1]


def _selection(text,quotes):
    text=text.strip()
    if text in quotes:return {'quote':_unquote(quotes[text])}
    if text in {'这句','这句话','this sentence','この文'}:return {'unresolved':True}
    return target(text)


def _target(text,lang,quotes,implicit=False):
    """A closed target grammar: no arbitrary clause may surround a selector."""
    text=_trim(text)
    if lang=='zh':
        selection=ZH_SELECTION
        pattern=rf'{ZH_PREVIOUS}(?:里的|中的|里|中|的)?\s*(?P<sel>{selection}|(?:各条|各项|每条|所有|全部)?(?:结论|主张|内容|回答)?)?'
    elif lang=='en':
        selection=EN_SELECTION
        pattern=rf'{EN_PREVIOUS}(?:[ ,]+(?P<sel>{selection}|(?:all\s+)?(?:claims|statements|conclusions)))?'
        first=re.fullmatch(rf'(?P<sel>{selection})\s+(?:in|of|from)\s+{EN_PREVIOUS}',text,re.I)
        if first:return _selection(first['sel'],quotes)
    else:
        selection=JA_SELECTION
        pattern=rf'{JA_PREVIOUS}(?:の(?P<sel>{selection}|各結論|各主張))?'
    match=re.fullmatch(pattern,text,re.I)
    if match:return _selection(match['sel'] or '',quotes)
    if implicit and re.fullmatch(selection,text,re.I):return _selection(text,quotes)
    return None


def pending_target(question):
    # The whole reply must be a selection. Quoted claim text remains data.
    lexed=_lex(_trim(question))
    if lexed is None:return {}
    text,quotes=lexed
    if re.fullmatch(rf'{ZH_SELECTION}|{EN_SELECTION}|{JA_SELECTION}',text,re.I):
        value=_selection(text,quotes)
        return value if not value.get('unresolved') else {}
    return {}


def _review(selection):
    if selection.get('unresolved'):return {'action':'clarify','reason':'target','pending':'review'}
    return {'action':'review',**selection}


def _replacement(text,quotes):
    text=text.strip()
    token=_trim(text)
    if token in quotes:return _unquote(quotes[token])
    # A second contrast/qualification clause is ambiguous control text.
    # Inside a quoted operand the same words remain literal replacement data.
    if re.search(r'[,，;；]\s*(?:但|不过|可是|but\b|however\b|ただし)|但是|但先',text,re.I):return None
    return re.sub(TOKEN,lambda m:quotes[m.group()],text)


def _revise(selection,replacement):
    if not selection or selection.get('unresolved'):return {'action':'clarify','reason':'target','pending':'revise'}
    return {'action':'revise',**selection,'replacement':replacement}


def _single(text,quotes,implicit=False):
    # Every execution branch full-matches a positive command or an explicit
    # evidence-assessment question. Unknown phrasing stays ordinary chat.
    for lang,pattern in [
        ('zh',rf'{ZH_PREFIX}(?:把|将)?(?P<target>.+?)(?:修改为|改为|降为|改成|替换为)\s*[：:]?\s*(?P<body>.+)'),
        ('en',rf'{EN_PREFIX}(?:replace|revise|recast)\s+(?P<target>.+?)\s+(?:with|to|as)\s+(?P<body>.+)'),
        ('ja',r'(?P<target>.+?)を(?P<body>.+?)に(?:変更してください|書き換えてください)[。.!]?')]:
        match=re.fullmatch(pattern,text,re.I|re.S)
        if not match:continue
        selection=_target(match['target'],lang,quotes,implicit)
        if selection is None:continue
        body=_replacement(match['body'],quotes)
        if body is None:return None
        if lang=='ja' and body=='仮説':body='hypothesis'
        return _revise(selection,body)
    clean=_trim(text)
    if re.fullmatch(rf'{ZH_PREFIX}(?:查查|查看|检查|看看)(?:还缺哪些证据|缺哪些证据)',clean):return {'action':'coverage'}
    for lang,pattern in [
        ('zh',rf'{ZH_PREFIX}(?:查查|查看|检查|看看)(?P<target>.+?)(?:的)?(?:证据覆盖|覆盖情况|还缺哪些证据)'),
        ('en',r'what evidence is (?:still )?missing from (?P<target>.+)'),
        ('en',rf'{EN_PREFIX}(?:check|show|review)\s+(?:the\s+)?(?:evidence\s+)?coverage\s+(?:of|for|in)\s+(?P<target>.+)'),
        ('ja',r'(?P<target>.+?)に足りない証拠を調べてください')]:
        match=re.fullmatch(pattern,clean,re.I)
        if match and _target(match['target'],lang,quotes,implicit) is not None:return {'action':'coverage'}
    facets={'conditions':r'条件|温度|conditions?|temperature','methods':r'方法|基线|methods?|baselines?',
            'sample':r'样本|独立性|samples?|independence|標本','results':r'结果|不确定性|results?|uncertainty',
            'limitations':r'局限(?:与反例)?|反例|limitations?|counterevidence|counterexamples?|反証'}
    match=re.fullmatch(rf'{ZH_PREFIX}(?:补查|再找|再检索)(?:一下)?(?P<rest>.+)',clean)
    if match:
        rest=match['rest']
        for facet,pattern in facets.items():
            part=re.fullmatch(rf'(?P<target>.+?)(?:的)?(?:{pattern})',rest,re.I)
            if part and _target(part['target'],'zh',quotes,implicit) is not None:return {'action':'probe','facet':facet}
        if _target(rest,'zh',quotes,implicit) is not None:return {'action':'clarify','reason':'facet','pending':'probe'}
    for facet,pattern in facets.items():
        match=re.fullmatch(rf'{EN_PREFIX}(?:find|look for)\s+(?:{pattern})\s+(?:for|in|of|to)\s+(?P<target>.+)',clean,re.I)
        if match and _target(match['target'],'en',quotes,implicit) is not None:return {'action':'probe','facet':facet}
    for lang,pattern in [
        ('zh',rf'{ZH_PREFIX}(?:审核|核对|复核|检查)(?:一下)?(?P<target>.+)'),
        ('zh',r'(?P<target>.+?)(?:的)?(?:依据(?:足够|够)|证据(?:充分|足够|够))(?:吗|么)?'),
        ('en',rf'{EN_PREFIX}(?:check|verify)\s+(?:if|whether)\s+(?P<target>.+?)\s+has\s+(?:enough|sufficient)\s+evidence'),
        ('en',r'does\s+(?P<target>.+?)\s+have\s+(?:enough|sufficient)\s+evidence'),
        ('en',rf'{EN_PREFIX}(?:review|verify|check)\s+(?P<target>.+)'),
        ('ja',r'(?P<target>.+?)(?:の根拠|の証拠)?を(?:それぞれ|個別に)?(?:確認|検証)してください')]:
        match=re.fullmatch(pattern,clean,re.I)
        if match:
            selection=_target(match['target'],lang,quotes,implicit)
            if selection is not None:return _review(selection)
    return None


def intent(question):
    lexed=_lex(question.strip())
    if lexed is None:return None
    text,quotes=lexed
    # Both clauses must be independently recognizable before clarification.
    # A conjunction inside quoted data is never an operation separator.
    for join in re.finditer(r'并且|并|同时|然后|\band(?: then)?\b|および|それから',text,re.I):
        left=_single(text[:join.start()].rstrip(' ,，;；'),quotes)
        right=_single(text[join.end():].lstrip(' ,，;；'),quotes,implicit=True)
        if left and right:return {'action':'clarify','reason':'multiple'}
    return _single(text,quotes)

