<div align="center">

# Whale Companion · 鲸娅

**A QQ AI companion with locally managed memory, permissions and tools.**

[简体中文](README.md) · [One-click setup](docs/ONE_CLICK_SETUP.md) · [Architecture](docs/ARCHITECTURE.md) · [Credits](ATTRIBUTIONS.md) · [Privacy](PRIVACY.md)

![Windows](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4)
![Python](https://img.shields.io/badge/Bootstrap-Python_3.13-3776AB)
![License](https://img.shields.io/badge/License-GPL--3.0-blue)

</div>

DeepSeek handles conversation and tool selection. SQLite stores identity-scoped memory and task state. A local application connects a dedicated QQ client and manages stickers, images, speech, public research and optional Codex work.

The repository contains application source, configuration templates and design notes. Accounts, keys, passwords, chats, contact databases, photos, reference recordings, model weights and downloaded sticker packs stay on the operator's computer. It does not train the hosted DeepSeek model.

> This documentation describes a release candidate. Available downloads, verified scope and known issues are listed in [Releases](https://github.com/LONG10222/whale-companion/releases). Publication requires functional acceptance and privacy checks.

## Get started

1. Download `WhaleCompanion-vVERSION-windows-x64.zip` from an official release and extract the whole folder.
2. Double-click **Install.cmd**. It verifies and prepares portable Python, an independent QQ runtime and base dependencies, then opens a local wizard.
3. Enter your own **DeepSeek API key**, scan with a dedicated bot QQ account and complete phone confirmation.
4. Add the bot from your personal account, select that account in the wizard and confirm it as administrator.

Later, use the desktop **Whale Companion** shortcut or **Start.cmd**. Running services are reused; an unrelated process occupying a required port is left running. Basic chat does not require preinstalled Python, Node, Git, Codex, Clash or GPU models. Initially, only the confirmed administrator can chat privately; groups are off. QQ may require personal confirmation or verification. See [setup and upgrades](docs/ONE_CLICK_SETUP.md).

## Capabilities and configuration

| Capability | Implementation and limits |
| --- | --- |
| Private and group chat | NapCat / OneBot v11; account binding, deduplication and batching; explicitly enabled groups normally respond only to mentions or replies |
| Contacts and roles | QQ number is the identity key; nicknames and custom names affect display only |
| Persona and memory | Local character card, positive examples, current context, evidence-backed preferences and task state; feedback and version recovery |
| Reactions and endings | Contextual image meaning, separate text/sticker bubbles, and a distinction between conversation endings and task confirmations |
| Images, videos and group files | Controlled working copies, large-image resizing, bounded video sampling and separately budgeted animation decoding |
| Voice input | Optional local SenseVoice int8 CPU transcription in the same sender/conversation scope; voice input does not force a voice reply |
| Speech and singing | Optional speech models, score-driven singing, pitch stabilization and conversion; requires permitted models, runtimes and references |
| Drawing and photo editing | Your Codex account; source-image ownership, main edit, lower-cost review, bounded repair and inspection of actual candidates |
| Public research | Public HTTPS checks, bounded responses, source links and partial results; normally summarized with Luna |
| Bilibili extension | Separate login, actual subtitle reads and sharing; administrator-only, with separate public-action settings and confirmation for comments/coins |
| Proactive chat | DeepSeek chooses whether to speak and what to discuss; current information and completed reading activities can supply topics, subject to timing and no-follow-up rules |
| Controlled workbench | Create or resume named work chats in the bound project, record actual status and recoverable archives; the model cannot expand permissions |
| Usage and maintenance | Official balances, reported tokens and task/financial events; update notices, owner-click upgrades, backups and failed-start rollback |

WeChat, Qzone publishing and proactive friend requests are not exposed as stable capabilities. Cancelled phone-information collection and desktop-pet experiments are excluded. Reading subtitles is not equivalent to watching all video frames and audio.

The wizard inventories all implemented capability families and reports missing dependencies. The [configuration index](docs/CONFIGURATION.md) maps 20 feature families to settings and dependencies. CPU, RAM, GPU and free VRAM determine **daily-use priority / balanced / quality priority** presets. Modules and resource budgets can be chosen manually. Selecting a module records a configuration plan; it does not pretend missing models or accounts were installed.

Speech, singing synthesis and conversion have separate GPU soft budgets. Local learning is optional and yields resources under load. Workers run on demand and audio caches have limits. Hardware presets cannot raise account permissions or change cloud service speed.

## Models, permissions and concurrency

Foreground conversation uses your DeepSeek API. These limits describe **Codex work chats**, not the name of the underlying image engine.

| Role | Work model ceiling | Active work chats | Public research limits |
| --- | --- | --- | --- |
| Regular | Luna | Up to 3 per user | 2 accepted questions per rolling 15 minutes; 5 minutes per question, including queue time |
| Plus | Luna for general work; available 5.6 Terra or lower for photo editing | Up to 3 per user | Exempt from those two research limits; shared queues and resource limits still apply |
| Administrator | Up to 6.1 Sol / high for general work; xhigh permitted for the main image-edit stage | Up to 6 | Manages locally permitted tools and roles |

All work uses standard service speed: no fast, ultrafast or 1.5× mode. Plus is an administrator-assigned application role, not QQ membership, and grants no computer-file or account-control access. Heavy work is queued; multiple retained work chats do not run multiple models simultaneously. Private memory stays out of group replies. An absent image-engine receipt does not justify guessing its model name.

## Bring your models and assets

- **Codex drawing, edits and workbench:** Sign in to your official client and bind a real project ID/path. A mismatch stops work instead of creating an unbound chat.
- **Voice input:** Supply a SenseVoice int8 environment and model; the default worker uses bounded CPU processing.
- **Speech and singing:** Supply permitted models, environments, references and scores. A song title alone does not guarantee an available complete song; speech or humming is not reported as successful singing of it.
- **Stickers:** The shipped catalog is empty. Import material under its original terms; private chat images are not public assets.
- **Dedicated emulator:** Reading supported mobile apps requires your own separate environment and login. Daily-phone information is not collected.

See [credits and asset policy](ATTRIBUTIONS.md) and [advanced setup](docs/SETUP.md). The original personal launcher remains available for an already configured Clash → Google HTTPS check → ChatGPT → services flow, separate from the basic installer.

## Updates and data

The application periodically checks this repository's official releases and shows a local notice. **An owner click is required to install.** Updates verify the archive and per-file manifest, wait for active work, back up private configuration and consistent SQLite snapshots, check startup/account binding and roll back on failure. Changes to the main Python or QQ runtime currently require a full-installer component update rather than replacing running QQ binaries.

Release archives contain no author login state. Configuration, databases and backups stay local; cloud inference sends the required text, media or tool results to the selected provider. Local storage does not mean offline inference. Read [PRIVACY.md](PRIVACY.md).

Work chats may write only inside their dedicated task directory. Explicitly requested external material is filtered into read-only snapshots; source files and running databases are not made writable. Known credential files and directory links are excluded. Keep local control pages off the public internet.

## Development and validation

Source development supports Python 3.12 / 3.13. The Windows installer pins Python 3.13:

```powershell
git clone https://github.com/LONG10222/whale-companion.git
cd whale-companion
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe scripts/scan_public.py
```

`requirements-bootstrap.lock` pins Windows x64 / CPython 3.13 wheels and hashes. `scripts/build-release.py --output <directory-outside-checkout>` builds a source installation archive without uploading or pushing. CI runs regression and privacy checks. Passing tests does not certify every account, platform version, voice model or aesthetic outcome.

Please remove accounts, keys, cookies, private chats and personal paths from reproductions. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [architecture](docs/ARCHITECTURE.md).

## License and credits

Application code is [GPL-3.0](LICENSE). Third-party programs, models, voices, stickers, characters and artwork keep their own terms. A source link is not redistribution permission. This project is not affiliated with or endorsed by DeepSeek, OpenAI, Tencent, Bilibili or Wuthering Waves.
