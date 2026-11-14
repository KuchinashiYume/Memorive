using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Globalization;
using System.Collections.Generic;
using System.Windows.Forms;
using File=LongFile;

static class InstallerLocale {
    public static string Current="en-US";
    static Dictionary<string,Dictionary<string,string>> catalog;
    public static string Normalize(string value){if(value=="zh-CN"||value=="en-US"||value=="ja-JP")return value;return null;}
    public static string FromOs(){string value=CultureInfo.CurrentUICulture.Name;return value.StartsWith("zh",StringComparison.OrdinalIgnoreCase)?"zh-CN":value.StartsWith("ja",StringComparison.OrdinalIgnoreCase)?"ja-JP":"en-US";}
    public static string Text(string id){
        if(catalog==null)catalog=Engine.Json.Deserialize<Dictionary<string,Dictionary<string,string>>>(Encoding.UTF8.GetString(UiBootstrap.Resource("installer-catalog.json")));
        Dictionary<string,string> row;if(!catalog.TryGetValue(id,out row))return id;
        string value;return row.TryGetValue(Current,out value)?value:row["en-US"];
    }
    public static string Error(string code){string key="error."+code;string value=Text(key);return value==key?Text("error.generic")+" ("+code+")":value;}
    public static void Select(string[] args,bool interactive){
        int at=Array.IndexOf(args,"--language");string selected=at>=0&&at+1<args.Length?Normalize(args[at+1]):null;
        if(at>=0&&selected==null)throw new Exception("INSTALLER_LANGUAGE_INVALID");
        string root=Arg(args,"--root")??Arg(args,"--ui-root");
        if(selected==null&&root!=null){
            root=Engine.RootPath(root);
            string binding=Path.Combine(root,"updates/binding.json");
            if(File.Exists(binding)){
                var b=Engine.Load<UpdateBinding>(binding);
                if(b.schema=="MemoriveUpdateBinding-v1"&&NativePaths.Same(b.program_root,root)){
                    UpdateProtocol.Require(Engine.Within(b.state_root,b.data_root),"UPDATE_DATA_BINDING_INVALID");
                    string settings=Engine.Under(b.state_root,"profile/settings/settings.json");
                    if(File.Exists(settings))selected=ReadUiLanguage(settings);
                    if(selected==null)selected=Normalize(b.language);
                }
            }
            if(selected==null&&File.Exists(Path.Combine(root,"current.json"))){
                var current=Engine.ReadState(root);string state=Path.Combine(current.data_root,"localappdata/Memorive/desktop-review/state");
                string selection=Path.Combine(state,"private-profile-selection.json");
                if(File.Exists(selection)){
                    var previous=Engine.Load<Dictionary<string,object>>(selection);
                    if(Convert.ToString(previous["package_id"])==current.package_id&&Convert.ToString(previous["status"])=="READY"){
                        string chosen=Engine.RootPath(Convert.ToString(previous["state_root"]));
                        UpdateProtocol.Require(Engine.Within(chosen,current.data_root),"UPDATE_LEGACY_PROFILE_SCOPE_INVALID");state=chosen;
                    }
                }
                string settings=Engine.Under(state,"profile/settings/settings.json");if(File.Exists(settings))selected=ReadUiLanguage(settings);
            }
            string prior=Path.Combine(root,"installer-language.json");
            if(selected==null&&File.Exists(prior)){
                var p=Engine.Load<Dictionary<string,object>>(prior);object owner,language;
                if(p.TryGetValue("owner",out owner)&&Convert.ToString(owner)==Engine.Owner&&p.TryGetValue("language",out language))selected=Normalize(Convert.ToString(language));
            }
        }
        Current=selected??FromOs();
        // Every installer surface exposes an inline language control. Selecting
        // a default never opens a separate dialog or interrupts the wizard.
    }
    public static string ReadUiLanguage(string path){
        try{var envelope=Engine.Load<Dictionary<string,object>>(path);object nested,preferences,value;
            var settings=envelope.TryGetValue("settings",out nested)?nested as Dictionary<string,object>:envelope;
            if(settings!=null&&settings.TryGetValue("preferences",out preferences)){var a=preferences as Dictionary<string,object>;if(a!=null&&a.TryGetValue("language",out value))return Normalize(Convert.ToString(value));}
            return null;
        }catch{return null;}
    }
    public static void Remember(string root){Engine.Save(Path.Combine(root,"installer-language.json"),new{owner=Engine.Owner,language=Current});}
    static string Arg(string[] args,string key){int i=Array.IndexOf(args,key);return i>=0&&i+1<args.Length?args[i+1]:null;}
}

sealed class LanguageGlyphButton:MemoAction {
    public LanguageGlyphButton():base(""){Width=44;AccessibleName="Language / 语言 / 言語";}
    protected override void OnPaint(PaintEventArgs e){
        base.OnPaint(e);float scale=DeviceDpi/96f,cx=Width/2f,cy=Height/2f,r=9*scale;
        e.Graphics.SmoothingMode=System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        using(var pen=new System.Drawing.Pen(System.Drawing.ColorTranslator.FromHtml("#6E675E"),1.4f*scale)){
            e.Graphics.DrawEllipse(pen,cx-r,cy-r,r*2,r*2);e.Graphics.DrawEllipse(pen,cx-r*.45f,cy-r,r*.9f,r*2);
            e.Graphics.DrawLine(pen,cx-r,cy,cx+r,cy);
        }
    }
}
sealed class NativeLanguageSwitch:FlowLayoutPanel {
    public readonly FlowLayoutPanel Choices;
    public NativeLanguageSwitch(Action changed,int dpi=96){
        Func<int,int> px=value=>(int)Math.Round(value*dpi/96d);
        AutoSize=true;AutoSizeMode=AutoSizeMode.GrowAndShrink;WrapContents=false;FlowDirection=FlowDirection.RightToLeft;Margin=Padding.Empty;
        var toggle=new LanguageGlyphButton{Size=new System.Drawing.Size(px(44),px(42)),Margin=Padding.Empty};Controls.Add(toggle);
        var choices=new FlowLayoutPanel{AutoSize=true,WrapContents=false,Visible=false,AutoSizeMode=AutoSizeMode.GrowAndShrink,Anchor=AnchorStyles.Right,Margin=Padding.Empty};Choices=choices;Controls.Add(choices);
        string[] locales={"zh-CN","en-US","ja-JP"},names={"简体中文","English","日本語"};
        for(int i=0;i<locales.Length;i++){string selected=locales[i];var button=new MemoAction(names[i],selected==InstallerLocale.Current){Size=new System.Drawing.Size(px(100),px(42))};choices.Controls.Add(button);button.Click+=(o,e)=>{InstallerLocale.Current=selected;BeginInvoke(changed);};}
        toggle.Click+=(o,e)=>{choices.Visible=!choices.Visible;if(choices.Visible)choices.Controls[Array.IndexOf(locales,InstallerLocale.Current)].Focus();};
    }
}


// A startup failure has a normal OS window. Language choices stay inline.
sealed class NativeNoticeWindow:Form {
    readonly string messageId;readonly Exception failure;bool expanded,languageExpanded;
    int dpi=96;Label message;
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    int Px(int value){return (int)Math.Round(value*dpi/96d);}
    MemoAction ActionButton(string key,bool primary=false){
        var button=new MemoAction(InstallerLocale.Text(key),primary){Font=Font,Margin=new Padding(Px(8),0,0,0)};
        button.Size=new System.Drawing.Size(Math.Max(Px(96),TextRenderer.MeasureText(button.Text,Font).Width+Px(32)),Px(40));return button;
    }
    public static void Run(string id,Exception failure,string[] args){
        // Retain only an explicitly sandboxed capture destination. A failed
        // Configure call must never make an arbitrary path writable.
        string evidence=null;int at=Array.IndexOf(args,"--ui-evidence");
        if(at>=0&&at+1<args.Length&&(args.Contains("--sandbox")||args.Contains("--ui-sandbox"))){
            try{string requested=Engine.RootPath(args[at+1]);Engine.Sandbox(requested);if(requested.StartsWith(Engine.TestRoot+@"\evidence\",StringComparison.OrdinalIgnoreCase)){LongDirectory.CreateDirectory(requested);evidence=requested;}}catch{}
        }
        using(var page=new NativeNoticeWindow(id,failure)){
            if(evidence!=null)page.Shown+=(s,e)=>{var timer=new Timer{Interval=500};timer.Tick+=(o,a)=>{timer.Stop();timer.Dispose();if(page.IsDisposed)return;string path=System.IO.Path.Combine(evidence,"native-notice-"+Guid.NewGuid().ToString("N"));using(var bitmap=new System.Drawing.Bitmap(page.Width,page.Height)){page.DrawToBitmap(bitmap,new System.Drawing.Rectangle(System.Drawing.Point.Empty,page.Size));bitmap.Save(path+".png",System.Drawing.Imaging.ImageFormat.Png);}Engine.Save(path+".json",new{method="LIVE_NATIVE_FORM_DRAWTOBITMAP_AFTER_LAYOUT",language=InstallerLocale.Current,message_id=id,visible_message=InstallerLocale.Text(id),diagnostics_visible=false,error=failure==null?null:failure.Message,image_sha256=Engine.Hash(path+".png")});};timer.Start();};
            Application.Run(page);
        }
    }
    NativeNoticeWindow(string id,Exception ex){messageId=id;failure=ex;
        using(var stream=new MemoryStream(UiBootstrap.Resource("memorive.ico")))using(var icon=new System.Drawing.Icon(stream)){Icon=(System.Drawing.Icon)icon.Clone();}
        AutoScaleMode=AutoScaleMode.None;StartPosition=FormStartPosition.CenterScreen;
        dpi=Math.Max(96,(int)GetDpiForWindow(Handle));ClientSize=new System.Drawing.Size(Px(680),Px(320));
        MinimumSize=SizeFromClientSize(new System.Drawing.Size(Px(620),Px(300)));
        SizeChanged+=(s,e)=>{if(message!=null&&!message.IsDisposed)message.MaximumSize=new System.Drawing.Size(Math.Max(Px(240),ClientSize.Width-Px(48)),0);};
        Build();
    }
    void Build(){
        SuspendLayout();while(Controls.Count>0){var control=Controls[0];Controls.Remove(control);control.Dispose();}
        Text=InstallerLocale.Text("notice.title");BackColor=System.Drawing.ColorTranslator.FromHtml("#FFFDF9");
        ForeColor=System.Drawing.ColorTranslator.FromHtml("#2A2723");
        
        Font=new System.Drawing.Font(InstallerLocale.Current=="zh-CN"?"SimSun":InstallerLocale.Current=="ja-JP"?"Yu Mincho":"Times New Roman",Px(16),System.Drawing.FontStyle.Regular,System.Drawing.GraphicsUnit.Pixel);
        var layout=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(Px(24)),ColumnCount=1,RowCount=5,Margin=Padding.Empty};
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));Controls.Add(layout);
        var header=new TableLayoutPanel{Dock=DockStyle.Fill,AutoSize=true,ColumnCount=2,RowCount=1,Margin=new Padding(0,0,0,Px(16))};
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));layout.Controls.Add(header,0,0);
        header.Controls.Add(new Label{Name="noticeHeading",Text=InstallerLocale.Text("notice.title"),Dock=DockStyle.Fill,AutoSize=true,TextAlign=System.Drawing.ContentAlignment.MiddleLeft,Font=new System.Drawing.Font(Font.FontFamily,Px(22),System.Drawing.FontStyle.Regular,System.Drawing.GraphicsUnit.Pixel),Margin=new Padding(0,0,Px(12),0)},0,0);
        var languages=new NativeLanguageSwitch(()=>Build(),dpi){Anchor=AnchorStyles.Right};
        var choices=languages.Choices;languages.Controls.Remove(choices);
        choices.Visible=languageExpanded;choices.Margin=new Padding(0,0,0,Px(16));choices.Anchor=AnchorStyles.Right;
        foreach(Control c in choices.Controls){c.Font=Font;c.Size=new System.Drawing.Size(Math.Max(Px(96),TextRenderer.MeasureText(c.Text,Font).Width+Px(28)),Px(40));c.Margin=new Padding(Px(8),0,0,0);}
        languages.Controls[0].Click+=(s,e)=>languageExpanded=choices.Visible;
        header.Controls.Add(languages,1,0);layout.Controls.Add(choices,0,1);
        message=new Label{Name="noticeMessage",Text=InstallerLocale.Text(messageId),AutoSize=true,MaximumSize=new System.Drawing.Size(ClientSize.Width-Px(48),0),ForeColor=System.Drawing.ColorTranslator.FromHtml("#6E675E"),Margin=new Padding(0,0,0,Px(20))};layout.Controls.Add(message,0,2);
        var details=new TextBox{Name="noticeDetails",Text=failure==null?"":failure.ToString(),Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Both,Dock=DockStyle.Fill,Visible=expanded,WordWrap=false,Margin=new Padding(0,0,0,Px(20)),BackColor=System.Drawing.ColorTranslator.FromHtml("#F0EDE6"),BorderStyle=BorderStyle.FixedSingle};layout.Controls.Add(details,0,3);
        var actions=new FlowLayoutPanel{Name="noticeActions",Anchor=AnchorStyles.Right,AutoSize=true,AutoSizeMode=AutoSizeMode.GrowAndShrink,WrapContents=false,Margin=Padding.Empty};layout.Controls.Add(actions,0,4);
        if(failure!=null){var toggle=ActionButton("common.details");toggle.Margin=Padding.Empty;toggle.Click+=(s,e)=>{expanded=!expanded;details.Visible=expanded;};actions.Controls.Add(toggle);
            var copy=ActionButton("notice.copyDetails");copy.Click+=(s,e)=>{try{Clipboard.SetText(failure.ToString());copy.Text=InstallerLocale.Text("notice.copied");copy.Width=Math.Max(copy.Width,TextRenderer.MeasureText(copy.Text,Font).Width+Px(32));}catch{expanded=true;details.Visible=true;details.SelectAll();details.Focus();}};actions.Controls.Add(copy);}
        var close=ActionButton("common.close",true);close.Click+=(s,e)=>Close();actions.Controls.Add(close);CancelButton=close;
        ResumeLayout(true);
    }
}
