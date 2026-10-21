# Setup notes / 安装说明

## Core / 基础服务

Run the README setup using Python 3.12. Start with `phone_server:app` and voice disabled. This separates text/QQ setup from optional audio runtimes. Keep services on `127.0.0.1`; do not publish the dashboard to a LAN or the internet without designing a separate authentication boundary.

先跑通文字与QQ，再加语音。QQ使用独立安装和数据目录，不替换日常QQ。管理员与机器人必须是不同号码，配置只在本机生成。第一次默认暂停，核对账号和好友关系后在页面恢复回复。

## Optional voice / 可选语音

`voice_server.py` is the media host on port 18765. The example settings identify model paths, Python environments and engine configuration. They are templates, not downloaded assets. Obtain upstream speech/singing runtimes from the sources in `ATTRIBUTIONS.md`, review their terms, and set your local paths.

Different synthesis engines may need incompatible Python packages. Keep separate environments rather than downgrading the text service. CUDA builds must support your GPU; an advertised GPU memory cap is not a guarantee about all processes on the device. Start with short demos and verify both content and sound by listening.

`requirements-voice.txt`, `requirements-local-learning.txt`, and `requirements-qq-video.txt` describe optional parts of the implementation snapshot. They are not a universal model installer. Model files, phoneme dictionaries, vocoders, reference audio and upstream source trees are not included.

语音服务器的模板不会自动下载权重。需要配置所选引擎、参考音频及独立环境；没有模型时，关闭语音，文字聊天仍可运行。清唱使用乐谱接口；要复现某一首歌，还需要合法可用的歌词与谱面。

## Codex / 受控工作台

Install/sign in through the official client. Create or choose the existing `deepseek娘` project and record its actual project ID/path in `configs/qq-workbench.local.json`. The service never silently creates an identically named replacement project. A missing project binding prevents execution; it is not treated as success.

Set `WHALE_WORKSPACE` to a dedicated writable directory if the default `workspace/` is unsuitable. Do not point it at your documents directory or a drive root. Database inputs are read-only source snapshots, not permission to mutate the running databases. Regular/Plus users cannot request those external inputs.

## Launcher / 启动器

The default configuration targets Clash Verge and the modern ChatGPT desktop app. If using ChatGPT Classic or another installed application ID, change `chatgpt_app_id` and `chatgpt_package_prefix` together. The launcher does not install software or import proxy profiles.

The Google readiness check uses the Windows system proxy and certificate validation, accepts only the expected HTTPS 204 response, and does not follow captive-portal redirects. A failed check stops new downstream launches. Already-running applications are left running. Use `-CheckOnly -NoGui` for a read-only readiness check; `logs/launcher/latest.json` records the steps.

启动器最多等待配置的联网时长，失败会说明原因。重复双击由互斥锁合并；本地服务健康检查发现端口被别的软件占用时会停止，不结束不属于本项目的进程。只有本人扫码、手机确认、安全验证或首次配置仍需手动。
