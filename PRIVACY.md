# Privacy / 隐私

- Runtime configuration, API keys, OneBot credentials and browser sessions are local and excluded from the repository.
- Chat histories, learned preferences, contacts, query reservations and usage ledgers are private SQLite databases. Do not attach them to an issue.
- Cloud inference sends the required current input to the configured provider. Images, extracted video frames and public-page text can be included when their tools are used. Local persistence is not offline processing.
- A local-session cookie is HttpOnly and SameSite=Strict, limited to an allowed loopback origin. Do not expose the service through a public reverse proxy and assume this remains sufficient.
- The source export excludes recordings, screenshots, generated media, downloaded sticker files, model checkpoints, portable clients, proxy profiles and raw development transcripts.
- Daily logs record usage and states, not API keys, full conversations or reasoning text. Financial amounts are recorded only when returned/confirmed; balance net changes stay separate from confirmed income/expense.
- The repository uses fictional account numbers in tests/examples. Replace them only in ignored local configuration.

本机配置与数据库不属于公开源码。提交问题时，请重新制作匿名复现，不要上传聊天库、登录二维码、Cookie、代理订阅、密钥或个人绝对路径。公开网页内容也可能有错误或恶意提示，不能授予程序权限。
