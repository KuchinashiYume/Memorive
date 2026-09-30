(() => {
  "use strict";
  const data = window.MEMORIVE_DOCS;
  if (!data) throw new Error("Documentation content is missing");
  const $ = id => document.getElementById(id);
  const allowedLanguages = ["zh-CN", "en-US", "ja-JP"];
  const state = {lang:"zh-CN",doc:"manual",id:"01",query:"",font:0};
  const features = {
    "zh-CN":[["读","读懂原文"],["理","整理资料"],["证","查证引用"],["存","保存判断"]],
    "en-US":[["READ","Return to sources"],["ORDER","Organize material"],["VERIFY","Check citations"],["KEEP","Preserve decisions"]],
    "ja-JP":[["読む","原文を読む"],["整える","資料を整理"],["確かめる","引用を検証"],["残す","判断を保存"]]
  };
  const labels = {
    "zh-CN":{files:"文件",offline:"离线 · 本地文件",language:"语言",navigation:"打开目录",articles:"章节",articleTools:"本页工具",documentType:"文档类型",searchArticles:"搜索章节",ready:"已复制链接",imported:"正文已替换，仅保存在本机浏览器",restored:"已恢复默认正文",invalid:"文件需要包含至少一篇 ## 章节，且小于 2 MB",limited:"仅使用文字 Markdown；图片仍由原有资源提供",designMd:"下载设计 Markdown"},
    "en-US":{files:"Files",offline:"Offline · local files",language:"Language",navigation:"Open navigation",articles:"Articles",articleTools:"Article tools",documentType:"Document type",searchArticles:"Search articles",ready:"Link copied",imported:"Body replaced in this browser only",restored:"Default body restored",invalid:"Choose a Markdown file under 2 MB with at least one ## article",limited:"Text Markdown is supported; original image assets remain",designMd:"Download design Markdown"},
    "ja-JP":{files:"ファイル",offline:"オフライン · ローカル",language:"言語",navigation:"目次を開く",articles:"章",articleTools:"ページの操作",documentType:"文書の種類",searchArticles:"章を検索",ready:"リンクをコピーしました",imported:"このブラウザー内で本文を差し替えました",restored:"既定の本文に戻しました",invalid:"2 MB 未満で ## の章がある Markdown を選んでください",limited:"文字の Markdown に対応し、画像は元の資源を使います",designMd:"設計 Markdown を保存"}
  };
  let toastTimer;
  function toast(message) { const node=$("toast");node.textContent=message;node.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.classList.remove("show"),2700); }
  function readStore(key) {try{return localStorage.getItem(key)}catch{return null}}
  function writeStore(key,value) {try{localStorage.setItem(key,value)}catch{}}
  function removeStore(key) {try{localStorage.removeItem(key)}catch{}}
  function escapeHtml(s) {return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
  function inline(s) {return escapeHtml(s).replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>").replace(/`(.+?)`/g,"<code>$1</code>");}
  function parseImported(raw) {
    const lines=raw.replace(/\r\n?/g,"\n").split("\n");
    const starts=[];lines.forEach((line,index)=>{if(/^##\s+/.test(line))starts.push(index)});
    if (!starts.length || starts.length>80) throw new Error("structure");
    starts.push(lines.length);
    return starts.slice(0,-1).map((start,index)=>{
      const title=lines[start].replace(/^##\s+/,"").trim();const body=[];const toc=[];let list="";let paragraph=[];
      const flush=()=>{if(paragraph.length){body.push("<p>"+inline(paragraph.join(" "))+"</p>");paragraph=[]}if(list){body.push("</"+list+">");list=""}};
      for(const line of lines.slice(start+1,starts[index+1]).concat([""])) {
        if(/^###\s+/.test(line)){flush();const heading=line.replace(/^###\s+/,"").trim();const id="part-"+(toc.length+1);toc.push({id,title:heading});body.push(`<h3 id="${id}">${inline(heading)}</h3>`);}
        else if(/^\d+\.\s+/.test(line)){if(list!=="ol"){flush();body.push("<ol>");list="ol"}body.push("<li>"+inline(line.replace(/^\d+\.\s+/,""))+"</li>");}
        else if(/^[-*]\s+/.test(line)){if(list!=="ul"){flush();body.push("<ul>");list="ul"}body.push("<li>"+inline(line.replace(/^[-*]\s+/,""))+"</li>");}
        else if(/^!\[/.test(line)){flush();body.push("<p>"+escapeHtml(line.replace(/^!\[([^]]*)\].*$/,"$1"))+"</p>");}
        else if(!line.trim())flush();
        else{if(list){body.push("</"+list+">");list=""}paragraph.push(line.trim())}
      }
      return {id:String(index+1).padStart(2,"0"),title,html:body.join(""),toc,text:lines.slice(start+1,starts[index+1]).join(" ")};
    });
  }
  function overrideKey(lang) {return "memorive-docs-r4-override-"+lang}
  function getArticles() {
    if(state.doc==="design")return data.design[state.lang];
    const raw=readStore(overrideKey(state.lang));
    if(raw){try{return parseImported(raw)}catch{removeStore(overrideKey(state.lang))}}
    return data.manual[state.lang];
  }
  function routeString() {return "#"+state.lang+"/"+state.doc+"/"+state.id}
  function readRoute() {
    const parts=location.hash.replace(/^#/,"").split("/");
    if(allowedLanguages.includes(parts[0]))state.lang=parts[0];
    if(parts[1]==="manual"||parts[1]==="design")state.doc=parts[1];
    if(/^\d{2}$/.test(parts[2]||""))state.id=parts[2];
  }
  function navigate(lang=state.lang,doc=state.doc,id=state.id){state.lang=lang;state.doc=doc;state.id=id;const route=routeString();if(location.hash!==route)location.hash=route;else render();}
  function setUI() {
    const u=data.ui[state.lang],t=labels[state.lang];
    document.documentElement.lang=state.lang;document.title="Memorive · "+(state.doc==="manual"?u.guide:u.design);
    document.querySelector(".language-label").textContent=t.language;
    $("open-nav").setAttribute("aria-label",t.navigation);
    $("left-panel").setAttribute("aria-label",t.articles);
    document.querySelector(".right-panel").setAttribute("aria-label",t.articleTools);
    document.querySelector(".document-switch").setAttribute("aria-label",t.documentType);
    $("search").setAttribute("aria-label",t.searchArticles);
    $("site-subtitle").textContent=u.subtitle;$("language").value=state.lang;$("language").setAttribute("aria-label",t.language);
    $("tab-guide").textContent=u.guide;$("tab-design").textContent=u.design;
    $("tab-guide").setAttribute("aria-selected",String(state.doc==="manual"));$("tab-design").setAttribute("aria-selected",String(state.doc==="design"));
    $("search").placeholder=u.search;$("nav-caption").textContent=u.all;$("offline-status").textContent=t.offline;
    $("breadcrumb-document").textContent=state.doc==="manual"?u.guide:u.design;$("toc-title").textContent=u.contents;
    $("source-note").textContent=u.reference;
    $("smaller").title=u.small;$("smaller").setAttribute("aria-label",u.small);$("larger").title=u.large;$("larger").setAttribute("aria-label",u.large);
    $("print").title=u.print;$("print").setAttribute("aria-label",u.print);$("copy-link").title=u.copy;$("copy-link").setAttribute("aria-label",u.copy);
    $("download-md").textContent=state.doc==="manual"?u.download:t.designMd;$("replace-md").textContent=u.replace;$("restore-md").textContent=u.restore;
    $("replace-md").hidden=state.doc!=="manual";$("restore-md").hidden=state.doc!=="manual";
    $("pdf-link").href=`downloads/${state.lang}/${state.doc==="manual"?"manual":"design"}.pdf`;
    $("pdf-link").setAttribute("download",`${state.doc}-${state.lang}.pdf`);
    document.querySelector(".right-panel .right-card:nth-child(2) .right-heading").textContent=t.files;
  }
  function renderNav(articles){
    const root=$("article-nav");root.replaceChildren();const q=state.query.trim().toLocaleLowerCase();let count=0;
    articles.forEach(a=>{if(q && !(a.title+" "+a.text).toLocaleLowerCase().includes(q))return;count++;
      const link=document.createElement("a");link.className="nav-link"+(a.id===state.id?" active":"");link.href="#"+state.lang+"/"+state.doc+"/"+a.id;
      const number=document.createElement("span");number.className="num";number.textContent=a.id;link.append(number,document.createTextNode(a.title.replace(/^\d+\s*/,"")));
      link.addEventListener("click",()=>{$("left-panel").classList.remove("open")});root.append(link);
    });
    if(!count){const item=document.createElement("p");item.className="nav-empty";item.textContent=data.ui[state.lang].empty;root.append(item)}
  }
  function render(){
    setUI();const articles=getArticles();let index=articles.findIndex(a=>a.id===state.id);
    if(index<0){index=0;state.id=articles[0].id;history.replaceState(null,"",routeString())}
    const a=articles[index];renderNav(articles);
    $("article-kicker").textContent="MEMORIVE · "+(state.doc==="manual"?data.ui[state.lang].guide:data.ui[state.lang].design)+" / "+a.id;
    $("article-title").textContent=a.title;$("breadcrumb-article").textContent=a.title;$("article-body").innerHTML=a.html;
    const featureStrip=$("feature-strip");featureStrip.replaceChildren();featureStrip.hidden=state.doc!=="manual"||a.id!=="01";
    if(!featureStrip.hidden){for(const [label,detail] of features[state.lang]){const card=document.createElement("div");card.className="feature-card";const title=document.createElement("strong");title.textContent=label;const copy=document.createElement("span");copy.textContent=detail;card.append(title,copy);featureStrip.append(card)}}
    const toc=$("on-page");toc.replaceChildren();for(const part of a.toc){const link=document.createElement("a");link.href="#";link.textContent=part.title;link.addEventListener("click",event=>{event.preventDefault();document.getElementById(part.id)?.scrollIntoView({behavior:"smooth",block:"start"})});toc.append(link)}
    const prev=$("previous"),next=$("next");prev.textContent="← "+data.ui[state.lang].prev+(index>0?" · "+articles[index-1].title:"");next.textContent=data.ui[state.lang].next+(index<articles.length-1?" · "+articles[index+1].title:"")+" →";
    prev.disabled=index===0;next.disabled=index===articles.length-1;prev.dataset.id=index>0?articles[index-1].id:"";next.dataset.id=index<articles.length-1?articles[index+1].id:"";
    window.scrollTo({top:0,behavior:"instant"});
  }
  function saveText(filename,text){const blob=new Blob([text],{type:"text/markdown;charset=utf-8"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500)}
  $("language").addEventListener("change",e=>{writeStore("memorive-docs-r4-lang",e.target.value);navigate(e.target.value,state.doc,state.id)});
  $("tab-guide").addEventListener("click",()=>navigate(state.lang,"manual",state.id));$("tab-design").addEventListener("click",()=>navigate(state.lang,"design",state.id));
  $("search").addEventListener("input",e=>{state.query=e.target.value;renderNav(getArticles())});
  $("previous").addEventListener("click",e=>{if(e.currentTarget.dataset.id)navigate(state.lang,state.doc,e.currentTarget.dataset.id)});
  $("next").addEventListener("click",e=>{if(e.currentTarget.dataset.id)navigate(state.lang,state.doc,e.currentTarget.dataset.id)});
  $("smaller").addEventListener("click",()=>{state.font=Math.max(-1,state.font-1);font()});$("larger").addEventListener("click",()=>{state.font=Math.min(2,state.font+1);font()});
  function font(){document.body.classList.remove("font-small","font-large","font-xlarge");if(state.font===-1)document.body.classList.add("font-small");if(state.font===1)document.body.classList.add("font-large");if(state.font===2)document.body.classList.add("font-xlarge");writeStore("memorive-docs-r4-font",String(state.font))}
  $("print").addEventListener("click",()=>window.print());
  $("copy-link").addEventListener("click",async()=>{const value=location.href;try{await navigator.clipboard.writeText(value)}catch{const temp=document.createElement("textarea");temp.value=value;temp.style.position="fixed";temp.style.opacity="0";document.body.append(temp);temp.select();document.execCommand("copy");temp.remove()}toast(labels[state.lang].ready)});
  $("download-md").addEventListener("click",()=>{const imported=state.doc==="manual"?readStore(overrideKey(state.lang)):null;if(imported){saveText(`manual-text-${state.lang}.md`,imported);return}const path=`downloads/${state.lang}/${state.doc==="manual"?"manual-text":"design"}.md`;const a=document.createElement("a");a.href=path;a.download=path.split("/").pop();document.body.append(a);a.click();a.remove()});
  $("replace-md").addEventListener("click",()=>{$("upload").click()});
  $("upload").addEventListener("change",async e=>{const file=e.target.files?.[0];if(!file)return;try{if(file.size>2_000_000)throw new Error("size");const raw=await file.text();parseImported(raw);writeStore(overrideKey(state.lang),raw);state.id="01";render();toast(labels[state.lang].imported)}catch{toast(labels[state.lang].invalid)}finally{e.target.value=""}});
  $("restore-md").addEventListener("click",()=>{removeStore(overrideKey(state.lang));state.id="01";render();toast(labels[state.lang].restored)});
  $("open-nav").addEventListener("click",()=>{$("left-panel").classList.toggle("open")});
  document.addEventListener("keydown",e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();$("left-panel").classList.add("open");$("search").focus()}});
  window.addEventListener("hashchange",()=>{readRoute();render()});
  const savedLang=readStore("memorive-docs-r4-lang");if(allowedLanguages.includes(savedLang))state.lang=savedLang;
  const savedFont=Number(readStore("memorive-docs-r4-font"));if([-1,0,1,2].includes(savedFont))state.font=savedFont;font();readRoute();render();
})();
