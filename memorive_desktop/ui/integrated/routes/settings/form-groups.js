(() => {
  'use strict';
  // Frame existing semantic fields without rebuilding controls or their handlers.
  function groupFields() {
    for(const stack of document.querySelectorAll('#desktop-settings-preview .desktop-settings-panel:not([data-panel="workflow"]) .desktop-settings-form-stack')){
      let group=null;
      for(const child of [...stack.children]){
        if(child.matches('.field-label,.desktop-settings-two,.desktop-settings-three')){
          if(!group){
            group=document.createElement('div');
            group.className='desktop-settings-form-group';
            stack.insertBefore(group,child);
          }
          group.append(child);
        }else group=null;
      }
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',groupFields,{once:true});else groupFields();
})();

