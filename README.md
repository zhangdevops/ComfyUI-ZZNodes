# ComfyUI-ZZNodes

ComfyUI 工具节点包。目前提供 **自动关机控制** 一个节点：等待整个任务队列结束后自动关闭 Windows，支持实时取消与可选诊断日志。

**Windows only.** Queue-aware automatic shutdown for ComfyUI, with cancellation, optional redacted diagnostic files, and a Chinese interface. File logging is disabled by default.

- GitHub：[zhangdevops/ComfyUI-ZZNodes](https://github.com/zhangdevops/ComfyUI-ZZNodes)
- 问题反馈：[Issues](https://github.com/zhangdevops/ComfyUI-ZZNodes/issues)
- 许可证：[MIT](LICENSE)
- 发布元数据版本：`1.0.1`（节点内部现有版本标记为 `1.0`）
- Registry / Manager 收录状态：待发布，不表示已经可以搜索安装。

## 功能

| 功能 | 行为 |
| --- | --- |
| 自动关机 | 等待所有正在运行及排队的任务结束，再进行空闲确认和关机倒计时 |
| 实时取消 | 关闭“启用自动关机”，无需重新执行工作流；以后端日志确认为准 |
| 旧任务保护 | 取消后旧凭证失效，已排队的旧任务不能恢复已取消的计划 |
| 新任务处理 | 收到新提交或检测到新任务时重新等待 |
| 界面日志 | 内存中保留最近100条带日期时间的运行日志 |
| 诊断文件 | 默认关闭；手动开启后记录执行、凭证校验、队列和命令错误详情 |
| 凭证隐藏 | 保留内部arm_token保护机制，界面隐藏该控件，诊断日志脱敏 |

本节点会执行真实系统关机，没有测试模式。使用前保存其他应用的工作。

## 环境与兼容范围

- Windows，以及能正常运行的 ComfyUI；Python 元数据要求 `>=3.10`。
- 使用 ComfyUI 提供的 `aiohttp`，不需要额外安装 Python 依赖。
- 浏览器使用本机 `http://127.0.0.1:8188`、`localhost` 或 `::1` 访问；端口以实际配置为准。
- 不支持局域网浏览器、远程代理部署；1.0主要面向本机界面操作。
- 不增加GPU或模型依赖，但出图工作流本身仍需要对应模型和环境。
- 中文来源标记针对经典画布的 `drawBadges` 路径；其他前端渲染器可能有不同表现。
- 已进行模拟和结构检查，未完成所有 ComfyUI/秋叶整合包版本的实机兼容验证，不承诺全部版本兼容。

## 安装

### Git 安装

在 `ComfyUI/custom_nodes` 目录运行：

```powershell
git clone https://github.com/zhangdevops/ComfyUI-ZZNodes.git
```

随后重启 ComfyUI，浏览器按 `Ctrl+F5` 强制刷新。

### ZIP 安装

从 GitHub 的 **Code → Download ZIP** 下载并解压，将包目录放入 `ComfyUI/custom_nodes/`。
可以将解压后的 `ComfyUI-ZZNodes-master` 改名为 `ComfyUI-ZZNodes`。
确认入口文件直接位于：

```text
ComfyUI/custom_nodes/ComfyUI-ZZNodes/__init__.py
```

不要多套一层同名目录。

### Manager 安装

Registry/Manager 收录后，可在 Manager 中搜索 `ZZNodes`。收录前可使用上述 Git 或 ZIP 方法安装。
不同 Manager 版本和节点数据缓存可能影响搜索结果。

### 从早期包升级

若之前安装了 `ComfyUI-QueueShutdown`、`ComfyUI-MyTools`，先完全退出 ComfyUI，
将旧包移到 `custom_nodes` 以外备份，再安装本包。不能同时加载它们，以免节点类型和接口重复。
内部类型仍是 `QueueShutdown`。新增/删除过界面参数的旧工作流，建议删除旧节点后重新添加。
诊断日志不自动跨目录迁移，如需保留请自行备份。

## 使用

1. 在画布搜索 `QueueShutdown` 或“自动关机控制”，添加节点，无需接线。
2. 设置“队列空闲等待”和“关机倒计时”。默认分别是60秒、30秒。
3. 如需排错，先开启“记录诊断日志”；默认不开启文件记录。
4. 打开“启用自动关机”，等待日志确认已启用。
5. 提交包含此节点的工作流。日志出现“工作流已执行，开始监控全部排队及运行任务”后，监控才真正开始。
6. 队列结束后，经过空闲等待和倒计时，由节点调用 `shutdown.exe /s /t 0`。

仅开启开关、但没有执行包含该节点的工作流，不会启动队列监控。
节点没有停止图片/视频生成的功能；“取消关机”只取消关机计划。

### 取消关机

关闭“启用自动关机”，收到日志 **“已由界面取消”** 才表示后台已确认。
通信失败不代表取消成功；可再次操作开关重试。

备用方式：在当前节点包的根目录创建 `STOP_SHUTDOWN.txt`，监控会检测并退出。
重新启用前删除该文件。

关机命令提交给 Windows 后不能保证撤回。本节点不显式使用 `/f` 强制结束应用，
因此其他应用或系统状态可能阻止实际关机；命令返回成功不等于电脑已关闭。

### 多页面与队列边界

同一 ComfyUI 实例的节点和页面共享一个全局关机计划。关闭页面、删除画布上的节点不会取消计划。
页面刷新会同步后台状态；ComfyUI重启则不会恢复之前的启用授权。
监控的是整个ComfyUI队列，其他工作流也会延后关机。
检测到启用后任务历史中的失败或中断时取消计划；清空尚未运行的队列项无法仅凭历史准确判断失败。
不验证输出文件内容，也不监控电脑上其他软件的任务。
最终检查会锁住队列，但已经通过入口检查、仍在验证的新请求存在极小竞争窗口。

## 参数

| 页面名称 | 内部参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 启用自动关机 | `enabled` | 关闭 | 开启准备计划；关闭即时请求取消 |
| 队列空闲等待（秒） | `idle_seconds` | 60 | 范围10～86400秒 |
| 关机倒计时（秒） | `countdown_seconds` | 30 | 范围10～600秒 |
| 记录诊断日志 | `record_logs` | 关闭 | 即时切换文件记录，不影响关机计划 |
| 隐藏凭证 | `arm_token` | 空 | 内部使用，不要手动修改 |

“清空日志”按钮仅清空界面内存日志，不删除诊断文件、不取消关机。

## 日志

### 界面日志

保留最近100条，格式为 `[YYYY-MM-DD HH:mm:ss]`，使用电脑本地时间。
完成、取消和失败后仍可查看。刷新页面重新读取；ComfyUI重启后重置。

### 诊断文件

默认不创建日志目录或文件。开启“记录诊断日志”后才写入：

```text
<实际安装目录>/logs/queue_shutdown_diagnostic.log
```

例如手动安装时：`ComfyUI/custom_nodes/ComfyUI-ZZNodes/logs/queue_shutdown_diagnostic.log`。
Registry安装的目录名可能由包标识规范化得到，以节点内显示的实际路径为准。
诊断文件按包根目录定位，不依赖固定的安装文件夹名。

每份约2MiB，保留4份轮转备份（`.1`至`.4`）。关闭文件记录后停止追加，但保留旧文件。
如果包目录不可写，尝试系统临时目录；实际路径显示在界面日志。
两个路径都不可写时开启失败，保持关闭。

记录节点execute进入、凭证是否提供/有效、队列变化及等待心跳、固定关机命令返回码、
标准输出、错误输出和异常堆栈。屏蔽已知arm_token和会话令牌；不转储完整工作流及堆栈局部变量。
文件可能包含本机路径、环境版本和错误详情，提交Issues前请检查这些内容。

此开关只控制本节点独立诊断文件，不修改秋叶启动器或ComfyUI自身的全局日志设置。

在节点安装目录打开 PowerShell 实时查看：

```powershell
Get-Content -LiteralPath '.\logs\queue_shutdown_diagnostic.log' -Tail 80 -Wait -Encoding UTF8
```

### 未执行关机如何排查

- 没有“节点execute进入”：确认节点被执行，检查禁用状态、工作流验证和前端加载。
- 凭证无效：关闭后重新开启，再提交新工作流；不要复用已取消的旧任务。
- 队列一直非零：检查运行/排队任务及无限生成设置。
- 命令返回非零：查看同条记录中的stderr和后续异常堆栈。
- 节点包IMPORT FAILED：查看ComfyUI控制台；模块未加载时可能还没有诊断文件。

## 项目结构

```text
ComfyUI-ZZNodes/
├── __init__.py             # 统一注册，WEB_DIRECTORY=./web
├── nodes/
│   ├── __init__.py
│   └── shutdown/
│       ├── __init__.py     # 功能模块注册
│       ├── node.py
│       ├── controller.py
│       └── diagnostics.py
├── web/
│   └── shutdown/
│       └── shutdown.js
├── README.md
├── pyproject.toml
├── LICENSE
└── .gitignore
```

新增功能可放入 `nodes/<功能>/` 和 `web/<功能>/`，然后在根入口明确导入。
不要随意修改已有节点内部名称，以免破坏工作流兼容。

## 开发者：发布到 Registry / Manager

发布准备文件已包含，但本仓库还需要开发者完成以下步骤；这些文件不代表已获收录。

1. 在 [Comfy Registry](https://registry.comfy.org/) 创建Publisher，取得实际Publisher ID。
2. 本仓库已确认Publisher ID为 `zhang`，`pyproject.toml` 已配置该ID。
   发布密钥必须属于该Publisher；它与GitHub用户名不是同一个配置项。
3. 确认Registry包标识 `zznodes` 尚未被占用；必要时在第一次发布前修改。
   Registry包标识和节点类型 `QueueShutdown` 是两回事。
4. 阅读并确认MIT许可证；当前署名为 `ZZNodes contributors`，如需个人/组织署名请在发布前修改。
5. 在目标Windows/ComfyUI版本验证运行、取消、默认不写文件，以及日志开关行为。
6. 将源码提交到Git仓库，确保没有日志、凭证、环境配置和STOP文件。
7. 在单独的开发环境安装 `comfy-cli`，在仓库根目录运行：

```powershell
python -m pip install comfy-cli
comfy node publish
```

按提示输入Registry发布密钥。密钥不要写进源码、README或提交到Git。
发布版本使用三段式 `1.0.0`，已发布版本不能覆盖；后续修复递增为 `1.0.1` 等。
Registry提供Manager的数据来源；搜索结果可能需要刷新缓存。

### GitHub Actions 自动发布

仓库已配置 `.github/workflows/publish-registry.yml`，工作流名为 **Publish to Comfy Registry**。

1. 在Comfy Registry为Publisher `zhang` 创建发布API Key。
2. 打开仓库 **Settings → Secrets and variables → Actions → New repository secret**。
   Name填写 `REGISTRY_ACCESS_TOKEN`，Secret填写Registry发布API Key，然后保存。
   请使用Repository secret，不要放在Variables中，也不要使用GitHub访问令牌代替。
3. 首次发布：进入 **Actions → Publish to Comfy Registry → Run workflow**，选择 `master` 并运行。
   当前版本是 `1.0.1`；若该版本已经发布，请先递增版本。
4. 后续发布：修改 `pyproject.toml` 中的 `version`，随源码一起推送到 `master`，工作流会自动运行。
   触发条件是该文件发生变化，因此仅修改其他元数据也会触发；发布时必须使用尚未发布的版本号。
5. 在Actions查看发布结果，再到Registry核对版本状态，并在Manager刷新列表搜索 `zznodes`。

工作流也支持手动重试；缺少密钥时会明确报错。成功发布过的版本不可重复发布。
仅修改README或节点源码不会自动发布；GitHub Release也不是这个工作流的触发条件。
发布在GitHub托管的Ubuntu runner上打包上传，不运行节点的Windows关机功能。

旧版Manager的传统收录还可按官方贡献说明，在其节点列表中添加仓库条目并提交PR。
应按提交时的仓库实际结构和要求操作，不要只修改本地Manager文件后认为已公开收录。

参考：[Registry发布指南](https://docs.comfy.org/registry/publishing)、
[元数据规范](https://docs.comfy.org/registry/specifications)、
[Manager项目](https://github.com/Comfy-Org/ComfyUI-Manager)。

## 问题反馈

请提供ComfyUI及前端版本、Windows版本、安装方式、复现步骤和脱敏后的相关日志。
不要提交arm_token、发布密钥、完整私密工作流或机器上的敏感文件。

## License

[MIT License](LICENSE). Copyright (c) 2026 ZZNodes contributors.
