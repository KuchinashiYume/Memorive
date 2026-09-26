# 安装与首次配置

1. 从 Memorive 项目的 Releases 下载安装器及同批 SHA256SUMS。使用 PowerShell 的 `Get-FileHash -Algorithm SHA256` 核对安装器。
2. 打开向导，在“系统检查”准备缺少的 Microsoft 组件。Visual C++ 必须选择 **x64**；安装 x86 不能满足本程序要求。安装成功后返回向导重新检查。
3. 选择程序目录和数据目录，完成安装。首次启动是空白配置，需要自行添加资料和模型服务。
4. 在“设置 → 模型服务与 API”添加自己的服务与模型，按使用文档连接 API、受支持的 CLI 或本地模型，再设置流程模型映射。不要将凭据写入公开 Issue。
5. 先导入一份资料，在研究问答中核对回答、引用和原始来源。

## 文档与故障信息

“设置 → 关于与版本”提供中英日使用文档、设计文档和许可与隐私说明。加载异常时，请提供版本、Windows 版本、复现步骤与脱敏错误；不要上传整个数据目录。

## 卸载与数据

通过 Windows 应用列表或程序卸载入口卸载。卸载程序与保留数据是不同操作；处理自己的文献、会话与配置前应自行备份。安装包不附带作者的 API key、文献库、会话库或预填个人账户。

## English

Verify the installer with the SHA256SUMS file from the same release. Follow System Check in the wizard; install the **x64** Visual C++ runtime if requested. Choose application and data folders, then configure your own model service in Settings. Start with one document and inspect its answer citations. The application includes English guides. Share only redacted diagnostics when reporting issues.

## 日本語

同じリリースの SHA256SUMS でインストーラーを確認してください。システムチェックで必要な Microsoft コンポーネントを準備します。Visual C++ は **x64** を選択してください。プログラムとデータの保存先を選び、設定から自分のモデルサービスを登録します。まず資料を一つ取り込み、回答の引用元を確認してください。日本語ガイドはアプリに同梱されています。
