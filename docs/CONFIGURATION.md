# 功能配置索引 / Feature configuration index

安装向导列出以下 20 类功能。勾选记录配置计划；依赖状态、账号授权和真正启用是分别检查的。基础安装只配置自己的 DeepSeek Key、机器人 QQ 和管理账号；增强功能不附带作者登录态、模型或私人素材。

The wizard lists these 20 capability families. Selection, dependency readiness and actual activation are separate states. The basic installer configures your own key, bot QQ and administrator; optional capabilities do not include the author's accounts, models or private assets.

| 功能 / Feature | 本机设置入口 / Local page | 主要配置或依赖 / Configuration or dependency |
| --- | --- | --- |
| DeepSeek 聊天 | `/qq` | `agent.local.json`；自己的 Key、实际可用模型 |
| 人格、上下文与记忆 | `/qq/persona` | 本机角色卡、按身份隔离的 SQLite；记忆候选与反馈 |
| 联系人与角色 | `/qq/contacts` | 联系人数据库；QQ 号为键，管理员设置 Plus |
| 表情与表情包 | `/stickers` | `assets/stickers/catalog.json`；自己导入的许可素材 |
| 图片与表情理解 | `/qq` | 具备对应能力的模型、私有图片工作副本 |
| 视频理解 | `/qq` | 受限抽帧、识图模型；可选本地 ASR |
| 群文件与大照片 | `/qq/groups` | 授权群、群文件接口、原图和工作副本分离 |
| 语音输入 | `/qq` | `configs/qq-voice.local.json`、SenseVoice int8 和识别环境 |
| 自然语音、朗读与换声 | 声音控制页 `:18765` | `settings.local.json`、`dania.local.json`、许可允许的模型与参考 |
| 清唱与稳音 | `/qq/creative` | `score.local.json`、`singing.local.json`、自己的模型与谱面 |
| Codex 绘图 | `/qq/creative` | `configs/qq-workbench.local.json`、自己的 Codex 登录与项目 |
| 修图工作流 | `/qq/creative/workflow` | 原图、角色模型上限、复核与返修状态 |
| 公开网页查询 | `/qq/creative` | 公共 HTTPS 读取；可选 Codex Luna 整理、持久化查询额度 |
| 受控工作台 | `/qq/creative` | 项目与工作目录绑定；仅管理员可用相应电脑工具 |
| 主动聊天与热点 | `/qq/proactive` | 专门的启用开关、对象、时段、频次和未回复保护 |
| 本地辅助学习 | `/learning` | `configs/local-learning.local.json`；可选模型和运行器、资源门槛 |
| 模拟器与小黑盒阅读 | `/mobile` | 专用模拟器、ADB 和本人登录；不接入日常手机信息 |
| 余额与用量日志 | `/qq/creative` | 自己的官方 API、用量数据库、提醒阈值 |
| 新版提醒与升级 | `/updates` | 发行元数据、校验清单、私人备份；点击后才升级 |
| B站阅读与互动 | `/qq/bilibili` | `configs/bilibili.local.json`；本人登录，阅读、主动分享与公开互动分别授权 |

路径指安装目录内的文件，所有 `*.local.*`、数据库和私有素材均不发布。设置页尚未接通基础实例时，功能链接保持不可跳转，避免误开另一份正在运行的服务。

## 硬件与手动调整

向导只检查 CPU、RAM 和 NVIDIA 显卡资源。推荐预设可更换，九项声音参数可单独调整：CPU 线程、朗读显存软比例、朗读加载门槛、空闲退出时间、磁盘缓存容量、缓存保留时长、清唱显存软比例、换声显存软比例和清唱加载门槛。换声比例不超过清唱比例。模型路径、Key、角色权限和普通速度不由这些参数改写。

例如 8 核 CPU、约 32GB RAM、16GB NVIDIA 显存使用均衡预设：4 个音频 CPU 线程，朗读软比例 40%，清唱/换声 65%/55%，清唱前至少 10GiB 空闲，声音工作器空闲 30 秒退出，声音磁盘缓存 512MiB / 24 小时。可选学习助手最多 2 线程，保留至少 6GiB 显存和内存。它是保守资源建议，无法保证所有第三方模型的速度、质量或分配上限。

Hardware profiles only change bounded local resource settings. They cannot expand roles, remove query limits, or grant cloud accounts. Missing optional models remain pending. Loading and release happen on demand; changing a preset does not interrupt an active audio task. The current CUDA workers use GPU index 0; multi-GPU operators should configure their actual runtime accordingly.

微信、Qzone、自动加好友不作为已验收功能列入；已取消的桌宠和日常手机信息采集不提供启用项。
