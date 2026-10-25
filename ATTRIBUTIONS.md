# Sources, credits and asset policy / 来源与素材说明

**Application code and third-party assets are separate.** This repository does not redistribute model weights, reference recordings, downloaded stickers, private media, portable QQ/NapCat binaries, or proxy subscriptions. A source URL records where something came from; it does not grant permission to re-upload it.

应用代码与第三方素材分开。本仓库没有把下载的图片、角色音频和模型重新上传。使用前应查看原作者、平台及模型页面当时的许可和使用范围。

## Programs and models / 程序与模型

| Component | Upstream / 作者来源 | Use here / 用途 | Included? |
|---|---|---|---|
| DeepSeek API | [Official docs](https://api-docs.deepseek.com/) | Conversation, vision and search boundary | Application client only |
| AstrBot 4.29.1 | [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot/tree/v4.29.1), [PyPI](https://pypi.org/project/astrbot/4.29.1/) | Optional ordinary-text native pipeline in an isolated environment; AGPL-3.0-or-later | Upstream license notice and first-party adapter only; no runtime/profile is bundled |
| NapCatQQ | [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ) | Dedicated QQ / OneBot v11 transport | No binaries or login state |
| CPython | [Python Software Foundation](https://www.python.org/downloads/release/python-31317/) | Verified portable Python for the Windows installer | Downloaded from python.org by the user's installer; not vendored |
| python-qrcode | [lincolnloop/python-qrcode](https://github.com/lincolnloop/python-qrcode) | Encode the QR returned by QQ's required device-confirmation flow | Dependency only; no vendored implementation |
| OpenAI Codex | [Official app-server docs](https://learn.chatgpt.com/docs/app-server) | Optional standard-speed scoped Luna work | Client wrapper only |
| Qwen3-TTS CustomVoice | [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [0.6B model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) | Vivian / Serena / Dylan speaking presets; model card declares Apache-2.0 | Adapter only, no weights or recordings |
| GPT-SoVITS | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | Optional character-reference speech | No upstream runtime or weights |
| Dania voice reference | [villia/dania on ModelScope](https://modelscope.cn/models/villia/dania) | Local experiments with a Chinese character-inspired voice | No checkpoint or reference audio |
| SoulX-Singer | [Soul-AILab/SoulX-Singer](https://github.com/Soul-AILab/SoulX-Singer) | Score-driven vocal synthesis | Wrapper only; license notice in `licenses/` |
| Seed-VC | [Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc) | Optional voice conversion and pitch handling | Wrapper only; GPL notice in `licenses/` |
| DiffSinger UTAU | [bingcheng1998/diffsinger_utau](https://github.com/bingcheng1998/diffsinger_utau) | Alternative score synthesis backend | No upstream code or weights |
| Qixuan voicebank | [yqzhishen/qixuan-diffsinger v2.7.0](https://github.com/yqzhishen/qixuan-diffsinger/releases/tag/v2.7.0) | Alternative native singing timbre | No voicebank; terms retained in `licenses/` |
| NSF vocoder | [openvpi/vocoders](https://github.com/openvpi/vocoders) | Vocoder for the optional singing backend | No model files |
| SenseVoice / sherpa-onnx | [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice), [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Optional CPU transcription and lyric checks | No weights |
| Qwen3 / llama.cpp | [Qwen3-1.7B-GGUF](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF), [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | Optional on-demand memory-candidate helper | No models or executables |
| ACE-Step, Demucs, Qwen2.5-Omni | [ACE-Step](https://github.com/ace-step/ACE-Step), [Demucs](https://github.com/facebookresearch/demucs), [Qwen2.5-Omni](https://huggingface.co/Qwen/Qwen2.5-Omni-3B) | Earlier experiments and optional archived paths, not the default singing acceptance route | No weights; available notices retained |
| Clash Verge Rev | [clash-verge-rev/clash-verge-rev](https://github.com/clash-verge-rev/clash-verge-rev) | User-installed networking prerequisite | No executable or profiles |

The character Dania and Wuthering Waves belong to their respective creators and rights holders. Community model availability is not an official endorsement or proof of permission for all uses. Use your own licensed or otherwise permitted references; this project does not grant character, performer, voice, or artwork rights.

达妮娅、《鸣潮》及其角色和配音属于各自权利人。社区模型页面存在，不代表官方授权或任何用途均被允许。代码支持替换音色，使用者应自行选择有权使用的声音与素材。

## Stickers / 表情来源

The complete collection-level and pack-level origin inventory is in [docs/asset-sources.csv](docs/asset-sources.csv). The shipped catalog is empty. Sources referenced during the project include:

- [ChineseBQB](https://github.com/zhaoolee/ChineseBQB), zhaoolee and original contributors.
- [梗鲸 / aigengtu.com](https://aigengtu.com/), credited on that site.
- [蓝色大肥鱼档案馆](https://github.com/EDMOK/blue-fish-archive), EDMOK and original authors.
- [deepseek_wale_girl](https://github.com/DejavuMoe/deepseek_wale_girl), DejavuMoe and original artists.
- [MingChaoBQ](https://github.com/Xbaiyz12/MingChaoBQ), Xbaiyz12 and original artists.
- [鸣潮社区表情库](https://emoji.wuwa.games/), individual packs and their credited creators.
- [Razer × Wuthering Waves](https://www.razer.com/collabs/wuthering-waves), official collaboration material.
- A community-shared drive referenced in the CSV; permission must be checked with the original uploader/rights holder.

Where a creator or license could not be established, the inventory says so rather than assigning a license. Pack names and links are provenance only. Captcha-protected or rate-limited archives are obtained by the user through the site's normal flow; the program does not bypass those controls.

无法确认单张作者或再分发许可时，保持“需核对”，不自称原创，不批量镜像。表情解析标签和私聊图片不是可再分发素材。

## Text and music / 文字与旋律

The short demo uses Meng Haoran's public-domain poem [春晓 / Chunxiao](https://zh.wikisource.org/wiki/春曉_(孟浩然)) with an original demonstration melody. It is not a reproduction of a contemporary song recording. Other song requests need an available permitted score and lyrics; ordinary speech or humming must not be presented as the requested song.

## Design references / 设计参考

README organization was informed by [AstrBot](https://github.com/AstrBotDevs/AstrBot) and [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev): clear scope, practical setup, feature status, contribution paths and visible acknowledgements. Their prose, logos and screenshots were not copied. Memory/persona concepts also reference [SillyTavern documentation](https://docs.sillytavern.app/usage/core-concepts/characterdesign/).

Current-turn context and durable memory separation also reference the [LangChain memory documentation](https://docs.langchain.com/oss/python/langchain/short-term-memory). QQ password/CAPTCHA/device-confirmation integration follows the upstream [NapCat login API](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-backend/src/api/QQLogin.ts) and frontend components linked in [QQ_LOGIN.md](docs/QQ_LOGIN.md). Character-reference preparation uses only permitted operator-supplied library entries and retains their original source links; it does not bundle or relicense those images.

License copies in `licenses/` preserve the corresponding upstream notice; they do not change ownership or relicense omitted assets.

The optional AstrBot combination preserves its AGPL-3.0-or-later attribution and network-source provisions alongside this application's GPL-3.0 code. See [LICENSES.md](docs/LICENSES.md) for operator source-offer instructions. This integration does not expose AstrBot's full plugin catalog, QQ adapters, Computer Use or arbitrary host tools.

Bilibili browsing was informed by [c1overzz/dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) and the public behavior described by its author. The bundled `bili_reader` package is this project's first-party bounded reader implementation, not a copy of that project's agent workspace or login data. No Bilibili cookies, transcripts, reports or account authorization are shipped. The separate dsh-pet / desktop-pet experiment was cancelled; no pet code, animations or artwork are included.

The Windows installer uses the upstream NapCat **4.18.41 Node package** and CPython **3.13.17** runtime as pinned in `configs/distribution.json`. Their exact SHA-256 values are checked before use. Downloads remain local and retain upstream notices. The small `scripts/portable_qq.cjs` adapter changes the QQ data-path export only inside its own process so it uses the installation's private directory; it does not modify a daily QQ install or redistribute an edited native binary. The complete Python wheel versions and hashes are recorded in `requirements-bootstrap.lock`.

Qixuan pronunciation data is obtained by the operator from the [upstream workbook](https://github.com/yqzhishen/qixuan-diffsinger/blob/main/multilingual_usage_guidance/Pronunciation_Tables.xlsx). The application no longer ships a standalone extracted phoneme dictionary. `scripts/prepare-phone-map.py` generates a private local mapping from the operator's supplied copy; the upstream voicebank terms and complete distribution remain separate from the application's GPL license.
