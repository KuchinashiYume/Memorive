/* Bundled PDF engine: load only when a document is opened. No remote fallback. */
(() => {
  'use strict';
  let pending=null;
  Memorive.loadPdfjs=()=>{
    if(globalThis.pdfjsLib?.getDocument&&globalThis.pdfjsWorker?.WorkerMessageHandler)
      return Promise.resolve(globalThis.pdfjsLib);
    if(pending)return pending;
    pending=new Promise((resolve,reject)=>{
      const script=document.createElement('script');
      script.src='pdf_runtime.js';
      script.async=true;
      const fail=()=>{
        script.remove();
        pending=null;
        reject(new Error('Desktop_PDFJS_RUNTIME_UNAVAILABLE'));
      };
      script.onerror=fail;
      script.onload=()=>{
        if(!globalThis.pdfjsLib?.getDocument||!globalThis.pdfjsWorker?.WorkerMessageHandler){
          fail();
          return;
        }
        resolve(globalThis.pdfjsLib);
      };
      document.head.append(script);
    });
    return pending;
  };
})();

