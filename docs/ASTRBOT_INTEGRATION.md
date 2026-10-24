# AstrBot 混合对话内核 / Hybrid dialogue kernel

鲸娅可选用 **AstrBot 4.29.1** 的原生对话管线处理普通文字聊天。QQ 收发仍只有一个入口：现有 NapCat / OneBot / QQBridge。不要另开一个 AstrBot QQ 适配器连接同一个机器人，否则可能重复回复。

The optional AstrBot 4.29.1 native pipeline handles ordinary text dialogue. The existing QQ bridge remains the only message ingress and delivery path. This is a bounded integration, not a migration to the full AstrBot WebUI or plugin ecosystem.

## 职责与权限

| 内容 | 处理位置 |
| --- | --- |
| 普通文字、局部上下文、原生上下文压缩 | AstrBot 原生管线，调用已有 DeepSeek API |
| QQ 账号身份、群触发、联系人、管理员 / Plus / 普通用户 | 原有执行层，以 QQ 号为身份依据 |
| 图片、表情、语音、视频、群文件、修图、唱歌、查网页、Codex 工作 | 原有经过权限检查的工具入口 |
| 收尾、已有作品找回、实际失败反馈、已确认任务 | 先经过已有语境和事实处理 |
| 长期记忆检索、学习记录、主动聊天 | 现有组件；未启用第二套主动发送器 |

AstrBot 只有 `end_chat_turn` 和 `route_whale_action` 两个工具。后者只提议转交，不能直接执行。没有给 AstrBot 开启 shell、本地文件读写、Computer Use、原生 QQ 连接或主动发送能力。模型提议不授予权限，原有模型上限、额度、目录限制仍需执行层检查。

Only two native tools are exposed: end the current chat turn, or propose routing to the existing executor. Proposals cannot execute work or grant access. Existing role, quota, model and filesystem checks remain authoritative.

## 本地安装

需要 Python 3.12 或更新版本。**单独创建环境**，在该环境中安装 `requirements-astrbot.txt`，不要覆盖现有语音环境的依赖。当前固定版本与部分语音环境的 psutil 版本要求不同。

在私有 `configs/chat-engine.local.json` 中设置：

```json
{
  "enabled": true,
  "mode": "legacy",
  "python": "runtime/astrbot/Scripts/python.exe",
  "profile": "data/astrbot",
  "port": 18769
}
```

路径可相对于本安装目录。已有后台维护程序会识别并启动这个可选服务；缺少解释器时保留原有聊天。确认端口未被其他程序占用。私有的 `agent.local.json` 必须已配置有效的 DeepSeek API Key。

打开本地 `/qq/engine`，连接本机后，确认内核就绪再切换混合模式。切回原有对话无需重启 QQ。未配置时默认保留原有模式。公开向导现提供可选独立安装按钮，使用发行版 CPython 3.13 和哈希锁。自动安装在本机新目录进行验证；仍不能将其等同于一台全新 Windows 虚拟机上的 QQ 本人登录全链路验收。

Create a separate Python environment and install the pinned optional requirements there. Configure its interpreter, private profile and port locally. The runtime supervisor manages the optional service. Enable hybrid mode in `/qq/engine` only after its health check passes; rollback does not require restarting QQ.

## 可靠性与数据

- 只监听本机回环地址，业务与健康检查均要求本机令牌。
- 每个原生会话使用机器人账号与完整会话范围的摘要作为键；群成员、群和私聊各自隔离。
- 生成回复后先暂存原生历史；只有原有 QQ 发送层收到文字发送回执才提交。失败、过期或重复确认不能写入一次新的成功对话。
- 请求超时、异常或内核不可用时回退原有聊天；短时间连续失败触发暂时回退。后台会按现有重试预算恢复退出的服务。
- 同时最多两个原生请求；模型调用与失败写入现有用量账本。预览也可能产生真实 API 费用。
- 原生配置只保存环境变量形式的 Key 引用。启动时从现有私有配置读取 Key；更换 Key 或模型后需要安全重启原生内核，不支持即时热更新。
- `data/astrbot`、自定义私有 profile、数据库、日志、配置、截图和验收记录都属于本机数据，禁止随源码上传。项目的公开模板不包含账号、聊天、表情包素材或声音权重。

The native profile contains private conversation data. Keep it and all local configuration, credentials, logs and evaluation artifacts out of version control. No extra local inference model is loaded by this kernel; cloud API use still incurs the provider's normal cost.

## 验证边界

已检查原生真实 API 调用、原有收尾规则、群与成员隔离、旧作品取回、故障回退，以及模拟 QQ 回执下的提交 / 失败 / 部分发送行为。预览不能代替真实 QQ 连续使用验证；“更像真人”需要结合实际聊天持续评估，不能仅凭框架名称或几条样例判定。

Native API calls and isolated conversation / receipt contracts have been exercised. This does not establish subjective conversational quality or every live QQ media workflow. Existing task and media executors remain separate acceptance targets.

## 上游与许可证

AstrBot 是外部依赖，其上游声明 **AGPL-3.0-or-later**。发布该集成、分发组合包或对外提供服务时，需要按实际组合和修改情况核对适用的许可义务，不应把上游重新标注为本项目的自有代码。

- [AstrBot 官方项目与许可证](https://github.com/AstrBotDevs/AstrBot)
- [固定依赖的 PyPI 发布页](https://pypi.org/project/astrbot/4.29.1/)
- [官方插件开发：AI 调用](https://docs.astrbot.app/dev/star/guides/ai.html)
- [官方 OneBot 接入说明](https://docs.astrbot.app/platform/aiocqhttp.html)
- [官方上下文压缩说明](https://docs.astrbot.app/use/context-compress.html)

AstrBot is an external dependency licensed upstream under AGPL-3.0-or-later. Preserve its attribution and review the applicable obligations for the actual distributed or hosted combination.

## 安装器与安全重载补充 / Installer and safe reload

独立安装使用 `requirements-astrbot-build.lock` 和 `requirements-astrbot.lock`，156 项已锁定分发文件来自官方 PyPI，主内核固定 4.29.1。两个上游仅源码分发的纯 Python 包在本机构建；构建器也固定版本。与声音环境的依赖保持隔离，失败保留原配置。依赖准备、真实健康检查与启用混合模式分别记录。

模式切换不触发进程重启。Key、模型、解释器、profile 或端口变化才进入安全重载；健康检查把生成和确认数据库提交都计为进行中，并清理过期暂存票据。配置已经变化时，新原生请求先回到既有执行层，等待旧请求与有效回执收尾。Windows venv 的包装器和基础 Python 子进程按完整 root/argv/profile/port 归属一起管理，不能按进程名泛杀。

原生收尾但未发送文字时不提交一次新的原生助手历史；收到真实文字回执才提交，部分发送仍按实际回执判断。此行为有独立测试；模拟回执不能冒充真实 QQ 发信。

原生 profile 的许可与源码要求见 [组合许可说明](LICENSES.md)。应用保留 GPL-3.0，AstrBot 保留 AGPL-3.0-or-later。默认对应源码来自当前 Git 标签；修改部署需提供实际修改源码，并在机器人资料或群说明中明显列出源码入口。

The Windows installer has a separate hashed environment, and safe reload accounts for both generation and database confirmation. Mode switches keep the native process. Dependency readiness does not by itself prove a cloud reply, actual QQ delivery, or subjective conversational quality. Preserve the documented real-device and media-workflow acceptance limits.

应用一键升级更新程序与基本依赖；若以后可选依赖锁发生变化，独立内核环境仍需在向导点击可选安装重新准备，界面按锁摘要显示状态，不把旧环境冒充新环境。现有内核不可用时仍可使用原有模式。

Application updates cover code and base dependencies. A changed optional lock requires preparing the separate optional environment again in the wizard; stale dependencies are not reported as updated.
