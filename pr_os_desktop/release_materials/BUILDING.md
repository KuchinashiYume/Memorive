# Memorive 本地源码构建

状态：可构建的本地 unsigned candidate。公开分发资格见 [RELEASE_PREPARATION.md](RELEASE_PREPARATION.md)。本说明不复用任何上一版 EXE。

## 输入

- Windows x64，CPython **3.13.14**。
- `SOURCE_MANIFEST.public-candidate.json`：当前公开候选源码／资源的逐文件 SHA256；包含旧字体兼容修复、正式项目链接、许可与隐私入口及 CSP 修复。
- `SOURCE_MANIFEST.r51.json`：正式工作区保留的不可变 r51 对照清单，不代表当前修复后的文件集合。
- `BUILD_RESOURCES.public-candidate.json`：build139 构建资源及 88 份运行时合同到包内位置的映射。资源路径相对于仓库根目录，故需保留映射所指向的 `项目文档/` 文件。
- `requirements-build-r51.lock`：原 r51 的 113 项精确版本。
- `requirements-build-agpl-candidate.lock`：新候选的 114 项版本；PyMuPDF 系列 1.28.2、psutil 7.2.2，其余不变。

构建器校验所有输入哈希、解释器版本和已安装依赖版本。除 pip 外，多装依赖也会被拒绝。不同依赖方案应各用一个新虚拟环境。

## 构建命令

以下命令在仓库根目录运行。示例输出目录须尚不存在，且位于源码目录之外。

```powershell
py -3.13 -m venv G:\Memorive-build-env
G:\Memorive-build-env\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r .\pr_os_desktop\requirements-build-agpl-candidate.lock
G:\Memorive-build-env\Scripts\python.exe -m pip check
G:\Memorive-build-env\Scripts\python.exe -X utf8 .\pr_os_desktop\tools\build_memorive.py --dependency-lock .\pr_os_desktop\requirements-build-agpl-candidate.lock --output G:\Memorive-build-candidate01
```

`py -3.13` 必须实际指向 3.13.14；仅安装其他补丁版本时，需先准备正确解释器。构建器不会修改已有安装目录、设置系统变量或创建后台服务。

输出包括 `dist/Memorive/`、`build-receipt.json`、PyInstaller spec／TOC 和构建日志。以目录中的 `Memorive.exe` 为启动程序。必须保留同目录的 `_internal/`。

原始依赖锁用于历史对照。当前旧字体兼容代码固定支持 1.28.2，不应与 1.28.0 混用。复现原 r51 须使用其对应的原始源码快照。`--stage-only` 只准备输入和回执，不生成 EXE。

## 干净构建的有限改动

当前公开候选由新的源码清单绑定。原 r51 清单及修复前副本保留用于对照。构建器在新的 staging 目录：

1. 排除私有测试引导与自动化验收模块。
2. 给 `app.py` 的私有 capsule 引导加存在性条件，并拒绝使用缺少 capsule 的私有验收模式。
3. 移除发布身份中的私有 capsule 字段，标为未验收、未授权发布的本地候选。

`build-receipt.json` 保存改动前后哈希、构建工具哈希、依赖版本和 EXE 哈希。此流程实现从已声明源码重新构建，不承诺二进制逐字节复现原 r51，也不声称源码镜像与所有旧嵌入字节码完全等价。

## 依赖与产物清单

使用同一解释器运行 `tools/collect_build_inventory.py --build <构建目录> --wheelhouse <wheel目录> --output <新的清单目录>`。输出：

- 依赖版本、原始许可元数据及许可文件副本；
- 根据 PyInstaller Analysis 标记实际参与打包的运行时文件；
- 全部产物文件与原生库的 SHA256／来源匹配；
- wheel 哈希清单及 `requirements-wheelhouse.lock`。

wheel 锁用于 `pip install --no-index --find-links <wheel目录> --require-hashes -r <wheel锁>`。`proxy-tools 0.1.0` 没有可下载的 wheel，本轮以其公开源码包构建本地 wheel 并记录哈希；不要声称该哈希一定等于第三方自行重新构建的结果。

## 安装器

`installer/r51/` 保留原私有测试实现，仅供对照。`installer/public_candidate/` 是新的干净载荷安装候选：复用已知安装界面及生命周期逻辑，绑定新的产物逐文件清单，排除私有 capsule，附带素材声明与第三方许可。

新构建将 PATH 限定为所选 Python 与 Windows 目录。构建回执记录原生库搜索策略，防止无关开发工具目录中的 DLL 被自动带入。两个从本机 System32 解析到的 MSVCP140 DLL 不再打包，排除记录写入 `excluded-system-runtime.json`；目标电脑使用 Microsoft 官方 Visual C++ v14 x64 运行库。Python／NumPy 原始发行包所带组件保留其来源与许可，见第三方清单。

安装候选构建器接受以下显式输入，不读取旧安装或用户配置：

```text
python installer/public_candidate/build_installer.py
  --app <新构建的 dist/Memorive>
  --artifact-manifest <collect_build_inventory 输出的 artifact-manifest.json>
  --toolchain <固定版本的 WebView2 SDK 文件与 toolchain-source.json>
  --notices <release_materials 目录>
  --output <尚不存在的输出目录>
```

命令中的路径均需由构建者提供。当前 SDK 为官方 NuGet `Microsoft.Web.WebView2` 1.0.3856.49：提取 `lib/net462/` 下 Core／WinForms DLL、`runtimes/win-x64/native/WebView2Loader.dll` 和 LICENSE.txt；来源与哈希记录见 [工具链来源](release_materials/webview2-toolchain-source.json)。`toolchain-source.json` 的文件名、成员来源和 SHA256 必须与所用文件一致。使用不同版本时应建立新的回执并重新验证。

公开安装候选检查 Microsoft 官方前置运行库，缺少时引导使用者到官网准备，再重新检查。安装包不携带 WebView2 Runtime 安装器。构建本身仍需指定版本的 WebView2 SDK；SDK 与 Runtime 的许可分别保留。

验证记录须绑定实际安装器哈希。本机隔离安装、离线健康检查、卸载及数据保留结果，不替代 GUI、缺少运行库的清洁 Windows 或公开分发资格。

## 独立源码导出

运行 `tools/export_source_candidate.py --output <仓库外的新目录>`，按显式源清单和资源映射导出。它排除私有测试代码、Git 历史与缓存，将私有交付身份字段移除，并更新导出内的源／资源哈希。输出 `SOURCE_REVIEW_MANIFEST.json`。导出是待审阅候选，不等于允许直接上传的最终清单。

`tools/collect_release_sources.py` 可按依赖清单读取官方 PyPI 精确版本元数据，下载所选源码包并核验 PyPI SHA256，不执行下载的源码。没有 sdist 的依赖由固定官方仓库提交补充；完整来源另见 `release_materials` 中的记录。

## build139 标识与文档

build139 是新的独立构建编号；软件版本仍为 v1.01，不新增 r 编号。EXE 固定数值版本为 (1,1,0,0)，字符串版本为 1.01。旧 r51 源码与资源清单保留为历史对照。

设置 → 关于与版本的最后一栏提供许可与隐私说明 HTML / Markdown。中、英、日六份文件与 AGPL 全文通过源码清单及资源清单双重校验，并随程序打包。

## 本次分发文件清理

在既有 build139 上直接移除 `chromadb/test/` 数据文件和两份开发检查结果，不重新编译程序或安装引擎。构建脚本已同步这些排除规则。源代码保留与现有 EXE 对应的模块和未启用的合成检查函数，未包含作者的测试运行数据。最终发布附件以清理后的 SHA256 为准。
