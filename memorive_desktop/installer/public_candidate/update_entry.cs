using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using File=LongFile;
using Directory=LongDirectory;

static class StableLaunch {
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Startup {
        public uint cb;public string reserved,desktop,title;public uint x,y,xsize,ysize,xchars,ychars,fill,flags;public ushort show,reserved2;public IntPtr reservedPtr,input,output,error;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {public IntPtr process,thread;public uint pid,tid;}
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcessW(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref Startup startup,out ProcessInfo process);
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h,uint ms);
    [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr h,out uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    public static string Argument(string value){
        if(value.Length>0&&!value.Any(c=>Char.IsWhiteSpace(c)||c=='"'))return value;
        var b=new StringBuilder("\"");int slashes=0;
        foreach(char c in value){if(c=='\\'){slashes++;continue;}if(c=='"'){b.Append('\\',slashes*2+1);b.Append(c);}else{b.Append('\\',slashes);b.Append(c);}slashes=0;}
        b.Append('\\',slashes*2);b.Append('"');return b.ToString();
    }
    public static int Run(string[] args){
        string directory=Path.GetDirectoryName(Engine.Self);var map=Engine.Load<Dictionary<string,object>>(Path.Combine(directory,"memorive-launch.json"));
        UpdateProtocol.Require(Convert.ToString(map["schema"])=="MemoriveStableLaunch-v1","UPDATE_LAUNCH_BINDING_INVALID");
        string root=Engine.RootPath(Convert.ToString(map["program_root"])),home=Path.Combine(root,"updates");
        InstallerLocale.Select(new[]{"--root",root},false);
        UpdateEngine.ReconcilePendingTerminal(root);
        UpdateProtocol.Require(!File.Exists(Path.Combine(home,"activation-gate.json")),"UPDATE_RECOVERY_REQUIRED");
        var state=Engine.ReadState(root);string bindingPath=Path.Combine(home,"binding.json");
        var binding=File.Exists(bindingPath)?Engine.Load<UpdateBinding>(bindingPath):null;
        UpdateProtocol.Require(NativePaths.Same(directory,root)||(binding!=null&&binding.portable&&NativePaths.Same(directory,Path.GetDirectoryName(binding.legacy_entry))),"UPDATE_LAUNCH_ENTRY_MISMATCH");
        var manifest=Engine.Load<Manifest>(Path.Combine(state.version,"manifest.json"));string exe=Engine.Under(state.version,"app/Memorive.exe");
        var member=manifest.members.Single(x=>x.path=="app/Memorive.exe");
        UpdateProtocol.Require(new FileInfo(LongFile.N(exe)).Length==member.size&&String.Equals(Engine.Hash(exe),member.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_LAUNCH_HASH_MISMATCH");
        var start=Engine.AppStart(state.version,state.data_root,false,manifest);
        foreach(string name in start.EnvironmentVariables.Keys)Environment.SetEnvironmentVariable(name,start.EnvironmentVariables[name]);
        var command=new StringBuilder(Argument(exe)+" "+String.Join(" ",args.Select(Argument)));
        var si=new Startup{cb=(uint)Marshal.SizeOf(typeof(Startup)),flags=0x100,input=GetStdHandle(-10),output=GetStdHandle(-11),error=GetStdHandle(-12)};
        ProcessInfo child;
        if(!CreateProcessW(exe,command,IntPtr.Zero,IntPtr.Zero,true,0,IntPtr.Zero,NativePaths.WorkingDirectory(exe),ref si,out child))throw new UpdateError("UPDATE_LAUNCH_FAILED");
        try{WaitForSingleObject(child.process,0xFFFFFFFF);uint code;UpdateProtocol.Require(GetExitCodeProcess(child.process,out code),"UPDATE_LAUNCH_EXIT_UNKNOWN");return unchecked((int)code);}
        finally{CloseHandle(child.thread);CloseHandle(child.process);}
    }
}

static class UpdateEntry {
    static string Get(string[] args,string key){int i=Array.IndexOf(args,key);return i>=0&&i+1<args.Length?args[i+1]:null;}
    public static object Execute(string[] args,Action<UpdateTransaction> progress){
        string root=Get(args,"--root"),action=Get(args,"--action")??"status",operation=Get(args,"--operation");
        var network=new UpdateNetworkPolicy();string policyPath=Get(args,"--network-policy");
        if(policyPath!=null)network=Engine.Load<UpdateNetworkPolicy>(Engine.RootPath(policyPath));
        if(action=="bind")return UpdateEngine.Bind(root,Get(args,"--data"),Get(args,"--state"),Get(args,"--source"),args.Contains("--portable"),args.Contains("--sandbox"),InstallerLocale.Current);
        var engine=new UpdateEngine(root);engine.Changed=progress;
        if(policyPath==null)network=engine.NetworkPolicy();
        var discovery=new UpdateDiscovery(Path.Combine(engine.Home,"discovery"),network);
        switch(action){
            case "status":return operation==null?(object)discovery.state:engine.Read(operation);
            case "check":return discovery.Check(engine.Binding.source_release,engine.Binding.source_package,args.Contains("--automatic"));
            case "preference":discovery.state.automatic_enabled=args.Contains("--enabled");discovery.Save();return discovery.state;
            case "release-orphan":return engine.ReleaseOrphanReference(operation);
            case "prepare":return engine.Prepare(operation,Get(args,"--target"),args.Contains("--allow-full"),network);
            case "download":return engine.Download(operation,args.Contains("--allow-full"),network);
            case "apply":return engine.Apply(operation,Get(args,"--fault"));
            case "recover":return engine.Recover(operation,Get(args,"--fault"));
            case "reconcile":return engine.ReconcileTerminal(operation);
            case "cancel":engine.Cancel(operation);return engine.Read(operation);
            default:throw new UpdateError("UPDATE_ACTION_INVALID");
        }
    }
    public static int Run(string[] args){
        if(!args.Contains("--json")&&Get(args,"--action")==null){Application.Run(new UpdateAssistantWindow(Get(args,"--root")));return 0;}
        if(!args.Contains("--json")){Application.Run(new UpdateWindow(args));return 0;}
        using(var output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false))){output.AutoFlush=true;
            try{object result=Execute(args,p=>{});output.WriteLine(Engine.Json.Serialize(new{schema="MemoriveUpdateIPC-v1",status="PASS",result=result}));return 0;}
            catch(Exception ex){output.WriteLine(Engine.Json.Serialize(new{schema="MemoriveUpdateIPC-v1",status="FAIL",error=ex is UpdateError?ex.Message:"UPDATE_OPERATION_FAILED",diagnostic_type=ex.GetType().Name}));return 1;}
        }
    }
}

// Extracted from the final Memo typography declarations by compile_engine.
sealed class MemoTypographyContract {
    public Dictionary<string,int> sizes,weights;
    public Dictionary<string,Dictionary<string,string[]>> stacks;
    public string[] code;
}
static class UpdateTypography {
    static MemoTypographyContract contract;
    static readonly Dictionary<string,System.Drawing.Font> fonts=new Dictionary<string,System.Drawing.Font>();
    public static System.Drawing.Font Font(string role,int dpi,string text=""){
        if(contract==null)contract=Engine.Json.Deserialize<MemoTypographyContract>(Encoding.UTF8.GetString(UiBootstrap.Resource("memo-typography.json")));
        string[] stack=role=="code"?contract.code:contract.stacks[InstallerLocale.Current][role=="page"||role=="section"||role=="control"?"heading":"body"];
        bool cjk=text.Any(c=>c>127);int first=cjk&&InstallerLocale.Current!="en-US"&&role!="code"?1:0;
        string key=InstallerLocale.Current+"/"+role+"/"+dpi+"/"+first;System.Drawing.Font existing;if(fonts.TryGetValue(key,out existing))return existing;
        var style=contract.weights[role]==700?System.Drawing.FontStyle.Bold:System.Drawing.FontStyle.Regular;
        foreach(string family in stack.Skip(first)){
            System.Drawing.Font font;
            if(family=="sans-serif"||family=="serif"||family=="monospace"){
                var generic=family=="sans-serif"?System.Drawing.FontFamily.GenericSansSerif:family=="serif"?System.Drawing.FontFamily.GenericSerif:System.Drawing.FontFamily.GenericMonospace;
                font=new System.Drawing.Font(generic,contract.sizes[role]*dpi/96f,style,System.Drawing.GraphicsUnit.Pixel);
            }else {
                font=new System.Drawing.Font(family,contract.sizes[role]*dpi/96f,style,System.Drawing.GraphicsUnit.Pixel);
                // Name is localized by Windows; GetName(1033) is the declared
                // canonical family identity. A missing face tries the next one.
                if(!String.Equals(font.FontFamily.GetName(1033),family,StringComparison.OrdinalIgnoreCase)){font.Dispose();continue;}
            }
            fonts[key]=font;return font;
        }
        throw new InvalidOperationException("MEMO_FONT_STACK_INVALID");
    }
}
// Shares the installer controls and the desktop setting-row palette.
sealed class UpdateCard:TableLayoutPanel {
    public UpdateCard(){DoubleBuffered=true;BackColor=System.Drawing.ColorTranslator.FromHtml("#FBF9F5");SetStyle(ControlStyles.ResizeRedraw,true);}
    protected override void OnPaint(PaintEventArgs e){base.OnPaint(e);e.Graphics.SmoothingMode=System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        int d=Math.Max(12,(int)(20*DeviceDpi/96f));var r=new System.Drawing.Rectangle(0,0,Width-1,Height-1);
        using(var path=new System.Drawing.Drawing2D.GraphicsPath()){path.AddArc(r.Left,r.Top,d,d,180,90);path.AddArc(r.Right-d,r.Top,d,d,270,90);path.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);path.AddArc(r.Left,r.Bottom-d,d,d,90,90);path.CloseFigure();using(var pen=new System.Drawing.Pen(System.Drawing.ColorTranslator.FromHtml("#DFD9CF")))e.Graphics.DrawPath(pen,path);}
    }
}
sealed class UpdateMeter:Control {
    int value;public int Value{get{return value;}set{this.value=Math.Max(0,Math.Min(100,value));Invalidate();}}
    public UpdateMeter(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.ResizeRedraw,true);AccessibleRole=AccessibleRole.ProgressBar;}
    protected override void OnPaint(PaintEventArgs e){e.Graphics.Clear(System.Drawing.ColorTranslator.FromHtml("#E8E3DA"));using(var brush=new System.Drawing.SolidBrush(System.Drawing.ColorTranslator.FromHtml("#8F5334")))e.Graphics.FillRectangle(brush,0,0,Width*Value/100,Height);}
}
sealed class UpdateAssistantWindow:Form {
    readonly Label location=new Label(),status=new Label(),size=new Label(),heading=new Label(),choose=new Label(),hint=new Label(),notes=new Label();
    readonly TextBox diagnostics=new TextBox();readonly UpdateMeter progress=new UpdateMeter();
    readonly MemoAction browse=new MemoAction(""),check=new MemoAction(""),download=new MemoAction("",true),apply=new MemoAction("",true),recover=new MemoAction("",true),cancel=new MemoAction(""),close=new MemoAction(""),detailButton=new MemoAction("");
    readonly CheckBox consent=new CheckBox();readonly ToolTip tips=new ToolTip();NativeLanguageSwitch languages;
    UpdateCard stateCard;TableLayoutPanel layout;string root,operation,errorCode,statusKey="assistant.ready";UpdateCheckState discovered;UpdateTransaction plan;bool busy,critical;
    int dpi=96;
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    int Px(int value){return (int)Math.Round(value*dpi/96d);}
    public UpdateAssistantWindow(string selectedRoot){
        AutoScaleMode=AutoScaleMode.None;StartPosition=FormStartPosition.CenterScreen;dpi=Math.Max(96,(int)GetDpiForWindow(Handle));
        using(var stream=new MemoryStream(UiBootstrap.Resource("memorive.ico")))using(var icon=new System.Drawing.Icon(stream)){Icon=(System.Drawing.Icon)icon.Clone();}
        ClientSize=new System.Drawing.Size(Px(760),Px(520));MinimumSize=SizeFromClientSize(new System.Drawing.Size(Px(640),Px(480)));
        BackColor=System.Drawing.ColorTranslator.FromHtml("#FFFDF9");ForeColor=System.Drawing.ColorTranslator.FromHtml("#2A2723");
        layout=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(Px(24)),ColumnCount=1,RowCount=4,Margin=Padding.Empty};
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));Controls.Add(layout);
        var header=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=1,AutoSize=true,Margin=new Padding(0,0,0,Px(16))};header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        heading.Name="assistantHeading";heading.AutoSize=true;heading.Anchor=AnchorStyles.Left;heading.Margin=Padding.Empty;header.Controls.Add(heading,0,0);
        languages=new NativeLanguageSwitch(()=>Localize(),dpi){Anchor=AnchorStyles.Right};languages.Controls.Remove(languages.Choices);header.Controls.Add(languages,1,0);layout.Controls.Add(header,0,0);
        languages.Choices.Anchor=AnchorStyles.Right;languages.Choices.Margin=new Padding(0,0,0,Px(12));layout.Controls.Add(languages.Choices,0,1);
        var scroll=new Panel{Name="assistantScroll",Dock=DockStyle.Fill,AutoScroll=true,Margin=Padding.Empty};layout.Controls.Add(scroll,0,2);
        var body=new TableLayoutPanel{Name="assistantContent",Dock=DockStyle.Top,ColumnCount=1,RowCount=4,AutoSize=true,AutoSizeMode=AutoSizeMode.GrowAndShrink,Margin=Padding.Empty};body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        for(int i=0;i<4;i++)body.RowStyles.Add(new RowStyle(SizeType.AutoSize));scroll.Controls.Add(body);
        var target=new UpdateCard{Name="assistantTarget",Dock=DockStyle.Top,AutoSize=true,ColumnCount=2,RowCount=2,Padding=new Padding(Px(16)),Margin=new Padding(0,0,0,Px(16))};target.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));target.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        choose.AutoSize=true;choose.Margin=new Padding(0,0,Px(16),Px(8));target.Controls.Add(choose,0,0);
        location.Name="assistantLocation";location.AutoEllipsis=true;location.Dock=DockStyle.Fill;location.Height=Px(24);location.Margin=new Padding(0,0,Px(16),0);location.ForeColor=System.Drawing.ColorTranslator.FromHtml("#6E675E");target.Controls.Add(location,0,1);
        browse.Name="assistantBrowse";browse.Anchor=AnchorStyles.Right;target.Controls.Add(browse,1,0);target.SetRowSpan(browse,2);body.Controls.Add(target,0,0);
        stateCard=new UpdateCard{Name="assistantState",Dock=DockStyle.Top,AutoSize=true,ColumnCount=1,RowCount=6,Padding=new Padding(Px(20)),Margin=new Padding(0,0,0,Px(16))};stateCard.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));for(int i=0;i<6;i++)stateCard.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        status.Name="assistantStatus";status.AutoSize=true;status.Margin=new Padding(0,0,0,Px(8));stateCard.Controls.Add(status,0,0);
        hint.Name="assistantHint";hint.AutoSize=true;hint.ForeColor=System.Drawing.ColorTranslator.FromHtml("#6E675E");hint.Margin=new Padding(0,0,0,Px(12));stateCard.Controls.Add(hint,0,1);
        size.AutoSize=true;size.Margin=new Padding(0,0,0,Px(8));stateCard.Controls.Add(size,0,2);
        notes.Name="assistantNotes";notes.AutoSize=true;notes.Margin=new Padding(0,0,0,Px(12));stateCard.Controls.Add(notes,0,3);
        consent.AutoSize=true;consent.Visible=false;consent.Margin=new Padding(0,0,0,Px(12));stateCard.Controls.Add(consent,0,4);
        progress.Name="assistantProgress";progress.Dock=DockStyle.Top;progress.Height=Px(4);progress.Margin=Padding.Empty;stateCard.Controls.Add(progress,0,5);body.Controls.Add(stateCard,0,1);
        detailButton.Name="assistantDetails";detailButton.Anchor=AnchorStyles.Left;detailButton.Margin=new Padding(0,0,0,Px(12));body.Controls.Add(detailButton,0,2);
        diagnostics.Name="assistantDiagnostics";diagnostics.Multiline=true;diagnostics.ReadOnly=true;diagnostics.ScrollBars=ScrollBars.Vertical;diagnostics.Dock=DockStyle.Top;diagnostics.Height=Px(100);diagnostics.Visible=false;diagnostics.BackColor=BackColor;diagnostics.BorderStyle=BorderStyle.FixedSingle;diagnostics.Margin=new Padding(0,0,0,Px(12));body.Controls.Add(diagnostics,0,3);
        detailButton.Click+=(s,e)=>{diagnostics.Visible=!diagnostics.Visible;};
        var buttons=new FlowLayoutPanel{Name="assistantActions",Dock=DockStyle.Fill,FlowDirection=FlowDirection.RightToLeft,AutoSize=true,WrapContents=true,Margin=new Padding(0,Px(16),0,0)};layout.Controls.Add(buttons,0,3);
        foreach(var button in new[]{close,apply,recover,download,cancel,check}){button.Name="assistant"+(button==close?"Close":button==apply?"Apply":button==recover?"Recover":button==download?"Download":button==cancel?"Cancel":"Check");buttons.Controls.Add(button);}
        browse.Click+=async(s,e)=>{using(var picker=new FolderBrowserDialog{Description=InstallerLocale.Text("assistant.choose"),ShowNewFolderButton=false})if(picker.ShowDialog(this)==DialogResult.OK)await Work(()=>BindFolder(picker.SelectedPath));};
        check.Click+=async(s,e)=>await Work(()=>{var engine=new UpdateEngine(root);var discovery=new UpdateDiscovery(Path.Combine(engine.Home,"discovery"),engine.NetworkPolicy());discovered=discovery.Check(engine.Binding.source_release,engine.Binding.source_package,false);
            Ui(()=>{errorCode=discovered.error;statusKey="state."+discovered.state;plan=null;operation=null;});});
        download.Click+=async(s,e)=>{bool full=consent.Checked;await Work(()=>{var engine=EngineForProgress();if(plan==null||new[]{"FAILED","CANCELLED","ROLLED_BACK"}.Contains(plan.stage)){operation="update-"+Guid.NewGuid().ToString("N");plan=engine.Prepare(operation,discovered.package_id,false,engine.NetworkPolicy());}
            if(plan.consent_required&&!full){Ui(()=>{consent.Visible=true;consent.Checked=false;statusKey="assistant.fullRequired";size.Text=(plan.asset.size/1048576d).ToString("N1")+" MiB";});return;}plan=engine.Download(operation,full,engine.NetworkPolicy());});};
        apply.Click+=async(s,e)=>await Work(()=>{critical=true;plan=EngineForProgress().Apply(operation,null);critical=false;});
        recover.Click+=async(s,e)=>await Work(()=>{critical=true;plan=EngineForProgress().Recover(operation);critical=false;});
        cancel.Click+=(s,e)=>{try{new UpdateEngine(root).Cancel(operation);}catch(Exception ex){errorCode=ex.Message;Render();}};
        close.Click+=(s,e)=>Close();CancelButton=close;
        FormClosing+=(s,e)=>{if(busy)e.Cancel=true;};
        SizeChanged+=(s,e)=>FitText();
        if(selectedRoot!=null)Shown+=async(s,e)=>await Work(()=>LoadBound(Engine.RootPath(selectedRoot)));
        Localize();
    }
    void FitText(){int width=Math.Max(Px(260),stateCard.ClientSize.Width-stateCard.Padding.Horizontal);foreach(var label in new[]{status,hint,notes})label.MaximumSize=new System.Drawing.Size(width,0);}
    void Localize(){SuspendLayout();Text=InstallerLocale.Text("update.title");Font=UpdateTypography.Font("body",dpi,InstallerLocale.Text("assistant.idleHint"));
        heading.Text=Text;heading.Font=UpdateTypography.Font("page",dpi,Text);
        choose.Text=InstallerLocale.Text("assistant.instance");choose.Font=UpdateTypography.Font("control",dpi,choose.Text);consent.Text=InstallerLocale.Text("assistant.fullConsent");consent.Font=UpdateTypography.Font("control",dpi,consent.Text);diagnostics.Font=UpdateTypography.Font("code",dpi);
        foreach(var pair in new[]{new KeyValuePair<MemoAction,string>(browse,"assistant.browse"),new KeyValuePair<MemoAction,string>(check,"assistant.check"),new KeyValuePair<MemoAction,string>(download,"assistant.download"),new KeyValuePair<MemoAction,string>(apply,"assistant.apply"),new KeyValuePair<MemoAction,string>(recover,"assistant.recover"),new KeyValuePair<MemoAction,string>(cancel,"assistant.cancel"),new KeyValuePair<MemoAction,string>(close,"common.close"),new KeyValuePair<MemoAction,string>(detailButton,"common.details")}){
            pair.Key.Text=InstallerLocale.Text(pair.Value);pair.Key.AccessibleName=pair.Key.Text;pair.Key.Font=UpdateTypography.Font("control",dpi,pair.Key.Text);pair.Key.Size=new System.Drawing.Size(Math.Max(Px(96),TextRenderer.MeasureText(pair.Key.Text,pair.Key.Font).Width+Px(32)),Px(40));pair.Key.Margin=new Padding(Px(8),0,0,Px(8));}
        detailButton.Margin=new Padding(0,0,0,Px(12));browse.Margin=Padding.Empty;
        string[] locales={"zh-CN","en-US","ja-JP"};for(int i=0;i<3;i++){var button=(MemoAction)languages.Choices.Controls[i];button.Font=UpdateTypography.Font("control",dpi,button.Text);button.Primary=InstallerLocale.Current==locales[i];button.Width=Math.Max(Px(100),TextRenderer.MeasureText(button.Text,button.Font).Width+Px(28));button.Invalidate();}
        Render();ResumeLayout(true);FitText();
    }
    void Ui(Action action){if(InvokeRequired)Invoke(action);else action();}
    async Task Work(Action action){if(busy)return;errorCode=null;busy=true;Render();try{await Task.Run(action);}catch(Exception ex){diagnostics.Text=ex.ToString();errorCode=ex is UpdateError?ex.Message:"UPDATE_OPERATION_FAILED";}finally{busy=false;critical=false;Render();}}
    UpdateEngine EngineForProgress(){var engine=new UpdateEngine(root);engine.Changed=p=>Ui(()=>{plan=p;statusKey="stage."+p.stage;progress.Value=p.total>0?(int)Math.Min(100,p.current*100/p.total):0;critical=!new[]{"DOWNLOADING","WAITING_IDLE"}.Contains(p.stage);Render();});return engine;}
    void Render(){
        bool terminal=plan!=null&&new[]{"COMPLETE","ROLLED_BACK","CANCELLED"}.Contains(plan.stage);
        bool recovery=plan!=null&&plan.new_version!=null&&new[]{"RECOVERY_REQUIRED","APPLYING","BACKING_UP","BACKED_UP","MIGRATING","MIGRATED","VERIFYING_START","ACTIVATING","WAITING_IDLE"}.Contains(plan.stage);
        browse.Enabled=!busy;check.Visible=!recovery&&(plan==null||terminal||plan.stage=="FAILED");check.Enabled=!busy&&root!=null;
        download.Visible=!recovery&&((discovered!=null&&discovered.state=="AVAILABLE")||(plan!=null&&new[]{"WAITING_FULL_CONSENT","DOWNLOAD_FAILED","DOWNLOADING"}.Contains(plan.stage)));download.Enabled=!busy;
        apply.Visible=plan!=null&&plan.stage=="VERIFIED";apply.Enabled=!busy;recover.Visible=recovery;recover.Enabled=!busy;
        cancel.Visible=operation!=null&&plan!=null&&plan.new_version==null&&new[]{"VERIFYING_BASE","AVAILABLE","WAITING_FULL_CONSENT","DOWNLOADING","VERIFIED","WAITING_IDLE","DOWNLOAD_FAILED"}.Contains(plan.stage);cancel.Enabled=cancel.Visible&&(!busy||!critical);
        close.Enabled=!busy;close.Primary=terminal&&!busy;close.Invalidate();AcceptButton=close.Primary?close:recover.Visible?recover:apply.Visible?apply:download.Visible?download:check;
        string key=root==null?"assistant.choose":terminal&&errorCode==null?"stage."+plan.stage:statusKey;status.Text=errorCode==null?InstallerLocale.Text(key):InstallerLocale.Error(errorCode);
        hint.Text=InstallerLocale.Text(errorCode!=null?"assistant.failureHint":terminal?"assistant.closeHint":recovery?"assistant.recoveryHint":busy?"assistant.workingHint":"assistant.idleHint");
        location.Text=root==null?InstallerLocale.Text("assistant.none"):location.Text;tips.SetToolTip(location,location.Text);
        notes.Text=discovered!=null&&discovered.notes!=null&&discovered.notes.ContainsKey(InstallerLocale.Current)?discovered.notes[InstallerLocale.Current]:"";notes.Visible=!String.IsNullOrWhiteSpace(notes.Text)&&!terminal;
        size.Visible=!String.IsNullOrWhiteSpace(size.Text)&&!terminal;consent.Visible=plan!=null&&plan.consent_required&&!terminal;progress.Visible=busy&&!terminal;
        if(plan!=null&&errorCode==null)diagnostics.Text=InstallerLocale.Text("stage."+plan.stage)+Environment.NewLine+operation;
        status.Font=UpdateTypography.Font("control",dpi,status.Text);hint.Font=UpdateTypography.Font("small",dpi,hint.Text);location.Font=UpdateTypography.Font("value",dpi,location.Text);notes.Font=UpdateTypography.Font("body",dpi,notes.Text);size.Font=UpdateTypography.Font("small",dpi,size.Text);
        FitText();
    }
    void LoadBound(string program){
        root=null;plan=null;operation=null;discovered=null;errorCode=null;statusKey="assistant.ready";
        // Terminal states reconcile exactly as before. A validated unfinished
        // activation remains gated but can be explicitly recovered in this UI.
        var pending=UpdateEngine.ReconcilePendingTerminal(program,true);
        var engine=new UpdateEngine(program);
        UpdateTransaction selected=null;string selectedOperation=null;
        foreach(string folder in Directory.GetDirectories(engine.Home)){
            string path=Path.Combine(folder,"transaction.json");if(!File.Exists(path))continue;var found=Engine.Load<UpdateTransaction>(path);
            if(!new[]{"COMPLETE","ROLLED_BACK","FAILED","CANCELLED"}.Contains(found.stage)){UpdateProtocol.Require(selected==null,"UPDATE_MULTIPLE_ACTIVE_OPERATIONS");selectedOperation=found.operation_id;UpdateProtocol.Require(Path.GetFileName(folder)==selectedOperation,"UPDATE_TRANSACTION_INVALID");selected=engine.Read(selectedOperation);}
        }
        if(pending!=null&&!new[]{"COMPLETE","ROLLED_BACK"}.Contains(pending.stage))UpdateProtocol.Require(selected!=null&&selectedOperation==pending.operation_id,"UPDATE_ACTIVATION_GATE_MISMATCH");
        // Publish only after every validation succeeded; a later scan failure
        // must not leave a partially loaded recovery button enabled.
        root=program;plan=selected;operation=selectedOperation;
        Ui(()=>{location.Text=engine.Binding.source_app;statusKey=plan==null?"assistant.ready":"stage."+plan.stage;});
    }
    void BindFolder(string chosen){
        chosen=Engine.RootPath(chosen);string program,data,source;bool portable;
        string possibleRoot=Path.GetFullPath(Path.Combine(chosen,"../../.."));
        if(Path.GetFileName(chosen)=="app"&&Path.GetFileName(Path.GetDirectoryName(Path.GetDirectoryName(chosen)))=="versions"&&File.Exists(Path.Combine(possibleRoot,"current.json"))){
            var installed=Engine.ReadState(possibleRoot);UpdateProtocol.Require(NativePaths.Same(chosen,Path.Combine(installed.version,"app")),"UPDATE_OLD_SLOT_SELECTED");chosen=possibleRoot;
        }
        if(File.Exists(Path.Combine(chosen,"current.json"))){var installed=Engine.ReadState(chosen);program=chosen;data=installed.data_root;source=Path.Combine(installed.version,"app");portable=false;}
        else {UpdateProtocol.Require(File.Exists(Path.Combine(chosen,"Memorive.exe")),"UPDATE_MAIN_APPLICATION_REQUIRED");program=chosen+".managed";data=Path.Combine(chosen,"sandbox_profile");source=chosen;portable=true;}
        if(File.Exists(Path.Combine(program,"updates/binding.json"))){LoadBound(program);return;}
        string state=Path.Combine(data,"localappdata/Memorive/desktop-review/state"),selection=Path.Combine(state,"private-profile-selection.json");
        if(File.Exists(selection)){var selected=Engine.Load<Dictionary<string,object>>(selection);var identity=Engine.Load<Dictionary<string,object>>(Path.Combine(source,"_internal/release_identity_binding.json"));UpdateProtocol.Require(Convert.ToString(selected["package_id"])==Convert.ToString(identity["package_id"]),"UPDATE_LEGACY_PROFILE_SELECTION_INVALID");state=Engine.RootPath(Convert.ToString(selected["state_root"]));}
        bool sandbox=program.StartsWith(Engine.TestRoot+"\\",StringComparison.OrdinalIgnoreCase);
        UpdateEngine.Bind(program,data,state,source,portable,sandbox,InstallerLocale.Current);LoadBound(program);
    }
}

sealed class UpdateWindow:Form {
    readonly string[] arguments;readonly Label status=new Label(),hint=new Label();readonly UpdateMeter progress=new UpdateMeter();readonly MemoAction close=new MemoAction("",true),details=new MemoAction("");readonly TextBox detail=new TextBox();
    public UpdateWindow(string[] args){
        arguments=args;Text=InstallerLocale.Text("update.title");AutoScaleMode=AutoScaleMode.Dpi;ClientSize=new System.Drawing.Size(660,380);MinimumSize=new System.Drawing.Size(620,380);StartPosition=FormStartPosition.CenterScreen;
        using(var stream=new MemoryStream(UiBootstrap.Resource("memorive.ico")))using(var icon=new System.Drawing.Icon(stream)){Icon=(System.Drawing.Icon)icon.Clone();}
        BackColor=System.Drawing.ColorTranslator.FromHtml("#FFFDF9");ForeColor=System.Drawing.ColorTranslator.FromHtml("#2A2723");Font=UpdateTypography.Font("body",96,InstallerLocale.Text("assistant.workingHint"));
        var layout=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(24),RowCount=5,ColumnCount=1};layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));for(int i=0;i<3;i++)layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));Controls.Add(layout);
        var title=new Label{Text=Text,AutoSize=true,Font=UpdateTypography.Font("page",96,Text),Margin=new Padding(0,0,0,20)};layout.Controls.Add(title,0,0);
        var card=new UpdateCard{Dock=DockStyle.Top,AutoSize=true,ColumnCount=1,RowCount=3,Padding=new Padding(20),Margin=new Padding(0,0,0,16)};card.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        status.Text=InstallerLocale.Text("update.preparing");status.Font=UpdateTypography.Font("control",96,status.Text);status.AutoSize=true;status.Margin=new Padding(0,0,0,12);card.Controls.Add(status,0,0);
        hint.Text=InstallerLocale.Text("assistant.workingHint");hint.Font=UpdateTypography.Font("small",96,hint.Text);hint.AutoSize=true;hint.ForeColor=System.Drawing.ColorTranslator.FromHtml("#6E675E");hint.Margin=new Padding(0,0,0,16);card.Controls.Add(hint,0,1);progress.Dock=DockStyle.Top;progress.Height=4;card.Controls.Add(progress,0,2);layout.Controls.Add(card,0,1);
        foreach(var button in new[]{details,close}){button.Text=InstallerLocale.Text(button==close?"common.close":"common.details");button.Font=UpdateTypography.Font("control",96,button.Text);button.Size=new System.Drawing.Size(Math.Max(96,TextRenderer.MeasureText(button.Text,button.Font).Width+32),40);}
        details.Anchor=AnchorStyles.Left;details.Margin=new Padding(0,0,0,12);details.Click+=(s,e)=>{detail.Visible=!detail.Visible;};layout.Controls.Add(details,0,2);
        detail.Font=UpdateTypography.Font("code",96);detail.Multiline=true;detail.ReadOnly=true;detail.ScrollBars=ScrollBars.Vertical;detail.Dock=DockStyle.Fill;detail.Visible=false;detail.BackColor=BackColor;detail.BorderStyle=BorderStyle.FixedSingle;detail.Margin=new Padding(0,0,0,12);layout.Controls.Add(detail,0,3);
        close.Anchor=AnchorStyles.Right;close.Enabled=false;close.Click+=(s,e)=>Close();layout.Controls.Add(close,0,4);AcceptButton=close;CancelButton=close;
        SizeChanged+=(s,e)=>{status.MaximumSize=hint.MaximumSize=new System.Drawing.Size(Math.Max(260,ClientSize.Width-88),0);};
        FormClosing+=(s,e)=>{if(!close.Enabled)e.Cancel=true;};Shown+=async(s,e)=>{
            try{object result=await Task.Run(()=>UpdateEntry.Execute(arguments,plan=>BeginInvoke((Action)(()=>{status.Text=InstallerLocale.Text("stage."+plan.stage);progress.Value=plan.total>0?(int)Math.Min(100,plan.current*100/plan.total):0;}))));detail.Text=Engine.Json.Serialize(result);var transaction=result as UpdateTransaction;status.Text=transaction==null?InstallerLocale.Text("update.finished"):InstallerLocale.Text("stage."+transaction.stage);hint.Text=InstallerLocale.Text("assistant.closeHint");}
            catch(Exception ex){status.Text=InstallerLocale.Error(ex.Message);detail.Text=ex.ToString();hint.Text=InstallerLocale.Text("assistant.failureHint");}
            finally{progress.Visible=false;close.Enabled=true;close.Focus();}
        };
    }
}