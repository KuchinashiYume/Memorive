using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Diagnostics;
using System.Threading;
using System.Collections.Generic;
using File=LongFile;
using Directory=LongDirectory;

class UpdateBinding {
    public string schema="MemoriveUpdateBinding-v1",program_root,data_root,state_root,source_app,source_package,source_release,language="en-US",legacy_entry;
    public bool portable,sandbox;public Member[] source_files;
}
class UpdateTransaction {
    public string schema="MemoriveUpdateTransaction-v1",operation_id,target_package_id,stage="AVAILABLE",error,source_version,new_version;
    public string language,archive,fallback_reason;public bool full_fallback,consent_required,writes_opened;
    public long current,total;public UpdateAsset asset;public State previous;public UpdateBinding previous_binding;
}

class UpdateTerminalSeal {
    public string schema="MemoriveUpdateTerminalSeal-v1",operation_id,stage,current_sha256,binding_sha256,profile_sha256;
}

class UpdateEngine {
    public readonly string Root,Home;public readonly UpdateBinding Binding;public UpdateTransaction Plan;public Action<UpdateTransaction> Changed;
    public UpdateEngine(string root) {
        Root=Engine.RootPath(root);Home=Engine.Under(Root,"updates");Binding=Engine.Load<UpdateBinding>(Path.Combine(Home,"binding.json"));
        UpdateProtocol.Require(Binding.schema=="MemoriveUpdateBinding-v1"&&NativePaths.Same(Binding.program_root,Root),"UPDATE_BINDING_INVALID");
        var owner=Engine.Load<Dictionary<string,object>>(Path.Combine(Root,"install-owner.json"));
        UpdateProtocol.Require(Convert.ToString(owner["owner"])==Engine.Owner&&NativePaths.Same(Convert.ToString(owner["root"]),Root),"UPDATE_INSTALL_OWNER_INVALID");
        Engine.NoReparse(Binding.data_root);Engine.NoReparse(Binding.state_root);Engine.NoReparse(Binding.source_app);
        UpdateProtocol.Require(Engine.Within(Binding.state_root,Binding.data_root)&&!Engine.Within(Binding.data_root,Root)&&!NativePaths.Same(Binding.data_root,Root),"UPDATE_DATA_BINDING_INVALID");
        if(UpdateProtocol.Trust.test_only){UpdateProtocol.Require(Binding.sandbox,"UPDATE_TEST_TRUST_SANDBOX_REQUIRED");Engine.Sandbox(Root);Engine.Sandbox(Binding.data_root);}
    }
    public static UpdateBinding Bind(string root,string data,string state,string source,bool portable,bool sandbox,string language) {
        root=Engine.RootPath(root);data=Engine.RootPath(data);source=Engine.RootPath(source);state=Engine.RootPath(state);
        if(sandbox){Engine.Sandbox(root);Engine.Sandbox(data);}if(UpdateProtocol.Trust.test_only)UpdateProtocol.Require(sandbox,"UPDATE_TEST_TRUST_SANDBOX_REQUIRED");
        UpdateProtocol.Require(Engine.Within(state,data)&&!Engine.Within(data,root)&&!Engine.Within(root,data)&&!NativePaths.Same(root,data),"UPDATE_DATA_BINDING_INVALID");
        string home=Engine.Under(root,"updates"),path=Path.Combine(home,"binding.json");
        if(File.Exists(path)){
            var existing=Engine.Load<UpdateBinding>(path);UpdateProtocol.Require(NativePaths.Same(existing.data_root,data)&&NativePaths.Same(existing.state_root,state),"UPDATE_INSTANCE_BINDING_CONFLICT");return existing;
        }
        if(!portable){var old=Engine.ReadState(root);UpdateProtocol.Require(NativePaths.Same(old.data_root,data)&&NativePaths.Same(Path.Combine(old.version,"app"),source),"UPDATE_INSTALL_BINDING_MISMATCH");}
        else {
            UpdateProtocol.Require(!Directory.Exists(root)||!Directory.EnumerateFileSystemEntries(root).Any(),"UPDATE_PORTABLE_ROOT_OCCUPIED");
            UpdateProtocol.Require(!NativePaths.Same(source,root)&&!Engine.Within(root,source),"UPDATE_PORTABLE_MANAGED_ROOT_REQUIRED");
        }
        var identity=Engine.Load<Dictionary<string,object>>(Engine.Under(source,"_internal/release_identity_binding.json"));
        UpdateProtocol.Require(File.Exists(Engine.Under(source,"Memorive.exe")),"UPDATE_MAIN_APPLICATION_REQUIRED");
        // A data folder nested in a legacy portable directory is explicitly bound,
        // retained in place and excluded from the managed program inventory.
        var binding=new UpdateBinding{program_root=root,data_root=data,state_root=state,source_app=source,source_files=null,
            source_package=Convert.ToString(identity["package_id"]),source_release=Convert.ToString(identity["release_version"]),portable=portable,sandbox=sandbox,language=language,
            legacy_entry=portable?Path.Combine(source,"Memorive.exe"):null};
        Directory.CreateDirectory(root);Directory.CreateDirectory(home);
        if(portable)Engine.Save(Path.Combine(root,"install-owner.json"),new{owner=Engine.Owner,root=root,role="EXPLICIT_PORTABLE_UPDATE_BINDING"});
        Directory.CreateDirectory(data);string dataOwner=Path.Combine(data,"data-owner.json");
        if(File.Exists(dataOwner)){var owner=Engine.Load<Dictionary<string,object>>(dataOwner);UpdateProtocol.Require(Convert.ToString(owner["owner"])==Engine.Owner&&NativePaths.Same(Convert.ToString(owner["data_root"]),data),"UPDATE_DATA_OWNER_MISMATCH");}
        else Engine.Save(dataOwner,new{owner=Engine.Owner,data_root=data,role="EXPLICIT_EXISTING_PROFILE_BINDING"});
        Engine.Save(path,binding);return binding;
    }
    Member[] InventorySource(){
        var files=new List<Member>();var dirs=new Stack<string>();dirs.Push(Binding.source_app);
        while(dirs.Count>0)foreach(string p in Directory.EnumerateFileSystemEntries(dirs.Pop())){
            UpdateProtocol.Require(!Cancelled(),"UPDATE_CANCELLED");
            if(NativePaths.Same(p,Binding.data_root))continue;Engine.NoReparse(p);if(Directory.Exists(p)){dirs.Push(p);continue;}
            string rel=p.Substring(Binding.source_app.Length+1).Replace('\\','/');UpdateProtocol.Relative(rel);
            files.Add(new Member{path=rel,size=new FileInfo(LongFile.N(p)).Length,sha256=Engine.Hash(p).ToLowerInvariant()});
        }
        UpdateProtocol.Members(files.ToArray(),files.Sum(x=>x.size));return files.ToArray();
    }
    public string Operation {get{return Engine.Under(Home,UpdateProtocol.Id(Plan.operation_id));}}
    public UpdateNetworkPolicy NetworkPolicy(){
        string path=Engine.Under(Binding.state_root,"profile/settings/settings.json");var policy=new UpdateNetworkPolicy();if(!File.Exists(path))return policy;
        var envelope=Engine.Load<Dictionary<string,object>>(path);var settings=(Dictionary<string,object>)envelope["settings"];var preferences=(Dictionary<string,object>)settings["preferences"];
        policy.proxy_mode=Convert.ToString(preferences["proxy_mode"]);policy.proxy_address=Convert.ToString(preferences["proxy_address"]);return policy;
    }
    void RetainFailure(Exception error){
        Engine.Save(Path.Combine(Operation,"failure-"+Guid.NewGuid().ToString("N")+".json"),new{stage=Plan.stage,operation_id=Plan.operation_id,type=error.GetType().FullName,hresult=error.HResult,diagnostic=error.ToString(),utc=DateTime.UtcNow.ToString("o")});
    }
    void Save(string stage){Plan.stage=stage;Engine.Save(Path.Combine(Operation,"transaction.json"),Plan);if(Changed!=null)Changed(Plan);}
    bool Cancelled(){return File.Exists(Path.Combine(Operation,"cancel"));}
    public UpdateTransaction Read(string operation) {
        Plan=Engine.Load<UpdateTransaction>(Engine.Under(Home,UpdateProtocol.Id(operation)+"/transaction.json"));
        UpdateProtocol.Require(Plan.schema=="MemoriveUpdateTransaction-v1"&&Plan.operation_id==operation,"UPDATE_TRANSACTION_INVALID");
        UpdateProtocol.Id(Plan.target_package_id);
        if(Plan.new_version!=null){
            Engine.NoReparse(Plan.new_version);
            UpdateProtocol.Require(NativePaths.Same(Path.GetDirectoryName(Plan.new_version),Path.Combine(Root,"versions"))&&Path.GetFileName(Plan.new_version).StartsWith(Plan.target_package_id+"-",StringComparison.Ordinal),"UPDATE_SLOT_SCOPE_INVALID");
            UpdateProtocol.Require(Plan.previous_binding!=null,"UPDATE_PREVIOUS_BINDING_REQUIRED");
        }
        if(Plan.previous_binding!=null){
            var previous=Plan.previous_binding;Engine.NoReparse(previous.source_app);
            UpdateProtocol.Require(previous.schema==Binding.schema&&NativePaths.Same(previous.program_root,Root)&&NativePaths.Same(previous.data_root,Binding.data_root)&&NativePaths.Same(previous.state_root,Binding.state_root)&&previous.portable==Binding.portable&&previous.sandbox==Binding.sandbox,"UPDATE_PREVIOUS_BINDING_INVALID");
            if(previous.portable&&Plan.previous==null)UpdateProtocol.Require(NativePaths.Same(previous.legacy_entry,Path.Combine(previous.source_app,"Memorive.exe"))&&NativePaths.Same(previous.source_app,Path.GetDirectoryName(Binding.legacy_entry)),"UPDATE_PREVIOUS_SOURCE_INVALID");
            else UpdateProtocol.Require(Plan.previous!=null&&NativePaths.Same(previous.source_app,Path.Combine(Plan.previous.version,"app")),"UPDATE_PREVIOUS_SOURCE_INVALID");
        }
        if(Plan.previous!=null){
            UpdateProtocol.Require(NativePaths.Same(Plan.previous.program_root,Root)&&NativePaths.Same(Plan.previous.data_root,Binding.data_root)&&NativePaths.Same(Path.GetDirectoryName(Plan.previous.version),Path.Combine(Root,"versions"))&&Plan.previous.sandbox_only==Binding.sandbox,"UPDATE_PREVIOUS_STATE_INVALID");
            Engine.NoReparse(Plan.previous.version);
        }
        return Plan;
    }
    static bool Terminal(string stage){return stage=="COMPLETE"||stage=="ROLLED_BACK";}
    static string CanonicalValue(object value){
        var map=value as Dictionary<string,object>;
        if(map!=null)return "{"+String.Join(",",map.Keys.OrderBy(k=>k,StringComparer.Ordinal).Select(k=>Engine.Json.Serialize(k)+":"+CanonicalValue(map[k])))+"}";
        var sequence=value as System.Collections.IEnumerable;
        if(sequence!=null&&!(value is string))return "["+String.Join(",",sequence.Cast<object>().Select(CanonicalValue))+"]";
        return Engine.Json.Serialize(value);
    }
    static bool SameStateValue(object left,object right){
        // CLR reflection/JSON property enumeration order is not a state change.
        // Compare every field and preserve array order and all value types.
        return CanonicalValue(Engine.Json.DeserializeObject(Engine.Json.Serialize(left)))==CanonicalValue(Engine.Json.DeserializeObject(Engine.Json.Serialize(right)));
    }
    string HashOrAbsent(string path){Engine.NoReparse(path);return File.Exists(path)?Engine.Hash(path):null;}
    void OwnGate(bool required){
        string path=Path.Combine(Home,"activation-gate.json");Engine.NoReparse(path);
        if(!File.Exists(path)){UpdateProtocol.Require(!required,"UPDATE_TERMINAL_GATE_MISSING");return;}
        var gate=Engine.Load<Dictionary<string,object>>(path);object schema,id;
        UpdateProtocol.Require(gate.TryGetValue("schema",out schema)&&Convert.ToString(schema)=="MemoriveActivationGate-v1"&&gate.TryGetValue("operation_id",out id)&&Convert.ToString(id)==Plan.operation_id,"UPDATE_ACTIVATION_GATE_MISMATCH");
    }
    void ValidateTerminalState(){
        UpdateProtocol.Require(Terminal(Plan.stage)&&Plan.new_version!=null,"UPDATE_TERMINAL_STAGE_INVALID");
        UpdateProtocol.Require(!Plan.writes_opened&&!File.Exists(Path.Combine(Operation,"writes-opened.json")),"UPDATE_NEW_WRITES_BLOCK_RECOVERY");
        var binding=Engine.Load<UpdateBinding>(Path.Combine(Home,"binding.json"));
        UpdateProtocol.Require(NativePaths.Same(binding.program_root,Root)&&NativePaths.Same(binding.data_root,Binding.data_root)&&NativePaths.Same(binding.state_root,Binding.state_root),"UPDATE_TERMINAL_BINDING_MISMATCH");
        string current=Path.Combine(Root,"current.json"),profile=Path.Combine(Home,"active-profile.json");
        if(Plan.stage=="COMPLETE"){
            var active=Engine.ReadState(Root);var selected=Engine.Load<Dictionary<string,object>>(profile);
            UpdateProtocol.Require(active.package_id==Plan.target_package_id&&NativePaths.Same(active.version,Plan.new_version)&&NativePaths.Same(active.data_root,Binding.data_root)&&NativePaths.Same(active.program_root,Root),"UPDATE_TERMINAL_VERSION_MISMATCH");
            UpdateProtocol.Require(binding.source_package==Plan.target_package_id&&NativePaths.Same(binding.source_app,Path.Combine(Plan.new_version,"app")),"UPDATE_TERMINAL_BINDING_MISMATCH");
            UpdateProtocol.Require(Convert.ToString(selected["schema"])=="MemoriveActiveUpdateProfile-v1"&&Convert.ToString(selected["operation_id"])==Plan.operation_id&&Convert.ToString(selected["package_id"])==Plan.target_package_id&&NativePaths.Same(Convert.ToString(selected["version"]),Plan.new_version)&&NativePaths.Same(Convert.ToString(selected["program_root"]),Root)&&NativePaths.Same(Convert.ToString(selected["data_root"]),Binding.data_root)&&NativePaths.Same(Convert.ToString(selected["state_root"]),Binding.state_root),"UPDATE_TERMINAL_PROFILE_MISMATCH");
        }else{
            UpdateProtocol.Require(Plan.previous==null?!File.Exists(current):SameStateValue(Engine.ReadState(Root),Plan.previous),"UPDATE_TERMINAL_VERSION_MISMATCH");
            UpdateProtocol.Require(SameStateValue(binding,Plan.previous_binding),"UPDATE_TERMINAL_BINDING_MISMATCH");
            UpdateProtocol.Require(HashOrAbsent(profile)==HashOrAbsent(Path.Combine(Operation,"previous-active-profile.json")),"UPDATE_TERMINAL_PROFILE_MISMATCH");
        }
    }
    void SaveTerminal(string stage,string fault){
        OwnGate(true);Plan.stage=stage;ValidateTerminalState();
        Engine.Save(Path.Combine(Operation,"terminal-seal.json"),new UpdateTerminalSeal{operation_id=Plan.operation_id,stage=stage,current_sha256=HashOrAbsent(Path.Combine(Root,"current.json")),binding_sha256=HashOrAbsent(Path.Combine(Home,"binding.json")),profile_sha256=HashOrAbsent(Path.Combine(Home,"active-profile.json"))});
        Save(stage);
        // A real process exit tests the exact durable-terminal / live-gate window.
        // No exception handler can rewrite the recorded terminal state.
        if(fault=="crash-after-"+stage.ToLowerInvariant()){
            UpdateProtocol.Require(UpdateProtocol.Trust.test_only&&Binding.sandbox,"UPDATE_FAULT_TEST_ONLY");Environment.Exit(86);
        }
        ReconcileTerminalLocked();
    }
    void ReconcileTerminalLocked(){
        OwnGate(true);ValidateTerminalState();
        var seal=Engine.Load<UpdateTerminalSeal>(Path.Combine(Operation,"terminal-seal.json"));
        UpdateProtocol.Require(seal.schema=="MemoriveUpdateTerminalSeal-v1"&&seal.operation_id==Plan.operation_id&&seal.stage==Plan.stage,"UPDATE_TERMINAL_SEAL_INVALID");
        UpdateProtocol.Require(seal.current_sha256==HashOrAbsent(Path.Combine(Root,"current.json"))&&seal.binding_sha256==HashOrAbsent(Path.Combine(Home,"binding.json"))&&seal.profile_sha256==HashOrAbsent(Path.Combine(Home,"active-profile.json")),"UPDATE_TERMINAL_STATE_CHANGED");
        Engine.Save(Path.Combine(Operation,"terminal-reconciliation.json"),new{schema="MemoriveTerminalReconciliation-v1",operation_id=Plan.operation_id,stage=Plan.stage,seal_sha256=Engine.Hash(Path.Combine(Operation,"terminal-seal.json")),data_restored=false,utc=DateTime.UtcNow.ToString("o")});
        File.Delete(Path.Combine(Home,"activation-gate.json"));
    }
    public UpdateTransaction ReconcileTerminal(string operation){
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
        using(var installLease=new FileStream(LongFile.N(Path.Combine(Root,"transaction.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
        using(var dataLease=new FileStream(LongFile.N(Path.Combine(Binding.data_root,".memorive-update.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            Read(operation);UpdateProtocol.Require(Terminal(Plan.stage),"UPDATE_RECOVERY_REQUIRED");
            // An already reconciled terminal operation is a read-only no-op,
            // including after the target has legitimately opened user writes.
            if(File.Exists(Path.Combine(Home,"activation-gate.json")))ReconcileTerminalLocked();
            return Plan;
        }
    }
    public UpdateTransaction InspectRecovery(string operation){
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
        using(var installLease=new FileStream(LongFile.N(Path.Combine(Root,"transaction.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
        using(var dataLease=new FileStream(LongFile.N(Path.Combine(Binding.data_root,".memorive-update.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            Read(operation);OwnGate(true);
            UpdateProtocol.Require(Plan.new_version!=null&&new[]{"RECOVERY_REQUIRED","APPLYING","BACKING_UP","BACKED_UP","MIGRATING","MIGRATED","VERIFYING_START","ACTIVATING","WAITING_IDLE"}.Contains(Plan.stage),"UPDATE_RECOVERY_REQUIRED");
            // Inspection grants no launch permission and changes no transaction,
            // profile, seal or gate. Recover retains all new-write safeguards.
            return Plan;
        }
    }
    public static UpdateTransaction ReconcilePendingTerminal(string root,bool inspectRecovery=false){
        string gate=Path.Combine(root,"updates/activation-gate.json");if(!File.Exists(gate))return null;
        var engine=new UpdateEngine(root);var value=Engine.Load<Dictionary<string,object>>(gate);
        object schema,id;UpdateProtocol.Require(value.TryGetValue("schema",out schema)&&Convert.ToString(schema)=="MemoriveActivationGate-v1"&&value.TryGetValue("operation_id",out id),"UPDATE_ACTIVATION_GATE_MISMATCH");
        string operation=UpdateProtocol.Id(Convert.ToString(value["operation_id"]));
        if(inspectRecovery&&!Terminal(engine.Read(operation).stage))return engine.InspectRecovery(operation);
        return engine.ReconcileTerminal(operation);
    }
    UpdateIndex Index(){return UpdateProtocol.ParseIndex(System.IO.File.ReadAllBytes(LongFile.N(Path.Combine(Operation,"index.json"))),File.ReadAllText(Path.Combine(Operation,"index.sig"),Encoding.UTF8));}
    public UpdateTransaction Prepare(string operation,string target,bool allowFull,UpdateNetworkPolicy network) {
        UpdateProtocol.Id(operation);UpdateProtocol.Id(target);
        if(File.Exists(Engine.Under(Home,operation+"/transaction.json"))){Read(operation);UpdateProtocol.Require(Plan.target_package_id==target,"UPDATE_OPERATION_TARGET_MISMATCH");return Plan;}
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            UpdateProtocol.Require(!File.Exists(Path.Combine(Home,"activation-gate.json")),"UPDATE_RECOVERY_REQUIRED");
            foreach(string existing in Directory.GetDirectories(Home)){
                string p=Path.Combine(existing,"transaction.json");if(!File.Exists(p))continue;var previous=Engine.Load<UpdateTransaction>(p);
                UpdateProtocol.Require(new[]{"COMPLETE","FAILED","CANCELLED","ROLLED_BACK"}.Contains(previous.stage),"UPDATE_OPERATION_ALREADY_ACTIVE");
            }
            var discovery=new UpdateDiscovery(Path.Combine(Home,"discovery"),network);var index=discovery.VerifiedIndex();
            UpdateProtocol.Require(index.package_id==target&&Engine.ReleaseOrder(index.release_version)>Engine.ReleaseOrder(Binding.source_release),"UPDATE_TARGET_NOT_NEWER");
            Plan=new UpdateTransaction{operation_id=operation,target_package_id=target,language=Binding.language};Directory.CreateDirectory(Operation);
            File.Copy(Path.Combine(Home,"discovery/verified-index.json"),Path.Combine(Operation,"index.json"));File.Copy(Path.Combine(Home,"discovery/verified-index.sig"),Path.Combine(Operation,"index.sig"));
            try {
            Save("VERIFYING_BASE");Binding.source_files=InventorySource();Engine.Save(Path.Combine(Home,"binding.json"),Binding);
            var baseId=UpdateProtocol.Fingerprint(Binding.source_files);var delta=index.deltas.FirstOrDefault(x=>x.source_manifest_sha256==baseId&&x.size<index.full.size);
            if(delta!=null)try{UpdateProtocol.VerifyFiles(Binding.source_app,Binding.source_files,(i,n)=>{UpdateProtocol.Require(!Cancelled(),"UPDATE_CANCELLED");Plan.current=i;Plan.total=n;Save("VERIFYING_BASE");},false);}catch(UpdateError ex){if(ex.Code!="UPDATE_FILE_HASH_MISMATCH")throw;delta=null;Plan.fallback_reason="UPDATE_BASE_MISMATCH";}
            Plan.full_fallback=delta==null;Plan.consent_required=delta==null&&!allowFull;Plan.asset=delta??index.full;Plan.total=Plan.asset.size;
            if(delta==null&&Plan.fallback_reason==null)Plan.fallback_reason="UPDATE_NO_SMALLER_MATCHING_DELTA";
            Save(Plan.consent_required?"WAITING_FULL_CONSENT":"AVAILABLE");return Plan;
            }catch(Exception ex){Plan.error=ex is UpdateError?ex.Message:"UPDATE_BASE_VERIFICATION_FAILED";Save(Plan.error=="UPDATE_CANCELLED"?"CANCELLED":"FAILED");throw;}
        }
    }
    public object ReleaseOrphanReference(string operation) {
        UpdateProtocol.Id(operation);
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            string receipt=Engine.Under(Binding.state_root,"updates/desktop-update.json");
            var record=Engine.Load<Dictionary<string,object>>(receipt);
            object reference;record.TryGetValue("operation_id",out reference);
            if(Convert.ToString(reference)!=operation)return new{disposition="CHANGED",record=record};
            if(File.Exists(Engine.Under(Home,operation+"/transaction.json"))||File.Exists(Path.Combine(Home,"activation-gate.json")))
                return new{disposition="RETAINED",record=record};
            foreach(string folder in Directory.GetDirectories(Home)){
                string path=Path.Combine(folder,"transaction.json");if(!File.Exists(path))continue;
                var found=Engine.Load<UpdateTransaction>(path);
                UpdateProtocol.Require(Path.GetFileName(folder)==found.operation_id,"UPDATE_TRANSACTION_INVALID");
                var active=Read(found.operation_id);
                if(!new[]{"COMPLETE","FAILED","CANCELLED","ROLLED_BACK"}.Contains(active.stage))
                    return new{disposition="RETAINED",record=record};
            }
            // Hold the same cross-process creation lock as Prepare. Preserve the
            // failed request and receipt before releasing only this exact reference.
            string audit=Path.Combine(Home,"ipc-receipts","orphan-reference-"+Guid.NewGuid().ToString("N")+".json");
            Directory.CreateDirectory(Path.GetDirectoryName(audit));
            Engine.Save(audit,new{schema="MemoriveOrphanReference-v1",operation_id=operation,record=record,utc=DateTime.UtcNow.ToString("o")});
            record["operation_id"]=null;record["orphan_reference_receipt"]=audit;
            Engine.Save(receipt,record);
            return new{disposition="RELEASED",record=record};
        }
    }
    public UpdateTransaction Download(string operation,bool allowFull,UpdateNetworkPolicy network) {
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            Read(operation);if(Plan.stage=="COMPLETE")return Plan;
            UpdateProtocol.Require(new[]{"AVAILABLE","WAITING_FULL_CONSENT","DOWNLOAD_FAILED","DOWNLOADING","VERIFIED"}.Contains(Plan.stage),"UPDATE_DOWNLOAD_STAGE_INVALID");
            if(Plan.consent_required){UpdateProtocol.Require(allowFull,"UPDATE_FULL_CONSENT_REQUIRED");Plan.consent_required=false;}
            if(Plan.stage=="DOWNLOADING"||Plan.error!=null){
                string prefix=Plan.stage=="DOWNLOADING"?"interrupted-download-":"previous-download-";
                Engine.Save(Path.Combine(Operation,prefix+Guid.NewGuid().ToString("N")+".json"),Plan);
            }
            bool selectionVerified=false;
            try {
                var index=Index();ValidateSelection(index);selectionVerified=true;
                UpdateProtocol.Require(!Cancelled(),"UPDATE_CANCELLED");
                // A new attempt owns the lock and has a valid signed selection.
                // Its current error/progress are independent of preserved attempts.
                Plan.error=null;Plan.current=0;Plan.total=Plan.asset.size;
                string cache=Engine.Under(Home,"downloads/"+Plan.asset.sha256+".zip");Directory.CreateDirectory(Path.GetDirectoryName(cache));
                if(File.Exists(cache)){
                    UpdateProtocol.Require(new FileInfo(LongFile.N(cache)).Length==Plan.asset.size&&String.Equals(Engine.Hash(cache),Plan.asset.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_CACHED_ARCHIVE_CORRUPT");
                    Plan.archive=cache;Plan.current=Plan.total;Save("VERIFIED");return Plan;
                }
                string partial=Path.Combine(Operation,"download-"+Guid.NewGuid().ToString("N")+".partial");Save("DOWNLOADING");
                var web=new UpdateDownload(Operation,network);web.Fetch(Plan.asset.url,Plan.asset.size,null,(n,total)=>{Plan.current=n;Plan.total=Plan.asset.size;Save("DOWNLOADING");},Cancelled,partial);
                UpdateProtocol.Require(new FileInfo(LongFile.N(partial)).Length==Plan.asset.size&&String.Equals(Engine.Hash(partial),Plan.asset.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_ARCHIVE_HASH_MISMATCH");
                File.Move(partial,cache);Plan.archive=cache;Plan.error=null;Plan.current=Plan.total;Save("VERIFIED");
            }catch(UpdateError ex){Plan.error=ex.Code;Save(ex.Code=="UPDATE_CANCELLED"?"CANCELLED":!selectionVerified||ex.Code=="UPDATE_ARCHIVE_HASH_MISMATCH"||ex.Code=="UPDATE_CACHED_ARCHIVE_CORRUPT"?"FAILED":"DOWNLOAD_FAILED");throw;}
            catch {Plan.error="UPDATE_NETWORK_FAILED";Save("DOWNLOAD_FAILED");throw;}
            return Plan;
        }
    }
    public void Cancel(string operation){
        Read(operation);UpdateProtocol.Require(new[]{"VERIFYING_BASE","AVAILABLE","WAITING_FULL_CONSENT","DOWNLOADING","VERIFIED","WAITING_IDLE","DOWNLOAD_FAILED"}.Contains(Plan.stage)&&Plan.new_version==null,"UPDATE_COMMIT_NOT_CANCELLABLE");
        File.WriteAllBytes(Path.Combine(Operation,"cancel"),new byte[]{1});
        try{using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){Read(operation);UpdateProtocol.Require(Plan.new_version==null,"UPDATE_COMMIT_NOT_CANCELLABLE");Save("CANCELLED");}}
        catch(IOException){/* A live worker observes the persistent cancellation latch. */}
    }
    bool Busy() {
        foreach(var p in Process.GetProcessesByName("Memorive"))using(p){try{string exe=p.MainModule.FileName;if(Engine.Within(exe,Root)||Engine.Within(exe,Binding.source_app))return true;}catch{throw new UpdateError("UPDATE_PROCESS_INSPECTION_FAILED");}}
        return false;
    }
    void Fault(string boundary,string requested){if(requested==boundary){UpdateProtocol.Require(UpdateProtocol.Trust.test_only&&Binding.sandbox,"UPDATE_FAULT_TEST_ONLY");throw new UpdateError("UPDATE_INJECTED_"+boundary);}}
    void ValidateSelection(UpdateIndex index) {
        UpdateProtocol.ValidateAsset(Plan.asset);
        var approved=(index.deltas??new UpdateAsset[0]).Concat(new[]{index.full});
        UpdateProtocol.Require(approved.Any(x=>x.name==Plan.asset.name&&x.url==Plan.asset.url&&x.size==Plan.asset.size&&x.sha256==Plan.asset.sha256&&x.source_manifest_sha256==Plan.asset.source_manifest_sha256),"UPDATE_PLAN_ASSET_MISMATCH");
        if(Plan.archive!=null)UpdateProtocol.Require(NativePaths.Same(Plan.archive,Engine.Under(Home,"downloads/"+Plan.asset.sha256+".zip")),"UPDATE_ARCHIVE_PATH_MISMATCH");
    }
    void CheckSpace(UpdateTarget target) {
        long backup=0;var pending=new Stack<string>();if(Directory.Exists(Binding.state_root))pending.Push(Binding.state_root);
        while(pending.Count>0)foreach(string p in Directory.EnumerateFileSystemEntries(pending.Pop())){Engine.NoReparse(p);if(Directory.Exists(p))pending.Push(p);else backup=checked(backup+new FileInfo(LongFile.N(p)).Length);}
        // The existing slot stays in place. Allow backup, retained failed data and
        // restored copy, in addition to a full independently assembled target.
        long programRequired=checked(target.total_bytes+backup+1024L*1024*1024),dataRequired=checked(backup*2+1024L*1024*1024);
        if(String.Equals(Path.GetPathRoot(Root),Path.GetPathRoot(Binding.data_root),StringComparison.OrdinalIgnoreCase))programRequired=checked(programRequired+dataRequired);
        UpdateProtocol.Require(new DriveInfo(Path.GetPathRoot(Root)).AvailableFreeSpace>=programRequired&&new DriveInfo(Path.GetPathRoot(Binding.data_root)).AvailableFreeSpace>=dataRequired,"UPDATE_SPACE_INSUFFICIENT");
    }
    void Maintenance(string action,UpdateTarget target) {
        string exe=Engine.Under(Plan.new_version,"app/Memorive.exe");string request=Path.Combine(Operation,"maintenance-request.json");
        Engine.Save(request,new{schema="MemoriveUpdateMaintenance-v1",program_root=Root,data_root=Binding.data_root,state_root=Binding.state_root,operation_id=Plan.operation_id,target_executable=exe,target_exe_sha256=target.files.Single(x=>x.path=="Memorive.exe").sha256});
        var start=new ProcessStartInfo(exe,"--memo-update-maintenance "+Engine.Quote(request)+" --action "+action){UseShellExecute=false,WorkingDirectory=NativePaths.WorkingDirectory(exe)};
        start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"]="1";
        using(var process=Process.Start(start)){process.WaitForExit();UpdateProtocol.Require(process.ExitCode==0,"UPDATE_MAINTENANCE_"+action.ToUpperInvariant()+"_FAILED");}
        var receipt=Engine.Load<Dictionary<string,object>>(Path.Combine(Operation,"maintenance-"+action+".json"));UpdateProtocol.Require(Convert.ToString(receipt["status"])=="PASS","UPDATE_MAINTENANCE_RECEIPT_INVALID");
    }
    public UpdateTransaction Apply(string operation,string fault) {
        Read(operation);UpdateProtocol.Require(Plan.stage=="VERIFIED"||(Plan.stage=="WAITING_IDLE"&&Plan.new_version==null),"UPDATE_APPLY_STAGE_INVALID");
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            Save("WAITING_IDLE");while(Busy()){UpdateProtocol.Require(!Cancelled(),"UPDATE_CANCELLED");Thread.Sleep(500);}
            UpdateProtocol.Require(!Cancelled(),"UPDATE_CANCELLED");
            using(var installLease=new FileStream(LongFile.N(Path.Combine(Root,"transaction.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
                var index=Index();ValidateSelection(index);UpdateProtocol.Require(index.package_id==Plan.target_package_id,"UPDATE_TARGET_IDENTITY_MISMATCH");
                Plan.previous=File.Exists(Path.Combine(Root,"current.json"))?Engine.ReadState(Root):null;
                Plan.previous_binding=Engine.Json.Deserialize<UpdateBinding>(Engine.Json.Serialize(Binding));
                string activeProfile=Path.Combine(Home,"active-profile.json");
                if(File.Exists(activeProfile))File.Copy(activeProfile,Path.Combine(Operation,"previous-active-profile.json"));
                if(Plan.previous!=null)UpdateProtocol.Require(NativePaths.Same(Path.Combine(Plan.previous.version,"app"),Binding.source_app),"UPDATE_ACTIVE_SOURCE_CHANGED");
                Plan.source_version=Plan.previous==null?Binding.source_app:Plan.previous.version;
                Plan.new_version=Engine.Under(Root,"versions/"+index.package_id+"-"+Plan.operation_id);Directory.CreateDirectory(Plan.new_version);
                try {
                    using(var archive=File.OpenRead(Plan.archive))using(var zip=new System.IO.Compression.ZipArchive(archive,System.IO.Compression.ZipArchiveMode.Read))CheckSpace(UpdateProtocol.Target(zip,index));
                    // Assemble before taking an exclusive executable handle: the
                    // same verified old executable may be a reused delta member.
                    Save("APPLYING");var target=UpdateProtocol.Assemble(Plan.archive,Plan.asset,index,Binding.source_app,Binding.source_files,Path.Combine(Plan.new_version,"app"),(n,total)=>{Plan.current=n;Plan.total=total;Save("APPLYING");},()=>false);
                    Engine.Save(Path.Combine(Operation,"target.json"),target);Fault("after-assemble",fault);
                    var manifest=new Manifest{schema=1,package_id=index.package_id,label="TEST_ONLY_UNSIGNED",contract="desktop-desktop-review-v1",health_phase="v101-empty",app_exe="Memorive.exe",release_version=index.release_version,
                        engine_sha256=Engine.Hash(Engine.Self),total_bytes=target.total_bytes,members=target.files.Select(x=>new Member{path="app/"+x.path,size=x.size,sha256=x.sha256.ToUpperInvariant()}).ToArray(),required_files=new[]{"Memorive.exe","_internal/release_identity_binding.json"}};
                    Engine.Save(Path.Combine(Plan.new_version,"manifest.json"),manifest);
                    return Commit(target,manifest,fault);
                }catch(Exception ex){RetainFailure(ex);if(!Terminal(Plan.stage)){Plan.error=ex is UpdateError?ex.Message:"UPDATE_APPLY_FAILED";Save("RECOVERY_REQUIRED");}throw;}
            }
        }
    }
    public static State CommitInstallerUpgrade(string root,string data,string version,Manifest manifest,bool sandbox,string fault,Action<int,string> progress){
        var previous=Engine.ReadState(root);string baseState=Path.Combine(data,"localappdata/Memorive/desktop-review/state");
        string selection=Path.Combine(baseState,"private-profile-selection.json");
        string existingBinding=Path.Combine(root,"updates/binding.json");
        if(File.Exists(existingBinding)){
            var existing=Engine.Load<UpdateBinding>(existingBinding);
            UpdateProtocol.Require(NativePaths.Same(existing.data_root,data)&&NativePaths.Same(existing.source_app,Path.Combine(previous.version,"app")),"UPDATE_INSTALL_BINDING_MISMATCH");
            baseState=Engine.RootPath(existing.state_root);
        }else if(File.Exists(selection)){
            var selected=Engine.Load<Dictionary<string,object>>(selection);
            UpdateProtocol.Require(Convert.ToString(selected["package_id"])==previous.package_id&&Convert.ToString(selected["status"])=="READY","UPDATE_LEGACY_PROFILE_SELECTION_INVALID");
            baseState=Engine.RootPath(Convert.ToString(selected["state_root"]));UpdateProtocol.Require(Engine.Within(baseState,data),"UPDATE_LEGACY_PROFILE_SCOPE_INVALID");
        }
        Bind(root,data,baseState,Path.Combine(previous.version,"app"),false,sandbox,InstallerLocale.Current);
        var engine=new UpdateEngine(root);
        using(var lease=new FileStream(LongFile.N(Path.Combine(engine.Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            UpdateProtocol.Require(!File.Exists(Path.Combine(engine.Home,"activation-gate.json")),"UPDATE_RECOVERY_REQUIRED");
            engine.Plan=new UpdateTransaction{operation_id="setup-"+Guid.NewGuid().ToString("N"),target_package_id=manifest.package_id,new_version=version,
                source_version=previous.version,previous=previous,previous_binding=Engine.Json.Deserialize<UpdateBinding>(Engine.Json.Serialize(engine.Binding)),language=InstallerLocale.Current};
            Directory.CreateDirectory(engine.Operation);engine.Save("APPLYING");
            string profile=Path.Combine(engine.Home,"active-profile.json");if(File.Exists(profile))File.Copy(profile,Path.Combine(engine.Operation,"previous-active-profile.json"));
            var files=manifest.members.Where(x=>x.path.StartsWith("app/")).Select(x=>new Member{path=x.path.Substring(4),size=x.size,sha256=x.sha256.ToLowerInvariant()}).ToArray();
            var target=new UpdateTarget{schema_version="MemoriveTargetManifest-v1",product="Memorive",platform="win-x64",package_id=manifest.package_id,release_version=manifest.release_version,files=files,total_bytes=files.Sum(x=>x.size),inventory_sha256=UpdateProtocol.Fingerprint(files)};
            UpdateProtocol.Members(files,target.total_bytes);UpdateProtocol.VerifyFiles(Path.Combine(version,"app"),files,(n,total)=>{},true);engine.CheckSpace(target);
            Engine.Save(Path.Combine(engine.Operation,"target.json"),target);
            Engine.Save(Path.Combine(engine.Operation,"installer-input.json"),new{transport="VERIFIED_LOCAL_SETUP_BUNDLE",signed_update_index=false,package_sha256=Engine.Hash(Engine.Self),manifest_sha256=Engine.Hash(Path.Combine(version,"manifest.json"))});
            engine.Changed=p=>progress(p.stage=="COMPLETE"?100:p.stage=="ACTIVATING"?94:80,InstallerLocale.Text("stage."+p.stage));
            try{engine.Commit(target,manifest,fault);return Engine.ReadState(root);}
            catch(Exception ex){engine.RetainFailure(ex);if(!Terminal(engine.Plan.stage)){engine.Plan.error=ex is UpdateError?ex.Message:"UPDATE_INSTALLER_UPGRADE_FAILED";engine.Save("RECOVERY_REQUIRED");}throw;}
        }
    }
    UpdateTransaction Commit(UpdateTarget target,Manifest manifest,string fault) {
                    Save("WAITING_IDLE");while(Busy()){UpdateProtocol.Require(!Cancelled(),"UPDATE_CANCELLED");Thread.Sleep(500);}
                    using(var dataLease=new FileStream(LongFile.N(Path.Combine(Binding.data_root,".memorive-update.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
                    using(var executableLease=new FileStream(LongFile.N(Path.Combine(Binding.source_app,"Memorive.exe")),FileMode.Open,FileAccess.Read,FileShare.Delete)){
                    UpdateProtocol.Require(!Busy(),"UPDATE_APPLICATION_RUNNING");
                    Engine.Save(Path.Combine(Home,"activation-gate.json"),new{schema="MemoriveActivationGate-v1",operation_id=Plan.operation_id});
                    Save("BACKING_UP");Maintenance("backup",target);Save("BACKED_UP");Fault("after-backup",fault);
                    Save("MIGRATING");Maintenance("migrate",target);Save("MIGRATED");Fault("after-migrate",fault);
                    Save("VERIFYING_START");Maintenance("verify",target);UpdateProtocol.VerifyFiles(Path.Combine(Plan.new_version,"app"),target.files,(n,total)=>{},true);Fault("after-verify",fault);
                    var next=new State{program_root=Root,data_root=Binding.data_root,webview_root=Engine.WebViewPath(Binding.data_root,true),version=Plan.new_version,previous=Plan.previous==null?null:Plan.previous.version,
                        package_id=target.package_id,contract="desktop-desktop-review-v1",sandbox_only=Binding.sandbox,registered=Plan.previous!=null&&Plan.previous.registered,desktop_shortcut=Plan.previous!=null&&Plan.previous.desktop_shortcut};
                    Engine.Save(Path.Combine(Plan.new_version,"version-state.json"),next);Save("ACTIVATING");Fault("before-activate",fault);
                    Engine.Save(Path.Combine(Root,"current.json"),next);Fault("after-activate",fault);
                    // Stable launch entries are installed only from verified target bytes.
                    InstallEntry(Root,Path.Combine(Plan.new_version,"app/_internal/update/Memorive.Launcher.exe"));
                    if(Binding.portable)InstallEntry(Path.GetDirectoryName(Binding.legacy_entry),Path.Combine(Plan.new_version,"app/_internal/update/Memorive.Launcher.exe"));
                    Engine.Save(Path.Combine(Home,"active-profile.json"),new{schema="MemoriveActiveUpdateProfile-v1",program_root=Root,data_root=Binding.data_root,state_root=Binding.state_root,package_id=target.package_id,operation_id=Plan.operation_id,version=Plan.new_version});
                    Binding.source_app=Path.Combine(Plan.new_version,"app");Binding.source_files=target.files;Binding.source_package=target.package_id;Binding.source_release=target.release_version;Engine.Save(Path.Combine(Home,"binding.json"),Binding);
                    if(next.registered){string host=Engine.Under(Root,"Memorive.Manager-"+manifest.engine_sha256.Substring(0,12)+".exe");if(!File.Exists(host))File.Copy(Engine.Self,host);UpdateProtocol.Require(String.Equals(Engine.Hash(host),manifest.engine_sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_MANAGER_HASH_MISMATCH");Engine.Register(next,host);}
                    Plan.writes_opened=false;SaveTerminal("COMPLETE",fault);return Plan;
                    }
    }
    void InstallEntry(string directory,string source) {
        Engine.NoReparse(directory);string destination=Path.Combine(directory,"Memorive.exe"),tmp=Path.Combine(directory,".memorive-entry-"+Plan.operation_id+".tmp");
        string configuration=Path.Combine(directory,"memorive-launch.json");
        string backup=Path.Combine(directory,"memorive-launch.previous-"+Plan.operation_id+".json");
        if(File.Exists(configuration)&&!File.Exists(backup))File.Copy(configuration,backup);
        Engine.Save(Path.Combine(Operation,"entry-"+UpdateProtocol.HashBytes(Encoding.UTF8.GetBytes(directory))+".json"),new{directory=directory,existed=File.Exists(destination),configuration_existed=File.Exists(configuration)});
        File.Copy(source,tmp);Engine.Save(configuration,new{schema="MemoriveStableLaunch-v1",program_root=Root});
        if(File.Exists(destination))File.Replace(tmp,destination,Path.Combine(directory,"Memorive.previous-"+Plan.operation_id+".exe"));else File.Move(tmp,destination);
    }
    public static void InstallInitialEntry(State state,Manifest manifest){
        string source=Engine.Under(state.version,"app/_internal/update/Memorive.Launcher.exe");
        var row=manifest.members.Single(x=>x.path=="app/_internal/update/Memorive.Launcher.exe");
        UpdateProtocol.Require(String.Equals(Engine.Hash(source),row.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_LAUNCH_HASH_MISMATCH");
        string destination=Path.Combine(state.program_root,"Memorive.exe");
        if(File.Exists(destination))UpdateProtocol.Require(String.Equals(Engine.Hash(destination),row.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_ENTRY_OCCUPIED");
        else File.Copy(source,destination);
        Engine.Save(Path.Combine(state.program_root,"memorive-launch.json"),new{schema="MemoriveStableLaunch-v1",program_root=state.program_root});
    }
    public UpdateTransaction Recover(string operation,string fault=null) {
        Read(operation);if(Terminal(Plan.stage))return ReconcileTerminal(operation);UpdateProtocol.Require(!Plan.writes_opened&&!File.Exists(Path.Combine(Operation,"writes-opened.json")),"UPDATE_NEW_WRITES_BLOCK_RECOVERY");
        UpdateProtocol.Require(new[]{"RECOVERY_REQUIRED","APPLYING","BACKING_UP","BACKED_UP","MIGRATING","MIGRATED","VERIFYING_START","ACTIVATING","COMPLETE","WAITING_IDLE"}.Contains(Plan.stage)&&Plan.new_version!=null,"UPDATE_RECOVERY_STAGE_INVALID");
        UpdateProtocol.Require(!Busy(),"UPDATE_APPLICATION_RUNNING");
        using(var lease=new FileStream(LongFile.N(Path.Combine(Home,"operation.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
        using(var installLease=new FileStream(LongFile.N(Path.Combine(Root,"transaction.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
        using(var dataLease=new FileStream(LongFile.N(Path.Combine(Binding.data_root,".memorive-update.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            Read(operation);OwnGate(false);
            UpdateProtocol.Require(!Plan.writes_opened&&!File.Exists(Path.Combine(Operation,"writes-opened.json")),"UPDATE_NEW_WRITES_BLOCK_RECOVERY");
            if(Terminal(Plan.stage)){if(File.Exists(Path.Combine(Home,"activation-gate.json")))ReconcileTerminalLocked();return Plan;}
            Engine.Save(Path.Combine(Home,"activation-gate.json"),new{schema="MemoriveActivationGate-v1",operation_id=Plan.operation_id});
            if(File.Exists(Path.Combine(Operation,"data-backup.json")))Maintenance("restore",Engine.Load<UpdateTarget>(Path.Combine(Operation,"target.json")));
            if(Plan.previous!=null)Engine.Save(Path.Combine(Root,"current.json"),Plan.previous);
            else if(File.Exists(Path.Combine(Root,"current.json")))File.Delete(Path.Combine(Root,"current.json"));
            foreach(string record in Directory.GetFiles(Operation,"entry-*.json")){
                var entry=Engine.Load<Dictionary<string,object>>(record);string dir=Engine.RootPath(Convert.ToString(entry["directory"]));
                UpdateProtocol.Require(NativePaths.Same(dir,Root)||(Plan.previous_binding.portable&&NativePaths.Same(dir,Path.GetDirectoryName(Plan.previous_binding.legacy_entry))),"UPDATE_ENTRY_RECOVERY_SCOPE_INVALID");
                string dest=Path.Combine(dir,"Memorive.exe"),backup=Path.Combine(dir,"Memorive.previous-"+Plan.operation_id+".exe");
                if(File.Exists(backup)){string restored=Path.Combine(dir,".memorive-restore-"+Guid.NewGuid().ToString("N"));File.Copy(backup,restored);if(File.Exists(dest))File.Replace(restored,dest,null);else File.Move(restored,dest);}
                else if(!Convert.ToBoolean(entry["existed"])&&File.Exists(dest))File.Move(dest,Path.Combine(dir,"Memorive.failed-"+Plan.operation_id+".exe"));
                string config=Path.Combine(dir,"memorive-launch.json"),prior=Path.Combine(dir,"memorive-launch.previous-"+Plan.operation_id+".json");
                if(File.Exists(prior))File.Copy(prior,config,true);else if(!Convert.ToBoolean(entry["configuration_existed"])&&File.Exists(config))File.Delete(config);
            }
            if(Plan.previous_binding!=null)Engine.Save(Path.Combine(Home,"binding.json"),Plan.previous_binding);
            string previousProfile=Path.Combine(Operation,"previous-active-profile.json"),activeProfile=Path.Combine(Home,"active-profile.json");
            if(File.Exists(previousProfile))File.Copy(previousProfile,activeProfile,true);else if(File.Exists(activeProfile))File.Delete(activeProfile);
            SaveTerminal("ROLLED_BACK",fault);return Plan;
        }
    }
}
