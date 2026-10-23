# Privacy / 隐私

- Runtime configuration, API keys, OneBot credentials and browser sessions are local and excluded from the repository.
- Chat histories, learned preferences, contacts, query reservations and usage ledgers are private SQLite databases. Do not attach them to an issue.
- Cloud inference sends the required current input to the configured provider. Images, extracted video frames and public-page text can be included when their tools are used. Local persistence is not offline processing.
- A local-session cookie is HttpOnly and SameSite=Strict, limited to an allowed loopback origin. Do not expose the service through a public reverse proxy and assume this remains sufficient.
- The source export excludes recordings, screenshots, generated media, downloaded sticker files, model checkpoints, portable clients, proxy profiles and raw development transcripts.
- Daily logs record usage and states, not API keys, full conversations or reasoning text. Financial amounts are recorded only when returned/confirmed; balance net changes stay separate from confirmed income/expense.
- The repository uses fictional account numbers in tests/examples. Replace them only in ignored local configuration.
- Optional QQ password credentials and unexpired verification state are protected with Windows DPAPI for the current Windows user in `configs/qq-login.secrets.local.json`. They are excluded from source export and never returned by the state API or provided to the chat model. Disabling password storage keeps verification credentials in memory only. QQ still receives the authentication material required by its own protocol.
- Voice files have a private bounded cache. Optional local ASR converts them to conversation text; the text then follows the same configured cloud-chat boundary. Original audio is not automatically sent to a chat model.
- Quote relationships and source message IDs are local records. They are conversation evidence, not new execution permissions. Old assistant claims cannot grant tools or file access.
- The beginner installer downloads public runtime/dependency archives from their pinned upstreams. Update discovery contacts this repository's public GitHub release endpoint; it sends no API key, QQ ID, chat or local file. Application upgrades require an authenticated local click. Configuration and SQLite backups stay in `data/update-backups/` and are never part of a release archive.
- The installer launch credential is a local, short-lived browser-session bootstrap value. The page removes it from the displayed fragment, exchanges it for a same-site HttpOnly cookie and does not print it in install logs. Long-lived provider keys are not placed in URLs.

本机配置与数据库不属于公开源码。提交问题时，请重新制作匿名复现，不要上传聊天库、登录二维码、Cookie、代理订阅、密钥或个人绝对路径。公开网页内容也可能有错误或恶意提示，不能授予程序权限。
