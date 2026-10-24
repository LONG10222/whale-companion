# Licensing the optional kernel / 可选内核与组合许可

鲸娅自身的应用源码继续适用根目录 [GPL-3.0](../LICENSE)。AstrBot 4.29.1 是独立下载的上游依赖，其声明为 **AGPL-3.0-or-later**；确切版本的许可文本保存在 [licenses/AstrBot-AGPL-3.0.txt](../licenses/AstrBot-AGPL-3.0.txt)。不能将它重新标成鲸娅原创、GPL-only、MIT 或无许可限制。

GPL-3.0 第 13 条允许与 AGPL-3.0 作品组合，并明确保留组合中的网络源码提供要求。独立进程和独立环境用于依赖与运行隔离，不作为免除上游许可义务的理由。应用自己的 GPL 条款、上游 AGPL 条款与第三方组件各自的许可通知应同时保留。

部署者应向远程使用者明显提供实际运行版本的对应源码及修改。默认来源为本项目当前版本的 Git 标签；如果你修改了程序，应通过私有 `configs/chat-engine.local.json` 中的 `source_url` 指向实际提供的对应源码，而不是把未包含修改的上游页面当成自己的完整源码。`source_url` 只接受无账号和查询参数的 HTTPS 地址。不要把仓库访问令牌放进链接。

可用的源码入口包括：对话设置页、公开静态元数据 `/qq/engine/source`、内核 `/source`，以及 QQ 中的“你的源码在哪”“关于鲸娅”“开源信息”。部署机器人时，还应在机器人资料或群说明中明显列出这一入口，方便远程使用者找到。静态源码接口只返回许可和公开链接，不返回 Key、账号、运行路径或聊天。

源码发行包不含 AstrBot wheel、Python 环境、模型、聊天库、登录态、表情或声音素材。使用者在自己电脑上从官方 PyPI 安装锁定依赖；安装后保留各分发包的许可与元数据。第三方素材与模型使用范围仍需分别核对，见 [ATTRIBUTIONS.md](../ATTRIBUTIONS.md)。私人凭据和聊天数据不应被放进公开源码包。

Whale Companion's own application remains GPL-3.0. The optional AstrBot dependency is AGPL-3.0-or-later. GPLv3 section 13 permits the combination while preserving the applicable AGPL network-source requirements. Process isolation does not remove those obligations. Keep both notices and the actual corresponding source available to remote users; modified deployments must provide their own changes, not only an unchanged upstream link. Public source-information endpoints expose no credentials or private conversation state. Models, artwork and other dependencies keep their separate terms.

Primary texts: [GPLv3 section 13](https://www.gnu.org/licenses/gpl-3.0.html#section13), [AGPLv3 section 13](https://www.gnu.org/licenses/agpl-3.0.html#section13), [AstrBot v4.29.1 license](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.1/LICENSE), [official package metadata](https://pypi.org/project/astrbot/4.29.1/).
