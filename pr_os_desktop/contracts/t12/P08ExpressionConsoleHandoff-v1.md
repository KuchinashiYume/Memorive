# build109 控制台表情对接

本文件为本轮候选接口说明，不代替 P08 正式验收。入口沿用 ENV_V1、仅本机回环、Bearer 与 X-PR-OS-Session 双绑定、一次性隔离会话。原有业务 operation 名称和参数不变。

## 许可与简洁文案

设置 → 隐私、日志与存储只展示一行：**允许接入控制台**、状态、开关。英文 Allow Console Access；日文 コンソール接続を許可。默认关闭，损坏或缺少许可文件也按关闭处理。不得展示“业务接口 · 表情接口 · 33”等计数、内部术语或多余说明。

许可保存于当前 profile 的 `ui/console_access_v1.json`，不沿用遥测开关，不启动远程监听。控制台启动的是新隔离 profile，必须由用户在这次启动的 PR-OS 设置中允许接入；不要从日常 profile 复制许可，也不要由控制台写入许可文件或新增远程开启指令。普通模式下保存许可不会启动桥接服务器。

握手返回 `console_access_enabled` 与 `console_access_required`。关闭时可握手、读取固定能力/连接元信息、预览的缓存状态，以及取消/结束会话；业务快照和业务执行、表情触发返回 403 / `CONSOLE_ACCESS_DISABLED`。控制台应显示“等待允许接入”，而不是故障或断线。关闭许可会取消并恢复当前预览；已运行的业务任务不强制中断，新任务和排队任务不再进入执行。停止、结束、静止确认仍可使用。

旧版控制台 0.2.4 的 capability allowlist 只有旧操作，且启动时会读取业务 snapshot；需要同步增加新 operation 并处理等待许可状态。不要因预期的 403 重启产品或反复请求重型 snapshot。

## 请求与结果

在既有 `/pr-os/test-bridge/v1` 下：

- `GET /expressions/catalog`：33 个固定事件，三语 label、group、target、animated、motion_ms。
- `POST /commands`：沿用原 command 包装，operation=`expression.trigger`，params=`{"event_id":"motion.working","duration_ms":1800}`，allow_model_calls=false。只能使用目录中的 ID；禁止脚本、URL、路径和自定义动作。
- `GET /expressions/state`：只读内存缓存，不经过重型 snapshot 锁。建议 250ms 轮询，一次请求在途，失败退避；不要同时为了表情刷新业务 snapshot。
- `POST /commands`：operation=`expression.cancel`，params={}，allow_model_calls=false。立即接受取消，但要继续观察恢复后的终态。

command 的 SUCCEEDED 只表示接受预览请求。取 result.probe.probe_id，随后从 active/history 读取 ACCEPTED → DISPLAYED → RESTORING → COMPLETED/CANCELLED/FAILED；必须同时检查 displayed、restored、error_code。重复 request_id 返回旧回执，不重复播放；同时只能有一个预览，忙时 EXPRESSION_BUSY。历史最多64条、状态事件128条、单项采样64条；长时间会话应按 sequence 增量处理，不能无限追加 DOM。

## 静态表情与真实动画必须分开

目录分为四组：静态插画10、桌宠姿态11、桌宠动画9、页面动画3。idle/drag/sleep 是原有静态姿态，不应标成正在播放的动画；拖动姿态不移动真实窗口。页面动画为主页歪头、Logo 按压与 Logo 晃动，复用现行 CSS 动作；不是给 PNG 换了名字。

桌宠通过现有 WinForms 原生渲染器播放，保留原动作时序/插值/帧切换。监控 samples 包含实际 frame、animation_locked 及 transform `[frame,x,y,rotation,scale,opacity]`；frame_sha256 只是源帧hash，不能以它不变判断没有晃动。坐标以90px参考盒为单位，控制台若镜像预览应按显示尺寸同比例换算平移，旋转/缩放/透明度直接应用。静态源来自 EXE 同级 `_internal/assets/illustrations`；frame→asset 映射见 JSON 契约，不要加载输入目录或网络素材。

控制台应让用户清楚选择“静态表情”或“动画”，提供播放/停止。**动画区域不能只渲染一个 `<img>` 然后显示“完成”**：可镜像实际缓存采样；页面动画可复用打包的 CSS 关键帧和转换参数，但要标明该镜像，不冒充原生截图。若仅提供真实桌宠播放，应明确“在桌宠播放”，同步显示 actual renderer 状态，不伪装成控制台内已播放。

预览不创建业务消息、作业、报告、错误记录或持久投递回执，不调用模型。显式动画预览暂时采用完整动作，结束恢复计时器/视觉状态，不更改偏好；“隐藏所有表情”仍阻止显示。若真实产品状态抢占，报告 EXPRESSION_INTERRUPTED_BY_PRODUCT 并保留真实状态，不覆盖为旧帧。原生 UI 工作经限时 BeginInvoke 调度，不能在产品锁内阻塞调用。

## 验证与交付边界

接口自检包括所有33项实际原生/网页显示与恢复、12项实际动画变换变化、早取消、并发忙拒绝、幂等、权限关闭恢复、三语单行布局、隐藏偏好、轻量轮询及结束会话。实际 EXE 复验结果和文件hash以新交付根的 final_receipt.json / validation_summary.json 为准。

产品包先完成，随后才启动独立控制台更新。控制台的 suite 业务类型、外部 API/CLI/付费模型调用不因本接口自动获得新增执行授权。继续保留 0.2.4 的 active-command polling isolation、超时退避和原生 QUIESCED 确认；旧 MESSAGE-004 文件并发问题不借此扩大修复范围。
