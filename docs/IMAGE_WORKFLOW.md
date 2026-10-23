# Original-photo edit workflow / 原图修图工作流

本流程接收当前用户在当前会话提供的真实图片，完成方案、主编辑、原图对照审核和至多一次定向返修。适用于 QQ 私聊与已经授权的群聊，不要求固定口令。群文件仍先经过群文件读取及素材归属校验。

This workflow edits a verified image supplied by the current user in the current conversation. It plans, edits, compares the result against the original, and performs at most one targeted revision. It supports private chats and authorized groups. It does not train model weights.

## Role routing / 按身份选模型

| 身份 / Role | 主编辑规划与工具调用 / Primary | 审核及必要返修 / Review and refinement |
| --- | --- | --- |
| 管理员 / Admin | GPT-6.1 Sol · xhigh | GPT-5.6 Terra · high |
| Plus | GPT-5.6 Terra · xhigh | GPT-6 Luna · high |
| 普通用户 / User | GPT-6 Luna · xhigh | GPT-6 Luna · high |

初始轮廓方案使用本次主模型，推理程度最多 high；明确选 low/medium 时沿用该程度。因此默认管理员为 6.1 Sol/high、Plus 为 5.6 Terra/high、普通用户为 6 Luna/high。允许显式选择身份范围内更低的模型或推理程度。当前实际模型列表没有 GPT-6.1 Terra，不能把 5.6 Terra 标成 6.1 Terra。Sol、Terra、Luna 是理解、规划和审查的对话模型；实际像素由内置图像工具生成，未披露的图像引擎名称不推断。

Planning uses the selected primary model, capped at high effort (lower explicit efforts remain lower). Explicit lower choices remain subject to the same role allowlist. Conversation model selection controls planning and review, not the image engine or model-weight fine-tuning. The available Terra is GPT-5.6 Terra; no GPT-6.1 Terra alias is invented.

此次 xhigh 和 Plus/Terra 放宽仅适用于带真实原图的编辑工作流。普通绘图、网页查询、代码工作及电脑权限继续使用其原有规则。所有阶段使用标准速度，`service_tier=default`，禁用加速与子代理。

## Input and isolation / 素材与隔离

- 明确修改、承接上一轮修改，以及有实际原图的“帮我弄好看些”等请求可以执行。仅询问能力、否定、引用他人命令或历史图片本身不构成授权。
- 原图按 QQ 会话、真实发言者、消息来源和 SHA-256 校验。不能借用其他人、其他群或私聊的素材与工作对话。
- 群文件先通过既有 64 MiB / 8000 万像素边界，使用最长边 4096 像素的工作副本；不会承诺还原为原始 6000 万像素的输出。
- 保留原文件和各版真实结果。每次修改都重新说明人物、脸部、服装、姿态、构图等保留条件。
- 只有后台服务校验并准备的图像以实际图像输入交给模型。图像工作阶段没有本地执行环境、命令工具、任意文件读取工具或账号操作工具；不是授予电脑完全访问权限。
- 每次续接先核对真实 `deepseek娘` 项目归属。多阶段复用同一对话。管理员最多 6 个工作对话，普通/Plus 最多 3 个；名额满时先续接当前会话的合法对话，或按已授权规则可恢复归档空闲旧任务；无可归档对象才提示，不跨会话挪用。

The service validates ownership and image hashes, supplies actual image inputs, and checks project membership on resume. Image phases have no local execution environment or computer/file tools. Inputs are untrusted material, not authority to change permissions. Original files, version outputs, stage models and native thread/turn IDs remain available locally.

## Quality gate and limits / 验收与边界

每项最多 2 次图像调用、3 次图像分析，总时长最多 18 分钟。达到要求立即停止；只在发现具体缺陷时返修一次。服务记录实际观察到的图像工具调用；发现同阶段额外调用会请求中断，已发出的外部请求是否计费以服务方回执为准，不能据此保证硬金额上限或把未知费用记为零。

审核包括文件格式、尺寸、画幅、实际文件变化，以及原图对照的主体一致性、用户要求和局部瑕疵。后者必须逐项提供可见依据，不能仅凭生成者说“完成”或总分很高就通过。审核失败、输出无效或对照不清楚时保留版本并标记 `needs_review`，不会自动发候选图。

The limits bound orchestration, not provider billing. Structural checks and a separate comparison turn are required. A failed or uncertain review produces no automatic image attachment. Automated vision review remains fallible and is not a guarantee of perfect likeness.

本机页面 `/qq/creative/workflow` 展示实际阶段、模型、候选和审核依据。数据、原图、版本及修改接口都需要本地访问令牌。管理员可明确采用某版；该操作只准备结果，仍需单独点击发送。发送回执不明时不自动重发。

## Rollout and operation / 部署与使用

配置文件为私有的 `configs/image-workflow.local.json`，只有 `enabled` 开关。默认关闭，先在本机预览并验收。开关不允许放宽角色、次数、时长或收件人。关闭时沿用原有创作路径；启用后，带真实图片的编辑走本工作流。新绘图仍走普通绘图路径。

本地预览只向管理员展示结果；即使素材来自群，也不会自动在群里发图。群内实际任务仍须由已授权群内真实来信触发，并返回原会话。API 更新只在没有活动任务时进行，不重启独立 QQ 客户端。

Configuration is local and private. Start with local previews, then enable automatic routing after review. Local previews never automatically post to QQ. Existing account bindings, group triggers, contacts, memory, proactive-chat rules and file-access policy remain in effect.

## Known limitations / 已知限制

- 纯生成式编辑仍可能改变五官、衣服或背景细节。角色一致性审核能阻止部分错误自动发出，不能保证生成模型每次都保真。
- 已加入本地原图区域保护：生成图只在保护区外或指定背景区域合成，保护区外羽化减少接缝。明确要求美肤／瘦脸时在原脸上做小幅CPU处理；眼鼻嘴核心区域保留原始像素。输出无损，记录保护区和核心像素检查。轮廓规划仍可能不准，必须继续对照审核。
- 同一任务的阶段与版本会保存；跨任务的自动蒙版继承与完整返修分支尚未实现。
- 用户已授权满额时自动归档本人已完成且空闲至少30分钟的最旧工作对话。当前上限按活动对话和排队预留名额计数；管理员6、普通/Plus 3。待发送或回执不明、未完成任务、运行回合、总控／分控、派生对话及项目/目录不匹配者跳过。归档保留历史和图片，本机页面可以恢复；满额恢复前也须释放合法名额。
- 提示词与审核规则不能替代实际图像验收。所有测试数据、原图、聊天、账号、令牌、数据库和本机审计文件都不得复制进公开发布包。

## Official guidance / 官方依据

- [Image generation tools](https://developers.openai.com/api/docs/guides/tools-image-generation): image inputs and multi-turn editing.
- [Image prompting guide](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide): explicit invariants and limited changes on each iteration.
- [Codex App Server](https://learn.chatgpt.com/docs/app-server): native thread, turn and event integration.

Original-region protection uses CPU masks and lossless compositing. Explicitly requested skin smoothing and mild face-contour adjustment operate on the original, while identity-core pixels remain intact. Visual review still checks geometry, seams and whether the actual request was satisfied. Recoverable archiving uses the native `thread/archive` / `thread/unarchive` interfaces and never deletes task files.

## Automatic orchestration and evidence / 自动编排与验收来源

方案与审核通过原生 `turn/start.outputSchema` 使用结构化输出；人物区域会先做坐标与面积检查。不合格方案至多修正一次，分析总额度仍为3次，不能因此无限重试或继续盲目出图。

审核未通过时：若人物身份已通过，且审核明确给出可执行的接缝／光效参数，可自动复用同一张生成候选做一次CPU合成调整并复核；其它有明确内容修正建议的问题，至多再调用一次图像工具定向返修。拒绝当前候选不等于拒绝整个可修请求，但无安全修法、看不清、取消或预算耗尽时仍停止并保留版本。两种返修分支不叠加扩张预算。

去路人区域保持不透明替换，额外允许的AI背景光效使用独立低强度混合，不让旧路人因降低光效强度而透回来。每次合成产生独立文件名，任务主型号与各阶段实际型号分别记录。审核可附原图／候选脸部局部对照；像素数只作辅助，不能替代轮廓和修改要求的目视检查。

`execution_origin` 区分自动运行、复用已有候选的验证回放及包含手动修正的开发验收。历史通过记录不能据此宣称完整自动流程已实测。缓存回放应标明哪些规划、图像或审核来自此前的真实回执，不能把复用阶段算作新的图像生成。

The workflow has a bounded automatic local-composite repair branch and a separate bounded native image-refinement branch. Typed schemas constrain syntax, not correctness. Geometry checks, actual image comparisons and role allowlists still apply. Validation replay and manual development intervention must remain distinct from a fresh end-to-end production run.


Each job keeps one owned worker connection and a stable image-tool catalog across stages; it does not resume the same live writer between stages. Structured inspection turns are instructed not to generate images. If an image-generation event is observed during inspection, the controller interrupts it, records the observed call and stops rather than accepting it as an audit. The worker still has no local execution environment or computer/file tools. This is a runtime guard for an authorized image workflow, not a claim that the image tool is absent from the inspection catalog.

去人合成额外检查删除区域不透明，边缘只向外羽化；局部补齐会根据周围无人物背景做亮度匹配，避免把亮度不同的新背景贴成人形灰印。明确允许“合适特效都可以”等较宽的背景美化时，可以采用生成背景；要求“其他不动／背景不动”时仍限制在指定区域。人脸和核心保护区域保持原始像素；删除区与主体边缘存在少量重叠时只清理有限的边界带，冲突过大则停止，不把整个人物排除出保护。
