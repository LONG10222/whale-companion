# QQ 一键登录 / One-click QQ login

在 QQ 接入页填写机器人小号和密码，点击「保存并一键登录」。后续启动先恢复客户端保存的会话；会话失效时再用保存的凭据登录。QQ 要求滑块或新设备确认时，在本地页面完成，程序接着处理剩余步骤。验证失败后停止自动尝试，修改密码或手动重试后再继续。

下拉框可以选择已经保存的小号，也可以填写新的小号。换号会暂停回复、备份已有配置与数据库、核对实际登录账号、迁移连接设置并检查测试好友关系；完成后恢复原有接收范围。不会把聊天昵称当成管理员身份。暂停回复不会因为重新登录而被解除。

凭据与未完成验证均通过 Windows DPAPI 为当前 Windows 用户加密。它们存放在 `configs/qq-login.secrets.local.json`，不返回给聊天模型，也不应复制到公开仓库。取消保存密码时，当前验证凭据只放在内存中；清除本机密码不会删除聊天记录。首次使用需安装 `requirements-qq-login.txt`，不需要显卡或常驻语音模型。

Enter the bot account and password on the local QQ page, then choose **Save and log in**. Startup prefers the client's saved session and falls back to the saved credential if necessary. Complete any QQ CAPTCHA or device confirmation in the local page. Failed authentication stops automatic retries until the owner takes action.

Select a saved account or enter another bot account to switch. The existing staged switch preserves history, pauses replies, verifies the signed-in identity and checks the configured test contact before resuming. A login does not undo a deliberate reply pause.

Credentials and pending verification state use Windows DPAPI scoped to the current Windows user. `configs/qq-login.secrets.local.json` is private and excluded by the existing local-configuration ignore rule. Neither credentials nor encrypted blobs are returned by the login-state API or supplied to the chat model. With password storage disabled, verification secrets stay in memory. Install `requirements-qq-login.txt`; no GPU model is required.

## Upstream references

- [NapCat login API implementation](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-backend/src/api/QQLogin.ts)
- [NapCat QQ login controller](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-frontend/src/controllers/qq_manager.ts)
- [NapCat Tencent CAPTCHA integration](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-frontend/src/components/tencent_captcha.tsx)
- [NapCat device-confirmation integration](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-frontend/src/components/new_device_verify.tsx)
