export type MemoMethod = 'memo.capabilities' | 'memo.projects' | 'memo.connections' | 'memo.search' | 'memo.read_evidence' | 'memo.prepare_tension' | 'memo.submit_draft' | 'memo.import_preview' | 'memo.import_commit' | 'memo.outputs' | 'memo.export' | 'memo.writeback_prepare' | 'memo.writeback_ack' | 'memo.job_submit' | 'memo.job_status' | 'memo.job_cancel' | 'memo.events';
export interface MemoEvent { cursor:number; type:string; id:string; at:string; project:string; kind:string; state:string|null; revision:number; }
export interface MemoEvents { events:MemoEvent[]; next_cursor:number; has_more:boolean; }
export interface MemoJob { id:string; status:'QUEUED'|'RUNNING'|'DISPATCHED'|'COMPLETE'|'ERROR'|'CANCELLED'|'INTERRUPTED'|'UNAVAILABLE'|'PAUSED'|'CANCELLING'; result?:Record<string,unknown>; error?:string; }
export class MemoClient {
 constructor(command:string[],workspace:string,clientId:string,options?:{timeout?:number});
 call<T=Record<string,unknown>>(method:MemoMethod,params?:Record<string,unknown>):Promise<T>;
 capabilities():Promise<{api_version:string;methods:Record<string,unknown>;scopes:string[]}>;
 events(cursor?:number,limit?:number):Promise<MemoEvents>;
 wait(jobId:string,options?:{timeout?:number;interval?:number}):Promise<MemoJob>;
}
