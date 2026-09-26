using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using System.Diagnostics;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Drawing;
using Microsoft.Win32;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using File=LongFile;
using Directory=LongDirectory;

class UiRequest {public int id;public string action;public Dictionary<string,object> args;}
static class UiBootstrap {
    public static int InitialPage=1; public static bool Sandbox;public static string Root,Data,Package,Evidence,Fault; public static string Cache;
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr LoadLibraryEx(string path,IntPtr reserved,uint flags);
    public static byte[] Resource(string name){using(var s=Assembly.GetExecutingAssembly().GetManifestResourceStream(name)){if(s==null)throw new Exception("UI_RESOURCE_MISSING: "+name);using(var m=new MemoryStream()){s.CopyTo(m);return m.ToArray();}}}
    public static void Configure(string[] args){
        Sandbox=args.Contains("--ui-sandbox")||args.Contains("--sandbox");Root=Get(args,"--root");Data=Get(args,"--data");Package=Get(args,"--package")??Engine.Self;Evidence=Get(args,"--ui-evidence");Fault=Get(args,"--fault");
        if(Sandbox){if(Root==null||Data==null){if(Root!=null&&File.Exists(Path.Combine(Root,"current.json")))Data=Engine.ReadState(Root).data_root;else throw new Exception("UI_SANDBOX_ROOTS_REQUIRED");}Engine.Sandbox(Root);Engine.Sandbox(Data);}
        if(Evidence!=null){Engine.Sandbox(Evidence);if(!Evidence.StartsWith(Engine.TestRoot+@"\evidence\",StringComparison.OrdinalIgnoreCase))throw new Exception("UI_EVIDENCE_SCOPE_INVALID");Directory.CreateDirectory(Evidence);}
        if(!Sandbox&&Fault!=null)throw new Exception("UI_FAULT_REQUIRES_EXACT_SANDBOX");
        AppDomain.CurrentDomain.AssemblyResolve+=(o,e)=>{string n=new AssemblyName(e.Name).Name;return n=="Microsoft.Web.WebView2.Core"||n=="Microsoft.Web.WebView2.WinForms"?Assembly.Load(Resource(n+".dll")):null;};
    }
    public static void PrepareLoader(){
        string id=Engine.Hash(Engine.Self).Substring(0,12);Cache=Sandbox?Path.Combine(Engine.TestRoot,"test_runtime","ui-host",id):Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Memorive-Setup-Host",id);
        Engine.NoReparse(Cache);Directory.CreateDirectory(Cache);
        string path=Path.Combine(Cache,"WebView2Loader.dll");byte[] b=Resource("WebView2Loader.dll");string hash;using(var sha=SHA256.Create())hash=BitConverter.ToString(sha.ComputeHash(b)).Replace("-","");
        if(!File.Exists(path))File.WriteAllBytes(path,b);if(Engine.Hash(path)!=hash)throw new Exception("UI_LOADER_HASH_MISMATCH");
        if(LoadLibraryEx(path,IntPtr.Zero,0x00000100|0x00001000)==IntPtr.Zero)throw new Exception("UI_LOADER_LOAD_FAILED_"+Marshal.GetLastWin32Error());
    }
    public static string Profile(){string basis=Sandbox?Engine.TestRoot:Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Memorive-Installer-UI");string path=Engine.WebViewPath(basis,true);return path;}
    public static void OpenOfficial(string name){
        string url;
        switch(name){
            case "webview":url=Engine.WebViewDownloadUrl;break;
            case "visualcpp":url=Engine.VcDownloadUrl;break;
            case "visualcpp-x64":url="https://aka.ms/vc14/vc_redist.x64.exe";break;
            case "dotnet48":url="https://dotnet.microsoft.com/en-us/download/dotnet-framework/net48";break;
            case "privacy":url="https://privacy.microsoft.com/privacystatement";break;
            case "edgeprivacy":url="https://learn.microsoft.com/en-us/microsoft-edge/privacy-whitepaper/";break;
            case "agpl":url="https://www.gnu.org/licenses/agpl-3.0.html";break;
            case "project":url="https://github.com/KuchinashiYume/Memorive";break;
            default:throw new Exception("OFFICIAL_LINK_NOT_ALLOWED");
        }
        Process.Start(new ProcessStartInfo(url){UseShellExecute=true});
    }
    public static bool EnsureRuntime(bool uninstall){
        if(!PrerequisiteFlow.NeedsNativeWelcome(Engine.WebViewVersion()))return true;
        using(var wizard=new PrerequisiteWizard(uninstall)){
            if(wizard.ShowDialog()!=DialogResult.OK)return false;
            InitialPage=2;return true;
        }
    }
    public static void Run(bool uninstall){if(!EnsureRuntime(uninstall))return;PrepareLoader();RunWindow(uninstall);}
    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)] static void RunWindow(bool uninstall){Application.Run(new HtmlWizard(uninstall));}
    public static string Get(string[] a,string key){int i=Array.IndexOf(a,key);return i>=0&&i+1<a.Length?a[i+1]:null;}
    public static void UninstallWorker(string[] args){
        string root=Root;if(!Engine.Within(Engine.Self,root)){Run(true);return;}
        string parent=Sandbox?Path.Combine(Engine.TestRoot,"test_runtime","uninstall-workers"):Path.Combine(Path.GetTempPath(),"Memorive-Uninstall");Engine.NoReparse(parent);string tmp=Path.Combine(parent,Guid.NewGuid().ToString("N"));Directory.CreateDirectory(tmp);string exe=Path.Combine(tmp,"Memorive.Uninstall.exe");File.Copy(Engine.Self,exe);if(Engine.Hash(exe)!=Engine.Hash(Engine.Self))throw new Exception("UNINSTALL_WORKER_HASH_MISMATCH");
        string extra=Sandbox?" --ui-sandbox --data "+Engine.Quote(Data):"";if(Evidence!=null)extra+=" --ui-evidence "+Engine.Quote(Evidence);Process.Start(new ProcessStartInfo(exe,"--uninstall --root "+Engine.Quote(root)+extra){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden});
    }
}


class UiPathSelection {public string root,data;public bool existing;}
// UI preflight is read-only. Engine ownership, activation and data checks remain authoritative.
class InstallPathPolicy {
    readonly bool sandbox;readonly string registered;UiPathSelection pinned;
    public UiPathSelection initial;
    static bool Same(string a,string b){return String.Equals(a,b,StringComparison.OrdinalIgnoreCase);}
    public static string RegisteredRoot(bool sandbox){if(sandbox)return null;using(var key=Registry.CurrentUser.OpenSubKey(Engine.RegPath)){if(key==null)return null;string value=Convert.ToString(key.GetValue("InstallLocation"));if(String.IsNullOrWhiteSpace(value))throw new Exception("REGISTERED_INSTALLATION_STATE_INVALID");return Canonical(value);}}
    public static string Canonical(string value){
        if(String.IsNullOrWhiteSpace(value))throw new Exception("ABSOLUTE_LOCAL_FOLDER_REQUIRED");
        string path=value.Trim().Replace('/','\\');
        if(!System.Text.RegularExpressions.Regex.IsMatch(path,@"\A[A-Za-z]:\\")||path.Length<=3)throw new Exception("ABSOLUTE_LOCAL_FOLDER_REQUIRED");
        try{Engine.Relative(path.Substring(3).TrimEnd('\\').Replace('\\','/'));}catch{throw new Exception("INVALID_FOLDER_COMPONENT");}
        path=Engine.RootPath(path);if(File.Exists(path))throw new Exception("FOLDER_IS_FILE");return path;
    }
    public InstallPathPolicy(string program,string data,bool isSandbox,string registeredRoot){
        sandbox=isSandbox;registered=registeredRoot;
        string root=Canonical(program??registered??Engine.DefaultProgram);
        if(registered!=null&&!Same(root,registered))throw new Exception("EXISTING_PROGRAM_LOCATION_LOCKED");
        initial=new UiPathSelection{root=root,data=Canonical(data??Engine.DefaultData)};
        if(File.Exists(Path.Combine(root,"current.json"))){var s=Engine.ReadState(root);initial.data=Canonical(s.data_root);initial.existing=true;pinned=initial;}
        else if(registered!=null)throw new Exception("REGISTERED_INSTALLATION_STATE_INVALID");
    }
    public UiPathSelection Validate(string program,string data){
        var next=new UiPathSelection{root=Canonical(program),data=Canonical(data)};
        if(sandbox){Engine.Sandbox(next.root);Engine.Sandbox(next.data);}
        if(pinned!=null&&(!Same(next.root,pinned.root)||!Same(next.data,pinned.data)))throw new Exception("EXISTING_INSTALL_LOCATIONS_LOCKED");
        if(registered!=null&&!Same(next.root,registered))throw new Exception("EXISTING_PROGRAM_LOCATION_LOCKED");
        Engine.CheckRoots(next.root,next.data);
        if(File.Exists(Path.Combine(next.root,"current.json"))){var s=Engine.ReadState(next.root);if(!Same(next.data,s.data_root))throw new Exception("EXISTING_DATA_LOCATION_LOCKED");if(s.sandbox_only!=sandbox)throw new Exception("INSTALL_SCOPE_CHANGED");next.root=s.program_root;next.data=s.data_root;next.existing=true;}
        else if(pinned!=null||registered!=null)throw new Exception("EXISTING_INSTALLATION_CHANGED_RESTART");
        return next;
    }
    public void Pin(UiPathSelection next){if(next.existing)pinned=next;}
    public string BrowseStart(string value){if(pinned!=null||registered!=null)throw new Exception("EXISTING_INSTALL_LOCATIONS_LOCKED");string path=Canonical(value);if(sandbox)Engine.Sandbox(path);while(!Directory.Exists(path)){path=Path.GetDirectoryName(path);if(String.IsNullOrEmpty(path))throw new Exception("ABSOLUTE_LOCAL_FOLDER_REQUIRED");}return path;}
}

static class WindowLayout {
    public static int Pixels(int logical,int dpi){return Math.Max(1,(int)Math.Round(logical*Math.Max(96,dpi)/96d));}
    public static Rectangle Available(Rectangle work,int dpi){int margin=Math.Min(Pixels(8,dpi),Math.Max(0,Math.Min(work.Width,work.Height)/8));return new Rectangle(work.Left+margin,work.Top+margin,Math.Max(1,work.Width-margin*2),Math.Max(1,work.Height-margin*2));}
    public static Size Minimum(Rectangle work,int dpi){var a=Available(work,dpi);return new Size(Math.Min(a.Width,Pixels(640,dpi)),Math.Min(a.Height,Pixels(400,dpi)));}
    public static Rectangle Fit(Rectangle work,int dpi,Size logical,Rectangle current,bool preferred,bool center){var a=Available(work,dpi);var min=Minimum(work,dpi);int w=Math.Max(min.Width,Math.Min(a.Width,preferred?Pixels(logical.Width,dpi):current.Width)),h=Math.Max(min.Height,Math.Min(a.Height,preferred?Pixels(logical.Height,dpi):current.Height));int x=center?a.Left+(a.Width-w)/2:Math.Max(a.Left,Math.Min(current.Left,a.Right-w)),y=center?a.Top+(a.Height-h)/2:Math.Max(a.Top,Math.Min(current.Top,a.Bottom-h));return new Rectangle(x,y,w,h);}
}
class HtmlWizard:Form {
    WebView2 web;bool uninstall,busy,closing,critical,embeddedNavigation;volatile bool cancelled;string root,data,package,trustedDocument,embeddedHtml;State installed;RemovalPlan plan;string confirmed;int shot;
    bool compactWindow,applyingWindowBounds,windowBoundsReady,embeddedNavigationPending;InstallPathPolicy pathPolicy;
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
    int WindowDpi {get{uint dpi=GetDpiForWindow(Handle);return dpi==0?96:(int)dpi;}}
    void ConstrainWindow(bool center,bool preferred){if(!windowBoundsReady||applyingWindowBounds||WindowState==FormWindowState.Minimized)return;applyingWindowBounds=true;try{var work=Screen.FromControl(this).WorkingArea;int dpi=WindowDpi;var desired=compactWindow?new Size(720,480):uninstall?new Size(944,608):new Size(1180,760);var bounds=WindowLayout.Fit(work,dpi,desired,Bounds,preferred,center);MinimumSize=Size.Empty;MaximumSize=Size.Empty;MinimumSize=WindowLayout.Minimum(work,dpi);MaximumSize=WindowLayout.Available(work,dpi).Size;if(Bounds!=bounds)Bounds=bounds;}finally{applyingWindowBounds=false;}}
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd,int attribute,ref int value,int size);
    protected override void OnHandleCreated(EventArgs e){base.OnHandleCreated(e);if(!uninstall&&Environment.OSVersion.Version.Build>=22000){int square=1;try{DwmSetWindowAttribute(Handle,33,ref square,sizeof(int));}catch(DllNotFoundException){}catch(EntryPointNotFoundException){}}}
    protected override void OnLocationChanged(EventArgs e){base.OnLocationChanged(e);ConstrainWindow(false,false);}
    protected override void OnSizeChanged(EventArgs e){base.OnSizeChanged(e);ConstrainWindow(false,false);}
    protected override void WndProc(ref Message m){base.WndProc(ref m);if(m.Msg==0x02E0)ConstrainWindow(false,true);else if(m.Msg==0x007E||m.Msg==0x001A)ConstrainWindow(false,false);}
    public HtmlWizard(bool remove){uninstall=remove;root=UiBootstrap.Root??Engine.DefaultProgram;data=UiBootstrap.Data??Engine.DefaultData;package=UiBootstrap.Package;
        if(!remove){pathPolicy=new InstallPathPolicy(UiBootstrap.Root,UiBootstrap.Data,UiBootstrap.Sandbox,InstallPathPolicy.RegisteredRoot(UiBootstrap.Sandbox));root=pathPolicy.initial.root;data=pathPolicy.initial.data;}
        else if(File.Exists(Path.Combine(root,"current.json")))data=Engine.ReadState(root).data_root;
        Text=remove?"Memorive · 卸载向导 · v1.01":"Memorive · 安装向导 · v1.01";FormBorderStyle=FormBorderStyle.None;MinimizeBox=false;MaximizeBox=false;StartPosition=FormStartPosition.Manual;BackColor=ColorTranslator.FromHtml("#F5F2EC");AutoScaleMode=AutoScaleMode.None;
        web=new WebView2{Dock=DockStyle.Fill,DefaultBackgroundColor=BackColor};Controls.Add(web);windowBoundsReady=true;ConstrainWindow(true,true);Shown+=async(o,e)=>{ConstrainWindow(true,true);await Initialize();};
        FormClosing+=(o,e)=>{if(closing)return;e.Cancel=true;if(!critical)Post(new{ @event="closeRequest"});};
    }
    async Task Initialize(){try{var env=await CoreWebView2Environment.CreateAsync(null,UiBootstrap.Profile(),new CoreWebView2EnvironmentOptions());await web.EnsureCoreWebView2Async(env);web.CoreWebView2.Settings.AreDefaultContextMenusEnabled=false;web.CoreWebView2.Settings.AreDevToolsEnabled=false;web.CoreWebView2.Settings.IsStatusBarEnabled=false;web.CoreWebView2.Settings.IsZoomControlEnabled=false;
            embeddedHtml=Encoding.UTF8.GetString(UiBootstrap.Resource(uninstall?"uninstall.html":"install.html"));embeddedNavigationPending=true;web.CoreWebView2.NavigationStarting+=(o,e)=>{bool allow=embeddedNavigationPending&&EmbeddedDocumentMatches(e.Uri,embeddedHtml);if(allow){trustedDocument=e.Uri;embeddedNavigationPending=false;embeddedNavigation=true;}else e.Cancel=true;if(UiBootstrap.Evidence!=null)Engine.Save(Path.Combine(UiBootstrap.Evidence,"navigation-"+Guid.NewGuid().ToString("N")+".json"),new{exact_embedded_document=allow,cancel=e.Cancel,uri_prefix=e.Uri.Substring(0,Math.Min(64,e.Uri.Length))});};web.CoreWebView2.NavigationCompleted+=async(o,e)=>{if(UiBootstrap.Evidence!=null){Engine.Save(Path.Combine(UiBootstrap.Evidence,"navigation-result-"+Guid.NewGuid().ToString("N")+".json"),new{success=e.IsSuccess,error=e.WebErrorStatus.ToString()});await Capture(new{stage="navigation",success=e.IsSuccess});}};web.CoreWebView2.NewWindowRequested+=(o,e)=>e.Handled=true;web.CoreWebView2.PermissionRequested+=(o,e)=>e.State=CoreWebView2PermissionState.Deny;web.CoreWebView2.DownloadStarting+=(o,e)=>e.Cancel=true;
            web.CoreWebView2.AddWebResourceRequestedFilter("*",CoreWebView2WebResourceContext.All);web.CoreWebView2.WebResourceRequested+=(o,e)=>{if(!e.Request.Uri.StartsWith("about:")&&!e.Request.Uri.StartsWith("data:"))e.Response=env.CreateWebResourceResponse(new MemoryStream(new byte[0]),403,"Blocked","Content-Type: text/plain");};
            web.CoreWebView2.WebMessageReceived+=Receive;web.CoreWebView2.NavigateToString(Encoding.UTF8.GetString(UiBootstrap.Resource(uninstall?"uninstall.html":"install.html")));if(UiBootstrap.Evidence!=null){var timer=new System.Windows.Forms.Timer{Interval=400};string previous="";bool reading=false;timer.Tick+=async(o,e)=>{if(reading||IsDisposed||closing)return;reading=true;try{string focus=await web.CoreWebView2.ExecuteScriptAsync("JSON.stringify({id:document.activeElement?.id,tag:document.activeElement?.tagName,type:document.activeElement?.type,value:document.activeElement?.type==='password'?null:document.activeElement?.value,readOnly:document.activeElement?.readOnly,checked:document.activeElement?.checked,text:document.activeElement?.innerText,aria:document.activeElement?.getAttribute('aria-label')})");if(focus!=previous){previous=focus;Engine.Save(Path.Combine(UiBootstrap.Evidence,"focus-"+DateTime.UtcNow.Ticks+".json"),new{method="READ_ONLY_LIVE_DOM_FOCUS",focus=Engine.Json.Deserialize<string>(focus)});}}finally{reading=false;}};timer.Start();FormClosed+=(o,e)=>timer.Dispose();}
        }catch(Exception e){Engine.Save(Path.Combine(UiBootstrap.Cache,"ui-failure-"+Guid.NewGuid().ToString("N")+".json"),new{error=e.ToString()});MessageBox.Show(this,"界面运行环境未就绪："+e.Message,"Memorive");closing=true;Close();}}
    void Post(object value){if(IsDisposed)return;if(InvokeRequired){BeginInvoke((Action)(()=>Post(value)));return;}if(web.CoreWebView2==null)return;web.CoreWebView2.PostWebMessageAsJson(Engine.Json.Serialize(value));}
    HashSet<string> capturedProgress=new HashSet<string>();
    void Progress(object value,string key){Post(value);if(UiBootstrap.Evidence==null||!capturedProgress.Add(key))return;var completion=new TaskCompletionSource<bool>();BeginInvoke((Action)(async()=>{try{await Capture(new{stage="real-progress",key=key});completion.SetResult(true);}catch(Exception e){completion.SetException(e);}}));completion.Task.GetAwaiter().GetResult();}
    string Arg(UiRequest r,string key){return r.args!=null&&r.args.ContainsKey(key)?Convert.ToString(r.args[key]):null;}
    // End the WebView callback before any native modal or window-close work.
    void Receive(object sender,CoreWebView2WebMessageReceivedEventArgs e){
        string origin=e.Source,message=e.WebMessageAsJson;
        BeginInvoke((Action)(async()=>await ReceiveRequest(origin,message)));
    }
    static bool EmbeddedDocumentMatches(string uri,string html){
        if(uri=="about:blank")return true;
        if(uri==null||html==null||!uri.StartsWith("data:text/html",StringComparison.OrdinalIgnoreCase))return false;
        int comma=uri.IndexOf(',');if(comma<0)return false;
        try{string header=uri.Substring(0,comma),body=uri.Substring(comma+1);
            string decoded=header.EndsWith(";base64",StringComparison.OrdinalIgnoreCase)?Encoding.UTF8.GetString(Convert.FromBase64String(body)):Uri.UnescapeDataString(body);
            return decoded==html;
        }catch(FormatException){return false;}
    }
    static bool TrustedOrigin(bool embedded,string expected,string origin,string current){
        return embedded&&!String.IsNullOrEmpty(expected)&&(origin=="about:blank"||origin==expected)&&(current=="about:blank"||current==expected);
    }
    async Task ReceiveRequest(string origin,string message){UiRequest r=null;try{
        r=Engine.Json.Deserialize<UiRequest>(message);
        if(r==null||r.id<=0||r.args==null)throw new Exception("UI_REQUEST_INVALID");
        if(!TrustedOrigin(embeddedNavigation,trustedDocument,origin,web.CoreWebView2.Source))throw new Exception("UI_SOURCE_REJECTED");
        object value=await Dispatch(r);Post(new{id=r.id,ok=true,value=value});
    }catch(Exception ex){
        Post(new{id=r==null?0:r.id,ok=false,error=Explain(ex)});
        if(UiBootstrap.Evidence!=null)Engine.Save(Path.Combine(UiBootstrap.Evidence,"error-"+Guid.NewGuid().ToString("N")+".json"),new{action=r==null?null:r.action,error=ex.ToString(),source_prefix=origin.Substring(0,Math.Min(100,origin.Length)),source_length=origin.Length});
    }}
    static string Explain(Exception ex){
        if(new[]{"EXISTING_INSTALL_LOCATIONS_LOCKED","EXISTING_PROGRAM_LOCATION_LOCKED","EXISTING_DATA_LOCATION_LOCKED","DATA_BINDING_OR_CONTRACT_CHANGED","OTHER_TRIAL_INSTALLATION_REGISTERED"}.Contains(ex.Message))return "已有安装须沿用原程序与数据位置。本安装器不支持直接迁移；请返回安装位置核对。";
        if(ex.Message=="REGISTERED_INSTALLATION_STATE_INVALID"||ex.Message=="EXISTING_INSTALLATION_CHANGED_RESTART")return "已有安装记录与磁盘状态不一致。请关闭向导，检查原安装记录后重试；数据未迁移。";
        if(ex.Message=="ABSOLUTE_LOCAL_FOLDER_REQUIRED"||ex.Message=="LOCAL_FIXED_VOLUME_REQUIRED")return "请输入本机固定磁盘上的完整文件夹路径，例如 D:\\Memorive。";
        if(ex.Message=="INVALID_FOLDER_COMPONENT"||ex.Message=="FOLDER_IS_FILE")return "该位置不是有效的文件夹路径。请检查名称，勿选择文件或保留名称。";
        if(ex.Message=="PROGRAM_DATA_OVERLAP")return "程序与用户数据目录不能相同，也不能互为父子目录。";
        if(ex.Message=="EXISTING_UNOWNED_DIRECTORY"||ex.Message=="OWNER_CONFLICT")return "该文件夹含有其他文件或无法确认归属。请选择空文件夹或 Memorive 原有的对应目录。";
        if(ex.Message=="REPARSE_POINT_BLOCKED")return "不能使用链接或重定向文件夹，请选择本机实际目录。";
if(ex.Message=="APPLICATION_RUNNING_CLOSE_FIRST")return "请先关闭 Memorive，然后重试。";if(ex.Message=="PROTECTED_DATA_ROOT"||ex.Message=="SHARED_PARENT_BLOCKED")return "该目录属于项目、系统或共享数据的受保护范围，不能删除。请选择保留个人数据。";if(ex.Message.StartsWith("DATA_OWNERSHIP_MANIFEST_REQUIRED")||ex.Message=="DATA_PROFILE_OWNER_MISMATCH")return "无法确认全部个人数据的归属，已停止卸载。请选择保留个人数据。";if(ex.Message.StartsWith("UNCLASSIFIED_DATA")||ex.Message=="DATA_OWNERSHIP_INVENTORY_DRIFT"||ex.Message=="REMOVAL_PLAN_CHANGED_CONFIRM_AGAIN")return "数据清单已变化或包含未登记文件，已停止卸载。请重新检查并保留这些文件。";if(ex.Message=="FAILED_STAGING_REQUIRES_REVIEW"||ex.Message=="EMPTY_VERSION_OWNERSHIP_UNVERIFIED"||ex.Message=="UNKNOWN_PROGRAM_FILE_REQUIRES_REVIEW")return "程序目录中存在无法确认归属的文件，已停止卸载。请保留目录并检查之前的安装记录。";if(ex is IOException&&ex.Message.Contains("使用"))return "文件正在使用中；请关闭 Memorive 后重试。";return ex.Message;}
    object Facts(){using(var b=new Bundle(package)){var f=Checks(root);f["initial_page"]=UiBootstrap.InitialPage;f["root"]=root;f["data"]=data;f["existing"]=pathPolicy.initial.existing;f["desktop"]=!UiBootstrap.Sandbox;f["build"]="v"+b.manifest.release_version+" · "+b.manifest.package_id;f["space"]="约 "+Math.Ceiling(b.manifest.total_bytes/1024d/1024d).ToString("0")+" MB（另需校验与回退空间）";return f;}}
    Dictionary<string,object> Checks(string path){var rows=new List<string[]>();int build=Environment.OSVersion.Version.Build;bool platform=Environment.Is64BitOperatingSystem&&build>=19045;rows.Add(new[]{platform?"pass":"error","Windows",(build>=22000?"Windows 11":"Windows 10")+" / "+build});rows.Add(new[]{Environment.Is64BitOperatingSystem?"pass":"error","系统架构",Environment.Is64BitOperatingSystem?"x64":"不支持的架构"});try{var drive=new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path)));rows.Add(new[]{drive.AvailableFreeSpace>2L*1024*1024*1024?"pass":"error","磁盘空间",(drive.AvailableFreeSpace/1024d/1024d/1024d).ToString("0.0")+" GB 可用"});}catch{rows.Add(new[]{"error","磁盘空间","无法读取目标卷"});}rows.Add(new[]{Engine.NetRelease()>=528040?"pass":"error",".NET Framework",Engine.NetRelease()>=528040?"4.8 或更高":"需要 .NET Framework 4.8"});rows.Add(new[]{"pass","安装范围","当前 Windows 用户"});string vc=Engine.VcRuntimeStatus();rows.Add(new[]{vc==null?"pass":"error","Microsoft Visual C++ v14 x64",vc??"14.51.36247 或更高"});string w=Engine.WebViewVersion();rows.Add(new[]{w!=null?"pass":"error","Microsoft Edge WebView2",w??"未检测到；请先从 Microsoft 官网安装"});return new Dictionary<string,object>{{"checks",rows},{"webview",w!=null}};}
    object Tool(string id,string name,string command,string support,string group){bool found=false,error=false;try{if(Path.IsPathRooted(command)){Engine.NoReparse(command);found=File.Exists(command);}else{if(command.IndexOfAny(new[]{' ','\t','&','|','>','<','"',';','\\','/'})>=0)throw new Exception("请输入一个命令名或完整可执行文件路径，不接受命令参数。");foreach(string dir in (Environment.GetEnvironmentVariable("PATH")??"").Split(';'))if(!String.IsNullOrWhiteSpace(dir))foreach(string ext in new[]{".exe",".cmd",".bat"})try{if(File.Exists(Path.Combine(dir.Trim('"'),command+ext)))found=true;}catch{error=true;}}}catch(Exception ex){if(support=="CUSTOM")throw new Exception(ex.Message);error=true;}return new{id=id,name=name,command=command,support=support,group=group,normalStatus=found?"DETECTED":error?"DETECTION_ERROR":"NOT_DETECTED"};}
    async Task<object> Dispatch(UiRequest r){
        if(busy&&!new[]{"cancel","uiState"}.Contains(r.action))throw new Exception("当前事务正在执行，请稍候。");
        switch(r.action){
            case "init":if(uninstall){Engine.ReadState(root);return new{root=root};}return Facts();
            case "openOfficial":UiBootstrap.OpenOfficial(Arg(r,"name"));return true;
            case "checks":return Checks(Arg(r,"root")??root);
            case "paths":if(uninstall)throw new Exception("UI_MODE_MISMATCH");var selection=pathPolicy.Validate(Arg(r,"root"),Arg(r,"data"));pathPolicy.Pin(selection);return selection;
            case "browse":if(uninstall)throw new Exception("UI_MODE_MISMATCH");string kind=Arg(r,"kind");if(kind!="program"&&kind!="data")throw new Exception("UI_PATH_ROLE_INVALID");using(var dialog=new FolderBrowserDialog{Description=kind=="program"?"选择最终程序文件夹（不再追加子目录）":"选择最终用户数据文件夹（不再追加子目录）",SelectedPath=pathPolicy.BrowseStart(Arg(r,"current")),ShowNewFolderButton=true}){if(dialog.ShowDialog(this)!=DialogResult.OK)return null;string selected=InstallPathPolicy.Canonical(dialog.SelectedPath);if(UiBootstrap.Sandbox)Engine.Sandbox(selected);if(UiBootstrap.Evidence!=null)Engine.Save(Path.Combine(UiBootstrap.Evidence,"browse-"+Guid.NewGuid().ToString("N")+".json"),new{kind=kind,current=Arg(r,"current"),selected=selected,method="NATIVE_FOLDER_BROWSER_RESULT"});return selected;}
            case "custom":return Tool("custom",Arg(r,"name"),Arg(r,"command"),"CUSTOM","custom");
            case "tools":var result=new List<object>();string[] ids={"ollama","codex","claude","gemini","qwen","kimi","codebuddy","copilot"},names={"Ollama","Codex CLI","Claude Code CLI","Gemini CLI","Qwen Code CLI","Kimi Code CLI","Tencent CodeBuddy Code CLI","GitHub Copilot CLI"};for(int i=0;i<ids.Length;i++)result.Add(Tool(ids[i],names[i],ids[i],i==0?"RUNTIME":"ADAPTED",i==0?"runtime":"cli"));if(r.args.ContainsKey("custom")){var a=Engine.Json.Deserialize<List<Dictionary<string,object>>>(Engine.Json.Serialize(r.args["custom"]));foreach(var t in a)result.Add(Tool(Convert.ToString(t["id"]),Convert.ToString(t["name"]),Convert.ToString(t["command"]),"CUSTOM","custom"));}return result;
            case "install":if(uninstall)throw new Exception("UI_MODE_MISMATCH");var chosen=pathPolicy.Validate(Arg(r,"root"),Arg(r,"data"));root=chosen.root;data=chosen.data;bool desktop=Convert.ToBoolean(r.args["desktop"]);busy=true;cancelled=false;critical=false;capturedProgress.Clear();try{installed=await Task.Run(()=>Engine.Install(package,root,data,desktop,UiBootstrap.Sandbox,UiBootstrap.Fault,(p,t)=>{if(cancelled&&!critical)throw new OperationCanceledException("安装已取消；当前程序版本未切换，诊断记录已保留。");critical=p>=94;int step=p<15?0:p<70?2:p<76?1:p<94?4:3;int[] completed=p<15?new int[0]:p<70?new[]{0}:p<76?new[]{0,2}:p<94?new[]{0,1,2}:new[]{0,1,2,4};Progress(new{@event="progress",percent=p,step=step,completed=completed,message=t},"install-"+step);}));return new{status="PASS",version=installed.version};}finally{busy=false;critical=false;}
            case "launch":Engine.Launch(root);return true;
            case "removalCheck":if(!uninstall)throw new Exception("UI_MODE_MISMATCH");Engine.CheckRunning(root);return true;
            case "removalPlan":if(!uninstall)throw new Exception("UI_MODE_MISMATCH");confirmed=null;plan=await Task.Run(()=>Lifecycle.Plan(root,UiBootstrap.Sandbox,Arg(r,"policy")=="delete",(p,t,c)=>{}));return new{hash=plan.hash,count=plan.data_files.Length,bytes=plan.data_bytes};
            case "confirmDeletion":if(plan==null||plan.policy!="delete"||Arg(r,"hash")!=plan.hash)throw new Exception("DELETION_CONFIRMATION_MISMATCH");using(var dialog=new DeleteConfirmation(plan)){if(dialog.ShowDialog(this)!=DialogResult.OK)return false;confirmed=plan.hash;return true;}
            case "remove":if(plan==null||plan.hash!=Arg(r,"hash")||plan.policy!=Arg(r,"policy")||(plan.policy=="delete"&&confirmed!=plan.hash))throw new Exception("REMOVAL_CONFIRMATION_MISMATCH");busy=true;cancelled=false;critical=false;capturedProgress.Clear();try{await Task.Run(()=>Lifecycle.Remove(root,UiBootstrap.Sandbox,plan.policy=="delete",plan.hash,(p,t,c)=>{critical=c;Progress(new{@event="progress",percent=p,message=t,finalizing=c},"remove-"+(p<20?"preflight":p<80?"program":"data"));},()=>cancelled));return true;}finally{busy=false;critical=false;confirmed=null;plan=null;}
            case "cancel":if(!critical)cancelled=true;return !critical;
            case "uiState":await Capture(r.args);return true;
            case "close":closing=true;BeginInvoke((Action)(()=>Close()));return true;
            case "minimize":WindowState=FormWindowState.Minimized;return true;
            case "resize":compactWindow=!compactWindow;ConstrainWindow(true,true);await Capture(new{stage="window-size",compact=compactWindow});return true;
            default:throw new Exception("UI_ACTION_NOT_ALLOWED");
        }
    }
    async Task Capture(object state){if(UiBootstrap.Evidence==null)return;await Task.Delay(150);string name=(++shot).ToString("000")+"-"+(uninstall?"uninstall":"install");string path=Path.Combine(UiBootstrap.Evidence,name);using(var f=new FileStream(path+".png",FileMode.CreateNew,FileAccess.Write))await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,f);string dom=await web.CoreWebView2.ExecuteScriptAsync("JSON.stringify({text:document.body.innerText,viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,elements:Array.from(document.querySelectorAll('button,input,h2,.step-item,.choice-row,.welcome-compact,#startInstall svg,#startInstall .button-label,#locationNote')).filter(e=>e.getClientRects().length).map(e=>({tag:e.tagName,id:e.id,text:e.innerText,value:e.type==='password'?null:e.value,checked:e.checked,disabled:e.disabled,readOnly:e.readOnly,rect:e.getBoundingClientRect().toJSON(),color:getComputedStyle(e).color,background:getComputedStyle(e).backgroundColor,font:getComputedStyle(e).fontFamily,display:getComputedStyle(e).display,alignItems:getComputedStyle(e).alignItems}))})");Engine.Save(path+".json",new{state=state,window_bounds=new{Bounds.Left,Bounds.Top,Bounds.Width,Bounds.Height},working_area=Screen.FromControl(this).WorkingArea,window_dpi=WindowDpi,method="LIVE_WEBVIEW2_CAPTURE_AFTER_NATIVE_GUI_EVENT",ui_origin="REAL_EXECUTABLE",runtime=web.CoreWebView2.Environment.BrowserVersionString,dom=Engine.Json.Deserialize<string>(dom),image_sha256=Engine.Hash(path+".png"),time=DateTime.UtcNow.ToString("o")});}
}
class DeleteConfirmation:Form {
    public DeleteConfirmation(RemovalPlan p){Text="Memorive · 确认永久删除";ClientSize=new Size(720,540);StartPosition=FormStartPosition.CenterParent;BackColor=ColorTranslator.FromHtml("#FBF9F5");Font=new Font("SimSun",11);var label=new Label{Bounds=new Rectangle(25,22,670,105),Text="仅删除以下逐项归属已核验的数据。此操作无法恢复。\r\n\r\n"+p.data_root+"\r\n"+p.data_files.Length+" 个文件 / "+p.data_bytes+" 字节"};Controls.Add(label);var list=new TextBox{Bounds=new Rectangle(25,135,670,230),Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Both,Text=String.Join("\r\n",p.data_files),WordWrap=false};Controls.Add(list);Controls.Add(new Label{Bounds=new Rectangle(25,380,670,28),Text="核对清单后输入“永久删除”："});var phrase=new TextBox{Bounds=new Rectangle(25,414,420,32)};Controls.Add(phrase);var yes=new Button{Bounds=new Rectangle(505,471,190,42),Text="永久删除已列数据",Enabled=false,BackColor=ColorTranslator.FromHtml("#FBEFEC"),ForeColor=ColorTranslator.FromHtml("#8E332A"),DialogResult=DialogResult.OK};Controls.Add(yes);phrase.TextChanged+=(o,e)=>yes.Enabled=phrase.Text=="永久删除";var no=new Button{Bounds=new Rectangle(25,471,145,42),Text="返回并保留数据",DialogResult=DialogResult.Cancel};Controls.Add(no);CancelButton=no;}
}
