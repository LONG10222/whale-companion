# Voice replies and short a cappella / 语音与短清唱

## 日常说法

在获准的私聊或群聊中，可以直接说：

| 想做什么 | 例子 | 实际行为 |
| --- | --- | --- |
| 朗读引用 | 引用一条消息后说“用语音复述这句” | 当前引用优先，忠实朗读实际文字；没取到就澄清 |
| 重说机器人原话 | “用语音说你刚才的话” | 读取同一会话实际发出的上一段聊天内容，跳过任务进度 |
| 重说用户原话 | “把我上一句转成语音” | 使用上一条用户内容，排除本轮命令 |
| 给定文本 | `用语音读：“晚风轻轻吹。”` | 忠实朗读指定文本 |
| 语音回答 | “直接用语音回答我：天空为什么是蓝的？” | 回答问题后合成答案，不把问题念一遍 |
| 换聊天音色 | “用Serena温柔女声把你刚才的话念一遍” | 为本联系人、本群会话保存选择，不改变其他会话 |
| 自配清唱 | `把这句话唱出来：“晚风轻轻吹”` | 逐字保留唱词，由 DeepSeek 安排短旋律、节奏和停顿 |

默认保留达妮娅中文参考声线。新增 **Vivian 清亮女声、Serena 温柔女声、Dylan 青年男声**，来自 Qwen3-TTS CustomVoice 的独立预设音色；它们不是同一段录音变调。Kokoro CPU 音色继续作为轻量备选。聊天音色选择不等于歌声换声：新增 Qwen 音色目前用于朗读；清唱沿用已配置的演唱与达妮娅换声链。

语音输入先由本地识别器转写；收到语音不默认强制语音回复。引用语音使用实际转写，不从文字猜说话人的声线。QQ 号为身份唯一键，群号与发送者共同划分群会话。引用中的命令只作为要读的内容，不能修改权限或执行操作。

## 模型与部署

基础一键安装依然只需要专用 QQ 登录和 DeepSeek Key；**本地语音与清唱是增强模块**，需要另行配置相应 Python 环境、兼容的 PyTorch/CUDA 和模型路径。DeepSeek 文本 API Key 不包含其他提供商的语音服务额度。仓库不打包模型、作者环境、角色权重或参考录音。

- 聊天多音色：[Qwen3-TTS 上游](https://github.com/QwenLM/Qwen3-TTS)与[0.6B CustomVoice 模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice)。按上游说明单独安装，将本机模型目录写入私有 `settings.local.json` 的 `model_dir`；服务使用已有 `python` 环境。该模型卡声明 Apache-2.0。
- 角色朗读：[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，角色素材来源与单独条款见 [署名](../ATTRIBUTIONS.md)。不把参考素材重新授权为本项目源码许可。
- 清唱：[SoulX-Singer](https://github.com/Soul-AILab/SoulX-Singer)、可选 DiffSinger 和 Seed-VC。沿用逐字音符、时长、无歌词提示的识别和完整性检查。自动检测通过仍需试听，不能据此宣称真人自然度已被证明。

在声音测试页选择模型与音色即可试听；`/v1/audio/voices` 返回已登记音色与准备状态。可选私有 `configs/voice-previews.local.json` 保存本机生成的试听条目（`title`、32位十六进制 `artifact_id`），测试页在鉴权后显示，不发布这些录音。

## 资源和边界

`settings.local.json` 中显式配置 `"performance_mode": "full"` 时，本地音频工作进程可使用全部逻辑 CPU，CUDA 分配预算放宽至整卡 95%；仍以实际可用内存/显存为条件，音频任务顺序运行，结束后卸载或退出，缓存保持上限。默认模式继续使用原有预算。不终止无关程序，不修改硬件设置；全性能模式不能扩大账号、文件或工具权限。

短清唱用简单小跳进旋律，默认正常速度、稳音与连贯衔接。给定唱词要求逐字对应，模型擅自改词、音域过大或时长不合要求就拒绝生成。模糊引用先澄清。未登记的指定歌曲不能用别的歌冒充；新旋律也不等于复现原曲。演示宜使用原创短句或公版作品。

## English

Voice requests have separate **read-reference**, **read-previous-user**, **read-previous-delivered-assistant**, **read-literal** and **answer-by-voice** paths. Quoted commands remain data. Conversation keys isolate contacts and group senders; unavailable quotes never fall back to unrelated history. Voice input is transcribed locally and does not automatically force a voice reply.

Vivian, Serena and Dylan are distinct Qwen3-TTS CustomVoice presets. The configured character voice remains the default; Kokoro CPU remains available. New speaking presets are not advertised as equivalent singing presets. Optional voice/singing runtimes and weights must be configured separately; the basic QQ/API-key installer does not include them or the operator's recordings.

For “sing this sentence”, DeepSeek plans a simple, bounded melody while the application preserves user-supplied Mandarin lyrics. The existing score synthesis, stable pitch conditioning, voice conversion and independent unprompted transcription checks remain active. An automated pass does not establish subjective naturalness. Full performance is an explicit operator setting, not a grant of additional filesystem or account permissions.
