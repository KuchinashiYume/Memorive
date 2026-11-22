Memorive web conversation extension 3.11.0 / Memorive 网页会话插件 3.11.0 / Memorive ウェブ会話拡張機能 3.11.0

English
=======
This extension brings conversations from AI websites into Memorive. It reads only what the page shows.

Popup
- Start recording / Stop and save: on a conversation page, click "Start recording", scroll up and down until the
  whole conversation has shown, then click "Stop and save". The messages seen while recording are saved as a
  partial record. Messages whose place could not be confirmed are counted and left out. Recording ends after
  10 minutes and saves; changing page ends it.
- Test read: reads the conversation visible on the page and shows the count only; nothing is saved.
- Site sync: syncs the enabled websites; a full sync is started from Memorive.
- Aa: chooses the language of the popup (简体中文, English, 日本語). The extension name in the browser's lists
  follows the browser's language.
- After saving, go back to Memo to import the file.

Syncs started by Memorive
- Conversations of the last 14 days; when the sidebar shows no dates, the first 30 in the sidebar.
- A sync opens its own browser window, opens each conversation, scrolls through it and closes it.
- A conversation is saved only when it was read from top to bottom without a break; otherwise it is reported
  as failed and the earlier saved version stays.
- Results go to the "Memorive会话桥接" folder in the browser's download folder; Memorive imports them when it
  refreshes.

Supported websites
- Sync reads DeepSeek, Gemini and Kimi by default; change this in Memorive under Settings > Conversation
  Refinement > Web Session Library.
- Recording the current page works on the official websites of DeepSeek, Gemini, Kimi, Qwen, 千问 (Qianwen),
  ChatGPT, Claude, Grok, Doubao, Tencent Yuanbao, ERNIE Bot (文心一言), Zhipu Qingyan (智谱清言), Microsoft Copilot,
  Perplexity, Poe and Mistral Le Chat, matched by full domain; mirror sites are not supported.

Install and update
1. Prepare the extension in Memorive, then load the folder on the extensions page of Edge, Chrome or Brave.
2. After an update or a change to the site settings, reload the extension there, then do a test read.
3. Sign in to the website in the browser before reading.

Reading rules
- No cookies, passwords or browser storage of websites are read; no internal website interfaces are called;
  nothing on the website is changed or deleted; no conversation is sent to any model.
- Reading stops when the website asks for a human check. Complete it on the page and try again.

简体中文
========
这个插件把 AI 官网上的会话读进 Memorive。它只读页面上显示的内容。

插件弹窗
- 开始录制 / 停止并录入：在会话页面点「开始录制」，上下滚动让整段对话显示出来，再点「停止并录入」。保存录制期间页面上
  出现过的消息，记为部分记录；位置确认不了的消息会计数，不保存。录制最长 10 分钟，到时自动停止并保存；切换页面会结束录制。
- 测试读取：读取当前页面上可见的对话，只显示条数，不保存。
- 网站同步：同步已启用的网站；完整同步由 Memorive 发起。
- Aa：选择弹窗的语言（简体中文、English、日本語）。浏览器扩展列表里的插件名跟随浏览器的语言。
- 保存后回到 Memo 导入文件。

Memorive 发起的同步
- 读取范围是最近 14 天的会话；侧栏没有日期时，取侧栏前 30 条。
- 同步时插件打开一个单独的浏览器窗口，逐条打开会话、滚动读取全文，读完关闭。
- 一段会话从头到尾连续读完才保存；中间接不上时记为失败，之前保存的版本保留。
- 结果保存在浏览器下载目录的「Memorive会话桥接」文件夹，Memorive 刷新时自动导入。

支持的网站
- 同步默认读取 DeepSeek、Gemini、Kimi，可以在 Memorive「设置 → 对话精炼设置 → 网页会话库」里更改。
- 录入当前页面支持 DeepSeek、Gemini、Kimi、Qwen、千问、ChatGPT、Claude、Grok、豆包、腾讯元宝、文心一言、智谱清言、
  Microsoft Copilot、Perplexity、Poe、Mistral Le Chat 的官网，按完整域名匹配，不支持镜像站。

安装和更新
1. 在 Memorive 里准备插件，然后在 Edge、Chrome 或 Brave 的扩展管理页加载生成的文件夹。
2. 插件更新或网站设置改动后，在扩展管理页重新加载插件，再做一次测试读取。
3. 读取前先在浏览器里登录目标网站。

读取规则
- 不读取网站的 Cookie、密码或浏览器存储，不调用网站的内部接口，不修改或删除网站上的会话，不把会话发给任何模型。
- 网页出现人机验证时会停止读取。请在网页上完成验证后重试。

日本語
======
この拡張機能は AI サイトの会話を Memorive に取り込みます。読み取るのはページに表示されている内容だけです。

ポップアップ
- 記録を開始 / 停止して保存：会話ページで「記録を開始」を押し、会話全体が表示されるまで上下にスクロールしてから
  「停止して保存」を押します。記録中に表示されたメッセージを部分記録として保存します。位置を確認できなかったメッセージは
  件数を表示し、保存しません。記録は最長 10 分で自動的に停止して保存します。ページを切り替えると記録は終わります。
- 読み取りテスト：ページに表示中の会話を読み取り、件数だけを表示します。保存はしません。
- サイト同期：有効なサイトを同期します。全体の同期は Memorive から始めます。
- Aa：ポップアップの言語（简体中文、English、日本語）を選びます。ブラウザーの拡張機能一覧の名前はブラウザーの言語に従います。
- 保存したら Memo に戻ってファイルを取り込んでください。

Memorive から始める同期
- 直近 14 日の会話を読み取ります。サイドバーに日付がない場合は、サイドバーの先頭 30 件です。
- 同期中は専用のブラウザーウィンドウを開き、会話を一つずつ開いて全文をスクロールして読み取り、終わると閉じます。
- 会話は最初から最後まで途切れずに読み取れた場合だけ保存します。途中がつながらない場合は失敗として報告し、
  以前に保存した版はそのまま残ります。
- 結果はブラウザーのダウンロードフォルダーにある「Memorive会话桥接」フォルダーに保存され、Memorive が更新時に取り込みます。

対応サイト
- 同期は既定で DeepSeek、Gemini、Kimi を読み取ります。Memorive の「設定 → 会話精製設定 → Web セッションライブラリ」で変更できます。
- 現在のページの記録は DeepSeek、Gemini、Kimi、Qwen、千問、ChatGPT、Claude、Grok、豆包、騰訊元宝、文心一言、智譜清言、
  Microsoft Copilot、Perplexity、Poe、Mistral Le Chat の公式サイトで使えます。完全なドメインで照合し、ミラーサイトには対応しません。

インストールと更新
1. Memorive で拡張機能を準備し、Edge、Chrome、Brave の拡張機能管理ページで作成されたフォルダーを読み込みます。
2. 更新後やサイト設定を変えた後は、管理ページで拡張機能を再読み込みし、読み取りテストを一度行ってください。
3. 読み取る前に、ブラウザーで対象サイトにログインしてください。

読み取りのルール
- サイトの Cookie、パスワード、ブラウザーのストレージは読み取りません。サイトの内部インターフェースは呼び出さず、
  サイト上の会話を変更・削除せず、会話をどのモデルにも送りません。
- サイトが人による確認を求めた場合は読み取りを止めます。ページで確認を済ませてから、もう一度お試しください。
