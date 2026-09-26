'use strict';
const {Plugin,PluginSettingTab,Setting,Modal,Notice,MarkdownView,ItemView,TFile}=require('obsidian');
const {spawn}=require('child_process');
const fs=require('fs'),path=require('path');

function invoke(config,method,params){
  return new Promise((resolve,reject)=>{
    if(!config.helper||!config.workspace)return reject(Error('请先配置 Memorive 工具与工作区'));
    if(!path.isAbsolute(config.helper)||!fs.existsSync(config.helper))return reject(Error('工具路径无效'));
    const child=spawn(config.helper,['--memo-agent','--workspace',config.workspace,method.startsWith('connector.')?'--connector-call':'--call',method],{windowsHide:true,shell:false,stdio:['pipe','pipe','pipe']});
    let output='',error='';const timer=setTimeout(()=>{child.kill();reject(Error('Memorive 工具超时'));},90000);
    child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
    child.stdout.on('data',b=>{output+=b;if(output.length>8*1024*1024){child.kill();reject(Error('结果过大'));}});
    child.stderr.on('data',b=>{error=(error+b).slice(0,1000);});
    child.on('error',e=>{clearTimeout(timer);reject(e);});
    child.on('close',code=>{clearTimeout(timer);if(code)return reject(Error(error||'Memorive 工具未完成'));try{resolve(JSON.parse(output));}catch(e){reject(e);}});
    child.stdin.end(JSON.stringify(params));
  });
}

class ResearchModal extends Modal {
  constructor(app,plugin,selection){super(app);this.plugin=plugin;this.selection=selection||'';}
  onOpen(){
    const root=this.contentEl;root.createEl('h2',{text:'Memo 研究检索'});
    const query=root.createEl('textarea',{attr:{placeholder:'在当前项目中检索…',rows:'3'}});query.value=this.selection.slice(0,4000);
    const status=root.createEl('p');const results=root.createDiv();let refs=[];const selected=new Set();
    const search=root.createEl('button',{text:'检索证据'});
    search.onclick=async()=>{search.disabled=true;status.textContent='正在检索…';try{
      const r=await invoke(this.plugin.settings,'memo.search',{query:query.value,project:this.plugin.settings.project,limit:8});refs=r.results;results.empty();selected.clear();
      for(const ref of refs){
        const card=results.createDiv({cls:'memorive-source'}),choice=card.createEl('input',{type:'checkbox'});choice.type='checkbox';choice.onchange=()=>choice.checked?selected.add(ref.id):selected.delete(ref.id);
        card.createEl('h4',{text:ref.title});card.createEl('p',{text:ref.text});
        card.createEl('small',{text:(ref.page?'p. '+ref.page:'L'+ref.line_start+'–'+ref.line_end)+' · '+ref.id});
        const insert=card.createEl('button',{text:'将引用插入当前笔记'});insert.onclick=async()=>{try{
          const fresh=await invoke(this.plugin.settings,'memo.read_evidence',{evidence_id:ref.id,project:this.plugin.settings.project,expected_hash:ref.content_hash});
          const editor=this.app.workspace.getActiveViewOfType(MarkdownView)?.editor;if(!editor)throw Error('请先打开笔记');
          editor.replaceSelection('\n> '+fresh.text.replace(/\n/g,'\n> ')+'\n\n来源：'+fresh.title+' · '+fresh.id+' · SHA256 '+fresh.content_hash+'\n');
          new Notice('引用已插入当前笔记');
          }catch(e){new Notice(e.message);}
        };
      }status.textContent=refs.length+' 条证据';
    }catch(e){status.textContent=e.message;}finally{search.disabled=false;}};
    const tension=root.createEl('button',{text:'准备张力分析材料'});tension.onclick=async()=>{try{
      const ids=selected.size?[...selected]:refs.map(r=>r.id);
      const pack=await invoke(this.plugin.settings,'memo.prepare_tension',{project:this.plugin.settings.project,evidence_ids:ids});
      const view=new Modal(this.app);view.contentEl.createEl('h2',{text:'张力分析材料 · 交给 Agent 继续'});
      view.contentEl.createEl('pre',{text:JSON.stringify(pack,null,2)});
      const insert=view.contentEl.createEl('button',{text:'将材料插入当前笔记'});insert.onclick=()=>{try{
        const editor=this.app.workspace.getActiveViewOfType(MarkdownView)?.editor;if(!editor)throw Error('请先打开笔记');
        editor.replaceSelection('\n## Memo 张力分析材料\n\n```json\n'+JSON.stringify(pack,null,2)+'\n```\n');new Notice('材料已插入');view.close();
      }catch(e){new Notice(e.message);}};view.open();
    }catch(e){status.textContent=e.message;}};
    root.createEl('h3',{text:'向 Memo 提交草稿'});
    const claim=root.createEl('textarea',{attr:{placeholder:'可复用结论',rows:'3'}}),scope=root.createEl('input',{attr:{placeholder:'适用范围'}}),limitations=root.createEl('input',{attr:{placeholder:'局限与待核实事项'}});
    const submit=root.createEl('button',{text:'提交待审核草稿'});submit.onclick=async()=>{try{
      const r=await invoke(this.plugin.settings,'memo.submit_draft',{project:this.plugin.settings.project,title:query.value.slice(0,100),claim:claim.value,scope:scope.value,limitations:limitations.value,evidence_ids:selected.size?[...selected]:refs.map(r=>r.id)});
      status.textContent='已提交 '+r.id+'，请在 Memo 的知识回流审核中确认。';
    }catch(e){status.textContent=e.message;}};
  }
  onClose(){this.contentEl.empty();}
}
class Settings extends PluginSettingTab {
  constructor(app,plugin){super(app,plugin);this.plugin=plugin;}
  display(){
    this.containerEl.empty();this.containerEl.createEl('h2',{text:'Memorive Companion'});
    this.containerEl.createEl('p',{text:'从 Memo → 研究问答与连接 → Agent 接入配置复制路径。仅调用本地工具，不保存 API 密钥。'});
    for(const [key,label,placeholder] of [['helper','Memorive.exe','绝对路径'],['workspace','Memo 研究工作区','绝对路径'],['project','项目 ID','default'],['connection','连接 ID','在 Memo 的连接与同步中创建'],['outputFolder','结果笔记文件夹','Memorive']]){
      new Setting(this.containerEl).setName(label).addText(t=>t.setPlaceholder(placeholder).setValue(this.plugin.settings[key]).onChange(async value=>{this.plugin.settings[key]=value;await this.plugin.saveData(this.plugin.settings);}));
    }
    new Setting(this.containerEl).setName('验证连接').addButton(b=>b.setButtonText('检测').onClick(async()=>{try{const r=await invoke(this.plugin.settings,'memo.capabilities',{});new Notice('已连接 · '+r.projects.length+' 个项目');}catch(e){new Notice(e.message);}}));
    new Setting(this.containerEl).setName('选择已授权的连接').addButton(b=>b.setButtonText('获取连接').onClick(async()=>{try{const r=await invoke(this.plugin.settings,'connector.connections',{project:this.plugin.settings.project});const m=new Modal(this.app);m.contentEl.createEl('h3',{text:'选择 Memo 连接'});for(const c of r.connections.filter(c=>c.provider==='obsidian')){const pick=m.contentEl.createEl('button',{text:c.name});pick.onclick=async()=>{this.plugin.settings.connection=c.id;await this.plugin.saveData(this.plugin.settings);m.close();this.display();};}m.open();}catch(e){new Notice(e.message);}}));
  }
}
module.exports=class MemoriveCompanion extends Plugin{
  async onload(){
    this.settings=Object.assign({helper:'',workspace:'',project:'default',connection:'',outputFolder:'Memorive'},await this.loadData());
    this.addSettingTab(new Settings(this.app,this));
    this.addRibbonIcon('search','Memo 研究检索',()=>new ResearchModal(this.app,this,'').open());
    this.addCommand({id:'research-selection',name:'在 Memo 中检索选中文本',editorCallback:editor=>new ResearchModal(this.app,this,editor.getSelection()).open()});
    this.addCommand({id:'research-search',name:'检索 Memo 研究资料',callback:()=>new ResearchModal(this.app,this,'').open()});
    const base=this.app.vault.adapter.getBasePath();
    require(path.join(base,this.manifest.dir,'companion.js')).install(this,invoke,ResearchModal);
  }
};
module.exports.invoke=invoke;
