# 短剧工作台：制作、接入与发布

核验日期：2026-09-05。平台事实引用官方页面；制作顺序和工程拆分是针对本项目的建议，不代表全行业采用同一种流程。

## 本轮交付

- 新增“故事与审阅”：简介输入、文本模型生成剧本和逐镜头分镜、草稿保存、剧本人工确认、分镜人工批准。
- 草稿存在 Script.structured_content 中。批准前不创建正式镜头；批准后在一次数据库事务内建立 Episode/Scene/Shot。重复发布不会重复创建剧集；过期版本返回 409。
- 可手动建立和编辑草稿。自动生成需要配置文本模型，没有密钥时明确显示未配置，不会用假生成结果替代。
- 视频页可选择 Seedance CLI 或可灵官方新版 API。Kling 3.0 支持文生、首帧、首尾帧，3–15 秒、720p/1080p/4k、原生音频开关、已有主体 ID 引用。
- 后续更新：可选择 Kling 3.0、3.0 Turbo 和 3.0 Omni；Turbo 仅文生/首帧、720p/1080p、原生声音；Omni 支持文生、首尾帧、图片/主体参考（参考合计最多 4 个）。已接入多图片主体创建、查询、持久绑定及镜头自动引用；暂未接入视频主体创建、视频参考/编辑、独立音频输入、创建音色或口型同步接口。
- 剪辑工程导出、独立 TTS、自动混音、抖音/小红书发布是下述设计建议，尚未实现。

## 工作流与审阅

项目 → 故事简介 → 剧本/分镜草稿 → 修改剧本并确认 → 修改分镜并批准 → 正式剧集/场景/镜头 → 平台选择 → 生成/选片 → 剪辑与声音 → 成片审阅 → 发布。

自动草稿会读取项目已有角色身份信息。先建立角色母版，通常比生成后再纠正角色更省力。画面、时长、景别、对白、运镜、生成模式均可在审阅页修改。更改剧本会撤销前端确认，保存后再批准当前分镜版本。

### 文本模型配置

后端从进程环境读取以下参数，前端不接触密钥：

```bash
export PLANNING_API_URL="你的完整 Chat Completions HTTPS 地址"
export PLANNING_API_KEY="你的文本模型密钥"
export PLANNING_MODEL="该服务实际可用的模型 ID"
```

接口需要支持 messages、response_format=json_object，并在 choices[0].message.content 返回 JSON。可接兼容该协议的 DeepSeek、方舟或其他服务；模型 ID 按账户控制台选取。本次没有代替用户购买或消耗文本模型/视频额度。配置后在同一终端启动或重启后端，界面检查 /planning/status。

## Seedance：继续用你已有的即梦会员

最短路径是已接入的 Dreamina CLI：

```bash
export PATH="$HOME/.local/bin:$PATH"
dreamina login
dreamina user_credit
dreamina text2video --prompt="雨夜街道，镜头缓慢推进" --model_version=seedance2.5 --duration=5 --ratio=16:9 --video_resolution=720p --poll=30
dreamina query_result --submit_id=你的任务ID --download_dir=./downloads
```

用户自行完成 OAuth 授权。首次视频使用若返回 AigcComplianceConfirmationRequired，按官方指南在即梦网页完成视频生成与确认。[即梦 CLI 指南](https://bytedance.larkoffice.com/wiki/FVTwwm0bGiishxkKOoScdHR2nsg)

Seedance 2.5 官方模型页提供 Get API 入口，说明其支持最长 30 秒的音画联合生成与参考控制。要做常驻服务/批量任务，可以从官方模型页进入火山方舟，创建 API Key、开通模型并复制控制台给出的调用示例。即梦会员、方舟 API 是不同接入渠道，应分别确认账户额度，不能拿 CLI 模型短名直接当成方舟模型 ID。[Seedance 2.5](https://seed.bytedance.com/en/seedance2_5)、[方舟 API Key 管理](https://console.volcengine.com/ark/apiKey)

本工作台这轮保留 CLI 通道，未新增方舟 Seedance HTTP 适配器。

## 可灵：新版 API 与官方命令行 Skill

官方当前同时文档化了新旧两套协议：新版使用 API Key，模型在 URL；旧版 AccessKey/SecretKey 生成 JWT，模型在 model_name。不要混用。国际端点已从 api.klingai.com 迁移为 api-singapore.klingai.com。[官方鉴权文档](https://kling.ai/document-api/api/get-started/authentication.md)

操作：进入可灵开放平台 → 创建 API Key → 确认 API 额度与模型权限 → 在启动后端的终端配置：

```bash
export KLING_API_KEY="你的新版 API Key"
export KLING_BASE_URL="https://api-singapore.klingai.com"
export KLING_MODEL="kling-3.0"
```

中国境内服务按控制台选择官方北京端点 https://api-beijing.klingai.com。界面“平台接入”和“镜头生成”都会显示配置是否就绪；有密钥并不代表账户权限已实际验证，需用你批准的一次任务联调。

本地 provider 配置也可存放在项目根目录 `.env.local`，后端启动时加载，进程环境优先。该文件必须保持 Git 忽略、仅本人可读写（600）；不得把密钥放进前端。更改后重启后端。北京/新加坡区的凭证应匹配各自端点，不能假定跨区通用。GET /account/costs 可免费验证资源包余额；其 remaining_quantity 统计可能延迟 12 小时。

已实现接口：

| 功能 | 新版端点 |
| --- | --- |
| 文生视频 | POST /text-to-video/kling-3.0 |
| 首帧/首尾帧 | POST /image-to-video/kling-3.0 |
| 查询状态 | GET /tasks?task_ids=任务ID |

请求使用 settings.duration、settings.resolution、settings.audio；multi_shot=false，以一个系统镜头对应一个生成片段。本地首尾帧上传后编码为 Base64；可灵当前只接 JPG/PNG，不能把 localhost 素材 URL 发给云端。[文生文档](https://kling.ai/document-api/api/video/3-0-omni/text-to-video.md)、[图生文档](https://kling.ai/document-api/api/video/3-0-omni/image-to-video.md)

可灵也有官方 Kling Skill：文档链接到 clawhub.ai/klingai-dev/klingai，要求 Node.js 18+，脚本入口 kling.mjs，支持视频、图片和主体操作。官方 Skill 页面在 2026-04-01 更新，仍写着 API Key 支持“预计 June”；该页时间承诺不能作为当前实现证明，安装前要以包内 README/help 为准。工作台使用已经核验的新 API，不依赖该 Skill 的旧鉴权流程。[官方 Skill 文档](https://kling.ai/document-api/api/get-started/kling-skills.md)

## 配音：按镜头用途选择，不必二选一

| 场景 | 建议声音路线 | 原因 |
| --- | --- | --- |
| 环境、动作、无关键台词 | 原生声音或后期音效 | 同步性好，替换成本低 |
| 单个角色短台词，表演优先 | 原生音画 + 平台主体/音色绑定 | 嘴型、语气、表演一起生成 |
| 多集固定演员，台词必须准确 | 固定 voice_id 的 TTS/真人配音，独立对白轨 | 可只重录一句，不必重生成画面 |
| 旁白、解说、画外音 | 后期独立 TTS/配音 | 不需要匹配嘴型，便于剪辑改稿 |

可灵 3.0 官方文档描述了人物主体的外观与音色绑定；Seedance 支持参考音视频并联合生成音画。这些产品事实表明原生声音已是可用路线，但不等于长剧所有对白都应该依赖随机原生声音。[可灵 3.0 指南](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide)、[Seedance 2.0 模型说明](https://seed.bytedance.com/en/seedance2_0)

针对你的微调需求，我建议混合制作：主角对白先锁台词和音色，记录独立音频；画面可用音频参考生成，或在画面选片后做口型同步；旁白、BGM、环境音保持分轨。画面已经有明显嘴型时，换一句长度不同的配音可能需要重做 lip-sync，不能仅替换音轨后假定嘴型仍正确。

独立声音资产建议记录 character_id、voice_provider、voice_id、模型版本、发音词典、语速、情绪、台词、音频版本。克隆声音使用本人或已获授权声音。固定 voice_id 主要控制音色，表演情绪、响度和句间连接仍需试听。

## 跨镜头一致性

同一人物并不是同一个 prompt 名称。建议建立版本化角色母版：正面/侧面/半身参考、标志特征、服装、配饰、声音样本和平台主体 ID。每一镜引用同一批批准过的资产。

可灵：角色页先保存正面母版和 1–3 张补充图，审阅后明确确认，通过官方主体 API 创建人物并保存 element_id；后续镜头生成自动引用。创建主体不等于生成视频。官网角色库与 API 账户的角色库互通尚未实测，不依赖官网 ID。Seedance：多模态请求复用同一组人物参考图、音视频样本及约束，固定引用顺序。CLI 上传音频不自动意味着“锁定说话人”，仍需要与提示词和当前模型能力配合。

### 本地主体同步（2026-09-06）

- 官方合同：`POST /v1/general/advanced-custom-elements` 创建；`GET /v1/general/advanced-custom-elements/{id}` 查询，支持创建时给出的 external_task_id。此接口与视频任务 `/tasks` 的结构不同。[官方主体管理文档](https://kling.ai/document-api/api/video/3-0-omni/elements.md)
- 仅上传当前项目素材目录中的实际 JPG/PNG 文件，以 Base64 提交；每张 ≤10 MB、两边 ≥300px、宽高比 2:5–5:2，拒绝同图重复、跨项目路径、目录穿越及逃逸符号链接。
- 本地保存与远端提交分开。远端提交必须 `confirmed: true` 和当前角色版本；重新创建成功绑定须 `replace_existing: true`，不删除旧远端主体。
- 任务先持久化再发送；并发创建会被拦截。超时/异常结果记为 unknown，禁止自动重提；使用原 task_id/external_task_id 查询。确定拒绝或远端任务失败才允许重新创建。
- 弹窗打开时对 submitted/processing 串行查询，约 10 秒一次，最多两分钟；关闭后不后台轮询，重新打开可手动刷新。unknown 仅手动查询；无法找回的未知任务需先向可灵核实，不能通过界面强制重提。
- 新增 `character_reference_assets`、`character_provider_bindings` 表；旧角色表不改列、不重建。保留来源版本、素材摘要及任务 ID，不持久化图片 Base64 或 API Key；响应不暴露账户指纹。
- 绑定按 API Key 指纹和区域隔离；换 Key（包括同账户轮换）或区域后采取保守策略，不自动复用。角色任意资料更新都使旧绑定过期，需重新审阅创建。
- 默认自动绑定镜头的全部角色，缺失、未就绪、过期、跨账户、超限时阻止生成。可以明确关闭自动绑定，使用手工素材。3.0 首帧/首尾帧、Omni 首帧/首尾帧/参考/文生支持此功能；常规文生和 Turbo 不支持。
- 自动附加稳定的 `@char_...` 引用及角色身份对应说明，在生成历史保存实际绑定快照。当前保守限制为最多 3 个主体，Omni 参考模式图片 + 主体总计最多 4 个；母版不自动充当完整镜头首帧。
- 测试使用 HTTP MockTransport，不消耗真实可灵额度。真实账户创建、结果质量及官网可见性仍需用户选择资产并确认后验证。

镜头衔接还要保持时间、位置、服装、光线、道具、视线和运动方向。相邻镜头可把已批准的末帧作为下一镜首帧，但连续传递也可能积累漂移，应定期回到原始母版。相同 seed 不提供跨模型或跨镜头身份保证。尽量在一个连续对话场景中固定视频平台，跨平台切换先做角色对照样片。

## 半自动剪辑：给剪辑师保留可编辑结构

建议工作台负责粗剪数据和资产管理，专业剪辑软件负责节奏与精修。未来导出包应包括逐镜头视频、独立 WAV 对白、音乐/环境音、SRT 字幕、镜头清单与选片记录，以及经导入验证的 XML/FCPXML/AAF 时间线。仅导出合成 MP4 会失去独立镜头和声音轨道。

优先候选是 DaVinci Resolve：剪辑与 Fairlight 声音后期在同一个项目中；也可按你已有习惯使用 Premiere/Final Cut。XML/AAF 交换会有差异，不承诺复杂转场/效果全部往返无损。[Resolve 剪辑页](https://www.blackmagicdesign.com/products/davinciresolve/edit)、[Blackmagic XML/AAF conform 教程](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-17-Colorist-Guide.pdf)

如果你更熟悉剪映，可先按镜头顺序导入视频、独立音轨和 SRT 后调整。当前没有核实剪映官方通用“写入本地草稿”API，不把第三方修改草稿 JSON 的方案视为稳定官方接口。

配音工具可试 ElevenCreative Studio，支持视频、字幕、对白、音乐、音效轨及逐句调整。旧 Voiceover Studio 已宣布在 2026-05-15 停用，应使用新 Studio。[当前 Studio 文档](https://elevenlabs.io/docs/eleven-creative/products/studio)、[旧产品迁移说明](https://elevenlabs.io/docs/eleven-creative/audio-tools/voiceover-studio)

建议顺序：剧本定稿 → 试配/读本 → 分镜节奏 → 生成并选片 → 时间线粗剪 → 对白/口型修正 → 音乐音效及混音 → 字幕校正 → 手动精剪 → 成片审阅。FFmpeg 适合转码、响度处理、代理文件和批量导出；剪辑微调用可编辑时间线完成。

## 抖音和小红书自动发布

### 抖音

有官方服务端接口：先上传得到 video_id，再创建视频。创建权限是 video.create.bind，必须申请能力并获得用户 OAuth 授权；作品发布后还会经过平台审核。[上传接口](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/upload-video)、[创建接口](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-create)

```text
OAuth 授权 → upload_video → video_id → create_video → item_id → 审核/结果同步
```

申请入口：正式网站应用 → 能力管理 → 能力实验室 → 代替用户发布内容到抖音。官方准入说明列出企业、党政机关或事业单位主体，并要求符合政务媒体/企业内容管理等场景。使用规范限定企业内部或签约账号，实时/预设发布仍需用户操作并明确选择同步账号与内容。普通个人账号不应假定能拿到该权限。[准入场景](https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/open-capacity/solutions/Life-Service-Solutions/marketing)、[能力使用规范](https://developer.open-douyin.com/docs/resource/zh-CN/dop/operation-standard/platform-capabilities/usage-spec)

H5/SDK 投稿是另一条能力：拉起抖音发布器由用户完成发布，也要申请，现行规范有主体/资历要求，不能视为零门槛替代。[发布能力规范](https://developer.open-douyin.com/docs/resource/zh-CN/dop/operation-standard/platform-capabilities/useclue)

### 小红书

在公开官方文档中，已确认小程序“发小红书”能力可把视频/图片、文本、话题传入小红书发布器，由用户发布。它依赖小程序载体、主体入驻及应用审核，不是任意服务器对个人账号静默发笔记的 REST API。[官方小程序介绍：笔记运营](https://miniapp.xiaohongshu.com/doc/DC137160)、[小程序平台](https://miniapp.xiaohongshu.com/)

本次没有找到面向普通个人创作者、普遍开放的服务端视频笔记直发接口。未找到不代表任何商业合作渠道都不存在；如你有专业号/企业合作权限，应以后台实际开放能力和官方工单为准。电商订单/商品 API 不能当成笔记发布 API。

### 本项目建议

你已确认拥有企业资质，因此优先按“企业内部内容制作与账号管理”场景申请抖音能力（仅在实际业务相符时）。企业主体认证、正式网站应用、代发布能力审批、具体账号 OAuth 授权是不同环节；营业执照或蓝 V 不自动开通接口。若管理他人账号，还需确认签约关系及授权符合使用规范。

建议申请前整理：主体认证资料、真实业务说明、应用访问地址、账号归属/签约关系说明，以及展示选账号、预览最终内容、确认立即/定时发布和取消操作的界面。此为准备建议，具体必填材料以申请页面为准。小红书企业身份同样不自动赋予笔记服务端发布权限；可向官方提交工单，明确询问“自有/签约账号的视频笔记服务端发布 API、准入要求与授权方式”，避免与电商商品发布混淆。

先做“成片 + 两个平台各自标题、封面、文案、话题”的发布包，人工审阅后上传。抖音权限获批后再接服务端发布队列；小红书先走创作中心人工发布或符合条件的小程序发布器。不要把 Cookie 自动化/第三方 MCP 标记成官方 API。

发布模块应保存目标账号、最终成片版本、用户批准记录、发布时间、平台任务/作品 ID 和状态。超时后先查询再决定重试，避免重复作品。本轮仅完成官方渠道调查，未创建平台应用、绑定账号或发布内容。
