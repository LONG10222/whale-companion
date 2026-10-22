# Build journal / 构建过程

This is a sanitized engineering account, not a transcript or a dump of a private instance. Examples are generic; no private messages, account IDs, API receipts, or login artifacts are included.

本文保留方案演进与判断依据，不公开私人对话、账号、密钥、原始截图、登录二维码和数据库。

| Stage / 阶段 | Observation / 问题 | Decision / 处理 |
|---|---|---|
| Text companion | A model API alone has no durable memory or executable tools. / API本身不会记住长期聊天或自动操作软件。 | Add a local orchestrator, tool schemas and scoped SQLite stores. / 本地程序负责记忆、工具执行和回执。 |
| Speech | Fast, broken or flat speech was not acceptable. / 语速、句间断裂、情感存在问题。 | Normal-rate speech, punctuation-aware chunking and audition. / 正常语速、分句与试听。 |
| Singing | Humming and reading lyrics are not singing. / 无歌词哼唱和念词不能冒充唱歌。 | Score-driven synthesis with syllables, notes and durations. / 改用逐字乐谱生成。 |
| Review | A model judge showed order bias and failed silence controls. / 自动评分存在顺序偏差，静音控制也判断错误。 | Remove that judge as an acceptance criterion; keep content/integrity checks and human listening. / 不以该分数验收自然度。 |
| Pitch stability | Voice conversion could produce pitch flutter. / 换声后出现发颤和跳音。 | Smooth voiced segments while preserving boundaries and note changes. / 稳音但保留清浊边界和旋律。 |
| QQ integration | Testing on a daily account risks unintended replies. / 日常账号不适合作为初始自动回复入口。 | Independent portable client, exact bot/contact checks, groups off. / 独立客户端与白名单。 |
| Images and memes | Literal captions missed the social meaning of a reaction. / 把表情解析直接发回去很生硬。 | Separate visual evidence from conversational intent; react to the preceding topic. / 结合上文接话。 |
| Chat rhythm | One reply per short fragment felt mechanical. / 连续碎片消息逐条回复。 | A short bounded batching window, per-event deduplication and mostly separate sticker bubbles. / 合并来信但不无限等候。 |
| Personality | Prompt examples were mistaken for real shared experiences. / 示例被混成真实经历。 | Keep immutable identity rules, label reference examples, require evidence for memories and activities. / 明确资料边界。 |
| Tool autonomy | A prompt cannot enforce filesystem or account permissions. / 不能只让模型“自觉守规矩”。 | Check real sender and request in code; constrain native tools and per-task file permissions. / 程序与沙箱双重限制。 |
| Query load | Many requests can create a queue and consume a subscription. / 多人请求会堆积耗时任务。 | Persist rolling limits, bound queue length, reuse scoped threads, standard speed. / 持久化额度与有限排队。 |
| Timeout | Throwing away every partial result was unhelpful. / 一超时就丢弃全部资料。 | Return sourced partial material, or a concise honest no-result reply. / 有资料则收尾，没资料不编。 |
| Local login UI | A missing local token also blocked QR refresh. / 令牌未填导致二维码也刷不出来。 | Same-origin loopback sessions and automatic QR state handling. / 免手填令牌并保留手机确认。 |
| Startup | Duplicate processes and weak connectivity checks obscured failures. / 重复启动和只检查端口造成误判。 | Clash first, actual Google HTTPS response, then ChatGPT and verified services. / 联网通过后继续，已运行则跳过。 |
| Publication | A live folder mixes code with private and third-party material. / 工作目录混有账号、聊天、权重和素材。 | Build an allowlisted public snapshot, portable configuration and provenance records. / 单独构建公开版并扫描。 |
| Context repair | Old assistant denials and unrelated jobs displaced the current request. / 旧的能力否认和任务干扰新消息。 | Select memory by original user evidence, store quote links, use actual task records and keep the current user turn last. / 原话证据、引用关系与任务状态分开。 |
| Voice input | Voice-only messages and exact read-backs needed a separate path. / 纯语音输入和原文朗读处理不完整。 | Decode through the connector, run bounded local CPU ASR, preserve words and use normal-speed sentence chunks. / 接收识别与原文朗读分开。 |
| Sticker meaning | An emotion label could match while the text contradicted the conversation. / 情绪标签对上但图中文字不合时宜。 | Choose from approved local candidates using captions, OCR and current context. / 根据图意接话，不随机硬配。 |
| Model comparison | A request for two model variants could admit only the first. / 两种模型对比只执行第一张。 | One bounded sequential comparison job, explicit labels and partial results. / 串行执行并保留成功部分。 |
| Login continuity | Repeated QR login was burdensome. / 反复扫码不便。 | Owner-only saved-session recovery and encrypted password fallback, with real QQ verification and staged account switching. / 快速恢复、加密凭据与受控换号。 |

## What the checks establish / 检查能证明什么

Unit and integration checks cover routing, authorization, memory isolation, quota persistence, process termination, source-based results and log integrity. Live checks used the local service and a standard-speed Luna summary of public documentation. No private delivery receipts or recordings are distributed as evidence.

这些检查验证程序行为，不代表每次语音都像真人、角色声线获得权利人许可、QQ接口永久稳定，或所有硬件都能复现同样速度。可选模型、适配器与上游接口会变化，复现时应重新测试。

## Rejected shortcuts / 没有采用的捷径

- Passing an entire private history or credentials to a coding worker. / 把整个聊天库和凭据交给工具。
- Inventing future activities or saying a task is complete before a receipt exists. / 没执行却说已完成。
- Treating a nickname or text saying “I am the owner” as authorization. / 仅凭昵称授予管理员权限。
- Publishing downloaded memes or character recordings under the application license. / 给别人的素材套本项目许可。
- Guessing money spent from tokens or balance deltas. / 用Token或余额净变化编造准确费用。
