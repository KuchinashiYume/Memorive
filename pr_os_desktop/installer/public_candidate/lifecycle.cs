using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;
using File=LongFile;
using Directory=LongDirectory;

class OwnedDataManifest {public int schema; public string owner,profile_id,data_root,owner_sid;public Member[] files;public string[] directories;}
class RemovalPlan {public string root,data_root,version,policy,hash,profile_id;public string[] program_files,program_dirs,legacy_empty_versions,data_files,data_dirs; public Member[] data_inventory; public long data_bytes; public bool sandbox;}
static class Lifecycle {
    [StructLayout(LayoutKind.Sequential)] struct HandleInfo {public uint attributes;public System.Runtime.InteropServices.ComTypes.FILETIME creation,access,write;public uint volume,sizeHigh,sizeLow,links,indexHigh,indexLow;}
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle h,out HandleInfo info);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFile(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetFileInformationByHandle(SafeFileHandle handle,int kind,ref byte delete,uint length);
    static string Canon(string p){return Path.GetFullPath(p).TrimEnd('\\');}
    static bool Equal(string a,string b){return String.Equals(Canon(a),Canon(b),StringComparison.OrdinalIgnoreCase);}
    static void Owner(string root,string marker){Engine.NoReparse(root);var o=Engine.Load<Dictionary<string,object>>(Path.Combine(root,marker));if(!o.ContainsKey("owner")||Convert.ToString(o["owner"])!=Engine.Owner)throw new Exception("OWNER_CONFLICT");}
    public static void Walk(string root,List<string> files,List<string> dirs){Engine.NoReparse(root);foreach(string path in Directory.EnumerateFileSystemEntries(root)){Engine.NoReparse(path);if(Directory.Exists(path)){Walk(path,files,dirs);dirs.Add(path);}else files.Add(path);}}
    static Member Fingerprint(string path){Engine.NoReparse(path);using(var f=new FileStream(LongFile.N(path),FileMode.Open,FileAccess.Read,FileShare.Read)){HandleInfo i;if(!GetFileInformationByHandle(f.SafeFileHandle,out i)||i.links!=1)throw new Exception("HARDLINK_OR_FILE_IDENTITY_UNVERIFIED");return new Member{path=path,size=f.Length,sha256=Engine.HashRange(f,0,f.Length)};}}
    public static RemovalPlan Plan(string root,bool sandbox,bool deleteData,Action<int,string,bool> progress){
        root=Engine.RootPath(root);if(sandbox)Engine.Sandbox(root);Owner(root,"install-owner.json");State s=Engine.ReadState(root);if(s.sandbox_only!=sandbox)throw new Exception("UNINSTALL_SCOPE_MISMATCH");Engine.CheckRoots(root,s.data_root);Engine.CheckRunning(root);
        var files=new List<string>();var dirs=new List<string>();var legacy=new List<string>();var managers=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string versions=Path.Combine(root,"versions");Engine.NoReparse(versions);
        foreach(string version in Directory.GetDirectories(versions)){
            Engine.NoReparse(version);var actual=new List<string>();var tree=new List<string>();Walk(version,actual,tree);
            string mf=Path.Combine(version,"manifest.json");
            if(!File.Exists(mf)){
                // Legacy empty trees require both the old uninstall receipt and exact version receipt.
                string oldRemoval=Path.Combine(root,"uninstall-receipt.json"), oldInstall=Path.Combine(root,"receipts",Path.GetFileName(version)+".json");
                if(actual.Count!=0||!File.Exists(oldRemoval)||!File.Exists(oldInstall))throw new Exception("FAILED_STAGING_REQUIRES_REVIEW");
                var ur=Engine.Load<Dictionary<string,object>>(oldRemoval);var ir=Engine.Load<Dictionary<string,object>>(oldInstall);
                if(!ur.ContainsKey("status")||Convert.ToString(ur["status"])!="PROGRAM_REMOVED_DATA_RETAINED"||!ur.ContainsKey("data_root")||!Equal(Convert.ToString(ur["data_root"]),s.data_root)||!ir.ContainsKey("version")||!Equal(Convert.ToString(ir["version"]),version)||!ir.ContainsKey("status")||Convert.ToString(ir["status"])!="PASS")throw new Exception("EMPTY_VERSION_OWNERSHIP_UNVERIFIED");
                legacy.Add(version);dirs.AddRange(tree);dirs.Add(version);continue;
            }
            Manifest m=Engine.Load<Manifest>(mf);if(m.schema!=1||m.label!="TEST_ONLY_UNSIGNED"||m.members==null||m.contract!=s.contract)throw new Exception("UNINSTALL_MANIFEST_INVALID");
            var allowed=new HashSet<string>(StringComparer.OrdinalIgnoreCase){mf,Path.Combine(version,"version-state.json")};
            foreach(var member in m.members){if(!member.path.StartsWith("app/")&&member.path!="prerequisites/WebView2-X64.exe")throw new Exception("UNINSTALL_MEMBER_ROLE_INVALID");allowed.Add(Engine.Under(version,member.path));}
            if(actual.Any(p=>!allowed.Contains(p)))throw new Exception("UNKNOWN_PROGRAM_FILE_REQUIRES_REVIEW");
            files.AddRange(actual);dirs.AddRange(tree);dirs.Add(version);
            if(m.engine_sha256!=null&&m.engine_sha256.Length==64){foreach(string prefix in new[]{"manager-","Memorive.Manager-"}){string manager=Path.Combine(root,prefix+m.engine_sha256.Substring(0,12)+".exe");if(File.Exists(manager)&&Engine.Hash(manager)==m.engine_sha256)managers.Add(manager);}}
            progress(6+Math.Min(8,files.Count/300),"正在核对程序文件清单",false);
        }
        files.AddRange(managers.Where(p=>!Equal(p,Engine.Self)));
        var plan=new RemovalPlan{root=root,data_root=s.data_root,version=s.version,policy=deleteData?"delete":"keep",sandbox=sandbox,program_files=files.Distinct(StringComparer.OrdinalIgnoreCase).ToArray(),program_dirs=dirs.OrderByDescending(p=>p.Length).ToArray(),legacy_empty_versions=legacy.ToArray(),data_files=new string[0],data_dirs=new string[0],data_inventory=new Member[0]};
        if(deleteData)PrepareData(plan);
        plan.hash=HashPlan(plan);return plan;
    }
    static string HashPlan(RemovalPlan p){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Engine.Json.Serialize(new{p.root,p.data_root,p.version,p.policy,p.profile_id,p.program_files,p.program_dirs,p.legacy_empty_versions,p.data_inventory,p.data_dirs,p.sandbox})))).Replace("-","");}
    static void DataRootGuard(RemovalPlan plan){
        string data=Engine.RootPath(plan.data_root);
        string[] protectedRoots={@"G:\PR-OS",@"G:\PR-OS-沙盒",@"G:\PR-OS-运维",@"G:\PR-OS-ops",Environment.GetFolderPath(Environment.SpecialFolder.Windows),Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86)};
        bool disposable=plan.sandbox&&data.StartsWith(Engine.TestRoot+@"\test_runtime\disposable-data-",StringComparison.OrdinalIgnoreCase);
        if(disposable){var a=Engine.Load<Dictionary<string,object>>(Path.Combine(data,"deletion-fixture-authorization.json"));if(Convert.ToString(a["purpose"])!="DISPOSABLE_SYNTHETIC_ONLY"||!Equal(Convert.ToString(a["exact_root"]),data))throw new Exception("DISPOSABLE_AUTHORIZATION_MISMATCH");}
        foreach(string p in protectedRoots.Where(x=>!String.IsNullOrEmpty(x)))if(Equal(data,p)||Engine.Within(data,p)||Engine.Within(p,data)){if(disposable&&Equal(p,@"G:\PR-OS-沙盒"))continue;throw new Exception("PROTECTED_DATA_ROOT");}
        foreach(var folder in new[]{Environment.SpecialFolder.UserProfile,Environment.SpecialFolder.MyDocuments,Environment.SpecialFolder.LocalApplicationData,Environment.SpecialFolder.ApplicationData,Environment.SpecialFolder.CommonApplicationData}){string p=Environment.GetFolderPath(folder);if(Equal(data,p)||Engine.Within(p,data))throw new Exception("SHARED_PARENT_BLOCKED");}
    }
    static void PrepareData(RemovalPlan plan){
        DataRootGuard(plan);Owner(plan.data_root,"data-owner.json");string manifest=Path.Combine(plan.data_root,"data-ownership.json");
        if(!File.Exists(manifest))throw new Exception("DATA_OWNERSHIP_MANIFEST_REQUIRED：缺少逐项数据归属清单，个人数据将保留。");
        var m=Engine.Load<OwnedDataManifest>(manifest);var marker=Engine.Load<Dictionary<string,object>>(Path.Combine(plan.data_root,"data-owner.json"));
        if(m.schema!=1||m.owner!=Engine.Owner||m.files==null||String.IsNullOrWhiteSpace(m.profile_id)||!Equal(m.data_root,plan.data_root)||m.owner_sid!=WindowsIdentity.GetCurrent().User.Value||!marker.ContainsKey("profile_id")||Convert.ToString(marker["profile_id"])!=m.profile_id)throw new Exception("DATA_PROFILE_OWNER_MISMATCH");
        var allowed=new HashSet<string>(StringComparer.OrdinalIgnoreCase){manifest,Path.Combine(plan.data_root,"data-owner.json")};
        if(plan.sandbox)allowed.Add(Path.Combine(plan.data_root,"deletion-fixture-authorization.json"));
        foreach(var entry in m.files){string p=Engine.Under(plan.data_root,entry.path);if(!allowed.Add(p))throw new Exception("DATA_CATEGORY_OR_FILE_OVERLAP");Member live=Fingerprint(p);if(live.size!=entry.size||live.sha256!=entry.sha256)throw new Exception("DATA_OWNERSHIP_INVENTORY_DRIFT");}
        var actual=new List<string>();var dirs=new List<string>();Walk(plan.data_root,actual,dirs);
        if(actual.Any(p=>!allowed.Contains(p)))throw new Exception("UNCLASSIFIED_DATA_BLOCKER：存在未登记的数据，不能完全删除。");
        var ownedDirs=new HashSet<string>(StringComparer.OrdinalIgnoreCase);foreach(string p in allowed){string d=Path.GetDirectoryName(p);while(d!=null&&!Equal(d,plan.data_root)){ownedDirs.Add(d);d=Path.GetDirectoryName(d);}}foreach(string d in m.directories??new string[0])ownedDirs.Add(Engine.Under(plan.data_root,d));if(dirs.Any(d=>!ownedDirs.Contains(d)))throw new Exception("UNCLASSIFIED_DATA_DIRECTORY_BLOCKER");
        plan.profile_id=m.profile_id;plan.data_files=actual.OrderBy(p=>p,StringComparer.OrdinalIgnoreCase).ToArray();plan.data_dirs=dirs.OrderByDescending(p=>p.Length).ToArray();plan.data_inventory=plan.data_files.Select(Fingerprint).ToArray();plan.data_bytes=plan.data_inventory.Sum(p=>p.size);
    }
    public static void Remove(string root,bool sandbox,bool deleteData,string confirmedHash,Action<int,string,bool> progress,Func<bool> cancel){
        using(var l=new FileStream(LongFile.N(Path.Combine(Engine.RootPath(root),"transaction.lock")),FileMode.Open,FileAccess.ReadWrite,FileShare.None)){
            var p=Plan(root,sandbox,deleteData,progress);if(confirmedHash!=null&&p.hash!=confirmedHash)throw new Exception("REMOVAL_PLAN_CHANGED_CONFIRM_AGAIN");if(deleteData&&confirmedHash==null)throw new Exception("DATA_DELETE_CONFIRMATION_REQUIRED");
            // No deletion precedes this cancellation and inventory boundary.
            if(cancel())throw new OperationCanceledException("卸载已取消，程序和个人数据均未更改。");
            var locks=new List<FileStream>();var dataLocks=new List<FileStream>();
            try {
            foreach(var member in p.data_inventory){Engine.NoReparse(member.path);var handle=CreateFile(LongFile.N(member.path),0x80000000|0x00010000,0,IntPtr.Zero,3,0x00200000,IntPtr.Zero);if(handle.IsInvalid){handle.Dispose();throw new IOException("DATA_EXCLUSIVE_HANDLE_UNAVAILABLE");}var stream=new FileStream(handle,FileAccess.Read);dataLocks.Add(stream);HandleInfo info;if(!GetFileInformationByHandle(handle,out info)||info.links!=1||(info.attributes&0x400)!=0||stream.Length!=member.size||Engine.HashRange(stream,0,stream.Length)!=member.sha256)throw new Exception("DATA_IDENTITY_CHANGED_BEFORE_COMMIT");}
            try{
                foreach(string file in p.program_files){Engine.NoReparse(file);locks.Add(new FileStream(LongFile.N(file),FileMode.Open,FileAccess.Read,FileShare.None));}
                if(!sandbox)using(var key=Registry.CurrentUser.OpenSubKey(Engine.RegPath)){if(key!=null&&!Equal(Convert.ToString(key.GetValue("InstallLocation")),p.root))throw new Exception("UNINSTALL_REGISTRY_OWNER_MISMATCH");}
                if(cancel())throw new OperationCanceledException("卸载已取消，程序和个人数据均未更改。");
            }finally{foreach(var f in locks)f.Dispose();}
            progress(20,"正在移除程序文件",true);
            int count=0;foreach(string f in p.program_files){Engine.NoReparse(f);File.Delete(f);count++;if(count%60==0)progress(20+count*50/p.program_files.Length,"正在移除程序文件",true);}
            foreach(string d in p.program_dirs){Engine.NoReparse(d);if(Directory.Exists(d)&&!Directory.EnumerateFileSystemEntries(d).Any())Directory.Delete(d);}
            if(!sandbox)RemoveRegistration(p.root);
            string receipt=Path.Combine(p.root,"receipts","uninstall-"+Guid.NewGuid().ToString("N")+".json");
            Engine.Save(receipt,new{status="PROGRAM_REMOVED_DATA_RETAINED",data_root=p.data_root,plan_hash=p.hash,program_removed=true,data_removed=false,created=DateTime.UtcNow.ToString("o")});
            if(deleteData){
                progress(80,"正在核对已确认的数据清单",true);
                // Revalidate all hashes and exact membership after program removal; no broad sweep.
                var liveFiles=new List<string>();var liveDirs=new List<string>();Walk(p.data_root,liveFiles,liveDirs);if(!new HashSet<string>(liveFiles,StringComparer.OrdinalIgnoreCase).SetEquals(p.data_files)||!new HashSet<string>(liveDirs,StringComparer.OrdinalIgnoreCase).SetEquals(p.data_dirs))throw new Exception("DATA_CHANGED_AFTER_PROGRAM_REMOVAL_DATA_RETAINED");
                for(int i=0;i<dataLocks.Count;i++){HandleInfo info;var f=dataLocks[i];if(!GetFileInformationByHandle(f.SafeFileHandle,out info)||info.links!=1||f.Length!=p.data_inventory[i].size||Engine.HashRange(f,0,f.Length)!=p.data_inventory[i].sha256)throw new Exception("DATA_IDENTITY_CHANGED_DATA_RETAINED");}
                // Delete through the verified, exclusively held handles; never reopen a path for deletion.
                count=0;foreach(var f in dataLocks){byte remove=1;if(!SetFileInformationByHandle(f.SafeFileHandle,4,ref remove,1))throw new IOException("DATA_DELETE_PARTIAL_REQUIRES_REVIEW_"+Marshal.GetLastWin32Error());count++;progress(80+count*15/p.data_files.Length,"正在删除已确认的个人数据",true);}foreach(var f in dataLocks)f.Dispose();dataLocks.Clear();
                foreach(string d in p.data_dirs)if(Directory.Exists(d)&&!Directory.EnumerateFileSystemEntries(d).Any())Directory.Delete(d);
                if(Directory.EnumerateFileSystemEntries(p.data_root).Any())throw new Exception("DATA_REMOVAL_RESIDUAL_REQUIRES_REVIEW");Directory.Delete(p.data_root);
            }
            var result=new{status=deleteData?"PROGRAM_AND_SELECTED_DATA_REMOVED":"PROGRAM_REMOVED_DATA_RETAINED",data_root=p.data_root,plan_hash=p.hash,program_removed=true,data_removed=deleteData,evidence_retained=true,legacy_empty_versions=p.legacy_empty_versions,created=DateTime.UtcNow.ToString("o")};
            File.Delete(Path.Combine(p.root,"current.json"));
            Engine.Save(Path.Combine(p.root,"uninstall-receipt.json"),result);Engine.Save(receipt+".completed.json",result);progress(100,"卸载完成",true);
            } finally {foreach(var f in dataLocks)f.Dispose();}
        }
    }
    static void RemoveRegistration(string root){Engine.RemoveLegacyShortcuts(root);State s=Engine.ReadState(root);Registry.CurrentUser.DeleteSubKeyTree(Engine.RegPath,false);string menu=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"Memorive");foreach(string name in new[]{"Memorive.lnk","维护 Memorive.lnk"}){string p=Path.Combine(menu,name);if(File.Exists(p))File.Delete(p);}if(Directory.Exists(menu)&&!Directory.EnumerateFileSystemEntries(menu).Any())Directory.Delete(menu);string desktop=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"Memorive.lnk");if(s.desktop_shortcut&&File.Exists(desktop))File.Delete(desktop);}
}
