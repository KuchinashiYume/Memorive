// Native welcome/check pages used only when the HTML renderer is unavailable.
// No runtime installer is downloaded or executed by this form.
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.Collections.Generic;

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
    public MemoAction(string text,bool primary=false){Text=text;AccessibleName=text;Primary=primary;AutoSize=false;Size=new Size(160,42);FlatStyle=FlatStyle.Flat;FlatAppearance.BorderSize=0;Font=new Font("Times New Roman",12);Cursor=Cursors.Hand;UseVisualStyleBackColor=false;SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.ResizeRedraw,true);}
    Color CanvasColor(){Control parent=Parent;while(parent!=null){if(parent.BackColor.A==255)return parent.BackColor;parent=parent.Parent;}return ColorTranslator.FromHtml("#FFFDF9");}
    protected override void OnPaintBackground(PaintEventArgs e){e.Graphics.Clear(CanvasColor());}
    protected override void OnPaint(PaintEventArgs e){
        e.Graphics.Clear(CanvasColor());
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
    readonly bool uninstall;int page=1;TableLayoutPanel shell,main,body,rail;
    Label title,subtitle,counter; Label message;MemoAction next,back,recheck;
    Font heading;
    Font smallHeading;
    int dpi=96,shot;bool built,license;Image brandImage;
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    int Px(int value){return (int)Math.Round(value*dpi/96d);}
    readonly Color ink=ColorTranslator.FromHtml("#2A2723"),muted=ColorTranslator.FromHtml("#6E675E");
    public int CurrentPage {get{return page;}}
    public PrerequisiteWizard(bool remove){
        uninstall=remove;AutoScaleMode=AutoScaleMode.None;StartPosition=FormStartPosition.CenterScreen;
        dpi=Math.Max(96,(int)GetDpiForWindow(Handle));Build();
        Shown+=(o,e)=>{FitWindow();CaptureSoon("shown");};
    }
    void FitWindow(){
        var work=Screen.FromControl(this).WorkingArea;var available=WindowLayout.Available(work,dpi);
        MinimumSize=new Size(Math.Min(available.Width,Px(780)),Math.Min(available.Height,Px(620)));
        MaximumSize=available.Size;
        if(Width>available.Width||Height>available.Height){Size=new Size(Math.Min(Width,available.Width),Math.Min(Height,available.Height));CenterToScreen();}
    }
    void CaptureSoon(string reason){
        if(UiBootstrap.Evidence==null||!IsHandleCreated)return;
        var timer=new System.Windows.Forms.Timer{Interval=500};timer.Tick+=(o,e)=>{timer.Stop();timer.Dispose();if(IsDisposed)return;
            PerformLayout();string name=(++shot).ToString("000")+"-native-"+InstallerLocale.Current;
            string image=System.IO.Path.Combine(UiBootstrap.Evidence,name+".png");
            using(var bitmap=new Bitmap(Width,Height)){DrawToBitmap(bitmap,new Rectangle(Point.Empty,Size));bitmap.Save(image,System.Drawing.Imaging.ImageFormat.Png);}
            var rows=new List<object>();CollectControls(this,rows);
            Engine.Save(System.IO.Path.Combine(UiBootstrap.Evidence,name+".json"),new{method="LIVE_NATIVE_FORM_DRAWTOBITMAP_AFTER_LAYOUT",reason=reason,page=page,language=InstallerLocale.Current,dpi=dpi,width=Width,height=Height,window_bounds=new{Left,Top,Width,Height},window_border=FormBorderStyle.ToString(),image_sha256=Engine.Hash(image),controls=rows});
        };timer.Start();
    }
    void CollectControls(Control parent,List<object> rows){foreach(Control c in parent.Controls){if(!c.Visible)continue;var r=RectangleToClient(c.RectangleToScreen(c.ClientRectangle));rows.Add(new{name=c.Name,type=c.GetType().Name,text=c.Text,x=r.X,y=r.Y,width=r.Width,height=r.Height,text_width=c is Button?TextRenderer.MeasureText(c.Text,c.Font).Width:0});CollectControls(c,rows);}}
    void Build(){
        SuspendLayout();while(Controls.Count>0){var child=Controls[0];Controls.Remove(child);child.Dispose();}
        if(brandImage!=null){brandImage.Dispose();brandImage=null;}
        if(heading!=null)heading.Dispose();if(smallHeading!=null)smallHeading.Dispose();
        heading=new Font("Times New Roman",Px(24),FontStyle.Bold,GraphicsUnit.Pixel);
        smallHeading=new Font("Times New Roman",Px(15),FontStyle.Bold,GraphicsUnit.Pixel);
        Text="Memorive · "+(uninstall?"卸载向导":"安装向导")+" · v1.01";
        if(!built)ClientSize=new Size(Px(1100),Px(720));MaximizeBox=false;
        BackColor=ColorTranslator.FromHtml("#FBF9F5");ForeColor=ink;Font=new Font("Times New Roman",Px(16),FontStyle.Regular,GraphicsUnit.Pixel);
        using(var stream=new System.IO.MemoryStream(UiBootstrap.Resource("memorive.ico")))using(var loaded=new Icon(stream,new Size(48,48))){var previous=Icon;Icon=(Icon)loaded.Clone();if(previous!=null)previous.Dispose();}
        // Use the existing product PNG for the brand artwork; the ICO remains the window icon.
        using(var stream=new System.IO.MemoryStream(UiBootstrap.Resource("memorive-brand.png")))using(var loaded=Image.FromStream(stream)){brandImage=new Bitmap(loaded);}
        shell=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=1,Margin=Padding.Empty};shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,Px(220)));shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));Controls.Add(shell);
        rail=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=9,BackColor=ColorTranslator.FromHtml("#F0EDE6"),Padding=new Padding(Px(16),Px(24),Px(16),Px(16)),Margin=Padding.Empty};
        rail.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(110)));for(int i=0;i<6;i++)rail.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(62)));rail.RowStyles.Add(new RowStyle(SizeType.Percent,100));rail.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(28)));shell.Controls.Add(rail,0,0);
        var brand=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=2,Margin=Padding.Empty};brand.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,Px(52)));brand.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));brand.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(54)));brand.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        brand.Controls.Add(new PictureBox{Image=brandImage,SizeMode=PictureBoxSizeMode.Zoom,Dock=DockStyle.Fill,Margin=Padding.Empty,Name="nativeBrandIcon"},0,0);brand.Controls.Add(new Label{Text="Memorive",Font=smallHeading,Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft,Margin=new Padding(Px(8),0,0,0)},1,0);
        var brandSubtitle=new Label{Text="Windows 安装向导",Dock=DockStyle.Fill,AutoEllipsis=false,TextAlign=ContentAlignment.TopLeft,Name="nativeBrandSubtitle"};brand.Controls.Add(brandSubtitle,0,1);brand.SetColumnSpan(brandSubtitle,2);rail.Controls.Add(brand,0,0);
        string[] steps={"欢迎","系统检查","安装位置","外部工具","准备安装","安装与完成"};
        for(int i=0;i<steps.Length;i++)rail.Controls.Add(new Label{Text=(i+1)+"   "+steps[i],Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft,Padding=new Padding(Px(12),0,0,0),Font=smallHeading,Name="step"+(i+1),Margin=Padding.Empty},0,i+1);
        rail.Controls.Add(new Label{Text="Memorive · v1.01",Dock=DockStyle.Fill,ForeColor=muted,Margin=Padding.Empty},0,8);
        main=new TableLayoutPanel{Dock=DockStyle.Fill,RowCount=3,ColumnCount=1,Margin=Padding.Empty};main.RowStyles.Add(new RowStyle(SizeType.AutoSize));main.RowStyles.Add(new RowStyle(SizeType.Percent,100));main.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(80)));shell.Controls.Add(main,1,0);
        var header=new TableLayoutPanel{Dock=DockStyle.Top,AutoSize=true,ColumnCount=3,RowCount=3,Padding=new Padding(Px(30),Px(24),Px(30),Px(16)),Margin=Padding.Empty};header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,Px(70)));header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,Px(44)));for(int i=0;i<3;i++)header.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        title=new Label{Dock=DockStyle.Top,Font=heading,AutoSize=true,Margin=Padding.Empty};subtitle=new Label{Dock=DockStyle.Top,ForeColor=muted,AutoSize=true,Margin=new Padding(0,Px(8),0,Px(10))};counter=new Label{AutoSize=true,Anchor=AnchorStyles.Right,ForeColor=muted,Margin=new Padding(0,0,Px(12),0)};header.Controls.Add(title,0,0);header.Controls.Add(counter,1,0);header.Controls.Add(subtitle,0,1);header.SetColumnSpan(subtitle,3);
        var languages=new NativeLanguageSwitch(()=>{var bounds=Bounds;bool wasLicense=license;Build();Bounds=bounds;if(wasLicense)License();CaptureSoon("language");},dpi){Anchor=AnchorStyles.Right};languages.Controls.Remove(languages.Choices);header.Controls.Add(languages,2,0);header.Controls.Add(languages.Choices,0,2);header.SetColumnSpan(languages.Choices,3);main.Controls.Add(header,0,0);
        body=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=1,Padding=new Padding(Px(30),Px(8),Px(30),Px(16)),Margin=Padding.Empty,AutoScroll=true};body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));body.RowStyles.Add(new RowStyle(SizeType.Percent,100));main.Controls.Add(body,0,1);
        var footer=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=1,Margin=Padding.Empty,Padding=new Padding(Px(30),Px(16),Px(30),Px(16))};footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));footer.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var left=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false,Margin=Padding.Empty};back=new MemoAction("上一步"){Size=new Size(Px(96),Px(42))};back.Click+=(o,e)=>ShowPage(license?page:1);recheck=new MemoAction("重新检查"){Size=new Size(Px(120),Px(42))};recheck.Click+=(o,e)=>ShowPage(2);left.Controls.Add(back);left.Controls.Add(recheck);
        var right=new FlowLayoutPanel{AutoSize=true,AutoSizeMode=AutoSizeMode.GrowAndShrink,WrapContents=false,Margin=Padding.Empty};var cancel=new MemoAction("取消"){Size=new Size(Px(120),Px(42))};cancel.Click+=(o,e)=>Close();next=new MemoAction("下一步",true){Size=new Size(Px(130),Px(42))};next.Click+=(o,e)=>{if(page==1){ShowPage(2);return;}if(Ready()){DialogResult=DialogResult.OK;Close();}else ShowPage(2);};right.Controls.Add(cancel);right.Controls.Add(next);footer.Controls.Add(left,0,0);footer.Controls.Add(right,1,0);main.Controls.Add(footer,0,2);
        message=new Label{AutoSize=true,ForeColor=muted,MaximumSize=new Size(Px(680),0)};
        ShowPage(page);built=true;ResumeLayout(true);FitWindow();
    }
    bool Ready(){return PrerequisiteFlow.CanContinue(Engine.WebViewVersion(),uninstall?null:Engine.VcRuntimeStatus(),Engine.NetRelease(),Environment.Is64BitOperatingSystem&&Environment.OSVersion.Version.Build>=19045);}
    Label Copy(string text,bool strong=false){return new Label{Text=text,AutoSize=true,MaximumSize=new Size(Px(640),0),Font=strong?smallHeading:Font,ForeColor=strong?ink:muted,Margin=new Padding(0,0,0,Px(12))};}
    void Official(string key){try{UiBootstrap.OpenOfficial(key);}catch(Exception ex){message.Text="无法打开官网："+ex.Message;}}
    void License(){
        license=true;body.SuspendLayout();while(body.Controls.Count>0){var c=body.Controls[0];body.Controls.Remove(c);c.Dispose();}
        title.Text="许可与隐私说明";subtitle.Text="";back.Visible=true;recheck.Visible=false;next.Visible=false;
        var contents=new FlowLayoutPanel{Dock=DockStyle.Fill,FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoScroll=true,Padding=Padding.Empty};body.Controls.Add(contents);
        contents.Controls.Add(Copy("代码采用 AGPL-3.0，角色素材单独声明。WebView2 会自动更新，并使用 Microsoft SmartScreen 服务。",true));
        contents.Controls.Add(Copy("作者鼓励个人学习、研究与非商业使用，此倡议不限制 AGPL 授予的权利。角色素材为受 Blue Archive 启发的非官方二创，与 Nexon、Nexon Games、Yostar 无直接授权或隶属关系。"));
        contents.Controls.Add(Copy("Microsoft 组件适用其原有许可。SmartScreen 按 Microsoft 隐私声明收集并向 Microsoft 发送相关信息，用于安全检查。"));
        foreach(var pair in new[]{new[]{"代码与素材声明","project"},new[]{"Microsoft 隐私声明","privacy"}}){var b=new MemoAction(pair[0]){Width=Px(240)};string key=pair[1];b.Click+=(o,e)=>Official(key);contents.Controls.Add(b);}
        contents.SizeChanged+=(o,e)=>{foreach(Control c in contents.Controls)if(c is Label)c.MaximumSize=new Size(Math.Max(Px(200),contents.ClientSize.Width-Px(24)),0);};
        body.ResumeLayout(true);CaptureSoon("license");
    }
    void Row(FlowLayoutPanel list,string label,string value,bool pass,string action,string key){
        var card=new MemoSurface{Width=Math.Max(Px(300),list.ClientSize.Width-Px(24)),Height=Px(120),Margin=new Padding(0,0,0,Px(12)),Padding=new Padding(Px(16))};
        var row=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=2,RowCount=2,Margin=Padding.Empty};row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));row.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,!pass&&key!=null?Px(160):0));row.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(30)));row.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        var name=Copy((pass?"✓  ":"!  ")+label,true);name.Margin=Padding.Empty;row.Controls.Add(name,0,0);var detail=Copy(value);detail.Margin=Padding.Empty;row.Controls.Add(detail,0,1);
        if(!pass&&key!=null){var b=new MemoAction(action){Anchor=AnchorStyles.Right,Size=new Size(Px(152),Px(42)),Margin=Padding.Empty};b.Click+=(o,e)=>Official(key);row.Controls.Add(b,1,0);row.SetRowSpan(b,2);}card.Controls.Add(row);list.Controls.Add(card);
        Action fit=()=>{card.Width=Math.Max(Px(300),list.ClientSize.Width-Px(24));int width=Math.Max(Px(120),card.Width-Px(32)-(!pass&&key!=null?Px(160):0));name.MaximumSize=detail.MaximumSize=new Size(width,0);int titleHeight=TextRenderer.MeasureText(name.Text,name.Font,new Size(width,Int32.MaxValue),TextFormatFlags.WordBreak).Height+Px(8);row.RowStyles[0].Height=titleHeight;card.Height=Math.Max(Px(112),Px(32)+titleHeight+TextRenderer.MeasureText(detail.Text,detail.Font,new Size(width,Int32.MaxValue),TextFormatFlags.WordBreak).Height);};
        list.SizeChanged+=(o,e)=>fit();fit();
    }
    void ShowPage(int requested){
        page=requested;license=false;next.Visible=true;body.SuspendLayout();while(body.Controls.Count>0){var c=body.Controls[0];body.Controls.Remove(c);c.Dispose();}
        for(int i=1;i<=6;i++){var label=rail.Controls["step"+i];label.BackColor=i==page?ColorTranslator.FromHtml("#E9E4DB"):rail.BackColor;label.ForeColor=i>2?ColorTranslator.FromHtml("#8E877E"):ink;}
        counter.Text=page+" / 6";back.Visible=recheck.Visible=page==2;next.Text=page==1?"下一步":"继续安装";next.Enabled=true;
        if(page==1){
            title.Text="欢迎使用 Memorive";subtitle.Text="此向导将帮助你完成 Memorive 安装。";
            var outer=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=3};outer.RowStyles.Add(new RowStyle(SizeType.Percent,50));outer.RowStyles.Add(new RowStyle(SizeType.Absolute,Px(270)));outer.RowStyles.Add(new RowStyle(SizeType.Percent,50));
            var card=new MemoSurface{Dock=DockStyle.Fill,Padding=new Padding(Px(24))};var welcome=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=3,Margin=Padding.Empty};welcome.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));welcome.RowStyles.Add(new RowStyle(SizeType.Percent,44));welcome.RowStyles.Add(new RowStyle(SizeType.Percent,28));welcome.RowStyles.Add(new RowStyle(SizeType.Percent,28));
            welcome.Controls.Add(new Label{Name="nativeWelcomeVersion",Text="准备安装 Memorive"+" v1.01",Font=heading,Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleCenter},0,0);
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
            if(uninstall){var remove=new MemoAction("卸载程序并保留个人数据"){Width=Px(300)};remove.Click+=(o,e)=>{try{Engine.Remove(UiBootstrap.Root,UiBootstrap.Sandbox);message.Text="程序已卸载，个人数据已保留。";remove.Enabled=false;next.Enabled=false;}catch(Exception ex){message.Text=ex.Message;}};list.Controls.Add(remove);}
            message=new Label{AutoSize=true,MaximumSize=new Size(Px(640),0),ForeColor=muted};list.Controls.Add(message);
            next.Enabled=Ready();
        }
        body.ResumeLayout(true);CaptureSoon("page");
    }
}
