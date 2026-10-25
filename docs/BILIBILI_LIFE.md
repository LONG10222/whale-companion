# B站阅读、作息与双日记 / Bilibili, rhythm and diaries

Whale Companion 0.5.0 adapts selected designs from [dsh-bili-whale at fixed commit 3e11454](https://github.com/Alterssss/dsh-bili-whale/tree/3e11454a5961300aa7094df0f08e630bd39999dd) into its existing NapCat / OneBot and DeepSeek application. It does not install DSH or add a second QQ receiver. See the [MIT notice and adaptation record](../licenses/dsh-bili-whale/SOURCE.md).

## 使用入口

- `/qq/life`：记忆、公开兴趣、活动作息、真实调度心跳、两本本机日记与阅读反馈。
- `/qq/bilibili`：复用本人网页扫码登录、公开视频资料阅读及现有互动权限。
- `/qq/bilibili/listeners`：评论收件箱、指定主人私信和指定直播房间的倾听、回复草稿、人工确认和请求保护状态。
- `/qq/proactive`：由 DeepSeek 决定是否分享、如何接聊天。沿用既有对象、09:00–22:00、空闲两小时、每天最多一次及未回复不追发规则。

这些页面需要本机已鉴权会话；登录凭据沿用原来的 Windows 加密存储。QQ 号与 B站 mid 是不同的身份键。扫码确定的是当前登录账号，不能由此猜出另一位私信收件人或直播间。新倾听渠道默认关闭；私信目标与直播房间未设置时保留入口，不启用外发。

## 目前实际实现

| 环节 | 行为和验收边界 |
| --- | --- |
| 作息 | 最多四段北京时间窗口，支持跨午夜；机会、随机时刻和本轮休息决定持久化，重启不重新抽签；活动有最小间隔与每日上限 |
| 暂停与恢复 | 保存设置会使当前活动在下个动作前停止；暂停不抹掉已用次数；失去工作器的旧机会在租约结束后记为中断，不重做，不永久挡住次日计划 |
| 选题 | 使用原有明确兴趣、带来源记忆、最近活动及本机“喜欢／少看”反馈；先获取最多五条简介资料，再深读有限候选；自主B站阅读仍最多每天一轮 |
| 资料组合 | 简介、官方字幕、热评和弹幕热区；可选封面与最多九帧画面。热评和弹幕明确属于观众观点，封面属于宣传图 |
| 无字幕 | 在可取得的有界视频文件上，复用已有 CPU 解码与 SenseVoice 转写；视觉模型读取抽样画面的可辨硬字幕。依赖缺失、文件过大或时间不足时准确降级 |
| 阅读证据 | `scope`、`channels`、字幕／OCR／转写的时间范围、实际抽帧时刻与依据引用；短期播放／字幕授权链接不长期缓存 |
| 双日记 | 分享版记录实际阅读内容，内部本记录状态、来源和是否发送；保存在本机，不自动推送。它不是模型推理过程，也不是虚构线下经历 |
| 主动分享 | DeepSeek 可以不浏览、不分享；沿用同一个 QQ 发送队列、来源去重、频次、空闲和未回复保护；本机显式分享后也会抑制随后自动打扰 |
| 评论／私信／直播 | 持久化基线、同秒消息ID去重、失败保留游标；评论通知在拟回复前回读楼层核对作者和正文；新渠道回复均先草稿审核，发送后须有消息编号 |
| 请求保护 | 跨进程单并发、全局间隔、读写预算、写冷却、未知回执去重；慢请求不会只因35秒过去而被抢占，死进程按身份恢复；验证码和限流停手 |

每次深读与视觉调用共用本地模型调用预算。一次没有足够内容的读取会如实留下空、失败或部分状态，不把简介或下载完成叫作“看完视频”。

## 与上游仍有差距的部分

直播使用间隔不低于60秒的有界历史轮询，**不是实时 WebSocket**；高流量时可能漏消息。抽帧与本地转写通常覆盖首P前120秒，最多下载64MB；长视频、DASH、分段文件、受限视频或网络失败可能只得到文字。封面、字幕与抽帧均不等于完整视听体验。

本版本没有移植上游的公开动态发布、收藏体系、疑似AI评论比例闸门或任意自定义推送命令。新渠道未开启主人免审自动回复。长期角色语气、日记的叙事自然度与“活人感”仍需要正常使用观察，不能用单元测试数量证明完全复刻。

一次真实账号只读测试确认了既有扫码登录有效；媒体请求遭平台拒绝后立即停止并留下暂停状态。该测试没有发送 QQ、评论、私信或弹幕，不能作为抽帧／无字幕识别的真实账号通过证明。合成测试覆盖恢复、单并发、去重和权限边界；实际发送回执验收仍取决于配置的真实目标和本人操作。

## English

The local rhythm persists absolute China-time windows, random opportunities and rest decisions. Pausing preserves consumed attempts. Activity leases renew during actual work; lost work is marked interrupted without replay and cannot permanently block the next day. The page reports the real worker heartbeat and pending plan.

Reading first screens up to five metadata candidates, then performs a bounded deeper read. Official captions, descriptions, audience comments, timed danmaku, cover images, sampled frames, OCR and optional local speech transcription carry distinct provenance. Frames/transcription normally cover the first 120 seconds, with nine frames and a 64 MB download limit. Missing dependencies or restricted media cause an explicit partial result. Vision and text judging share the daily model budget; short-lived playback/subtitle authorization URLs are not persisted as reusable cache entries.

Both diary views stay local: one presents source-backed reading, the other records factual activity state and provenance. They contain no chain of thought or invented offline experiences. Feedback affects future choices. Proactive sharing uses the existing single QQ sender and its owner, idle, daily and unanswered-message rules; the model may choose not to speak.

Optional comment/owner-DM/live inboxes start disabled. They preserve cursors on failure, baseline old history, deduplicate same-second IDs and require reviewed reply drafts. Comment notifications are resolved against the actual thread before drafting and again before sending. Owner DMs require an explicit numeric Bilibili target and mutual following. Live history polling is not a real-time stream and can miss busy-room messages. A missing send receipt remains uncertain and is not automatically retried.

This is a partial adaptation, not complete parity: upstream public dynamic posting, favorites, the optional AI-comment-ratio gate, arbitrary push commands and unreviewed owner replies are not added. Existing Windows-encrypted login is reused without copying credentials. A real read confirmed that login but stopped on a platform media rejection; no live QQ or Bilibili writes were made by that test. Long-term conversational quality and untested real targets remain observational limitations.
