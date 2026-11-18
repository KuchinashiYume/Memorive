/* SHARED report presentation and local due-time settings. */
(() => {
  'use strict';
  if(Memorive.reports)return;
  const labelSets=[['日报','周报','月报'],['Daily Report','Weekly Report','Monthly Report'],['日報','週報','月報']];
  const reportPattern=/^(日报|周报|月报|Daily Report|Weekly Report|Monthly Report|日報|週報|月報)(?=$|\s*·)/;
  function translateText(text,language){
    const locale=language==='en-US'?1:language==='ja-JP'?2:0;
    const progress=text.match(/^(?:研究任务|Research task|研究タスク) · (\d+)\/(\d+) (?:节点|steps|ステップ) · (\d+) (?:份产物|outputs|成果)$/);
    if(progress)return [`研究任务 · ${progress[1]}/${progress[2]} 节点 · ${progress[3]} 份产物`,`Research task · ${progress[1]}/${progress[2]} steps · ${progress[3]} outputs`,`研究タスク · ${progress[1]}/${progress[2]} ステップ · ${progress[3]} 成果`][locale];
    const date=text.match(/^(?:今天|Today|今日) (\d{2}:\d{2})$/);
    if(date)return ['今天','Today','今日'][locale]+' '+date[1];
    const checksum=text.match(/^(?:校验值：|Checksum: |チェックサム: )([a-f0-9]{64})$/i);
    if(checksum)return ['校验值：','Checksum: ','チェックサム: '][locale]+checksum[1];
    const match=text.match(reportPattern);
    if(!match)return null;
    const index=labelSets.find(row=>row.includes(match[1])).indexOf(match[1]);
    return labelSets[locale][index]+text.slice(match[1].length);
  }
  const t=text=>Memorive.app?.translate?.(text)||text;
  const q=selector=>document.querySelector(selector);
  const call=(method,params={})=>Memorive.service.call(method,params);
  const clone=value=>JSON.parse(JSON.stringify(value));
  let saved=null,draft=null,loading=false;
  const dirty=()=>saved!==null&&JSON.stringify(draft)!==JSON.stringify(saved.config);
  function sync(){Memorive.settingsBridge?.syncSaveState();}
  function valid(){
    return draft && ['daily_time','weekly_time','monthly_time'].every(key=>/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(draft[key]))
      && Number.isInteger(draft.weekly_day)&&draft.weekly_day>=0&&draft.weekly_day<7
      && Number.isInteger(draft.monthly_day)&&draft.monthly_day>=1&&draft.monthly_day<=31;
  }
  function render(){
    const root=q('#settings-report-schedule');
    if(!root)return;
    root.querySelectorAll('input,select,button,summary').forEach(node=>{
      if(node.tagName!=='SUMMARY')node.disabled=!saved;
    });
    if(!draft)return;
    for(const key of ['daily_time','weekly_time','monthly_time','weekly_day']){
      const input=root.querySelector('[data-report-field="'+key+'"]');
      if(input.value!==String(draft[key]))input.value=String(draft[key]);
      input.lang=document.documentElement.lang;
    }
    const locale=document.documentElement.lang||'zh-CN';
    const day=draft.monthly_day;
    const caption=locale.startsWith('en')?'Day '+day:locale.startsWith('ja')?'毎月'+day+'日':'每月 '+day+' 日';
    q('#settings-report-month-label').textContent=caption;
    for(const button of root.querySelectorAll('[data-month-day]')){
      button.setAttribute('aria-pressed',String(Number(button.dataset.monthDay)===day));
      button.setAttribute('aria-label',locale.startsWith('en')?'Day '+button.dataset.monthDay:button.dataset.monthDay+'日');
    }
    const status=q('#settings-report-schedule-status');
    if(status)status.textContent=!valid()?t('请选择有效的生成时间'):'';
  }
  async function load(force=false){
    if(loading||(!force&&dirty()))return;
    loading=true;
    try{
      const value=await call('research.schedule_get');
      if(!force&&dirty())return;
      saved=value;
      draft=clone(value.config);
      render();
      sync();
    }catch(_){q('#settings-report-schedule-status').textContent=t('报告时间读取失败，请重新打开设置');}
    finally{loading=false;}
  }
  async function save(){
    if(!valid())throw new Error('REPORT_SCHEDULE_TIME_INVALID');
    const value=await call('research.schedule_save',{config:draft,expected_revision:saved.revision});
    saved=value;
    draft=clone(value.config);
    render();
    sync();
    return value;
  }
  async function reset(){
    if(!saved)await load();
    const value=await call('research.schedule_save',{config:{daily_time:'00:00',weekly_time:'00:00',weekly_day:0,monthly_time:'00:00',monthly_day:1},expected_revision:saved.revision});
    saved=value;
    draft=clone(value.config);
    render();
    sync();
  }
  function init(){
    const root=q('#settings-report-schedule');
    if(!root)return;
    const calendar=q('#settings-report-calendar');
    for(let day=1;day<=31;day++){
      const button=document.createElement('button');
      button.type='button';
      button.dataset.monthDay=String(day);
      button.textContent=String(day);
      button.className='desktop-report-day';
      button.addEventListener('click',()=>{
        if(!draft)return;
        draft.monthly_day=day;
        render();
        sync();
        q('#settings-report-month-picker').open=false;
        q('#settings-report-month-picker summary').focus();
      });
      calendar.append(button);
    }
    for(const input of root.querySelectorAll('[data-report-field]')){
      const changed=event=>{
        event.stopPropagation();
        if(!draft)return;
        draft[input.dataset.reportField]=input.tagName==='SELECT'?Number(input.value):input.value;
        render();
        sync();
      };
      input.addEventListener('change',changed);
      input.addEventListener('input',changed);
    }
    const picker=q('#settings-report-month-picker');
    picker.addEventListener('keydown',event=>{
      if(event.key==='Escape'){
        picker.open=false;
        picker.querySelector('summary').focus();
        event.stopPropagation();
      }
    });
    document.addEventListener('pointerdown',event=>{if(picker.open&&!picker.contains(event.target))picker.open=false;});
    Memorive.settingsControls?.help(q('#settings-report-schedule-label'),'报告生成时间',
      '按 UTC+08:00 汇总上一完整周期；仅在有新成果时生成。关闭期间错过的报告会在下次启动补生成，已生成的周期不重复。月报所选日期不存在时使用当月最后一天。');
    Memorive.settingsBridge?.registerSaveParticipant('report-schedule',{
      isDirty:dirty,validate:valid,save,discard:()=>load(true),afterUnlock:render
    });
    q('[data-category="notice"]')?.addEventListener('click',()=>load());
    window.addEventListener('desktop:settings-locale-change',render);
    new MutationObserver(render).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
    render();
  }
  Memorive.reports=Object.freeze({translateText,load,save,reset,inspect:()=>({saved,draft,dirty:dirty()})});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();

