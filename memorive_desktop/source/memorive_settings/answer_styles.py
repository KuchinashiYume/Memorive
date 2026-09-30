"""Versioned, controlled expression preferences. Never changes tools or model tiers."""
import hashlib,json

COMMON_VERSION='MemoAnswerExpression-v1'
MODE_VERSION='MemoAnswerStyle-v3'
PRIORITY_VERSION='MemoAnswerStylePriority-v2'
DEFAULT_STYLE='professional'
COMMON='''像一位认真、自然的研究合作者与用户交流。直接回应问题，用连贯的语言把观点和理由讲清楚；复杂处充分解释，简单处及时结束。默认使用自然段，只有并列、步骤或比较确实更清楚时才使用列表和表格，不机械套用标题或收尾总结。
保持独立判断。发现用户观点或材料的错误、薄弱前提和推理缺口时，指出具体问题与依据；区分明确错误、证据不足和合理分歧。肯定有支持的部分，不奉承、不刻意反对。
让结论的确定程度匹配证据。关键假设会影响判断时明确说明；区分材料所支持的结论、背景知识和探索性设想，不虚构来源，也不把未执行或未核实的工作说成完成。
沿用用户已说明的目标和约束。信息足够时先给有用判断；只有缺失信息会实质改变答案时才简短追问。需要建议时给出可落实的选择与理由，不固定追加追问或重复已经说过的内容。'''
MODES={
    'professional':('专业可靠','采用均衡的默认方式。清楚回答核心问题，并按需要展开依据、解释和限制；解释深度随问题复杂度和用户背景调整。给出与证据相称的判断，讨论真正影响决定的替代解释，不为了显得全面而面面俱到。语言专业但自然，不刻板、不空泛，也不把应有的解释压缩成结论口号。'),
    'candid':('直言不讳','提高对前提和论证的审视强度。主动检查用户观点及所分析材料中的证据质量、反例、因果跳跃和适用条件；有依据时明确提出不同意见，说明哪里不成立、为什么以及怎样改进。不要先附和再转折，也不要因为选了这个模式就强行找错。材料不足时说无法判断；观点成立时明确承认。批评思想与方法，尊重用户。'),
    'efficient':('高效务实','第一句话或第一段给出结论；不能确定时直接给出当前判断和决定性的缺口。随后只保留支撑判断或执行下一步必需的依据、条件和步骤。句子简洁具体，每个观点只表达一次，删除铺垫、同义转述和无必要的例子。结尾不再总结或复述。必须保留会改变结论的限定与反证；用户明确要求详细说明时提供所需深度。'),
    'expansive':('思维拓展','在回答核心问题的基础上，主动寻找相关的跨领域知识、类比、替代解释、不同尺度和方法，帮助用户看到新的可能性。挑选有启发价值的方向，解释联系为什么成立、适用边界在哪里、如何进一步核实；不要只罗列名词。明确标示背景知识和探索性假设，不把类比当证明，也不把外部联想冒充当前资料支持的结论。')}

# Retain exact v1 text for existing queued jobs and historical generation snapshots.
# New answers use v2; selecting an old answer never rewrites its stored contract.
_MODES_V1=MODES.copy()
MODES=dict(MODES,efficient=('高效务实','这是精简输出模式，不只是先说结论。先用一两句给出判断，再写支持判断的关键理由和最优先的行动。普通问题通常一至三个短段；复杂比较或实验设计通常三至五个紧凑要点，按任务需要调整而不凑数。用户只说“解释推理”或“给出实验设计”不等于要求长文。保留会改变结论的条件、反证和必要执行细节，但不要把通用研究检查清单逐项展开成教程。优先给一个可执行方案；备选只有会改变决策时才补充。每个观点只表达一次，不在开头、依据、限制和结尾反复换说法。完成实质任务就结束，删除末尾再次总结、重复结论与固定追加提问。只有用户明确要求详尽展开、完整长报告或指定长度时才按要求增加篇幅；精炼不能删掉必要证据或掩盖不确定性。'))
_MODES_V2=MODES.copy()
MODES={
    'professional':('专业可靠',_MODES_V2['professional'][1]+' 先给当前能成立的判断，再解释会改变决定的依据与限制。把同一限制讲清一次；不为显得严谨反复重述。'),
    'candid':('直言不讳',_MODES_V2['candid'][1]+' 开门见山指出决定或方法中最关键的漏洞；不用贬损、责备或揣测用户能力的语气。给出修正路径后结束。'),
    'efficient':('高效务实',_MODES_V2['efficient'][1]+' 优先级是完成实质任务、保留决定性条件、消除重复。结论、依据、行动各承担不同信息，不把同一个警告换措辞写三遍。无需逐项展开已被当前问题排除的通用检查。'),
    'expansive':('思维拓展',_MODES_V2['expansive'][1]+' 先完成资料能够回答的核心判断，再补最有价值的拓展。每个新方向标明是已有证据、外部背景还是待检验设想；没有来源的数值不能写成采用标准。')}
_MODE_VERSIONS={'MemoAnswerStyle-v1':_MODES_V1,'MemoAnswerStyle-v2':_MODES_V2,MODE_VERSION:MODES}

def _modes(version):
    if not isinstance(version,str) or version not in _MODE_VERSIONS:raise ValueError('ANSWER_STYLE_VERSION_INVALID')
    return _MODE_VERSIONS[version]

def validate_style(style):
    if not isinstance(style,str) or style not in MODES:raise ValueError('ANSWER_STYLE_INVALID')
    return style

def instructions(style,*,override=False,mode_version=MODE_VERSION):
    validate_style(style)
    priority=('本次明确选择的回答风格替代原问题和旧对话中的旧风格偏好；保留原问题的实质任务、明确长度和格式约束。' if override else '本次问题中明确的长度、格式和临时表达要求优先细化默认风格；历史语气偏好不覆盖当前选择。')
    return COMMON+'\n\n'+_modes(mode_version)[style][1]+'\n'+priority+'\n这些偏好只约束表达，不改变证据、引用、JSON契约、工具权限、模型参数或人工确认要求。'

def effective_override(value):
    source=value.get('source','SAVED_DEFAULT')
    if source not in {'SAVED_DEFAULT','RETRY_ORIGINAL','RETRY_SELECTION'}:raise ValueError('ANSWER_STYLE_SOURCE_INVALID')
    override=value.get('overrides_question_style',source=='RETRY_SELECTION')
    if type(override) is not bool:raise ValueError('ANSWER_STYLE_PRIORITY_INVALID')
    if (source=='SAVED_DEFAULT' and override) or (source=='RETRY_SELECTION' and not override):raise ValueError('ANSWER_STYLE_PRIORITY_INVALID')
    return override

def snapshot(style=DEFAULT_STYLE,*,preference_revision=0,source='SAVED_DEFAULT',overrides_question_style=None,mode_version=MODE_VERSION):
    validate_style(style)
    value={'source':source}
    if overrides_question_style is not None:value['overrides_question_style']=overrides_question_style
    override=effective_override(value)
    text=instructions(style,override=override,mode_version=mode_version)
    return dict(style_id=style,label=MODES[style][0],common_version=COMMON_VERSION,mode_version=mode_version,
                preference_revision=preference_revision,source=source,priority_version=PRIORITY_VERSION,
                overrides_question_style=override,instruction_sha256=hashlib.sha256(text.encode()).hexdigest())

def from_snapshot(value):
    if not isinstance(value,dict):raise ValueError('ANSWER_STYLE_SNAPSHOT_INVALID')
    override=effective_override(value)
    expected=snapshot(value.get('style_id'),preference_revision=value.get('preference_revision',0),source=value.get('source','SAVED_DEFAULT'),overrides_question_style=override,mode_version=value.get('mode_version'))
    # Existing queued snapshots retain their original instruction identity.
    # Only new retries inherit corrected priority; history is never rewritten.
    if 'priority_version' not in value and 'overrides_question_style' not in value:
        expected.pop('priority_version');expected.pop('overrides_question_style')
    if value!=expected:raise ValueError('ANSWER_STYLE_SNAPSHOT_INVALID')
    return instructions(value['style_id'],override=override,mode_version=value['mode_version'])
