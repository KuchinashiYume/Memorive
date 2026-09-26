window.memoLoadPreferences=function(){
 const doc=document,api=Zotero.MemoriveCompanion;
 const get=id=>doc.getElementById("memo-pref-"+id);
 for(const key of ["helper","workspace","project"])get(key).value=api.pref(key)||(key==="project"?"default":"");
 const option=doc.createElementNS("http://www.w3.org/1999/xhtml","option");option.value=api.pref("connection")||"";option.textContent=option.value||"请先读取连接";get("connection").append(option);
 const save=()=>{for(const key of ["helper","workspace","project","connection"])api.pref(key,get(key).value.trim());};
 get("save").onclick=()=>{save();get("status").textContent="设置已保存";};
 get("detect").onclick=async()=>{try{save();const value=await api.invoke("connector.connections",{project:get("project").value});get("connection").replaceChildren();
 for(const c of value.connections.filter(c=>c.provider==="zotero")){const o=doc.createElementNS("http://www.w3.org/1999/xhtml","option");o.value=c.id;o.textContent=c.name+" · "+c.root;get("connection").append(o);}get("status").textContent="请选择连接并保存";
 }catch(e){get("status").textContent=e.message;}};
};
