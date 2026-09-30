// INSTALLATION/CROSS. Local unsigned installer trial; no system Python, Node or WebView2 needed by this UI.
using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Drawing;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Win32;
using File = LongFile;
using Directory = LongDirectory;

// Explicit extended paths work even when the machine has not opted into long paths.
static class LongFile {
    public static string N(string p){return p.StartsWith(@"\\?\")?p:@"\\?\"+Path.GetFullPath(p);}
    public static string Plain(string p){return p.StartsWith(@"\\?\")?p.Substring(4):p;}
    public static bool Exists(string p){return System.IO.File.Exists(N(p));}
    public static FileAttributes GetAttributes(string p){return System.IO.File.GetAttributes(N(p));}
    public static FileStream OpenRead(string p){return System.IO.File.OpenRead(N(p));}
    public static string ReadAllText(string p,Encoding e){using(var file=new FileStream(N(p),FileMode.Open,FileAccess.Read,FileShare.Read|FileShare.Delete))using(var reader=new StreamReader(file,e,true)){return reader.ReadToEnd();}}
    public static void WriteAllBytes(string p,byte[] b){System.IO.File.WriteAllBytes(N(p),b);}
    public static void Delete(string p){System.IO.File.Delete(N(p));}
    public static void Copy(string a,string b){System.IO.File.Copy(N(a),N(b));}
    public static void Copy(string a,string b,bool overwrite){System.IO.File.Copy(N(a),N(b),overwrite);}
    public static void Move(string a,string b){System.IO.File.Move(N(a),N(b));}
    public static void Replace(string a,string b,string c){System.IO.File.Replace(N(a),N(b),c==null?null:N(c));}
}
static class LongDirectory {
    public static bool Exists(string p){return System.IO.Directory.Exists(LongFile.N(p));}
    public static void CreateDirectory(string p){System.IO.Directory.CreateDirectory(LongFile.N(p));}
    public static void Delete(string p){System.IO.Directory.Delete(LongFile.N(p));}
    public static IEnumerable<string> EnumerateFileSystemEntries(string p){return System.IO.Directory.EnumerateFileSystemEntries(LongFile.N(p)).Select(LongFile.Plain);}
    public static string[] GetFiles(string p,string pattern){return System.IO.Directory.GetFiles(LongFile.N(p),pattern).Select(LongFile.Plain).ToArray();}
    public static string[] GetFiles(string p,string pattern,SearchOption o){return System.IO.Directory.GetFiles(LongFile.N(p),pattern,o).Select(LongFile.Plain).ToArray();}
    public static string[] GetDirectories(string p){return System.IO.Directory.GetDirectories(LongFile.N(p)).Select(LongFile.Plain).ToArray();}
}

class Member { public string path; public long size; public string sha256; }
class Manifest {
    public int schema; public string package_id, label, contract, health_phase, app_exe, engine_sha256, payload_sha256, release_version;
    public long total_bytes; public Member[] members; public string[] required_files;
}
class State {
    public int schema = 1; public string owner = Engine.Owner;
    public string program_root, data_root, webview_root, version, previous, package_id, contract;
    public bool registered, desktop_shortcut, sandbox_only;
}
class Bundle : IDisposable {
    public FileStream file; public long engineSize, zipSize, manifestSize;
    public Manifest manifest; public byte[] manifestBytes;
    public Bundle(string path) {
        file = new FileStream(LongFile.N(path), FileMode.Open, FileAccess.Read, FileShare.Read);
        if(file.Length < 40) throw new Exception("PACKAGE_FOOTER_MISSING");
        file.Position = file.Length-40;
        using(var br = new BinaryReader(file, Encoding.UTF8, true)) {
            engineSize=br.ReadInt64(); zipSize=br.ReadInt64(); manifestSize=br.ReadInt64();
            if(Encoding.ASCII.GetString(br.ReadBytes(16))!="MEMORIVESETUP001") throw new Exception("PACKAGE_IDENTITY_INVALID");
        }
        if(engineSize<1024 || zipSize<22 || manifestSize<100 || manifestSize>8*1024*1024 || engineSize+zipSize+manifestSize+40!=file.Length)
            throw new Exception("PACKAGE_LENGTH_INVALID");
        file.Position=engineSize+zipSize;
        manifestBytes=new byte[(int)manifestSize]; int done=0;
        while(done<manifestBytes.Length) { int n=file.Read(manifestBytes,done,manifestBytes.Length-done); if(n==0) throw new EndOfStreamException(); done+=n; }
        manifest=Engine.Json.Deserialize<Manifest>(Encoding.UTF8.GetString(manifestBytes));
        if(manifest.schema!=1 || manifest.label!="TEST_ONLY_UNSIGNED" || manifest.contract!="desktop-desktop-review-v1" || !new[]{"Memorive.exe","Memorive.exe"}.Contains(manifest.app_exe))
            throw new Exception("UNSUPPORTED_PACKAGE_CONTRACT");
        if(!System.Text.RegularExpressions.Regex.IsMatch(manifest.package_id,@"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\z")) throw new Exception("PACKAGE_ID_INVALID");
        if(!new[]{"v101-empty","desktop-console","window-lifecycle","console-ui","console-probe","construction-a"}.Contains(manifest.health_phase)) throw new Exception("HEALTH_CONTRACT_UNSUPPORTED");
        var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach(var m in manifest.members) {
            Engine.Relative(m.path);
            if(!names.Add(m.path) || m.size<0 || m.sha256==null || m.sha256.Length!=64 || (!m.path.StartsWith("app/") && m.path!="prerequisites/WebView2-X64.exe")) throw new Exception("MANIFEST_MEMBER_INVALID");
        }
        foreach(var name in manifest.required_files) if(!names.Contains("app/"+name)) throw new Exception("REQUIRED_MEMBER_MISSING");
    }
    public void Verify() {
        if(Engine.HashRange(file,0,engineSize)!=manifest.engine_sha256) throw new Exception("SETUP_HOST_HASH_MISMATCH");
        if(Engine.HashRange(file,engineSize,zipSize)!=manifest.payload_sha256) throw new Exception("PAYLOAD_HASH_MISMATCH");
    }
    public void Extract(string target, Action<int,string> progress) {
        file.Position=engineSize;
        using(var slice=new Slice(file,engineSize,zipSize)) using(var zip=new ZipArchive(slice,ZipArchiveMode.Read,true)) {
            if(zip.Entries.Count!=manifest.members.Length) throw new Exception("ZIP_MEMBER_COUNT_MISMATCH");
            var entries=new Dictionary<string,ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
            foreach(var e in zip.Entries) { Engine.Relative(e.FullName); if(entries.ContainsKey(e.FullName)) throw new Exception("ZIP_DUPLICATE"); entries.Add(e.FullName,e); }
            int i=0;
            foreach(var m in manifest.members) {
                ZipArchiveEntry entry;
                if(!entries.TryGetValue(m.path,out entry) || entry.Length!=m.size) throw new Exception("ZIP_MEMBER_MISMATCH");
                string dst=Engine.Under(target,m.path); Directory.CreateDirectory(Path.GetDirectoryName(dst));
                using(var inp=entry.Open()) using(var output=new FileStream(LongFile.N(dst),FileMode.CreateNew,FileAccess.Write,FileShare.None)) inp.CopyTo(output);
                if(new FileInfo(LongFile.N(dst)).Length!=m.size || Engine.Hash(dst)!=m.sha256) throw new Exception("FILE_HASH_MISMATCH: "+m.path);
                i++; if(i%30==0 || i==manifest.members.Length) progress(15+i*50/manifest.members.Length,"安装程序与自带运行时  "+i+" / "+manifest.members.Length);
            }
        }
    }
    public void HostCopy(string path) {
        file.Position=0;
        using(var dst=new FileStream(LongFile.N(path),FileMode.CreateNew,FileAccess.Write,FileShare.None)) { byte[] b=new byte[1024*1024]; long n=engineSize; while(n>0) { int r=file.Read(b,0,(int)Math.Min(b.Length,n)); if(r==0) throw new EndOfStreamException(); dst.Write(b,0,r); n-=r; } dst.Flush(true); }
    }
    public void Dispose() { if(file!=null) file.Dispose(); }
}
class Slice : Stream {
    Stream s; long start,len,pos;
    public Slice(Stream source,long offset,long length){s=source;start=offset;len=length;pos=0;}
    public override int Read(byte[] b,int o,int n){s.Position=start+pos; int got=s.Read(b,o,(int)Math.Min(n,len-pos));pos+=got;return got;}
    public override long Seek(long off,SeekOrigin from){long p=(from==SeekOrigin.Begin?0:from==SeekOrigin.Current?pos:len)+off;if(p<0||p>len)throw new IOException();pos=p;return p;}
    public override long Position{get{return pos;}set{Seek(value,SeekOrigin.Begin);}}
    public override long Length{get{return len;}}
    public override bool CanRead{get{return true;}} public override bool CanSeek{get{return true;}} public override bool CanWrite{get{return false;}}
    public override void Flush(){} public override void SetLength(long v){throw new NotSupportedException();} public override void Write(byte[] b,int o,int n){throw new NotSupportedException();}
}

static class NativePaths {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFile(string path,uint access,uint share,IntPtr security,uint disposition,uint flags,IntPtr template);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(Microsoft.Win32.SafeHandles.SafeFileHandle handle,StringBuilder path,uint size,uint flags);
    public static string Physical(string value){
        string path=Path.GetFullPath(value);var suffix=new Stack<string>();
        while(!LongFile.Exists(path)&&!LongDirectory.Exists(path)){suffix.Push(Path.GetFileName(path));string parent=Path.GetDirectoryName(path);if(parent==null)throw new IOException("PATH_VOLUME_UNAVAILABLE");path=parent;}
        using(var handle=CreateFile(LongFile.N(path),0,7,IntPtr.Zero,3,0x02000000,IntPtr.Zero)){
            if(handle.IsInvalid)throw new IOException("PATH_IDENTITY_UNAVAILABLE_"+Marshal.GetLastWin32Error());
            var buffer=new StringBuilder(32768);uint size=GetFinalPathNameByHandle(handle,buffer,(uint)buffer.Capacity,0);
            if(size==0||size>=buffer.Capacity)throw new IOException("PATH_IDENTITY_UNAVAILABLE_"+Marshal.GetLastWin32Error());
            path=LongFile.Plain(buffer.ToString());
        }
        while(suffix.Count>0)path=Path.Combine(path,suffix.Pop());return path.TrimEnd('\\');
    }
    public static bool Same(string a,string b){return String.Equals(Path.GetFullPath(a).TrimEnd('\\'),Path.GetFullPath(b).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase)||String.Equals(Physical(a),Physical(b),StringComparison.OrdinalIgnoreCase);}
    public static string WorkingDirectory(string executable){string path=Path.GetDirectoryName(Path.GetFullPath(executable));while(path.Length>=248)path=Path.GetDirectoryName(path);return path;}
}

static class Engine {
    public static string TestRoot {get{var trust=UpdateProtocol.Trust;if(trust.test_only){if(String.IsNullOrEmpty(trust.test_root)||Path.GetFileName(trust.test_root)!="Memorive-Installer-Tests")throw new UpdateError("UPDATE_TEST_ROOT_INVALID");return RootPath(trust.test_root);}return Path.Combine(Path.GetTempPath(),"Memorive-Installer-Tests");}}
    public static string TestWebViewRoot {get{return Path.Combine(Path.GetDirectoryName(TestRoot),"Memorive-Installer-WV");}}
    public const string Owner="Memorive-INSTALLER-TRIAL-V1";
    public const string RegPath=@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Memorive";
    public static JavaScriptSerializer Json=new JavaScriptSerializer{MaxJsonLength=16*1024*1024};
    public static string Self {get{return Application.ExecutablePath;}}
    public static string DefaultProgram {get{return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","Memorive");}}
    public static string DefaultData {get{return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Memorive-Data");}}
    public static string WebViewPath(string data,bool create) {
        string id;using(var sha=SHA256.Create())id=BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(data).ToUpperInvariant()))).Replace("-","").Substring(0,16);
        string parent=(data.Equals(TestRoot,StringComparison.OrdinalIgnoreCase)||data.StartsWith(TestRoot+@"\",StringComparison.OrdinalIgnoreCase))?TestWebViewRoot:Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Memorive-Installer-WV2");
        string path=Path.Combine(parent,id);NoReparse(path);if(path.Length>96)throw new Exception("WEBVIEW2_SHORT_PATH_UNAVAILABLE");
        if(create){CheckOwner(path,"data-owner.json");Directory.CreateDirectory(path);string marker=Path.Combine(path,"data-owner.json");if(!File.Exists(marker))Save(marker,new{owner=Owner,data_root=data,role="WEBVIEW2_PROFILE"});}
        return path;
    }
    public static string Hash(string file) {using(var f=File.OpenRead(file)) using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(f)).Replace("-","");}
    public static string HashRange(Stream f,long start,long count) { f.Position=start; using(var h=SHA256.Create()) {byte[] b=new byte[1024*1024];while(count>0){int n=f.Read(b,0,(int)Math.Min(b.Length,count));if(n==0)throw new EndOfStreamException();h.TransformBlock(b,0,n,null,0);count-=n;}h.TransformFinalBlock(new byte[0],0,0);return BitConverter.ToString(h.Hash).Replace("-","");} }
    public static void Save(string path,object value) {Directory.CreateDirectory(Path.GetDirectoryName(path));string tmp=Path.Combine(Path.GetDirectoryName(path),"."+Guid.NewGuid().ToString("N")+".tmp");try{using(var f=new FileStream(LongFile.N(tmp),FileMode.CreateNew,FileAccess.Write,FileShare.None)){byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(value));f.Write(bytes,0,bytes.Length);f.Flush(true);}for(int attempt=0;;attempt++){try{if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);break;}catch(IOException ex){int code=ex.HResult&0xffff;if(attempt>=19||(code!=32&&code!=33))throw;Thread.Sleep(50);}}}finally{if(File.Exists(tmp))File.Delete(tmp);}}
    public static T Load<T>(string path) {return Json.Deserialize<T>(File.ReadAllText(path,Encoding.UTF8));}
    public static void Relative(string path) {if(String.IsNullOrWhiteSpace(path)||path.StartsWith("/")||path.Contains("\\")||path.Contains(":")||path.IndexOf('\0')>=0)throw new Exception("UNSAFE_MEMBER_PATH");foreach(string p in path.Split('/'))if(p==""||p=="."||p==".."||p.TrimEnd(' ','.')!=p||System.Text.RegularExpressions.Regex.IsMatch(p,@"\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)",System.Text.RegularExpressions.RegexOptions.IgnoreCase))throw new Exception("UNSAFE_MEMBER_COMPONENT");}
    public static string Under(string root,string relative) {Relative(relative);string full=Path.GetFullPath(Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar)));if(!Within(full,root))throw new Exception("PATH_ESCAPE");NoReparse(full);return full;}
    public static bool Within(string path,string root) {return Path.GetFullPath(path).StartsWith(Path.GetFullPath(root).TrimEnd('\\')+"\\",StringComparison.OrdinalIgnoreCase);}
    public static void NoReparse(string path) {string p=Path.GetFullPath(path);while(p!=null){if((File.Exists(p)||Directory.Exists(p))&&(File.GetAttributes(p)&FileAttributes.ReparsePoint)!=0)throw new Exception("REPARSE_POINT_BLOCKED");p=Path.GetDirectoryName(p);}}
    public static string RootPath(string path) {string r=Path.GetFullPath(path).TrimEnd('\\');NoReparse(r);if(r.StartsWith(@"\\")||r.Length<8||new DriveInfo(Path.GetPathRoot(r)).DriveType!=DriveType.Fixed)throw new Exception("LOCAL_FIXED_VOLUME_REQUIRED");return r;}
    public static State ReadState(string root) {var s=Load<State>(Path.Combine(root,"current.json"));if(s.owner!=Owner||s.schema!=1||!NativePaths.Same(s.program_root,root)||!Within(NativePaths.Physical(s.version),NativePaths.Physical(root)))throw new Exception("INSTALL_OWNERSHIP_MISMATCH");NoReparse(s.version);return s;}
    public static void CheckRoots(string root,string data) {root=RootPath(root);data=RootPath(data);string physicalRoot=NativePaths.Physical(root),physicalData=NativePaths.Physical(data);if(physicalRoot.Equals(physicalData,StringComparison.OrdinalIgnoreCase)||Within(physicalRoot,physicalData)||Within(physicalData,physicalRoot))throw new Exception("PROGRAM_DATA_OVERLAP");CheckOwner(root,"install-owner.json");CheckOwner(data,"data-owner.json");}
    static void CheckOwner(string dir,string marker) {if(!Directory.Exists(dir))return;string m=Path.Combine(dir,marker);if(File.Exists(m)){var x=Load<Dictionary<string,object>>(m);if(!x.ContainsKey("owner")||Convert.ToString(x["owner"])!=Owner)throw new Exception("OWNER_CONFLICT");}else if(Directory.EnumerateFileSystemEntries(dir).Any())throw new Exception("EXISTING_UNOWNED_DIRECTORY");}
    public static void Sandbox(string root) {string current=RootPath(root);if(!current.StartsWith(TestRoot+"\\",StringComparison.OrdinalIgnoreCase)&&!NativePaths.Physical(current).StartsWith(NativePaths.Physical(TestRoot)+"\\",StringComparison.OrdinalIgnoreCase))throw new Exception("AUTOMATION_REQUIRES_EXACT_SANDBOX");}
    public static void VmTest(string root,string data) {throw new Exception("VM_TEST_NOT_INCLUDED_IN_THIS_CANDIDATE");}
    public static string WebViewVersion() {if(UiBootstrap.TestMissingWebView)return null;foreach(var hive in new[]{RegistryHive.CurrentUser,RegistryHive.LocalMachine})foreach(var view in new[]{RegistryView.Registry32,RegistryView.Registry64})using(var b=RegistryKey.OpenBaseKey(hive,view))using(var k=b.OpenSubKey(@"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")){if(k!=null){string v=Convert.ToString(k.GetValue("pv"));Version n;if(Version.TryParse(v,out n)&&n>new Version(0,0,0,0))return v;}}return null;}
    public const string VcDownloadUrl="https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist";
    public const string WebViewDownloadUrl="https://developer.microsoft.com/en-us/microsoft-edge/webview2/";
    public static bool VcVersionSupported(Version version){return version!=null&&version>=new Version(14,51,36247,0);}
    public static string VcRuntimeStatus(){
        foreach(string name in new[]{"MSVCP140.dll","MSVCP140_1.dll"}){
            string file=Path.Combine(Environment.SystemDirectory,name);
            if(!File.Exists(file))return "缺少 Microsoft Visual C++ v14 x64 运行库";
            var info=FileVersionInfo.GetVersionInfo(file);
            var version=new Version(info.FileMajorPart,info.FileMinorPart,info.FileBuildPart,info.FilePrivatePart);
            if(!VcVersionSupported(version))return "需要 Microsoft Visual C++ v14 x64 14.51.36247 或更新版本";
        }
        return null;
    }
    public static int NetRelease() {using(var k=Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"))return k==null?0:Convert.ToInt32(k.GetValue("Release",0));}
    public static string SystemCheck() {int build=Environment.OSVersion.Version.Build;bool x64=Environment.Is64BitOperatingSystem;string web=WebViewVersion();return "Windows  "+Environment.OSVersion.Version+"  /  "+(x64?"x64":"不支持的架构")+"\r\n\r\n.NET Framework  "+(NetRelease()>=528040?"4.8 或更高 · 已具备":"未满足 4.8，安装阻断")+"\r\n\r\nWebView2  "+(web??"未检测到 · 请先从 Microsoft 官网安装")+"\r\n\r\nPython 3.13  ·  随程序携带；Visual C++ v14 x64  ·  使用官方系统组件\r\n\r\n目标范围  Windows 10 22H2 / Windows 11 x64\r\n清洁系统兼容性资格仍待独立验证。";}
    static void RequirePlatform() {if(!Environment.Is64BitOperatingSystem||Environment.OSVersion.Version.Major<10||Environment.OSVersion.Version.Build<19045)throw new Exception("WINDOWS_10_22H2_OR_WINDOWS_11_X64_REQUIRED");if(NetRelease()<528040)throw new Exception("NET_FRAMEWORK_48_REQUIRED");if(VcRuntimeStatus()!=null)throw new Exception("VISUAL_CPP_X64_REQUIRED: "+VcRuntimeStatus()+" · "+VcDownloadUrl);}
    public static string ExternalTools() {var lines=new List<string>();foreach(string tool in new[]{"ollama","codex","claude","gemini","qwen","kimi","codebuddy","copilot"}){bool found=false;foreach(string dir in (Environment.GetEnvironmentVariable("PATH")??"").Split(';')){if(String.IsNullOrWhiteSpace(dir))continue;foreach(string ext in new[]{".exe",".cmd",".bat"})try{if(File.Exists(Path.Combine(dir.Trim('"'),tool+ext)))found=true;}catch{}}lines.Add(tool.PadRight(14)+"  "+(found?"已检测到入口":"未检测到"));}return String.Join("\r\n\r\n",lines)+"\r\n\r\n仅检查命令入口；不代表已登录或模型可用。";}
    public static void CheckRunning(string root) {foreach(var p in Process.GetProcessesByName("Memorive").Concat(Process.GetProcessesByName("Memorive"))){try{string file=p.MainModule.FileName;if(Within(file,root))throw new InvalidOperationException("APPLICATION_RUNNING_CLOSE_FIRST");}catch(InvalidOperationException){throw;}catch{throw new Exception("APPLICATION_PROCESS_INSPECTION_FAILED");}finally{p.Dispose();}}}
    static void Space(string root,long bytes) {var disk=new DriveInfo(Path.GetPathRoot(root));if(disk.AvailableFreeSpace<bytes+1024L*1024*1024)throw new Exception("INSUFFICIENT_DISK_SPACE");}
    static void ValidateInstalled(string version,Manifest m) {NoReparse(version);foreach(var f in m.members.Where(x=>x.path.StartsWith("app/"))){string p=Under(Path.Combine(version,"app"),f.path.Substring(4));if(!File.Exists(p)||new FileInfo(LongFile.N(p)).Length!=f.size||Hash(p)!=f.sha256)throw new Exception("INSTALLED_FILE_HASH_MISMATCH: "+f.path);}}
    public static ProcessStartInfo AppStart(string version,string data,bool probe,Manifest m) {
        string app=Under(version,"app/"+m.app_exe);string local=Path.Combine(data,"localappdata");
        foreach(string sub in new[]{"localappdata","appdata","temp","home","workspace"})Directory.CreateDirectory(Path.Combine(data,sub));
        var si=new ProcessStartInfo(app){UseShellExecute=false,WorkingDirectory=NativePaths.WorkingDirectory(app),CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden};
        if(probe){si.EnvironmentVariables.Clear();foreach(string name in new[]{"SystemRoot","WINDIR","SystemDrive","COMSPEC","ProgramFiles","ProgramFiles(x86)","ProgramW6432","NUMBER_OF_PROCESSORS","PROCESSOR_ARCHITECTURE"}){string v=Environment.GetEnvironmentVariable(name);if(v!=null)si.EnvironmentVariables[name]=v;}si.EnvironmentVariables["PATH"]=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"System32");si.EnvironmentVariables["USERPROFILE"]=Path.Combine(data,"home");si.EnvironmentVariables["MEMORIVE_Desktop_TEST_FIXTURE_MODE"]="1";
            if(m.health_phase=="v101-empty"){si.EnvironmentVariables.Remove("MEMORIVE_Desktop_TEST_FIXTURE_MODE");si.Arguments="--memo-install-health "+Quote(data);}else{si.Arguments="--desktop-test-mode --desktop-phase "+m.health_phase+" --desktop-test-pdf "+Quote(Under(version,"app/_internal/fixtures/source/sample.pdf"));}
        }
        si.EnvironmentVariables["LOCALAPPDATA"]=local;si.EnvironmentVariables["APPDATA"]=Path.Combine(data,"appdata");si.EnvironmentVariables["TEMP"]=Path.Combine(data,"temp");si.EnvironmentVariables["TMP"]=Path.Combine(data,"temp");si.EnvironmentVariables["PYTHONUTF8"]="1";si.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"]="1";
        si.EnvironmentVariables["MEMORIVE_WEBVIEW2_STORAGE_PATH"]=WebViewPath(data,true);
        si.EnvironmentVariables["MEMORIVE_WORKSPACE_ROOT"]=Path.Combine(data,"workspace");
        Save(Path.Combine(data,"installer-directory-roles.json"),new{owner=Owner,data_root=data,localappdata=local,webview2=si.EnvironmentVariables["MEMORIVE_WEBVIEW2_STORAGE_PATH"],program_root=version});
        return si;
    }
    public static string Quote(string s){return "\""+s.Replace("\"", "")+"\"";}
    public static void Health(string version,string probe,Manifest m,Action<int,string> progress) {
        progress(76,"正在检查运行环境和首次启动");
        Directory.CreateDirectory(probe);
        using(var p=Process.Start(AppStart(version,probe,true,m))) {
            if(!p.WaitForExit(240000)){try{p.CloseMainWindow();if(!p.WaitForExit(5000))p.Kill();}catch{}throw new Exception("APP_HEALTH_TIMEOUT");}
            if(p.ExitCode!=0)throw new Exception("APP_HEALTH_FAILED_EXIT_"+p.ExitCode);
        }
        string expected=m.health_phase=="v101-empty"?"v101_health.json":m.health_phase=="desktop-console"?"desktop-console_native.json":m.health_phase=="window-lifecycle"?"window-lifecycle_packaged_native.json":m.health_phase=="console-ui"?"console-ui_packaged_native.json":m.health_phase=="console-probe"?"console-probe_packaged_native.json":"pywebview_product_construction-a.json";
        var receipts=new[]{Path.Combine(probe,"localappdata","Memorive","desktop-review","evidence",expected)};
        if(!File.Exists(receipts[0])||Convert.ToString(Load<Dictionary<string,object>>(receipts[0])["status"])!="PASS")throw new Exception("APP_HEALTH_RECEIPT_MISSING_OR_FAILED");
        Save(Path.Combine(probe,"installer-health-result.json"),new{status="PASS",kind=m.health_phase=="v101-empty"?"EMPTY_OFFLINE_RUNTIME":"PACKAGED_NATIVE_SELF_TEST",app_hash=Hash(Under(version,"app/"+m.app_exe)),phase=m.health_phase,receipt_count=receipts.Length,clean_windows_qualified=false});
    }
    static void Prerequisite(string version,bool sandbox,Action<int,string> progress) {
        if(WebViewVersion()==null)throw new Exception("WEBVIEW2_REQUIRED: 请先从 Microsoft 官网安装运行库 · "+WebViewDownloadUrl);
        if(VcRuntimeStatus()!=null)throw new Exception("VISUAL_CPP_X64_REQUIRED: "+VcRuntimeStatus()+" · "+VcDownloadUrl);
    }
    public static Version ReleaseOrder(string text) {
        if(String.IsNullOrEmpty(text)||!System.Text.RegularExpressions.Regex.IsMatch(text,@"^(?:[0-9]+\.[0-9]{2,}|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)$"))throw new Exception("RELEASE_VERSION_INVALID");
        string[] p=text.Split('.');return new Version(Int32.Parse(p[0]),Int32.Parse(p[1]),p.Length==4?Int32.Parse(p[2]):0,p.Length==4?Int32.Parse(p[3]):0);
    }
    public static State Install(string package,string root,string data,bool desktop,bool sandbox,string fault,Action<int,string> progress) {
        root=RootPath(root);data=RootPath(data);if(sandbox){Sandbox(root);Sandbox(data);}else if(!String.IsNullOrEmpty(fault))throw new Exception("FAULT_TEST_ONLY");
        RequirePlatform();CheckRoots(root,data);CheckRunning(root);
        using(var b=new Bundle(package)) {
            progress(3,"校验安装包完整性");b.Verify();Space(root,b.manifest.total_bytes*2);Space(data,128*1024*1024);
            State old=File.Exists(Path.Combine(root,"current.json"))?ReadState(root):null;
            ReleaseOrder(b.manifest.release_version);
            if(old!=null){var oldManifest=Load<Manifest>(Path.Combine(old.version,"manifest.json"));string oldRelease=oldManifest.release_version??FileVersionInfo.GetVersionInfo(Under(old.version,"app/"+oldManifest.app_exe)).FileVersion;if(ReleaseOrder(b.manifest.release_version)<ReleaseOrder(oldRelease))throw new Exception("VERSION_DOWNGRADE_BLOCKED_USE_ROLLBACK");}
            if(old!=null&&(!NativePaths.Same(old.data_root,data)||old.contract!=b.manifest.contract||old.sandbox_only!=sandbox))throw new Exception("DATA_BINDING_OR_CONTRACT_CHANGED");
            if(old!=null){root=old.program_root;data=old.data_root;}
            if(!sandbox)using(var key=Registry.CurrentUser.OpenSubKey(RegPath)){if(key!=null&&Convert.ToString(key.GetValue("InstallLocation"))!=root)throw new Exception("OTHER_TRIAL_INSTALLATION_REGISTERED");}
            Directory.CreateDirectory(root);Directory.CreateDirectory(data);
            if(!File.Exists(Path.Combine(root,"install-owner.json")))Save(Path.Combine(root,"install-owner.json"),new{owner=Owner,root=root,created=DateTime.UtcNow.ToString("o")});
            if(!File.Exists(Path.Combine(data,"data-owner.json")))Save(Path.Combine(data,"data-owner.json"),new{owner=Owner,data_root=data});
            InstallerLocale.Remember(root);
            if(old==null&&!File.Exists(Path.Combine(data,"installer-default-language.json")))Save(Path.Combine(data,"installer-default-language.json"),new{owner=Owner,data_root=data,language=InstallerLocale.Current});
            using(var transaction=new FileStream(LongFile.N(Path.Combine(root,"transaction.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)) {
                if(File.Exists(Path.Combine(root,"current.json"))?(old==null||ReadState(root).version!=old.version):old!=null)throw new Exception("ACTIVE_VERSION_CHANGED_DURING_PREFLIGHT");
                string id=b.manifest.package_id+"-"+Guid.NewGuid().ToString("N").Substring(0,12);
                string version=Under(root,"versions/"+id);string receipt=Under(root,"receipts/"+id+".json");
                try {
                    Directory.CreateDirectory(version);File.WriteAllBytes(Path.Combine(version,"manifest.json"),b.manifestBytes);b.Extract(version,progress);
                    if(fault=="after-extract")throw new Exception("INJECTED_AFTER_EXTRACT");
                    Prerequisite(version,sandbox,progress);
                    Health(version,HealthProbePaths.Prepare(root,version,b.manifest,sandbox),b.manifest,progress);
                    ValidateInstalled(version,b.manifest);
                    if(fault=="before-activate")throw new Exception("INJECTED_BEFORE_ACTIVATE");
                    string host=Under(root,"Memorive.Manager-"+b.manifest.engine_sha256.Substring(0,12)+".exe");
                    if(!File.Exists(host))b.HostCopy(host);if(Hash(host)!=b.manifest.engine_sha256)throw new Exception("MANAGER_HASH_MISMATCH");
                    if(old!=null){
                        State upgraded=UpdateEngine.CommitInstallerUpgrade(root,data,version,b.manifest,sandbox,fault,progress);
                        Save(receipt,new{status="PASS",package_id=upgraded.package_id,version=version,previous=old.version,data_root=data,user_data_migrated=true,shared_update_engine=true,acceptance_verdict="NOT_ASSESSED"});
                        return upgraded;
                    }
                    var next=new State{program_root=root,data_root=data,webview_root=WebViewPath(data,true),version=version,previous=old==null?null:old.version,package_id=b.manifest.package_id,contract=b.manifest.contract,sandbox_only=sandbox,registered=!sandbox,desktop_shortcut=desktop};
                    Save(Path.Combine(version,"version-state.json"),next);
                    progress(94,"激活已验证版本并保存安装回执");
                    if(fault=="activation-crash")throw new Exception("INJECTED_ACTIVATION_ABORT");
                    Save(Path.Combine(root,"current.json"),next);
                    try {if(fault=="after-activate")throw new Exception("INJECTED_AFTER_ACTIVATE");UpdateEngine.InstallInitialEntry(next,b.manifest);if(!sandbox)Register(next,host);}catch{if(old!=null)Save(Path.Combine(root,"current.json"),old);else File.Delete(Path.Combine(root,"current.json"));throw;}
                    Save(receipt,new{status="PASS",package_id=next.package_id,version=version,previous=next.previous,data_root=data,health="PASS",user_data_migrated=false,system_registration=!sandbox,created=DateTime.UtcNow.ToString("o"),acceptance_verdict="NOT_ASSESSED"});
                    progress(100,"安装完成");return next;
                } catch(Exception e) {Save(receipt,new{status="FAIL",error=e.Message,active_version_preserved=old==null?null:old.version,failed_version_retained=version,created=DateTime.UtcNow.ToString("o")});throw;}
            }
        }
    }
    static void Shortcut(string path,string host,string args,string cwd){Type t=Type.GetTypeFromProgID("WScript.Shell");dynamic shell=Activator.CreateInstance(t);dynamic link=shell.CreateShortcut(path);link.TargetPath=host;link.Arguments=args;link.WorkingDirectory=cwd;link.Description="Memorive";link.IconLocation=host+",0";link.Save();Marshal.FinalReleaseComObject(link);Marshal.FinalReleaseComObject(shell);}
    public static void RemoveLegacyShortcuts(string root) {
        string menu=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"Memorive Installer Trial");
        string[] paths={Path.Combine(menu,"Memorive 测试版.lnk"),Path.Combine(menu,"维护 Memorive 测试版.lnk"),Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"Memorive 安装测试版.lnk")};
        foreach(string path in paths)if(File.Exists(path)){
            object shell=null,link=null;bool owned=false;
            try{shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));link=((dynamic)shell).CreateShortcut(path);string target=((dynamic)link).TargetPath;owned=!String.IsNullOrWhiteSpace(target)&&Path.IsPathRooted(target)&&Within(target,root)&&Path.GetFileName(target).StartsWith("Memorive.Manager-",StringComparison.OrdinalIgnoreCase)&&((string)((dynamic)link).Arguments).Contains(Quote(root));}
            catch{owned=false;}
            finally{if(link!=null)Marshal.FinalReleaseComObject(link);if(shell!=null)Marshal.FinalReleaseComObject(shell);}
            if(owned)File.Delete(path);
        }
        if(Directory.Exists(menu)&&!Directory.EnumerateFileSystemEntries(menu).Any())Directory.Delete(menu);
    }
    public static void Register(State s,string host) {
        string menu=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"Memorive");Directory.CreateDirectory(menu);
        Shortcut(Path.Combine(menu,"Memorive.lnk"),host,"--launch --root "+Quote(s.program_root),s.program_root);
        Shortcut(Path.Combine(menu,"维护 Memorive.lnk"),host,"--manage --root "+Quote(s.program_root),s.program_root);
        if(s.desktop_shortcut)Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"Memorive.lnk"),host,"--launch --root "+Quote(s.program_root),s.program_root);
        using(var k=Registry.CurrentUser.CreateSubKey(RegPath)){k.SetValue("DisplayName","Memorive");k.SetValue("DisplayVersion",Load<Manifest>(Path.Combine(s.version,"manifest.json")).release_version);k.SetValue("Publisher","Memorive");k.SetValue("InstallLocation",s.program_root);k.SetValue("UninstallString",Quote(host)+" --uninstall --root "+Quote(s.program_root));k.SetValue("NoModify",1);k.SetValue("NoRepair",0);}
        RemoveLegacyShortcuts(s.program_root);
    }
    public static void Launch(string root) {State s=ReadState(root);RequirePlatform();if(WebViewVersion()==null)throw new Exception("WEBVIEW2_REQUIRED");Manifest m=Load<Manifest>(Path.Combine(s.version,"manifest.json"));ValidateInstalled(s.version,m);Process.Start(AppStart(s.version,s.data_root,false,m));}
    public static State Rollback(string root,Action<int,string> progress) {root=RootPath(root);if(File.Exists(Path.Combine(root,"updates/active-profile.json")))throw new UpdateError("UPDATE_ROLLBACK_REQUIRES_RECOVERY_ASSISTANT");CheckRunning(root);using(var l=new FileStream(LongFile.N(Path.Combine(root,"transaction.lock")),FileMode.Open,FileAccess.ReadWrite,FileShare.None)){State old=ReadState(root);root=old.program_root;if(String.IsNullOrEmpty(old.previous)||!Within(old.previous,root))throw new Exception("NO_ROLLBACK_VERSION");State prev=Load<State>(Path.Combine(old.previous,"version-state.json"));if(!NativePaths.Same(prev.data_root,old.data_root)||prev.contract!=old.contract)throw new Exception("ROLLBACK_DATA_CONTRACT_BLOCKED");Manifest m=Load<Manifest>(Path.Combine(old.previous,"manifest.json"));ValidateInstalled(old.previous,m);Health(old.previous,HealthProbePaths.Prepare(root,old.previous,m,old.sandbox_only),m,progress);prev.previous=old.version;Save(Path.Combine(root,"current.json"),prev);Save(Under(root,"receipts/rollback-"+Guid.NewGuid().ToString("N")+".json"),new{status="PASS",from=old.version,to=prev.version,user_data_modified=false});return prev;}}
    public static void Remove(string root,bool sandbox) {
        Lifecycle.Remove(root,sandbox,false,null,(p,t,c)=>{},()=>false);
    }

}

// Every probe stays in this installation's retained health evidence tree.
static class HealthProbePaths {
    public const int OrdinaryPathBudget=259;
    public static string Mapped(string root,string id){
        if(!System.Text.RegularExpressions.Regex.IsMatch(id??"",@"\A[0-9a-f]{16}\z"))throw new Exception("HEALTH_PROBE_ID_INVALID");
        return Engine.Under(root,"health/h-"+id);
    }
    public static int MaximumEndpointTemporaryLength(string probe){
        return Path.Combine(probe,@"localappdata\Memorive\desktop-review\state\service-runtime",
            ".service-"+new string('0',32)+".endpoint.json."+new string('0',10)+"."+new string('0',32)+".tmp").Length;
    }
    public static string Prepare(string root,string version,Manifest manifest,bool sandbox){
        root=Engine.RootPath(root);Engine.NoReparse(version);
        if(!Engine.Within(version,Path.Combine(root,"versions")))throw new Exception("HEALTH_PROBE_VERSION_SCOPE_INVALID");
        string installOwner=Path.Combine(root,"install-owner.json");
        if(!File.Exists(installOwner)||Convert.ToString(Engine.Load<Dictionary<string,object>>(installOwner)["owner"])!=Engine.Owner)throw new Exception("HEALTH_PROBE_INSTALL_OWNER_REQUIRED");
        string probe=Mapped(root,Guid.NewGuid().ToString("N").Substring(0,16));int maximum=MaximumEndpointTemporaryLength(probe);
        if(!sandbox&&maximum>OrdinaryPathBudget)throw new Exception("HEALTH_PROBE_PATH_EXCEEDS_SAFE_BUDGET");
        if(File.Exists(probe)||Directory.Exists(probe))throw new Exception("HEALTH_PROBE_PATH_COLLISION");
        Directory.CreateDirectory(probe);
        if(Directory.EnumerateFileSystemEntries(probe).Any())throw new Exception("HEALTH_PROBE_PATH_COLLISION");
        byte[] metadata=Encoding.UTF8.GetBytes(Engine.Json.Serialize(new{schema=1,owner=Engine.Owner,role="INSTALLER_HEALTH_PROBE",program_root=root,probe_root=probe,source_version=version,package_id=manifest.package_id,manifest_sha256=Engine.Hash(Path.Combine(version,"manifest.json")),maximum_endpoint_temporary_chars=maximum,ordinary_path_budget=OrdinaryPathBudget,sandbox_only=sandbox}));
        using(var f=new FileStream(LongFile.N(Path.Combine(probe,"health-probe-owner.json")),FileMode.CreateNew,FileAccess.Write,FileShare.None)){f.Write(metadata,0,metadata.Length);f.Flush(true);}
        return probe;
    }
}

static class Trust {
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct FileInfoNative {public uint size;[MarshalAs(UnmanagedType.LPWStr)]public string path;public IntPtr hFile,knownSubject;}
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Data {public uint size;public IntPtr policy,sip;public uint ui,revocation,union;public IntPtr file;public uint state;public IntPtr stateData;[MarshalAs(UnmanagedType.LPWStr)]public string url;public uint flags,context;}
    [DllImport("wintrust.dll",CharSet=CharSet.Unicode,ExactSpelling=true)] static extern int WinVerifyTrust(IntPtr hwnd,[MarshalAs(UnmanagedType.LPStruct)] Guid action,ref Data data);
    public static int Verify(string path){var f=new FileInfoNative{size=(uint)Marshal.SizeOf(typeof(FileInfoNative)),path=path};IntPtr ptr=Marshal.AllocHGlobal(Marshal.SizeOf(f));Marshal.StructureToPtr(f,ptr,false);try{var d=new Data{size=(uint)Marshal.SizeOf(typeof(Data)),ui=2,union=1,file=ptr,flags=0x1000};return WinVerifyTrust(new IntPtr(-1),new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE"),ref d);}finally{Marshal.DestroyStructure(ptr,typeof(FileInfoNative));Marshal.FreeHGlobal(ptr);}}
}

static class Program {
    [STAThread] static int Main(string[] args){AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling",false);AppContext.SetSwitch("Switch.System.IO.BlockLongPaths",false);Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);string result=Get(args,"--result");try{
        string executableName=Path.GetFileName(Engine.Self);
        if(executableName.Equals("Memorive.exe",StringComparison.OrdinalIgnoreCase))return StableLaunch.Run(args);
        bool update=executableName.Equals("Memorive.Update.exe",StringComparison.OrdinalIgnoreCase)||args.Contains("--update");
        bool noninteractive=args.Contains("--json")||args.Contains("--install")||args.Contains("--inspect")||args.Contains("--signature")||args.Contains("--rollback")||args.Contains("--remove")||args.Contains("--launch");
        InstallerLocale.Select(args,!noninteractive);
        if(update)return UpdateEntry.Run(args);
        if(args.Contains("--vm-authorization")||args.Contains("--vm-authorization-sha256"))throw new Exception("NEW_CANDIDATE_VM_AUTHORIZATION_REQUIRED");
        string root=Get(args,"--root");bool sandbox=args.Contains("--sandbox");
        if(args.Contains("--inspect")){using(var b=new Bundle(Get(args,"--package")??Engine.Self)){b.Verify();Engine.Save(result,new{status="PASS",package_id=b.manifest.package_id,members=b.manifest.members.Length,system_check=Engine.SystemCheck()});}return 0;}
        if(args.Contains("--signature")){Engine.Save(result,new{status=Trust.Verify(Get(args,"--package"))==0?"PASS":"FAIL"});return 0;}
        if(args.Contains("--render"))throw new Exception("RENDER_ONLY_IS_NOT_GUI_EVIDENCE");
        if(args.Contains("--launch")){Engine.Launch(root);return 0;}
        if(args.Contains("--install")){bool vm=args.Contains("--vm-test");if(vm)throw new Exception("NEW_CANDIDATE_VM_AUTHORIZATION_REQUIRED");if(!sandbox&&!vm)throw new Exception("NONINTERACTIVE_INSTALL_REQUIRES_SANDBOX_OR_AUTHORIZED_VM");if(vm){if(sandbox)throw new Exception("TEST_SCOPE_AMBIGUOUS");Engine.VmTest(root,Get(args,"--data"));}State s=Engine.Install(Get(args,"--package")??Engine.Self,root,Get(args,"--data"),vm,sandbox,Get(args,"--fault"),(p,t)=>{});Engine.Save(result,new{status="PASS",state=s});return 0;}
        if(args.Contains("--rollback")){if(!sandbox)throw new Exception("NONINTERACTIVE_ROLLBACK_REQUIRES_SANDBOX");Engine.Sandbox(root);var s=Engine.Rollback(root,(p,t)=>{});Engine.Save(result,new{status="PASS",state=s});return 0;}
        if(args.Contains("--remove")){string parent=Get(args,"--parent");if(parent!=null)try{using(var p=Process.GetProcessById(Int32.Parse(parent))){if(!p.WaitForExit(10000))throw new Exception("UNINSTALL_PARENT_RUNNING");}}catch(ArgumentException){}Engine.Remove(root,sandbox);if(result!=null)Engine.Save(result,new{status="PASS",data_preserved=true});return 0;}
        UiBootstrap.Configure(args);if(args.Contains("--manage")||args.Contains("--uninstall")){UiBootstrap.UninstallWorker(args);return 0;}
        bool created=false;
        using(var mutex=new Mutex(true,@"Local\Memorive.v1.01.Installer",out created)){
            if(!created){NativeNoticeWindow.Run("notice.alreadyOpen",null,args);return 0;}
            UiBootstrap.Run(false);return 0;
        }
    }catch(Exception ex){
        if(result!=null)Engine.Save(result,new{status="FAIL",error=ex.Message,details=ex.ToString()});
        else if(args.Contains("--json"))using(var output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false))){output.WriteLine(Engine.Json.Serialize(new{schema="MemoriveUpdateIPC-v1",status="FAIL",error=ex is UpdateError?ex.Message:"UPDATE_OPERATION_FAILED",diagnostic_type=ex.GetType().Name}));}
        else if(args.Any(x=>x.StartsWith("--memo-agent",StringComparison.Ordinal)))using(var output=new StreamWriter(Console.OpenStandardError(),new UTF8Encoding(false))){output.WriteLine(ex is UpdateError?ex.Message:"UPDATE_LAUNCH_FAILED");}
        else NativeNoticeWindow.Run("error.generic",ex,args);return 1;}}
    static string Get(string[] a,string key){int i=Array.IndexOf(a,key);return i>=0&&i+1<a.Length?a[i+1]:null;}
}
