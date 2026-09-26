// Native welcome/check pages used only when the HTML renderer is unavailable.
// No runtime installer is downloaded or executed by this form.
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

static class PrerequisiteFlow {
    public static bool NeedsNativeWelcome(string webview){return String.IsNullOrEmpty(webview);}
    public static bool CanContinue(string webview,string vc,int net,bool platform){return !String.IsNullOrEmpty(webview)&&vc==null&&net>=528040&&platform;}
}
class MemoSurface:Panel {
    public MemoSurface(){DoubleBuffered=true;BackColor=ColorTranslator.FromHtml("#FFFDF9");Padding=new Padding(24);}
    protected override void OnPaint(PaintEventArgs e){base.OnPaint(e);using(var p=new Pen(ColorTranslator.FromHtml("#DFD9CF")))e.Graphics.DrawRectangle(p,0,0,Width-1,Height-1);}
}
class MemoAction:Button {
    public bool Primary;
    public MemoAction(string text,bool primary=false){Text=text;AccessibleName=text;Primary=primary;AutoSize=false;Size=new Size(160,42);FlatStyle=FlatStyle.Flat;FlatAppearance.BorderSize=0;Font=new Font("Arial",11);Cursor=Cursors.Hand;UseVisualStyleBackColor=false;SetStyle(ControlStyles.UserPaint|ControlStyles.OptimizedDoubleBuffer,true);}
    protected override void OnPaint(PaintEventArgs e){
        e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;var r=new Rectangle(1,1,Math.Max(1,Width-3),Math.Max(1,Height-3));
        using(var path=new GraphicsPath()){
            int d=18;path.AddArc(r.Left,r.Top,d,d,180,90);path.AddArc(r.Right-d,r.Top,d,d,270,90);path.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);path.AddArc(r.Left,r.Bottom-d,d,d,90,90);path.CloseFigure();
            using(var b=new SolidBrush(!Enabled?ColorTranslator.FromHtml("#F0EDE6"):Primary?ColorTranslator.FromHtml("#8F5334"):ColorTranslator.FromHtml("#FFFDF9")))e.Graphics.FillPath(b,path);
            using(var p=new Pen(ColorTranslator.FromHtml("#DFD9CF")))if(!Primary||!Enabled)e.Graphics.DrawPath(p,path);
        }
        TextRenderer.DrawText(e.Graphics,Text,Font,r,!Enabled?Color.Gray:Primary?Color.White:ColorTranslator.FromHtml("#2A2723"),TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
        if(Focused)ControlPaint.DrawFocusRectangle(e.Graphics,new Rectangle(6,6,Width-12,Height-12));
    }
}
class PrerequisiteWizard:Form {
    readonly bool uninstall;int page=1;readonly TableLayoutPanel shell,main,body,rail;
    readonly Label title,subtitle,counter; Label message;readonly MemoAction next,back,recheck;
    readonly Font heading=new Font("Microsoft YaHei",18,FontStyle.Bold);
    readonly Font smallHeading=new Font("Microsoft YaHei",11,FontStyle.Bold);
    readonly Color ink=ColorTranslator.FromHtml("#2A2723"),muted=ColorTranslator.FromHtml("#6E675E");
    public int CurrentPage {get{return page;}}
    public PrerequisiteWizard(bool remove){
        uninstall=remove;Text="Memorive · "+(remove?"卸载向导":"安装向导")+" · v1.01";StartPosition=FormStartPosition.CenterScreen;
        AutoScaleMode=AutoScaleMode.Dpi;ClientSize=new Size(1100,720);MinimumSize=new Size(780,620);MaximizeBox=false;
        BackColor=ColorTranslator.FromHtml("#FBF9F5");ForeColor=ink;Font=new Font("Times New Roman",12);
        using(var s=new System.IO.MemoryStream(UiBootstrap.Resource("memorive.ico")))Icon=new Icon(s);
        shell=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=1,Margin=Padding.Empty};shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,220));shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));Controls.Add(shell);
        rail=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=9,BackColor=ColorTranslator.FromHtml("#F0EDE6"),Padding=new Padding(16,24,16,16)};
        rail.RowStyles.Add(new RowStyle(SizeType.Absolute,98));for(int i=0;i<6;i++)rail.RowStyles.Add(new RowStyle(SizeType.Absolute,66));rail.RowStyles.Add(new RowStyle(SizeType.Percent,100));rail.RowStyles.Add(new RowStyle(SizeType.Absolute,24));shell.Controls.Add(rail,0,0);
        var brand=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false};brand.Controls.Add(new PictureBox{Image=Icon.ToBitmap(),SizeMode=PictureBoxSizeMode.Zoom,Size=new Size(54,58)});brand.Controls.Add(new Label{Text="Memorive\nWindows 安装向导",Font=smallHeading,AutoSize=true,Margin=new Padding(8,8,0,0)});rail.Controls.Add(brand,0,0);
        string[] steps={"欢迎","系统检查","安装位置","外部工具","准备安装","安装与完成"};
        for(int i=0;i<steps.Length;i++)rail.Controls.Add(new Label{Text=(i+1)+"   "+steps[i],Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft,Padding=new Padding(12,0,0,0),Font=smallHeading,Name="step"+(i+1)},0,i+1);
        rail.Controls.Add(new Label{Text="Memorive · v1.01",Dock=DockStyle.Fill,ForeColor=muted},0,8);
        main=new TableLayoutPanel{Dock=DockStyle.Fill,RowCount=3,ColumnCount=1,Margin=Padding.Empty};main.RowStyles.Add(new RowStyle(SizeType.Absolute,122));main.RowStyles.Add(new RowStyle(SizeType.Percent,100));main.RowStyles.Add(new RowStyle(SizeType.Absolute,80));shell.Controls.Add(main,1,0);
        var header=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=2,Padding=new Padding(30,24,30,16)};header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,70));
        title=new Label{Dock=DockStyle.Fill,Font=heading,AutoSize=true};subtitle=new Label{Dock=DockStyle.Fill,ForeColor=muted,AutoSize=true};counter=new Label{Dock=DockStyle.Fill,TextAlign=ContentAlignment.TopRight,ForeColor=muted};header.Controls.Add(title,0,0);header.Controls.Add(counter,1,0);header.Controls.Add(subtitle,0,1);header.SetColumnSpan(subtitle,2);main.Controls.Add(header,0,0);
        body=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=1,Padding=new Padding(30,8,30,16),AutoScroll=true};body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));main.Controls.Add(body,0,1);
        var footer=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,Padding=new Padding(30,16,30,16)};footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));footer.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var left=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false};back=new MemoAction("上一步"){Width=96};back.Click+=(o,e)=>ShowPage(1);recheck=new MemoAction("重新检查"){Width=120};recheck.Click+=(o,e)=>ShowPage(2);left.Controls.Add(back);left.Controls.Add(recheck);
        var right=new FlowLayoutPanel{AutoSize=true,WrapContents=false};var cancel=new MemoAction("取消"){Width=84};cancel.Click+=(o,e)=>Close();next=new MemoAction("下一步",true){Width=130};next.Click+=(o,e)=>{if(page==1){ShowPage(2);return;}if(Ready()){DialogResult=DialogResult.OK;Close();}else ShowPage(2);};right.Controls.Add(cancel);right.Controls.Add(next);footer.Controls.Add(left,0,0);footer.Controls.Add(right,1,0);main.Controls.Add(footer,0,2);
        message=new Label{AutoSize=true,ForeColor=muted,MaximumSize=new Size(680,0)};
        ShowPage(1);
        Shown+=(o,e)=>{var work=Screen.FromControl(this).WorkingArea;if(Width>work.Width||Height>work.Height){MinimumSize=Size.Empty;Size=new Size(Math.Min(Width,work.Width-16),Math.Min(Height,work.Height-16));CenterToScreen();}};
    }
    bool Ready(){return PrerequisiteFlow.CanContinue(Engine.WebViewVersion(),uninstall?null:Engine.VcRuntimeStatus(),Engine.NetRelease(),Environment.Is64BitOperatingSystem&&Environment.OSVersion.Version.Build>=19045);}
    Label Copy(string text,bool strong=false){return new Label{Text=text,AutoSize=true,MaximumSize=new Size(640,0),Font=strong?smallHeading:Font,ForeColor=strong?ink:muted,Margin=new Padding(0,0,0,12)};}
    void Official(string key){try{UiBootstrap.OpenOfficial(key);}catch(Exception ex){message.Text="无法打开官网："+ex.Message;}}
    void License(){using(var f=new Form{Text="许可与隐私说明",StartPosition=FormStartPosition.CenterParent,ClientSize=new Size(680,420),BackColor=BackColor,Font=Font}){
        var contents=new FlowLayoutPanel{Dock=DockStyle.Fill,FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoScroll=true,Padding=new Padding(24)};f.Controls.Add(contents);
        contents.Controls.Add(Copy("代码采用 AGPL-3.0，角色素材单独声明。WebView2 会自动更新，并使用 Microsoft SmartScreen 服务。",true));
        contents.Controls.Add(Copy("作者鼓励个人学习、研究与非商业使用，此倡议不限制 AGPL 授予的权利。角色素材为受 Blue Archive 启发的非官方二创，与 Nexon、Nexon Games、Yostar 无直接授权或隶属关系。"));
        contents.Controls.Add(Copy("Microsoft 组件适用其原有许可。SmartScreen 按 Microsoft 隐私声明收集并向 Microsoft 发送相关信息，用于安全检查。"));
        foreach(var pair in new[]{new[]{"代码与素材声明","project"},new[]{"Microsoft 隐私声明","privacy"}}){var b=new MemoAction(pair[0]){Width=240};string key=pair[1];b.Click+=(o,e)=>Official(key);contents.Controls.Add(b);}f.ShowDialog(this);
    }}
    void Row(FlowLayoutPanel list,string label,string value,bool pass,string action,string key){
        var card=new MemoSurface{Width=Math.Max(460,body.ClientSize.Width-70),Height=108,Margin=new Padding(0,0,0,12),Padding=new Padding(18)};
        var row=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=2};row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));row.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,188));
        row.Controls.Add(Copy((pass?"✓  ":"!  ")+label,true),0,0);var detail=Copy(value);detail.MaximumSize=new Size(Math.Max(220,card.Width-250),0);row.Controls.Add(detail,0,1);
        if(!pass&&key!=null){var b=new MemoAction(action){Dock=DockStyle.Fill,Margin=new Padding(10,6,0,6)};b.Click+=(o,e)=>Official(key);row.Controls.Add(b,1,0);row.SetRowSpan(b,2);}card.Controls.Add(row);list.Controls.Add(card);
    }
    void ShowPage(int requested){
        page=requested;body.SuspendLayout();while(body.Controls.Count>0){var c=body.Controls[0];body.Controls.Remove(c);c.Dispose();}
        for(int i=1;i<=6;i++){var label=rail.Controls["step"+i];label.BackColor=i==page?ColorTranslator.FromHtml("#E9E4DB"):rail.BackColor;label.ForeColor=i>2?ColorTranslator.FromHtml("#8E877E"):ink;}
        counter.Text=page+" / 6";back.Visible=recheck.Visible=page==2;next.Text=page==1?"下一步":"继续安装";next.Enabled=true;
        if(page==1){
            title.Text="欢迎使用 Memorive";subtitle.Text="此向导将帮助你完成 Memorive 安装。";
            var outer=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=3};outer.RowStyles.Add(new RowStyle(SizeType.Percent,50));outer.RowStyles.Add(new RowStyle(SizeType.Absolute,270));outer.RowStyles.Add(new RowStyle(SizeType.Percent,50));
            var card=new MemoSurface{Dock=DockStyle.Fill};var welcome=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=3};
            welcome.Controls.Add(new Label{Text="准备安装 Memorive",Font=heading,Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleCenter},0,0);
            welcome.Controls.Add(new Label{Text="建议先关闭其他应用，然后点击“下一步”继续。",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleCenter,ForeColor=muted},0,1);
            var link=new LinkLabel{Text="许可与隐私说明",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleCenter,LinkColor=ColorTranslator.FromHtml("#73563C")};link.LinkClicked+=(o,e)=>License();welcome.Controls.Add(link,0,2);card.Controls.Add(welcome);outer.Controls.Add(card,0,1);body.Controls.Add(outer);
        }else{
            title.Text="系统检查";subtitle.Text="补齐下列组件后，点击“重新检查”继续。";
            var list=new FlowLayoutPanel{Dock=DockStyle.Fill,FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoScroll=true};body.Controls.Add(list);
            bool platform=Environment.Is64BitOperatingSystem&&Environment.OSVersion.Version.Build>=19045;
            if(!platform)list.Controls.Add(Copy("需要 Windows 10 22H2 / Windows 11 x64。",true));
            string web=Engine.WebViewVersion(),vc=Engine.VcRuntimeStatus();int net=Engine.NetRelease();
            Row(list,"Microsoft Edge WebView2",web??"尚未安装 · 选择 Evergreen x64 运行库",web!=null,"WebView2 官网","webview");
            Row(list,"Visual C++ v14 · x64",vc==null?"已满足 · 14.51.36247 或更高":"请安装 vc_redist.x64.exe；x86 不能替代 x64。",vc==null,"下载 x64 运行库","visualcpp-x64");
            Row(list,".NET Framework",net>=528040?"已满足 · 4.8 或更高":"需要 .NET Framework 4.8 Runtime",net>=528040,".NET 官方下载","dotnet48");
            list.Controls.Add(Copy("从 Microsoft 官方来源安装组件后返回此页；若安装程序要求重启，请先重启，再打开本向导。"));
            var link=new LinkLabel{Text="许可与隐私说明",AutoSize=true,LinkColor=ColorTranslator.FromHtml("#73563C")};link.LinkClicked+=(o,e)=>License();list.Controls.Add(link);
            if(uninstall){var remove=new MemoAction("卸载程序并保留个人数据"){Width=300};remove.Click+=(o,e)=>{try{Engine.Remove(UiBootstrap.Root,UiBootstrap.Sandbox);message.Text="程序已卸载，个人数据已保留。";remove.Enabled=false;next.Enabled=false;}catch(Exception ex){message.Text=ex.Message;}};list.Controls.Add(remove);}
            message=new Label{AutoSize=true,MaximumSize=new Size(640,0),ForeColor=muted};list.Controls.Add(message);
            next.Enabled=Ready();
        }
        body.ResumeLayout(true);
    }
}
