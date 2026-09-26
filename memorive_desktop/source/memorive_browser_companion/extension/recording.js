"use strict";
(() => {
  // Rendered windows may contain pinned turns and gaps. Preserve the observed
  // relative order instead of assuming every window is a contiguous slice.
  const createMerger = () => {
    const rows = new Map(), edges = new Map(); let serial = 0;
    const edge = (a,b) => { if(a!==b) edges.get(a).add(b); };
    const ordered = (strict = false) => {
      const links = new Map([...edges].map(([k,v])=>[k,new Set(v)]));
      const numbered = [...rows].filter(([,r])=>Number.isInteger(r.source_order))
        .sort((a,b)=>a[1].source_order-b[1].source_order);
      for(let i=1;i<numbered.length;i++) {
        if(numbered[i-1][1].source_order < numbered[i][1].source_order)
          links.get(numbered[i-1][0]).add(numbered[i][0]);
      }
      const degree = new Map([...rows.keys()].map(k=>[k,0]));
      for(const values of links.values()) for(const k of values) degree.set(k,degree.get(k)+1);
      const ready = [...degree].filter(([,n])=>n===0).map(([k])=>k), result=[];
      while(ready.length) {
        if(strict && ready.length>1) throw Error('RECORDING_ORDER_UNRESOLVED');
        const k=ready.shift();result.push({...rows.get(k)});
        for(const next of links.get(k)) { degree.set(next,degree.get(next)-1);if(!degree.get(next)) ready.push(next); }
      }
      if(result.length!==rows.size) throw Error('RECORDING_ORDER_CONFLICT');
      return result;
    };
    return {
      add(incoming) {
        const previousRows=new Map([...rows].map(([k,v])=>[k,{...v}]));
        const previousEdges=new Map([...edges].map(([k,v])=>[k,new Set(v)]));
        try {
        const page=incoming.filter(r=>r.text && ['user','assistant'].includes(r.role));
        const occurrences=new Map(), window=[];
        for(const r of page) {
          // Ordinals are stable when a provider has no message IDs. Text-only
          // fallback uses occurrence counts so two identical turns in one window survive.
          const body=r.role+'\0'+r.text;
          const occurrence=(occurrences.get(body)||0)+1;occurrences.set(body,occurrence);
          const key=r.source_key ? 'id:'+r.source_key : Number.isInteger(r.source_order) ?
            'order:'+r.role+':'+r.source_order : 'text:'+body+'\0'+occurrence;
          if(window.includes(key)) continue;
          const old=rows.get(key);
          if(old && Number.isInteger(old.source_order) && Number.isInteger(r.source_order) && old.source_order!==r.source_order)
            throw Error('RECORDING_ORDER_CONFLICT');
          rows.set(key,{...old,...r,source_order:Number.isInteger(r.source_order)?r.source_order:old?.source_order,
            capture_key:old?.capture_key||'r'+(++serial)});
          if(!edges.has(key)) edges.set(key,new Set());
          window.push(key);
        }
        for(let i=1;i<window.length;i++) edge(window[i-1],window[i]);
        if(rows.size>10000 || [...rows.values()].reduce((n,r)=>n+r.text.length,0)>10000000)
          throw Error('RECORDING_SIZE_LIMIT');
        ordered();
        } catch(e) {
          rows.clear();edges.clear();
          for(const [k,v] of previousRows) rows.set(k,v);
          for(const [k,v] of previousEdges) edges.set(k,v);
          throw e;
        }
      },
      get:ordered
    };
  };

  const createRecorder = ({capture,validate,finish,now=()=>Date.now(),schedule=setInterval,cancel=clearInterval}) => {
    let state = null, timer = null, merger = null, base = null, lastPosition = 0;
    const status = () => state ? {...state, message_count:merger.get().length,
      remaining_seconds:Math.max(0,Math.ceil((state.deadline-now())/1000))} : {status:"IDLE",message_count:0};
    const stop = async (reason="USER_STOP") => {
      if (!state || state.status !== "RECORDING") return status();
      state.status="SAVING";state.stop_reason=reason;
      if (timer!==null) cancel(timer);timer=null;
      try {
        const messages = merger.get(true).map((r,index)=>({role:r.role,text:r.text,
          message_id:r.source_key ? "source-"+r.source_key : "scroll-"+index+"-"+r.role}));
        if (!messages.some(r=>r.role==="user") || !messages.some(r=>r.role==="assistant"))
          throw Error("CURRENT_PAGE_CONVERSATION_NOT_FOUND");
        const reply=await finish({ ...base, messages,
          error_code:"SCROLL_RECORDING_VISIBLE_WINDOWS_ONLY",recording_stop_reason:reason},state.token);
        if (!reply?.ok) throw Error(reply?.error_code || "RECORDING_SAVE_FAILED");
        state.status="SAVED";
      } catch(e) { state.status="ERROR";state.error_code=String(e?.message || "RECORDING_SAVE_FAILED"); }
      return status();
    };
    const sample = (position=lastPosition) => {
      if (state?.status !== "RECORDING") return;
      if (now()>=state.deadline) { void stop("TIME_LIMIT");return; }
      try {
        validate(base.source_url);
        const page=capture();
        if (page.source_url!==base.source_url) { void stop("PAGE_CHANGED");return; }
        merger.add(page.messages,position<lastPosition?-1:1);lastPosition=position;
      } catch(e) {
        const code=String(e?.message || "");
        if (code==="CURRENT_PAGE_CONVERSATION_NOT_FOUND") return;
        if (code==="RECORDING_ORDER_CONFLICT") {
          if(timer!==null) cancel(timer);timer=null;
          state.status="ERROR";state.error_code=code;return;
        }
        void stop(code.includes("SIZE_LIMIT")?"SIZE_LIMIT":"PAGE_CHANGED_OR_UNAVAILABLE");
      }
    };
    return {
      start({token,deadline}) {
        if (state?.status==="RECORDING" || state?.status==="SAVING") return status();
        validate();
        base=capture();merger=createMerger();merger.add(base.messages);
        state={status:"RECORDING",token,deadline:Math.min(Number(deadline),now()+600000),started_at:now()};
        timer=schedule(()=>sample(),250);
        return status();
      },sample,stop,status
    };
  };
  globalThis.MEMORIVE_SCROLL_RECORDING = {createMerger,createRecorder};
})();
