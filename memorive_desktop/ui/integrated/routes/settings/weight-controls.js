/* Bounded integer allocation; the server supplies all limits and preset values. */
(() => {
  'use strict';
  const relevance=['Similarity','Semantic'];
  const clamp=(v,low,high)=>Math.max(low,Math.min(high,v));
  function valid(weights,limits,group){
    const keys=Object.keys(limits);
    return weights&&Object.keys(weights).length===keys.length&&keys.every(k=>Number.isFinite(weights[k])&&weights[k]*100>=limits[k][0]-1e-7&&weights[k]*100<=limits[k][1]+1e-7)
      &&Math.abs(keys.reduce((n,k)=>n+weights[k],0)-1)<1e-6
      &&relevance.reduce((n,k)=>n+weights[k]*100,0)>=group[0]-1e-7&&relevance.reduce((n,k)=>n+weights[k]*100,0)<=group[1]+1e-7;
  }
  function allocate(weights,pinned,requested,limits,group){
    const keys=Object.keys(limits);if(!keys.includes(pinned)||!Number.isFinite(requested))throw Error('WEIGHT_RANGE_INVALID');
    const value=clamp(Math.round(requested),...limits[pinned]),old=Object.fromEntries(keys.map(k=>[k,Math.max(0,Number(weights[k])*100||0)]));
    const bounds=Object.fromEntries(keys.map(k=>[k,k===pinned?[value,value]:limits[k]]));
    const authority=keys.filter(k=>!relevance.includes(k));
    const sum=(ks,side)=>ks.reduce((n,k)=>n+bounds[k][side],0);
    const lo=Math.max(group[0],sum(relevance,0),100-sum(authority,1));
    const hi=Math.min(group[1],sum(relevance,1),100-sum(authority,0));
    if(lo>hi)throw Error('WEIGHT_RANGE_INVALID');
    const previous=relevance.reduce((n,k)=>n+old[k],0);
    const mass=clamp(Math.round(previous+(relevance.includes(pinned)?1:-1)*(value-old[pinned])),lo,hi);
    const out={};
    for(const [members,total] of [[relevance,mass],[authority,100-mass]]){
      const fixed=members.includes(pinned)?value:0,free=members.filter(k=>k!==pinned),wanted=total-fixed;
      if(members.includes(pinned))out[pinned]=value;
      let left=0,right=10000;
      for(let i=0;i<64;i++){const mid=(left+right)/2,n=free.reduce((s,k)=>s+clamp(mid*Math.max(old[k],1),...bounds[k]),0);if(n<wanted)left=mid;else right=mid;}
      const raw=free.map(k=>({key:k,value:clamp(right*Math.max(old[k],1),...bounds[k])}));
      raw.forEach(r=>out[r.key]=Math.floor(r.value+1e-9));
      let remainder=total-members.reduce((n,k)=>n+(out[k]||0),0);
      raw.sort((a,b)=>(b.value-Math.floor(b.value+1e-9))-(a.value-Math.floor(a.value+1e-9))||keys.indexOf(a.key)-keys.indexOf(b.key));
      for(const item of raw){if(remainder>0&&out[item.key]<bounds[item.key][1]){out[item.key]++;remainder--;}}
      if(remainder!==0)throw Error('WEIGHT_ALLOCATION_FAILED');
    }
    const normalized=Object.fromEntries(keys.map(k=>[k,out[k]/100]));
    if(!valid(normalized,limits,group))throw Error('WEIGHT_ALLOCATION_FAILED');
    return normalized;
  }
  window.__MEMO_WEIGHT_CONTROLS__=Object.freeze({allocate,valid});
})();

