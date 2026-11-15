/* E2 additions inside the adopted shell; no second application or demo data. */
(() => {
  const q=s=>document.querySelector(s);
  const copy={
    'GLOBAL_PASS_USES_SELECTED_SOURCE_LINES_AND_LOCAL_INVENTORIES_NOT_WHOLE_DOCUMENT_REREAD':['Cross-section comparisons use selected original passages and the inventories from all local passes; this does not constitute a second full-text review.','節をまたぐ比較には各区間の確認結果と選択した原文を使用しており、全文を再読した確認ではありません。','跨段关系核查依据各段清单和选取的原文片段，未在此阶段再次逐行核查全文。'],
    '数值核查规则':['Numeric check rules','数値確認ルール'],'规则':['Rule','ルール'],'添加规则':['Add rule','ルール追加'],'移除':['Remove','削除'],
    '保存规则':['Save rules','ルールを保存'],'适用条件依据':['Basis for these conditions','適用条件の根拠'],'未指定':['Unspecified','未指定'],
    '是':['Yes','はい'],'否':['No','いいえ'],'原样数值':['Exact numeric text','数値の原表記'],'原文片段':['Exact source quote','原文引用'],
    '多个数值每行一个；片段必须能唯一定位。':['One value per line; the quote must identify each value uniquely.','数値は一行ずつ入力し、引用で一意に特定してください。'],
    '只填写有明确依据的参数。未填写的项目保留为信息不足。':['Supply only parameters supported by evidence. Omitted fields remain insufficient information.','根拠のある条件のみ入力してください。未記入は情報不足として保持します。'],
    '未覆盖范围':['Not covered','対象外の範囲'],'每个参数只允许一个数值。':['Each parameter accepts one value.','各条件に入力できる数値は一つです。'],'数值必须附上原文片段。':['A value requires an exact source quote.','数値には原文引用が必要です。'],
    'Card 缺项辅助定位':['Locate an omitted Card field','Card の欠落項目を特定'],'Card 字段位置':['Card field location','Card の項目位置'],'原文数值标签':['Exact source value label','原文の数値ラベル'],'PDF 页码（最多两页）':['PDF pages (at most two)','PDF ページ（最大二ページ）'],
    '只在对应 Card 项被删去且本规则缺少此参数时回查；先查原文锚点，再查指定 PDF 页，不改写 Card。':['Used only when the matching Card field was removed and this rule lacks the parameter. Checks source anchors first, then the specified PDF pages, without changing the Card.','対応する Card 項目が削除され、このルールの条件が不足する場合のみ使用します。原文の引用を先に確認し、次に指定 PDF ページを参照します。Card は変更しません。'],
    '请同时填写 Card 位置和数值标签。':['Supply both the Card location and exact value label.','Card の位置と数値ラベルの両方を入力してください。'],'页码须为一至两个正整数。':['Enter one or two positive page numbers.','正のページ番号を一つまたは二つ入力してください。'],
    'scientific meaning':['Scientific interpretation','科学的な解釈','科研含义解释'],'images':['Images','画像','图像'],'unloaded supplements':['Unloaded supplementary materials','未読込の補足資料','未装入的补充材料'],'whole-paper authenticity':['Whole-paper authenticity','論文全体の真正性','整篇论文真实性'],'digit-frequency hypothesis tests':['Digit-frequency hypothesis tests','数字の頻度に関する仮説検定','末位频率假设检验'],'pixel image forensics':['Image pixel forensics','画像ピクセルの鑑定','图像像素取证'],'author investigation':['Investigation of authors','著者の調査','作者调查'],
    '已检查范围':['Checked scope','確認範囲'],'原文行数':['Source lines','原文行数'],'数值对象':['Numeric objects','数値項目'],'已完成核查':['Completed checks','完了した確認'],
    '最小值':['Minimum','最小値'],'最大值':['Maximum','最大値'],'不同对象的数值描述，不代表同一研究样本。':['Values may describe different objects; this is not a single research sample.','異なる対象の数値を含み、一つの研究標本を表しません。'],
    '数值与原文':['Values and source','数値と原文'],'核查结果':['Check results','確認結果'],'公式':['Formula','式'],'期待区间':['Expected interval','期待区間'],'报告区间':['Reported interval','報告区間'],
    '条件相容':['Compatible under the conditions','条件下で整合'],'条件不相容':['Inconsistent under the conditions','条件下で不整合'],'信息不足':['Insufficient information','情報不足'],'输入问题':['Input issue','入力問題'],'不适用':['Not applicable','適用外'],
    '缺少参数':['Missing parameters','不足する条件'],'原始计算记录':['Calculation record','計算記録'],'更多数值':['More values','さらに表示'],'人工意见记录':['Disposition history','判断履歴'],'原文定位':['Locate source','原文を表示'],
    '参数':['Parameters','条件'],'原文出处':['Source reference','原文の出典'],'核查项目':['Finding','確認項目'],'处理方式':['Disposition','処理方法'],
    '逻辑分析':['Logic review','論理分析'],'数据分析':['Data analysis','データ分析'],
    '原文就绪后排队，使用已配置的云端模型。':['Queues when the source is ready, using the configured cloud model.','原文準備後、設定済みクラウドモデルで実行します。'],
    '已随任务冻结；更改只适用于新任务。':['Frozen for this task; changes require a new task.','このタスクで固定済みです。変更には新しいタスクが必要です。'],
    '已完成':['Completed','完了'],'失败':['Failed','失敗'],'结果待确认':['Outcome uncertain','結果を確認してください'],'报告登记失败':['Report publication failed','報告の登録に失敗'],'已跳过':['Skipped','スキップ済み'],'已取消':['Cancelled','取消済み'],'等待原文':['Waiting for source','原文待ち'],'排队':['Queued','待機中'],'执行中':['Running','実行中'],'已保存':['Saved','保存済み'],
    '暂停':['Pause','一時停止'],'继续':['Resume','再開'],'重试':['Retry','再試行'],'取消':['Cancel','キャンセル'],'跳过':['Skip','スキップ'],
    '主线已完成，正在等待附加核查。':['Main workflow complete; waiting for review.','主処理完了。追加分析を待機しています。'],
    '结果不确定；重试可能产生新的调用与费用。':['Outcome uncertain; retry may create another call and charge.','結果不明です。再試行は追加呼び出しと費用を生む可能性があります。'],
    '已检查范围未见重要疑点':['No important concerns in the checked scope','確認範囲に重要な疑問点なし'],
    '存在需要澄清的疑点':['Concerns need clarification','説明を要する疑問点あり'],
    '存在重要疑点，建议优先复核':['Important concerns; prioritize review','重要な疑問点あり。優先的な確認を推奨'],
    '核查受限，无风险色结论':['Limited review; no risk color conclusion','確認範囲に制限あり。色による結論なし'],
    '历史结果：输入或任务版本已变化':['Historical result: input or task version changed','履歴結果：入力またはタスクの版が変更済み'],
    '关闭':['Close','閉じる'],'已解释':['Explained','説明済み'],'确认报告问题':['Confirmed report issue','報告の問題を確認'],
    '待补材料':['Needs material','資料待ち'],'暂缓':['Deferred','保留'],'重新打开':['Reopened','再検討'],
    '记录人工意见':['Record disposition','判断を記録'],'理由':['Reason','理由'],'依据或出处':['Evidence or source','根拠・出典'],
    '保存意见':['Save disposition','判断を保存'],'机器报告保留不变，人工意见另行记录。':['The machine report stays unchanged; dispositions are recorded separately.','機械報告を保持し、人の判断を別途記録します。']
  };
  const t=s=>copy[s]?.[document.documentElement.lang.startsWith('en')?0:document.documentElement.lang.startsWith('ja')?1:2]||s;
  Object.assign(copy,{'数值列摘要':['Numeric column summaries','数値列の要約'],'字面加减对':['Literal plus/minus pairs','原表記のプラスマイナス対'],
    '跨章节关系':['Cross-section relationships','章をまたぐ関係'],'全文原文已联合检查':['Full source reviewed together','全文をまとめて確認済み'],
    '跨段原文关系已检查':['Cross-segment source relationships checked','分割間の原文関係を確認済み'],'全局关系检查受限':['Global relationship review limited','全体の関係確認に制限あり'],
    'literal_arithmetic':['Printed arithmetic','記載された算術式','原文明确算术关系'],'numeric_domain':['Named numeric domain','明示された数値領域','明确标签的数值范围'],
    '自动识别明确格式并提供数值摘要；规则表单用于补充条件。Card 缺口会按原文锚点自动关联，可选定位仅用于补充。':['Supported explicit formats and numeric summaries run automatically. Rules and Card-gap locators are optional additions.','明示された形式と数値要約は自動処理します。ルールと Card の位置指定は任意の補足です。']});
  const call=async(method,params={})=>{
    if(!window.pywebview?.api?.call)throw Error('NATIVE_TRANSPORT_REQUIRED');
    return window.pywebview.api.call(method,params);
  };
  const el=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  let itemId=null,selectionSequence=0;
  const inbox=async id=>{
    const seq=++selectionSequence;const box=q('#inbox-logic-analysis-option');if(!box)return;
    box.hidden=true;if(!id||!window.pywebview?.api?.call)return;
    const item=await call('inbox.item_detail',{item_id:id});if(seq!==selectionSequence)return;
    itemId=id;box.hidden=item.item_kind==='SESSION_REFINEMENT';
    const input=q('#inbox-logic-analysis');input.checked=Boolean(item.logic_review_selected);input.disabled=item.review_choice_frozen===true;
    q('#inbox-logic-analysis-help').textContent=t(input.disabled?'已随任务冻结；更改只适用于新任务。':'原文就绪后排队，使用已配置的云端模型。');
    let rules=q('#paper-review-binding-button');if(!rules){rules=el('button',t('数值核查规则'),'soft-button');rules.id='paper-review-binding-button';rules.type='button';box.append(rules);}
    rules.textContent=t('数值核查规则')+' · '+(item.data_review_bindings||[]).length;
    rules.onclick=()=>openBindings(id,item.data_review_bindings||[],item.review_choice_frozen===true);
  };
  q('#inbox-logic-analysis')?.addEventListener('change',async e=>{
    const selected=e.target.checked,id=itemId;e.target.disabled=true;
    try{await call('inbox.set_logic_review',{item_id:id,selected});await inbox(id);}
    catch(error){e.target.checked=!selected;q('#inbox-logic-analysis-help').textContent=String(error.message);await inbox(id);}
  });
  document.addEventListener('click',e=>{
    const card=e.target.closest('.desktop-inbox-card[data-item-id]');
    if(card)void inbox(card.dataset.itemId).catch(error=>{q('#inbox-logic-analysis-help').textContent=error.message;});
    if(e.target.closest('#inbox-close-detail')){selectionSequence++;q('#inbox-logic-analysis-option').hidden=true;}
  });
  const tones={GREEN:'green',YELLOW:'yellow',RED:'red'};
  const badgeReports=new WeakMap();
  const riskText=report=>t(report.stale?'历史结果：输入或任务版本已变化':report.color==='RED'?'存在重要疑点，建议优先复核':report.color==='YELLOW'?'存在需要澄清的疑点':report.color==='GREEN'?'已检查范围未见重要疑点':'核查受限，无风险色结论');
  const reportButton=report=>{
    const button=el('button',null,'text-button paper-review-report-risk');button.type='button';button.dataset.reviewReportId=report.report_id;
    badgeReports.set(button,report);
    const dot=el('span',null,'status-dot '+(tones[report.color]||'neutral'));dot.setAttribute('aria-hidden','true');
    button.append(dot,document.createTextNode(riskText(report)));button.setAttribute('aria-label',riskText(report));
    button.addEventListener('click',()=>void openReport(report.report_id).catch(error=>{button.title=error.message;button.textContent=error.message;}));return button;
  };
  const ruleNames={percentage:'比例与计数',sum:'分项合计',sd_se:'SD / SE 关系',cross:'同一量交叉核对',grid_mean:'离散量均值',test_p:'统计量与 p 值',raw_summary:'原始数值摘要'};
  const inputNames={count:'计数',denominator:'分母',percent:'百分比',parts:'分项数值',total:'合计',sd:'SD',se:'SE',n:'n',left:'左侧数值',right:'右侧数值',mean:'均值',statistic:'统计量',p:'p 值',values:'原始数值'};
  const paramNames={unweighted_count:'未加权计数',rounding:'舍入方式',mutually_exclusive:'各部分互斥',exhaustive:'各部分穷尽总体',definition:'SE 定义',same_quantity:'明确为同一量',step:'离散步长',origin:'起点',equal_weights:'等权',distribution:'分布',tail:'尾部',adjustment:'校正',df:'自由度',df1:'分子自由度',df2:'分母自由度',missing_policy:'缺失值处理',weights:'权重',sd_denominator:'标准差定义'};
  const spec={percentage:[['count','denominator','percent'],['unweighted_count','rounding']],sum:[['parts','total'],['mutually_exclusive','exhaustive','rounding']],sd_se:[['sd','se','n'],['definition','rounding']],cross:[['left','right'],['same_quantity','rounding']],grid_mean:[['mean','n'],['step','origin','equal_weights','rounding']],test_p:[['statistic','p'],['distribution','tail','adjustment','df','df1','df2','rounding']],raw_summary:[['values'],['missing_policy','weights','sd_denominator']]};
  const choices={rounding:[['nearest','四舍五入'],['toward_zero','向零截断']],definition:[['independent_equal_weight_mean','独立、等权均值']],distribution:[['normal','正态'],['t','t'],['chi_square','卡方'],['f','F']],tail:[['two_sided','双侧'],['upper','上尾'],['lower','下尾']],adjustment:[['none','未校正']],missing_policy:[['no_missing','无缺失']],weights:[['equal','等权']],sd_denominator:[['sample','样本'],['population','总体']]};
  Object.assign(copy,{
    '比例与计数':['Proportion and counts','割合と件数'],'分项合计':['Sum of parts','内訳の合計'],'SD / SE 关系':['SD / SE relationship','SD / SE の関係'],'同一量交叉核对':['Cross-check the same quantity','同じ量の照合'],'离散量均值':['Discrete mean','離散値の平均'],'统计量与 p 值':['Statistic and p value','統計量と p 値'],'原始数值摘要':['Raw-value summary','生データの要約'],
    '计数':['Count','件数'],'分母':['Denominator','分母'],'百分比':['Percent','割合'],'分项数值':['Parts','内訳'],'合计':['Total','合計'],'左侧数值':['Left value','左の値'],'右侧数值':['Right value','右の値'],'均值':['Mean','平均'],'统计量':['Statistic','統計量'],'p 值':['p value','p 値'],'原始数值':['Raw values','生データ'],
    '未加权计数':['Unweighted counts','重みなしの件数'],'舍入方式':['Rounding','丸め方'],'各部分互斥':['Mutually exclusive parts','互いに排他的な内訳'],'各部分穷尽总体':['Exhaustive parts','全体を網羅する内訳'],'SE 定义':['SE definition','SE の定義'],'明确为同一量':['Explicitly the same quantity','同じ量と明示'],'离散步长':['Discrete step','刻み幅'],'起点':['Origin','原点'],'等权':['Equal weights','等しい重み'],'分布':['Distribution','分布'],'尾部':['Tail','裾'],'校正':['Adjustment','補正'],'自由度':['Degrees of freedom','自由度'],'分子自由度':['Numerator df','分子の自由度'],'分母自由度':['Denominator df','分母の自由度'],'缺失值处理':['Missing values','欠測値の扱い'],'权重':['Weights','重み'],'标准差定义':['SD definition','標準偏差の定義'],
    '四舍五入':['Nearest','最近接丸め'],'向零截断':['Toward zero','ゼロ方向に切り捨て'],'独立、等权均值':['Independent, equally weighted mean','独立・等加重の平均'],'正态':['Normal','正規'],'卡方':['Chi-square','カイ二乗'],'双侧':['Two-sided','両側'],'上尾':['Upper','上側'],'下尾':['Lower','下側'],'未校正':['Unadjusted','補正なし'],'无缺失':['No missing values','欠測なし'],'样本':['Sample','標本'],'总体':['Population','母集団']
  });
  function openBindings(id,saved,frozen){
    const d=el('dialog',null,'paper-review-dialog');(q('#desktop-inbox-preview')||document.body).append(d);const head=el('div',null,'toolbar-group'),close=el('button',t('关闭'),'soft-button');close.type='button';close.onclick=()=>d.close();head.append(el('h3',t('数值核查规则')),close);d.append(head,el('p',t('只填写有明确依据的参数。未填写的项目保留为信息不足。')));
    d.append(el('p',t('自动识别明确格式并提供数值摘要；规则表单用于补充条件。Card 缺口会按原文锚点自动关联，可选定位仅用于补充。')));
    const form=el('form'),list=el('div');d.append(form);form.append(list);const entries=[];
    const field=(parent,title,control)=>{const label=el('label',t(title),'paper-review-field');control.setAttribute('aria-label',t(title));control.disabled=frozen;label.append(control);parent.append(label);return control;};
    function add(rule={}){
      const section=el('fieldset');list.append(section);const type=el('select',null,'select');for(const [key,name] of Object.entries(ruleNames)){const o=el('option',t(name));o.value=key;type.append(o);}type.value=rule.type||'percentage';field(section,'规则',type);
      const body=el('div'),basis=el('textarea'),remove=el('button',t('移除'),'soft-button');basis.value=rule.binding_source?.note||'';basis.required=true;basis.maxLength=2000;section.append(body);field(section,'适用条件依据',basis);remove.type='button';remove.disabled=frozen;section.append(remove);
      const entry={section,type,basis,rule,fields:{},parameters:{}};entries.push(entry);remove.onclick=()=>{section.remove();entries.splice(entries.indexOf(entry),1);};
      function render(){body.replaceChildren();entry.fields={};entry.parameters={};const definition=spec[type.value];entry.gaps={};
        for(const key of definition[0]){const group=el('fieldset');group.append(el('legend',t(inputNames[key]||key)));body.append(group);const v=el('textarea'),quote=el('textarea'),existing=rule.inputs?.[key];const array=Array.isArray(existing)?existing:existing?[existing]:[];v.value=array.map(a=>a.raw||'').join('\n');quote.value=array[0]?.quote||'';v.maxLength=10000;quote.maxLength=4000;field(group,'原样数值',v);field(group,'原文片段',quote);if(['parts','values'].includes(key))group.append(el('small',t('多个数值每行一个；片段必须能唯一定位。')));entry.fields[key]={v,quote,existing};
          if(!['parts','values'].includes(key)){const old=(rule.card_gap_bindings||[]).find(g=>g.parameter===key)||{},details=el('details');details.append(el('summary',t('Card 缺项辅助定位')),el('p',t('只在对应 Card 项被删去且本规则缺少此参数时回查；先查原文锚点，再查指定 PDF 页，不改写 Card。')));group.append(details);
            const location=el('input'),label=el('input'),pages=el('input');location.value=old.card_location||'';label.value=old.label||'';pages.value=(old.pages||[]).join(', ');location.maxLength=300;label.maxLength=80;pages.maxLength=30;
            field(details,'Card 字段位置',location);field(details,'原文数值标签',label);field(details,'PDF 页码（最多两页）',pages);entry.gaps[key]={old,location,label,pages};}
        }
        for(const key of definition[1]){const boolean=['unweighted_count','mutually_exclusive','exhaustive','same_quantity','equal_weights'].includes(key),options=boolean?[[true,'是'],[false,'否']]:choices[key];const c=el(options?'select':'input',null,options?'select':'');if(options){const unknown=el('option',t('未指定'));unknown.value='';c.append(unknown);for(const [v,name] of options){const o=el('option',t(name));o.value=String(v);c.append(o);}}else{c.type='text';c.maxLength=100;}c.value=rule.parameters?.[key]===undefined?'':String(rule.parameters[key]);field(body,paramNames[key]||key,c);entry.parameters[key]={c,boolean};}
      }type.onchange=()=>{rule={};entry.rule={};render();};render();
    }
    for(const r of saved)add(r);const addButton=el('button',t('添加规则'),'soft-button');addButton.type='button';addButton.disabled=frozen;addButton.onclick=()=>add();const save=el('button',t('保存规则'),'primary-button');save.type='submit';save.disabled=frozen;const status=el('p');status.setAttribute('role','status');form.append(addButton,save,status);
    form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{const bindings=entries.map(e=>{const inputs={},parameters={};for(const [k,x] of Object.entries(e.fields)){const values=x.v.value.split('\n').map(v=>v.trim()).filter(Boolean);if(values.length){if(!['parts','values'].includes(k)&&values.length!==1)throw Error(t('每个参数只允许一个数值。'));if(!x.quote.value.trim())throw Error(t('数值必须附上原文片段。'));
        const prior=Array.isArray(x.existing)?x.existing:[x.existing];const selectors=values.map((raw,i)=>prior[i]?.raw===raw&&prior[i]?.quote===x.quote.value.trim()?prior[i]:{raw,quote:x.quote.value.trim()});inputs[k]=['parts','values'].includes(k)?selectors:selectors[0];}else if(typeof x.existing==='string')inputs[k]=x.existing;}for(const [k,x] of Object.entries(e.parameters))if(x.c.value!=='')parameters[k]=x.boolean?x.c.value==='true':x.c.value;const gaps=[];for(const [parameter,g] of Object.entries(e.gaps)){const location=g.location.value.trim(),label=g.label.value.trim(),pageText=g.pages.value.trim();if(!location&&!label&&!pageText)continue;if(!location||!label)throw Error(t('请同时填写 Card 位置和数值标签。'));const pages=pageText?pageText.split(/[,，\s]+/).map(Number):[];if(pages.length>2||pages.some(n=>!Number.isSafeInteger(n)||n<1)||new Set(pages).size!==pages.length)throw Error(t('页码须为一至两个正整数。'));gaps.push({...g.old,parameter,card_location:location,label,pages});}
        return {...e.rule,card_gap_bindings:gaps,rule_id:e.rule.rule_id||'user-'+crypto.randomUUID(),type:e.type.value,inputs,parameters,binding_source:{kind:'EXPLICIT_USER_MAPPING',note:e.basis.value.trim()}};});await call('inbox.set_data_bindings',{item_id:id,bindings});d.close();await inbox(id);}catch(error){status.textContent=error.message;save.disabled=false;}};
    d.addEventListener('close',()=>d.remove(),{once:true});d.showModal();close.focus();
  }
  let dialog;
  Object.assign(copy,{'核查报告':['Review report','確認レポート'],
    'Meaningful explicit conditions are missing; priority escalation is not supported.':['Key conditions are missing; clarification is required before priority review.','重要な条件が未記載のため、優先確認とする前に明確化が必要です。','关键条件未写明，暂列为待澄清。'],
    'Priority prerequisites are incomplete.':['The prerequisites for priority review are incomplete.','優先確認の前提条件が不足しています。','优先复核的前置条件不完整，暂列为待澄清。']});
  Object.assign(copy,{"Check conversion, labels and the supplied method binding.": ["Check conversion, labels and the supplied method binding.", "換算、ラベル、指定された手法との対応を確認してください。", "请核对换算、标签和所提供的方法绑定。"], "Reported values do not overlap under the supplied conditions.": ["Reported values do not overlap under the supplied conditions.", "指定された条件では報告値の区間が重なりません。", "在所提供的条件下，报告数值的区间不重叠。"], "Descriptive only; mixed numeric objects are not a research sample; no uniform-digit assumption.": ["Descriptive only; mixed numeric objects are not a research sample; no uniform-digit assumption.", "記述のみです。異なる数値項目は一つの研究標本ではなく、数字の一様分布も仮定しません。", "仅作描述；不同数值对象不构成同一研究样本，也不假设数字均匀分布。"], "No explicit rule bindings; numeric descriptions are available.": ["No explicit rule bindings; numeric descriptions are available.", "明示的なルールの対応付けはありません。数値の記述は利用できます。", "没有明确的规则绑定；可查看数值描述。"]});
  async function openReport(id){
    const library=q('#desktop-library-preview');
    if(!library||library.closest('.desktop-route-surface')?.hidden)return;
    const data=await call('review.report',{report_id:id});const report=data.report;
    if(!dialog){dialog=el('section',null,'paper-review-panel');dialog.setAttribute('aria-label',t('核查报告'));dialog.tabIndex=-1;}
    const host=library.querySelector('#desktop-right-panel .inspector-inner');if(!host)return;
    if(dialog.parentElement!==host)host.append(dialog);
    dialog.hidden=false;dialog.dataset.reportId=id;
    dialog.replaceChildren();const head=el('div',null,'toolbar-group');const close=el('button',t('关闭'),'soft-button');close.type='button';close.onclick=()=>{dialog.hidden=true;library.querySelector('.paper-review-report-risk')?.focus();};
    head.append(el('h3',t(report.kind==='LOGIC'?'逻辑分析':'数据分析')),close);dialog.append(head,el('p',riskText({...report,...data.source_status})),el('p',t('机器报告保留不变，人工意见另行记录。')));
    const body=el('div',null,'paper-review-source-body');body.setAttribute('translate','no');dialog.append(body);
    const sourceLocale=report.language_context?.template_locale||report.language_context?.effective_output_locale||'en-US';
    const rt=s=>copy[s]?.[sourceLocale==='ja-JP'?1:sourceLocale==='en-US'?0:2]||s;
    const scope=report.scope||{},summary=el('dl',null,'paper-review-facts');body.append(el('h4',rt('已检查范围')),summary);
    const fact=(title,value)=>{if(value!=null){summary.append(el('dt',rt(title)),el('dd',String(value)));}};
    fact('原文行数',scope.processed_lines);fact('数值对象',scope.numeric_objects);fact('已完成核查',scope.completed_checks==null?null:scope.completed_checks+' / '+scope.planned_checks);
    fact('缺少参数',scope.missing_parameter_count);
    if(report.descriptions){fact('最小值',report.descriptions.minimum);fact('最大值',report.descriptions.maximum);body.append(el('p',rt('不同对象的数值描述，不代表同一研究样本。')));}
    if(report.numeric_blocks){const block=el('details');const columns=report.numeric_blocks.table_columns||[];block.append(el('summary',rt('数值列摘要')+' · '+columns.length));for(const c of columns)block.append(el('p',c.header_literal+' · L'+c.header_line+' · '+c.record_count+' · '+c.minimum+' — '+c.maximum));block.append(el('p',rt('字面加减对')+' · '+(report.numeric_blocks.plus_minus_pairs||[]).length),el('p',rt('不同对象的数值描述，不代表同一研究样本。')));body.append(block);}
    if(scope.global_relationship_check){const global=scope.global_relationship_check;body.append(el('h4',rt('跨章节关系')),el('p',rt(global.status==='FULL_SOURCE_CHECKED'?'全文原文已联合检查':global.status==='CHECKED'?'跨段原文关系已检查':'全局关系检查受限')));for(const relation of global.checks||[]){const d=el('details');d.append(el('summary',relation.relation),el('p',relation.reasoning));for(const c of relation.citations||[])d.append(el('blockquote','L'+c.line+' · '+c.quote,'paper-review-source'));body.append(d);}}
    for(const limitation of scope.limitations||[])body.append(el('p',rt(limitation)));
    if(scope.not_run?.length)body.append(el('h4',rt('未覆盖范围')),el('p',scope.not_run.map(rt).join(' · ')));
    for(const note of report.missing_parameters||[])body.append(el('p',note));
    const statuses={COMPATIBLE:'条件相容',INCONSISTENT:'条件不相容',INSUFFICIENT_INFORMATION:'信息不足',INPUT_ERROR:'输入问题',NOT_APPLICABLE:'不适用'};
    if(report.checks?.length)body.append(el('h4',rt('核查结果')));
    const records=new Map([...(report.records||[]),...(report.supplemental_records||[])].map(r=>[r.record_id,r]));
    for(const check of report.checks||[]){const d=el('details',null,'paper-review-check');d.open=check.status!=='COMPATIBLE';d.append(el('summary',rt(ruleNames[check.type]||check.type)+' · '+rt(statuses[check.status]||check.status)));
      if(check.reason)d.append(el('p',check.reason));
      if(check.calculation?.formula)d.append(el('p',rt('公式')+'：'+check.calculation.formula));
      for(const key of ['expected','expected_interval','reported_interval'])if(check.calculation?.[key]!=null)d.append(el('p',rt(key==='reported_interval'?'报告区间':'期待区间')+'：'+[].concat(check.calculation[key]).join(' — ')));
      if(check.missing?.length)d.append(el('p',rt('缺少参数')+'：'+check.missing.map(k=>rt(inputNames[k.split('.')[1]]||paramNames[k.split('.')[1]]||k)).join('、')));
      if(check.reason)d.append(el('p',check.reason));
      for(const id of check.input_ids||[]){const row=records.get(id);if(row)d.append(el('blockquote',row.raw+' · L'+(row.source.line||'?')+'\n'+row.source.quote,'paper-review-source'));}body.append(d);
    }
    for(const finding of report.findings||[]){if(!finding.claim)continue;const section=el('section',null,'paper-review-finding');section.append(el('h4',finding.claim),el('p',rt(finding.reasoning)),el('p',rt(finding.alternative_explanation)));if(finding.escalation_limited)section.append(el('p',rt(finding.escalation_limited)));for(const ref of finding.citations||[])section.append(el('blockquote','L'+ref.line+' · '+ref.quote,'paper-review-source'));if(finding.missing_material?.length)section.append(el('p',rt('待补材料')+'：'+finding.missing_material.join('、')));body.append(section);}
    if(report.records?.length){const disclosure=el('details');disclosure.append(el('summary',rt('数值与原文')+' · '+report.records.length));const list=el('div');const more=el('button',rt('更多数值'),'soft-button');more.type='button';let count=0;const next=()=>{for(const r of report.records.slice(count,count+30))list.append(el('p',r.raw+(r.unit_literal?' '+r.unit_literal:'')+' · L'+r.source.line+' · '+r.source.quote,'paper-review-source'));count+=30;more.hidden=count>=report.records.length;};more.onclick=next;disclosure.append(list,more);disclosure.addEventListener('toggle',()=>{if(disclosure.open&&!count)next();});body.append(disclosure);}
    if(data.dispositions.length){dialog.append(el('h4',t('人工意见记录')));for(const item of data.dispositions)dialog.append(el('p',t(({EXPLAINED:'已解释',CONFIRMED_REPORT_ISSUE:'确认报告问题',NEEDS_MATERIAL:'待补材料',DEFERRED:'暂缓',REOPENED:'重新打开'})[item.action]||item.action)+' · '+item.reason+'\n'+(item.evidence||''),'paper-review-source'));}
    if(report.findings?.length){
      const form=el('form');form.append(el('h4',t('记录人工意见')));const select=el('select',null,'select');select.setAttribute('aria-label',t('核查项目'));
      for(const f of report.findings){const check=report.checks?.find(c=>c.rule_id===f.rule_id);const option=el('option',f.claim||t(ruleNames[check?.type]||f.rule_id));option.value=f.finding_id;select.append(option);}
      const action=el('select',null,'select');action.setAttribute('aria-label',t('处理方式'));
      for(const [value,name] of [['EXPLAINED','已解释'],['CONFIRMED_REPORT_ISSUE','确认报告问题'],['NEEDS_MATERIAL','待补材料'],['DEFERRED','暂缓'],['REOPENED','重新打开']]){const option=el('option',t(name));option.value=value;action.append(option);}
      const reason=el('textarea');reason.required=true;reason.maxLength=4000;reason.setAttribute('aria-label',t('理由'));reason.placeholder=t('理由');
      const evidence=el('textarea');evidence.maxLength=8000;evidence.setAttribute('aria-label',t('依据或出处'));evidence.placeholder=t('依据或出处');
      const save=el('button',t('保存意见'),'primary-button');save.type='submit';const status=el('p');status.setAttribute('role','status');
      form.append(select,action,reason,evidence,save,status);
      form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await call('review.disposition',{report_id:id,finding_id:select.value,action:action.value,reason:reason.value,evidence:evidence.value,operation_id:crypto.randomUUID()});await openReport(id);}catch(error){status.textContent=error.message;save.disabled=false;}};
      dialog.append(form);
    }
    const technical=el('details');technical.append(el('summary','ID / SHA-256'),el('pre',report.report_id+'\n'+report.report_sha256+'\n'+report.source_sha256,'paper-review-source'));dialog.append(technical);
    const calculation=el('details');calculation.append(el('summary',t('原始计算记录')));calculation.addEventListener('toggle',()=>{if(calculation.open&&calculation.childElementCount===1)calculation.append(el('pre',JSON.stringify(report,null,2),'paper-review-source'));});dialog.append(calculation);
    dialog.scrollIntoView({block:'start'});dialog.focus({preventScroll:true});
  }
  const taskLabels={FAILED:'失败',UNCERTAIN:'结果待确认',PUBLICATION_FAILED:'报告登记失败',SKIPPED:'已跳过',CANCELLED:'已取消',WAITING_INPUT:'等待原文',QUEUED:'排队',RUNNING:'执行中',SENDING:'执行中',PUBLISHING:'执行中',COMPLETED:'已完成',COMPLETED_LIMITED:'已完成'};
  function renderTask(binding,flowList,options={}){
    let box=q('#paper-review-logic-task');if(!box){box=el('section',null,'paper-review-logic-task');box.id='paper-review-logic-task';flowList.after(box);}
    const state=binding?.literature_review;box.hidden=!state?.logic_selected;if(box.hidden)return;
    box.dataset.jobId=state.job_id;box.dataset.logicState=state.logic_state;box.setAttribute('aria-label',t('逻辑分析'));
    const failed=['FAILED','UNCERTAIN','PUBLICATION_FAILED'].includes(state.logic_state);
    const tone=state.paused?'yellow':failed?'red':['COMPLETED','COMPLETED_LIMITED'].includes(state.logic_state)?'green':['RUNNING','SENDING','PUBLISHING'].includes(state.logic_state)?'blue':['SKIPPED','CANCELLED'].includes(state.logic_state)?'disabled':'neutral';
    let node=box.querySelector('.paper-review-logic-node');
    if(!node){
      const list=el('ol',null,'desktop-tasks-flow-list'),step=el('li',null,'desktop-tasks-flow-step');node=el('button',null,'desktop-tasks-node paper-review-logic-node');node.type='button';node.setAttribute('aria-expanded','false');node.setAttribute('aria-selected','false');
      const index=el('span',null,'desktop-tasks-node-index'),brain=q('#inbox-logic-analysis-option svg');if(brain)index.append(brain.cloneNode(true));else index.textContent='◇';index.setAttribute('aria-hidden','true');
      const copyNode=el('span',null,'desktop-tasks-node-copy');copyNode.append(el('strong'),el('small',null,'desktop-tasks-node-purpose'),el('small',null,'desktop-tasks-node-model'));
      node.append(index,copyNode);step.append(node);list.append(step);box.append(list);
    }
    const status=t(state.paused?'暂停':taskLabels[state.logic_state]||state.logic_state);
    node.dataset.tone=tone;node.dataset.node=t('逻辑分析');node.dataset.status=status;node.dataset.desktopDynamicTitle=status;node.title=status;node.setAttribute('aria-label',t('逻辑分析')+'，'+status);node.parentElement.className='desktop-tasks-flow-step state-'+tone;
    node.querySelector('strong').textContent=t('逻辑分析');node.querySelector('.desktop-tasks-node-purpose').textContent=t('独立核查文献数据与逻辑');node.querySelector('.desktop-tasks-node-model').textContent=state.model_label||t('已配置的云端模型');
    window.__MEMO_NODE_PROGRESS__?.apply(node,{node_id:'E2_LOGIC_REVIEW',state:(!state.paused&&['RUNNING','SENDING'].includes(state.logic_state))?'RUNNING':state.logic_state,node_progress:state.node_progress},binding);
    node.reviewPanelState=state;node.reviewOwnerControlState=binding.control_state;node.onclick=()=>options.openNode?.(node);
    node.reviewRefresh=updated=>{renderTask({...binding,literature_review:updated},flowList,options);renderTaskInspector(node,q('#desktop-tasks-preview'));};
  }
  function renderTaskInspector(node,root){
    let detail=root.querySelector('#paper-review-task-controls');
    if(!node?.classList.contains('paper-review-logic-node')){if(detail)detail.hidden=true;return false;}
    const state=node.reviewPanelState;if(!state)return false;
    const query=s=>root.querySelector(s),status=node.dataset.status;
    for(const id of ['tasks-delete-task','tasks-node-control','tasks-human','tasks-capacity','tasks-adjust','tasks-adjust-footer','tasks-node-control-feedback'])query('#'+id).hidden=true;
    query('#tasks-node-title').textContent=t('逻辑分析');query('#tasks-state-node').textContent=t('逻辑分析');query('#tasks-state-text').textContent=status;
    query('#tasks-state-config').textContent=state.model_label||t('已配置的云端模型');query('#tasks-state-summary').textContent=t('独立核查文献数据与逻辑');
    const badge=query('#tasks-node-status');badge.className='badge '+node.dataset.tone;badge.textContent=status;
    if(!detail){detail=el('section',null,'inspector-section paper-review-task-controls');detail.id='paper-review-task-controls';query('.desktop-tasks-inspector').append(detail);}
    detail.hidden=false;
    const stamp=[state.job_id,state.revision,state.logic_state,state.paused,state.completed_segments,node.reviewOwnerControlState,document.documentElement.lang].join(':');
    if(detail.dataset.stamp===stamp)return true;detail.dataset.stamp=stamp;detail.replaceChildren();
    const active=['WAITING_INPUT','QUEUED','RUNNING','SENDING','PUBLISHING'].includes(state.logic_state),failed=['FAILED','UNCERTAIN','PUBLICATION_FAILED'].includes(state.logic_state);
    if(state.mainline_complete&&active)detail.append(el('p',t('主线已完成，正在等待附加核查。')));
    if(state.reason_code)detail.append(el('p',state.reason_code));
    if(!['RUNNING','PAUSED'].includes(node.reviewOwnerControlState))return true;
    const actions=[];if(active)actions.push([state.paused?'resume':'pause',state.paused?'继续':'暂停']);if(failed)actions.push(['retry','重试']);if(['WAITING_INPUT','QUEUED','FAILED'].includes(state.logic_state))actions.push(['skip','跳过']);if(active||failed)actions.push(['cancel','取消']);
    let acknowledgement=null;if(state.logic_state==='UNCERTAIN'){const label=el('label');acknowledgement=el('input');acknowledgement.type='checkbox';label.append(acknowledgement,document.createTextNode(t('结果不确定；重试可能产生新的调用与费用。')));detail.append(label);}
    const controls=el('div',null,'toolbar-group');const message=el('p');message.setAttribute('role','status');
    for(const [action,name] of actions){const button=el('button',t(name),'soft-button');button.type='button';button.onclick=async()=>{button.disabled=true;try{const updated=await call('review.control',{job_id:state.job_id,action,expected_revision:state.revision,acknowledge_uncertain:acknowledgement?.checked===true});node.reviewRefresh(updated);}catch(error){message.textContent=error.message;button.disabled=false;}};controls.append(button);}detail.append(controls,message);return true;
  }
  Object.assign(copy,{'独立核查文献数据与逻辑':['Review data and logic independently','データと論理の独立確認'],'已配置的云端模型':['Configured cloud model','設定済みクラウドモデル'],'节点状态与操作':['Node status and controls','ノードの状態と操作'],'已设置暂停点':['Pause point set','一時停止位置を設定済み'],'处理中':['Processing','処理中']});
  const pauseHint=name=>document.documentElement.lang.startsWith('en')?'Will pause before '+name:document.documentElement.lang.startsWith('ja')?name+'の前で一時停止':'将在'+name+'前暂停';
  function renderInboxState(status,row){
    status.replaceChildren();status.classList.toggle('has-pause-hint',row.execution_state==='PAUSE_SCHEDULED');
    if(row.execution_state==='PAUSE_SCHEDULED')status.append(el('span',pauseHint(t(row.pause_node_name||'指定节点')),'paper-review-pause-hint'));
    status.append(el('span',t(row.execution_state==='PAUSE_SCHEDULED'?'处理中':row.display_state||row.state)));
  }
  new MutationObserver(()=>{for(const button of document.querySelectorAll('.paper-review-report-risk')){const report=badgeReports.get(button);if(!report)continue;const dot=button.firstElementChild;button.replaceChildren(dot,document.createTextNode(riskText(report)));button.setAttribute('aria-label',riskText(report));}}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  document.addEventListener('click',event=>{if(dialog&&event.target.closest('#desktop-library-preview .desktop-library-card,#library-close'))dialog.hidden=true;});
  window.__MEMORIVE_REVIEW_UI__={inbox,reportButton,openReport,renderTask,renderTaskInspector,renderInboxState,pauseHint};
})();

