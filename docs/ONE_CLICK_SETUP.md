# 一键安装与升级 / One-click setup and updates

The installer targets **Windows 10/11 x64**. It is a source-only application bundle: it downloads the pinned official Python and NapCat runtime on the user's computer. No author account, key, QQ session, chat database, image or voice model is included.

安装器目前面向 **Windows 10/11 x64**。第一次下载约 240 MB 的运行组件与 Python 依赖，磁盘还需要为解压后的环境预留空间。默认不下载大模型，不安装 CUDA，不占用常驻显存。

## 初次使用

1. 从本项目 [GitHub Releases](https://github.com/LONG10222/whale-companion/releases) 下载 `WhaleCompanion-v版本-windows-x64.zip`，完整解压到自己的普通文件夹，例如 `D:\Apps\WhaleCompanion`。不要直接在压缩包内运行，不要放进系统目录或目录链接内。
2. 双击 `Install.cmd`。它会校验下载、准备自己的 Python 环境、启动独立 QQ，并打开本机设置页。无需预装 Python、Node、Git、Codex 或 Clash。
3. 在本地页面填写自己的 **DeepSeek API Key**，点击验证并保存；用专用机器人 QQ 扫码，在手机确认。
4. 用你的日常 QQ 添加机器人为好友，在页面从好友列表确认管理账号，再点击接通。这个确认用于防止陌生人的第一条消息抢占管理员，不需要手填配置文件。

之后双击桌面的 **Whale Companion** 或解压目录中的 `Start.cmd`。已运行的服务会复用。断线时保留 QQ 数据；扫码后的手机确认或安全验证仍必须本人完成。

如果正式发行包尚未出现，请等待发布完成；GitHub 的 `Code → Download ZIP` 是源码入口，不代表已完成这一版本的正式验收。

## First run

1. Download the Windows x64 archive from [Releases](https://github.com/LONG10222/whale-companion/releases), and extract the entire folder into a normal writable directory.
2. Double-click `Install.cmd`. The bootstrapper obtains verified upstream runtimes, creates a private environment and opens the local wizard.
3. Enter your own DeepSeek key, then scan the QR using a dedicated bot QQ account and confirm on your phone.
4. Add the bot from your personal account, select that account in the wizard's friend list and confirm it as administrator.

Later, use the desktop shortcut or `Start.cmd`. The basic chat setup does not require an OpenAI account, a GPU model, or developer tools. QQ may still require personal confirmation or verification.

## 一键升级与提醒

- 运行期间约每 6 小时检查一次本项目的 GitHub 正式发行版；发现新版后在本地控制页提醒。网络不通不会影响聊天，也不会自动覆盖程序。
- 打开“版本与升级”，点击 **备份并一键升级**。升级只接受本仓库的正式版本、固定命名的发行包、GitHub 给出的 SHA-256，以及包内逐文件校验清单。
- 程序先在独立目录下载和校验，必要时创建新的依赖环境；再暂缓接收消息，等待当前任务收尾。5 分钟后任务仍在运行就推迟本次升级并恢复接收。
- 切换前备份本机配置，并用 SQLite 的备份接口保存数据库一致快照；不会上传这些备份。只停止本安装的应用和控制页进程，QQ 进程不按名称批量结束。
- 新版通过启动和账号绑定检查后恢复聊天；未通过就自动恢复旧程序、配置与数据库。无法自动恢复时保留完整备份并显示需要处理的状态，不能把这种状态报告成升级成功。
- 一键升级更新应用源码与 Python 依赖。**当前版本若检测到 Python 主运行包或 QQ 运行包也发生变化，会保留原版并要求使用完整安装包升级组件**，避免在会话中直接替换 QQ 二进制。

Upgrades are discovered automatically and installed only after a local owner click. The updater verifies the release origin, archive hash and every application file; waits for active jobs; keeps a private configuration/database backup; and restores the prior application if startup checks fail. Private files, model weights, stickers and media are never release payloads. Runtime-engine changes currently require the full installer rather than replacing a running QQ binary.

## 全部功能与硬件推荐

向导列出文字聊天、人格与记忆、联系人/Plus、表情、识图、视频、群文件大照片、语音输入、自然语音、清唱、Codex 绘图、修图工作流、公开查询、受控电脑工作、主动聊天、本地学习、专用模拟器阅读、余额与日志、升级和 B站等 20 类功能，并分别显示依赖状态。详细入口见 [功能配置索引](CONFIGURATION.md)。旧有功能不会因为安装流程简化就从源码中删掉。

设置页只读取 CPU 名称与核心数、内存总量/可用量、NVIDIA 显卡名称和显存/占用；不扫描用户文档、不读取手机数据。根据这些信息给出“日常使用优先 / 均衡 / 质量优先”，也可手动选择需要配置的模块、线程数、显存软比例、加载门槛、空闲退出时间和磁盘缓存上限。

例如 32 GiB 内存、16 GiB NVIDIA 显存的机器，均衡预设使用日常朗读约 0.4 的进程显存软比例、较高的空闲显存门槛、最多 4 个音频 CPU 线程、30 秒空闲退出、512 MiB 磁盘声音缓存。清唱合成与换声分别沿用 0.65 / 0.55 的软比例，清唱前要求至少 10 GiB 空闲显存，完成即退出对应工作进程；它们与日常朗读分开显示和配置。可选本地学习最多 2 线程，并保留至少 6 GiB 显存和内存。正在玩游戏、显存不足或 CPU 繁忙时，后台辅助会暂缓；这些预算不保证第三方程序的显存用量。当前内置 CUDA 工作器使用第 0 块 NVIDIA 卡，多卡环境需按自己的运行环境配置。

“准备配置”与“已经可用”分开显示。没有模型、自己的 Codex 登录或许可允许的素材时，界面明确提示待配置，不会仅靠勾选把功能报成成功。现有声音进程不被性能预设强行中断，资源参数在下次启动该服务时读取。管理员、Plus 与普通用户的权限和普通速度保持原限制，不随硬件预设放宽。

The feature catalog preserves every implemented capability family and reports missing models/accounts separately. Hardware presets adjust only bounded local resource settings; they do not grant tools or raise cloud model permissions. Selection records a configuration plan, not a claim that omitted assets were installed. Model paths and credentials remain private local configuration.

## 设置和文件在哪里

| 路径 | 内容 | 是否公开上传 |
| --- | --- | --- |
| `.runtime/`、`.venv/` | 下载的 Python、QQ 与依赖环境 | 否 |
| `*.local.json`、`configs/*.local.*` | 本机 Key、访问凭据、账号与配置 | 否 |
| `data/qq/` | 联系人、记忆、任务与用量数据库 | 否 |
| `data/qq-client/` | 独立 QQ 账号数据 | 否 |
| `data/update-backups/` | 升级前的程序与私人数据备份 | 否 |
| `logs/setup/` | 本机启动与依赖安装诊断 | 否 |

本机设置页通过启动器建立本地会话，无需复制访问令牌。Key 与账号配置使用当前 Windows 用户和 SYSTEM 的文件访问权限；DeepSeek Key 仍是本机配置内容，不能称为硬件加密或完全离线。QQ 密码只有在用户另行选择保存时才走现有 DPAPI 凭据功能。

在安装器创建的实例中，主控制页和声音页也要求同一份本机启动会话；只提交公开的界面请求头不能认领管理权。已有浏览器窗口的本机会话可跨正常升级保留，声音页与主控制页的 Cookie 名称分别处理，避免相互覆盖。

## 常见情况

**下载中断：** 重跑 `Install.cmd`。已有正确文件复用；支持的下载器会续传，校验失败的文件不会执行。只使用上游官方地址，不自动替换成未知代理站。

**提示端口被其他程序占用：** 保持原程序不动。安装器不会为了抢端口结束其他服务；确认是否已打开另一份鲸娅，再使用原目录的快捷方式。两个独立安装目前不共享一套端口。

**二维码未出现：** 看本页真实状态，不要反复提交密码；已扫码或正在初始化时等待手机确认。QQ 的验证和远端撤销会话无法由应用绕过。

**Key 验证失败：** 在 DeepSeek 官方平台核对完整 Key、账号和网络；不要把 Key 贴进 GitHub Issue 或聊天截图。

**画图或声音不可用：** 它们是增强模块，需要你自己配置 Codex 账号、项目或许可允许的模型。安装器不会附带作者登录态，也不会用无关图片或哼唱假装任务成功。

**升级失败：** 先看状态是“已恢复旧版”还是“需要手动恢复”。不要删除 `data/update-backups/`，不要整包覆盖私人配置。报告故障时只提供脱敏后的状态与错误编号。

## 开发与构建

Python 3.13 / Windows x64 的安装依赖锁在 `requirements-bootstrap.lock` 中；每个二进制 wheel 都有 SHA-256。其他开发环境使用 `requirements.txt`。重新生成依赖锁时，应重新进行干净环境安装和完整回归。

`scripts/build-release.py --output <安装目录外的发行目录>` 仅生成可校验的源码包，不创建 GitHub 仓库、不上传、不自动推送。正式发布还必须完成版本冻结、隐私扫描、素材许可核查、功能验收与 CI。
