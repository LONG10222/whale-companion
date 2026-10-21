<div align="center">

# Whale Companion

**A local QQ companion with memory, contextual reactions, bounded tools, and visible usage.**

[简体中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Build journal](docs/BUILD_LOG.md) · [Credits](ATTRIBUTIONS.md)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4)
![License](https://img.shields.io/badge/License-GPL--3.0-blue)

</div>

Whale Companion is a Windows-first local QQ companion prototype. DeepSeek handles conversation and tool selection. SQLite stores contact-scoped memory, feedback, and task state. Optional Codex Luna, speech, and singing integrations add source-grounded research, images, and audio.

This repository contains application source, design decisions, and configuration templates. It includes **no personal instance, account session, chat database, API key, character checkpoint, or downloaded sticker artwork**. It does not train the hosted DeepSeek model or implement every QQ client feature.

## Features and current scope

| Capability | Implementation |
|---|---|
| Private QQ chat | NapCat / OneBot v11, a dedicated bot account, explicit allowlist, deduplication, burst batching |
| Memory and persona | Recent context, rolling summaries, evidence-backed memory candidates, owner-reviewed feedback |
| Images and reactions | Image/GIF understanding, contextual local stickers, mostly separate text and image bubbles |
| Video | Bounded frame sampling, cloud vision, optional CPU speech recognition |
| Speech and singing | Optional on-demand speech, score-driven singing and voice conversion, content/integrity checks |
| Public research | Controlled public-page reads, Luna summaries, source links, partial results on timeout |
| Access and budgets | QQ-number identities; administrator, regular, manual Plus; rolling two queries/15 min and five-minute limit |
| Usage ledger | Task events, reported tokens, balance snapshots and net changes; unknown cost stays unknown |
| Desktop launch | Clash → Google HTTPS check → ChatGPT → local services; existing processes are reused |

**Not integrated:** WeChat, live group replies, proactive friend requests, Qzone browsing, and Qzone publishing. An upstream API does not mean a capability has been enabled or validated here. Proactive chat prepares drafts for the owner to send.

## Quick start

### Prerequisites

Windows 10/11, Python 3.12, a dedicated QQ test account, your own DeepSeek API key, and an independent [NapCat installation](https://napneko.github.io/). Model weights, CUDA runtimes, Codex, and Clash are external optional dependencies and are not redistributed.

```powershell
git clone https://github.com/LONG10222/whale-companion.git
cd whale-companion
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/configure.py
```

The interactive setup asks for your administrator and bot QQ numbers, generates fresh local credentials, and hides API-key input. Generated `*.local.json` files are ignored by Git. Existing local settings are never overwritten. **Replies start paused; groups and voice are disabled.**

Copy `configs/qq-client.example.json` to `configs/qq-client.local.json` and enter your own NapCat installation and boot paths. Configure its loopback WebSocket listener at `127.0.0.1:3001` with the same `onebot_token` generated in `qq.local.json`; use loopback WebUI port `6099`.

```powershell
.\.venv\Scripts\python.exe -m uvicorn phone_server:app --host 127.0.0.1 --port 18766 --no-access-log
```

Open <http://127.0.0.1:18766/qq>. A constrained same-origin loopback session connects the local UI automatically, without asking you to paste the service token. QR refresh preserves scanned/initializing/reconnecting states. Complete phone confirmation and required security verification yourself, then enable only the configured test contact.

### One-click desktop shortcut

After individual services work, review `configs/launcher.local.json`, then install the shortcut:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-shortcut.ps1
```

The launcher starts or reuses Clash, requires a valid Google HTTPS `generate_204` response, starts or reuses ChatGPT, and then checks/starts local services. It stops if networking fails or another program occupies a service port. It does not change proxy nodes, subscriptions, or system settings. The execution-policy option applies to this process only.

```powershell
# Inspect readiness without starting missing applications
powershell -NoProfile -ExecutionPolicy Bypass -File launch-whale.ps1 -CheckOnly -NoGui
```

## Optional integrations

- **Codex:** Sign in using the official local CLI. Configure an existing project ID/path using `configs/qq-workbench.example.json`. Only a task's own workspace is writable; outside reads require explicit current-request permission. Regular/Plus users use Luna, administrator ceiling is 6.1 Sol/high, and every run uses standard speed.
- **Audio:** Install upstream runtimes and obtain permitted model weights/reference audio yourself. Read [setup](docs/SETUP.md) and [credits](ATTRIBUTIONS.md). Missing models cause a clear failure rather than a fake output.
- **Stickers:** The shipped catalog is empty. [Source provenance](docs/asset-sources.csv) records origins, not redistribution permission. Obtain material under its own terms and import it with `scripts/import_stickers.py`.
- **Phone reading:** Requires a separate Android emulator and ADB/UIAutomator configuration. The adapter is restricted to supported read-only flows.

## Budgets, privacy, and verification

A user question counts once, regardless of internal page reads. The regular-user budget is two accepted queries in any rolling 15-minute window, keyed by QQ number across conversations and preserved across restarts. The five-minute deadline includes queue time and terminates the owned worker tree. Existing source material is returned as a grounded summary or clearly attributed partial excerpts; missing evidence produces an honest short reply. Manual Plus exempts only these query limits, not computer permissions or model restrictions.

Heavy jobs run one at a time with three pending slots; a regular/Plus user can hold one outstanding job. Group messaging remains disabled. Token counts and balance net changes are not converted into guessed costs. Original chat and memory remain local, but material needed for cloud inference is sent to the selected provider. See [privacy](PRIVACY.md).

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe scripts/scan_public.py
```

Tests cover source identity, deduplication, scoped memory, permissions, quotas, worker termination, ledger integrity, local-session isolation, and QR states. They do not certify human-like behavior, emotional quality, or universal QQ compatibility. Please submit small reproducible issues without credentials, chat history, account numbers, or private filesystem paths.

## License and acknowledgements

Application code is [GPL-3.0](LICENSE). External programs, models, character voices, artwork, and brands retain their own rights. They are not automatically covered by this repository's license. See [ATTRIBUTIONS.md](ATTRIBUTIONS.md) for sources and omitted assets. This project is not affiliated with or endorsed by DeepSeek, OpenAI, Tencent, or Wuthering Waves.

Import a locally obtained, permitted collection:

```powershell
.\.venv\Scripts\python.exe scripts/import_stickers.py C:/PublicStickers --source chinesebqb --pack "My permitted collection"
```

Unlabeled images are not automatically selected. Use `--emotion` only when the label is supported by the collection.
