<div align="center">

# Whale Companion · 鲸娅

**让 QQ 里的 AI 能记事、接梗和使用工具，同时把权限、用量和本机资源管住。**

[English](README.en.md) · [快速开始](#快速开始) · [实现原理](docs/ARCHITECTURE.md) · [构建过程](docs/BUILD_LOG.md) · [素材与依赖来源](ATTRIBUTIONS.md)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4)
![License](https://img.shields.io/badge/License-GPL--3.0-blue)

</div>

Whale Companion 是一个 Windows 优先的本地 QQ AI 伙伴原型。DeepSeek 负责对话和工具选择；SQLite 保存按联系人隔离的记忆、反馈与任务状态；可选的 Codex Luna、语音和歌声模型负责公开资料整理、绘图及声音生成。

这个仓库公开应用源码、设计决策和配置模板，**不包含个人实例、账号登录状态、聊天记录、API Key、角色权重或下载的表情图片**。它不训练或修改云端 DeepSeek 的权重，也不保证接入 QQ 的所有客户端功能。

## 能做什么

| 能力 | 实现与状态 |
|---|---|
| QQ 私聊 | NapCat / OneBot v11，独立机器人账号、好友白名单、消息去重与连续来信合并 |
| 记忆与人格 | 最近对话、滚动摘要、带原文证据的记忆候选、人工反馈；不同联系人隔离 |
| 图片与表情 | 图片/GIF 抽帧理解、按聊天语境选本地反应图；文字与表情多数分开发送 |
| 视频 | 有界抽帧 + 云端视觉，可选 CPU 对白识别；不声称完整逐帧看过视频 |
| 语音与清唱 | 可选本地语音、乐谱驱动演唱与换声；检查唱词和音频完整性后返回 |
| 公开资料查询 | 受控网页读取 + Codex Luna 整理、真实来源链接、超时保留已有结果 |
| 权限与用量 | QQ 号作身份主键，管理员/普通/手动 Plus；普通查询滚动 15 分钟 2 次、单次 5 分钟 |
| 用量与收支日志 | 任务状态、模型 Token、余额快照及净变化；未知费用不当成免费 |
| 一键启动 | Clash → Google HTTPS 检查 → ChatGPT → 本地服务；运行中的程序跳过 |

**尚未接入：** 微信、群聊的实际发送、主动加好友、空间浏览与发说说。连接器可能提供其中部分接口，接口存在不等于本项目已经开放或验证。主动聊天默认只准备草稿，需主人发送。

## 快速开始

### 1. 准备环境

- Windows 10/11、Python 3.12；QQ 使用单独的测试账号。
- 按 [NapCat 官方文档](https://napneko.github.io/) 安装与配置独立客户端。
- DeepSeek API Key 由自己在官方平台申请，费用由自己的账号承担。
- 本地大模型、CUDA、Codex 与 Clash 都是按功能需要配置的外部依赖，不随源码分发。

```powershell
git clone https://github.com/LONG10222/whale-companion.git
cd whale-companion
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/configure.py
```

配置程序交互填写管理员和机器人 QQ 号，随机生成本机访问凭据，密钥输入不会回显。生成的 `*.local.json` 被 Git 忽略；重复运行不会覆盖已有个人配置。**默认暂停回复、关闭群聊和语音。**

### 2. 接入自己的 QQ

1. 将 `configs/qq-client.example.json` 复制为 `configs/qq-client.local.json`，填自己的独立 NapCat 安装目录和启动程序。
2. 在 NapCat 配置本机 WebSocket 服务 `127.0.0.1:3001`，访问凭据与 `qq.local.json` 的 `onebot_token` 一致。WebUI 使用本机 `127.0.0.1:6099`。
3. 启动本地控制页，按页面提示扫码并在手机确认；先只允许管理员测试账号。

```powershell
.\.venv\Scripts\python.exe -m uvicorn phone_server:app --host 127.0.0.1 --port 18766 --no-access-log
```

打开 <http://127.0.0.1:18766/qq>。浏览器通过受限的本机同源会话自动连接，**不用手填本机令牌**。二维码按状态更新；已扫码、初始化或重连期间保持当前流程。手机确认与安全验证由本人完成。

### 3. 桌面一键启动

确认上面的单独启动已可用，再编辑生成的 `configs/launcher.local.json`，核对 Clash 和 ChatGPT 应用位置。默认检查 Google `generate_204` 的 HTTPS 204 响应；不能联网就停止后续启动，不修改你的代理节点、订阅或系统设置。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-shortcut.ps1
```

桌面快捷方式执行 `launch-whale.ps1`。它复用已有进程，检查服务是否属于本项目；发现端口被其他程序占用会停止。`-ExecutionPolicy Bypass` 只用于这一进程运行本地脚本，不更改系统执行策略。

```powershell
# 仅检查，不启动任何缺失的应用
powershell -NoProfile -ExecutionPolicy Bypass -File launch-whale.ps1 -CheckOnly -NoGui
```

## 可选模块

- **Codex：** 使用本机已登录的官方 Codex CLI；将 `configs/qq-workbench.example.json` 复制为本机配置，填已有项目的真实 ID 和目录。每个任务只可写自己的工作目录，其他资料需要当前明确只读授权。普通用户/Plus 仅 Luna，管理员上限 6.1 Sol/high；全部普通速度。
- **语音、清唱与换声：** 需要自行获取上游依赖、权重、合法可用的参考音频及配置。见 [模型与素材](ATTRIBUTIONS.md) 和 [安装说明](docs/SETUP.md)。没有这些文件时，声音功能会不可用，代码不会伪造生成成功。
- **本地表情：** 仓库只带空索引。来源登记见 [asset-sources.csv](docs/asset-sources.csv)，按来源条款自行获取并使用 `scripts/import_stickers.py` 导入，在本机表情页查看。不要把私聊图片导入公开素材目录。
- **手机阅读：** 独立安卓模拟器 + ADB/UIAutomator，只读适配需按自身环境配置。见 [实现原理](docs/ARCHITECTURE.md)。

## 权限、超时与隐私

普通用户的一个问题计一次查询，内部读取多个网页不重复扣次数；额度按 QQ 号跨会话累计，重启不清零。5 分钟包括排队时间，超时结束所属查询进程树。有已读来源时返回摘要/来源摘录并保留未核实范围；没有可靠资料就自然说明，不编造、不假装继续后台工作。Plus 只能由本地管理页手动设置，免除这两项查询限制，**不会获得管理员或电脑访问权限**。

耗时任务全局串行，最多再等待三项；普通/Plus 每人同一时间一项。群聊尚未启用，不能把这些队列规则当作已经完成群聊接入的证明。

原始聊天、记忆、媒体缓存和收支日志保存在本机。调用云端模型时，本轮所需的文本、图片或工具结果会发送到相应服务商；“本地保存”不等于“全部离线”。详见 [PRIVACY.md](PRIVACY.md)。

## 测试与贡献

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe scripts/scan_public.py
```

测试覆盖消息来源与去重、权限、记忆隔离、额度、进程终止、日志、同源自动连接和二维码状态。自动检查能发现错误，不能证明“像真人”、音色好听或所有 QQ 版本兼容。发布前请验证可选依赖和本机服务。

欢迎提交可复现问题、匿名测试用例和小范围 PR。报告问题前删除 QQ 号、Key、Cookie、代理订阅、聊天文本和本机绝对路径。参见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [SECURITY.md](SECURITY.md)。

## 致谢与许可

应用代码采用 [GPL-3.0](LICENSE)。第三方程序、模型、角色、音频、表情和品牌属于各自权利人，并不自动适用本项目许可证。完整来源、所用角色参考和未分发资产说明见 [ATTRIBUTIONS.md](ATTRIBUTIONS.md)。项目与 DeepSeek、OpenAI、腾讯或《鸣潮》官方无隶属或背书关系。

表情导入示例（目录应只包含你有权使用的公开图片）：

```powershell
.\.venv\Scripts\python.exe scripts/import_stickers.py C:/PublicStickers --source wuwa_user_download --pack "My permitted collection" --theme 鸣潮
```

默认不自动发送未标注图片；确认情绪标签后可使用 `--emotion` 显式标注。
