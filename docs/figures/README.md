# Whale Companion 流程图

核对源码基线：`d7c61fc04043241c654e28210964abf69ef11916`（2026-10-22）。四幅图对应根目录 README 的四个层次，不是未实现功能的路线图。

| 图 | 可编辑源文件 | GitHub 预览 |
|---|---|---|
| 组件与数据边界 | [01-system.drawio](01-system.drawio) | [SVG](01-system.svg) |
| 私聊消息流程 | [02-turn.drawio](02-turn.drawio) | [SVG](02-turn.svg) |
| 人格与记忆 | [03-context.drawio](03-context.drawio) | [SVG](03-context.svg) |
| 任务与送达状态 | [04-tools.drawio](04-tools.drawio) | [SVG](04-tools.svg) |

## 编辑与导出

用 diagrams.net / draw.io 打开 `.drawio` 文件即可编辑所有节点、连接与文字。四幅图使用 Noto Sans CJK SC，未安装时请使用微软雅黑等中文无衬线字体。统一白底、灰蓝层级、正交连线；不要在字体替换后直接发布，应再次检查溢出与线条位置。

初始 `.drawio` 与 SVG 由同一套节点、文本和坐标生成，SVG 为无脚本、无外部字体请求、无 `foreignObject` 的原生文本和几何图形。维护时以 draw.io 源文件为主；修改后请同步导出 SVG（并关闭外部资源依赖），不要只修改 README 中的图像。SVG 是矢量预览，并非运行截图。

## 本次校验范围

- XML / SVG 可解析；每条连接的源和目标节点存在。
- SVG 与 draw.io 的节点文字、位置及连接端点由相同数据生成。
- 四幅 SVG 已通过 Inkscape 渲染为 PNG 并逐幅检查文字、边界和连线。
- README 新增图像链接与图源链接均指向同一提交中的文件。
- 本次只修改文档，不调用真实 DeepSeek / Codex API、不发送 QQ 消息、不运行带个人配置的服务。没有把静态校验说成真实账号端到端验收。

业务代码发生变化后，应重新核对 QQ 入口、上下文装配、权限判定、队列和送达语义；尤其不要把 `accepted`、`completed`、`sent` 或 `partial` 混为一谈。
