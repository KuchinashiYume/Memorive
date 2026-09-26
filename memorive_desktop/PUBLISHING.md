# Memorive GitHub 发布操作说明

本版本正在私有仓库中复核，Release 保持草稿。完成检查后再执行下列发布流程。

## 正式项目地址

作者已确认公开仓库使用 **KuchinashiYume/Memorive**。

- 项目主页：https://github.com/KuchinashiYume/Memorive
- 版本与下载：https://github.com/KuchinashiYume/Memorive/releases
- 问题反馈：https://github.com/KuchinashiYume/Memorive/issues

EXE 内的项目主页按钮从 `release_links.py` 读取地址。先创建空仓库，再按上述地址构建和检查正式 EXE，最后提交已审阅源码、建立标签并上传 Release 附件。地址不依赖安装包提前存在；当前项目主页与 Release 均已公开。

## 发布材料如何组织

- **源码仓库**：以独立导出并核对的源文件集合建立，避免把整个历史工作区、沙盒或本机配置上传。源码中保留构建入口、依赖版本、必要合同与资源。
- **README**：提供中文、英文、日文版本。中文入口使用 `README_PUBLIC.md` 的内容。
- **代码许可证**：作者已确认 AGPL-3.0-only，标准全文见 LICENSE，适用范围见 LICENSING.md。角色素材继续单独声明。
- **Release 附件**：安装器、源码归档、需提供的第三方对应源码归档、SHA256SUMS；不把大安装器作为普通 Git 文件提交。
- **发布说明**：记录实际产品版本、绑定的源码提交、安装器哈希、未签名情况和已知限制。

## 最后一次审阅应看到什么

1. 最终源码文件列表、差异与哈希；安装器须与该源码构建记录对应。
2. 最终代码许可证、角色素材声明、第三方许可证与来源材料。
3. 安装／启动／卸载结果及其适用范围；明确旧字体 PDF 的支持范围和未签名状态。
4. 已检查私有配置、文献、会话、凭据、截图与内嵌图像的最终交付集合。

正式发布时先在选定的 Memorive 仓库导入已审阅源码，绑定提交与版本标签，再上传附件并核对 GitHub 上下载文件的 SHA256。当前文件中的候选状态应根据实际结论更新后再发布，不能把准备稿直接当作正式完成声明。

## 下载者如何核对安装器

在 PowerShell 中对下载的文件运行：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\Memorive-Setup-1.01-x64.exe'
```

将输出与同一 Release 的 SHA256SUMS 中对应文件逐字核对。正式文件名以最终 Release 为准；校验值证明文件一致性，不代替作者数字签名。

## 本地源码版本

使用独立导出目录创建单一初始提交，不导入 Memorive 历史。正式版本使用 `v1.01`；对应标签与源码提交在发布时绑定。分发文件的内容清理以正式版本号和文件校验值标识，差异由 SHA256 与发布清单记录。最终源码归档、对应第三方源码和安装器一起作为 Release 附件。
