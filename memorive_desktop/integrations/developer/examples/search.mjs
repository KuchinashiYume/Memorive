import { MemoClient } from '../javascript/memorive.mjs';
// node search.mjs '["C:/Memo/Memorive.exe","--memo-agent"]' WORKSPACE CLIENT PROJECT QUERY
const [command,workspace,client,project,query='method']=process.argv.slice(2);
const memo=new MemoClient(JSON.parse(command),workspace,client);
await memo.capabilities();
console.log(JSON.stringify(await memo.call('memo.search',{project,query}),null,2));
