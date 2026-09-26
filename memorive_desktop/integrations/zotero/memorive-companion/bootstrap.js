var Memorive;
async function startup({id,version,rootURI}) {
  Services.scriptloader.loadSubScript(rootURI+"main.js");
  await Memorive.init({id,version,rootURI});
  await Zotero.PreferencePanes.register({id:"memorive-connection-pane",label:"Memorive 连接",pluginID:id,src:rootURI+"preferences.xhtml",scripts:[rootURI+"preferences.js"]});
  for(const window of Zotero.getMainWindows()) Memorive.addToWindow(window);
}
function onMainWindowLoad({window}) { Memorive?.addToWindow(window); }
function onMainWindowUnload({window}) { Memorive?.removeFromWindow(window); }
function shutdown() { Memorive?.shutdown(); Memorive=undefined; }
function install() {}
function uninstall() {}
