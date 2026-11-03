# AI Drama Studio

面向高质量 AI 短剧的本地、shot-first 制作系统。当前版本包含可操作的 React
制作台、FastAPI 后端、故事简介生成剧本与分镜草稿、分阶段人工审阅、角色与造型资产、
Episode/Scene/Shot 分镜、Seedance 方舟 API / Kling API / MiniMax Token Plan 生成、素材上传、额度消耗二次确认和完整生成历史。

系统不会把整集内容直接作为一个 prompt 发送给视频模型。持久化主链路是：

```text
Project
├── Character ── Outfit
├── Script
└── Episode ── Scene ── Shot ── Generation attempt 1..N
```

每个 `Generation` 都保存调用时的 prompt、negative prompt、references、模型、
seed、时长、分辨率、宽高比、API 参数、Shot 版本、外部任务 ID、成本、状态、错误和
provider 原始响应、输出位置。即使 provider 配置错误，尝试记录仍会保留，单个镜头失败
也不会要求整集重做。

详细设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

新工作流、平台配置、配音/剪辑建议及企业发布渠道调查见
[制作与接入指南](docs/PRODUCTION_AND_INTEGRATIONS.md)。

## 当前实现

- React 19 + Vite 制作台，覆盖项目、故事与审阅、角色、分镜、生成、历史和平台接入页
- 可配置 Chat Completions 文本模型：简介 → 剧本/分镜草稿 → 剧本确认 → 分镜批准 → 正式镜头
- FastAPI + SQLAlchemy 2，开发环境默认 SQLite，容器环境使用 PostgreSQL
- Project / Character / Outfit / Script / Episode / Scene / Shot 持久化 API
- Script、Scene、Shot、Character 的带版本人工编辑，过期写入返回 `409`
- `GET /episodes/{episode_id}/storyboard` 聚合有序分镜
- Provider-neutral `VideoGenerationRequest` 和 `VideoProvider` interface
- Seedance 2.0 通过火山方舟 HTTP API 执行，密钥仅在后端 `.env.local` 配置
- Kling 使用官方 API Key 协议：3.0 文生/首尾帧、Turbo 文生/首帧、Omni 文生/首尾帧/图片主体参考，以及任务查询；未配置时禁止提交
- 角色页支持本地母版 + 多角度图审阅、明确确认后创建可灵主体、任务查询与持久绑定；镜头自动引用就绪角色，缺失/过期时阻止静默退化
- 角色页“母版 prompt · 生成候选图”自动带入已有肖像 prompt，确认后使用 Kling Image 3.0 生成 1–4 张候选图；支持历史、放大选择、本地设为母版，下载重试不重新生图
- 有母版的角色默认“基于母版参考图生成”：实际把 JPG/PNG 图片传给 Image 3.0 Omni，支持角度预设、上传确认、图片快照与 SHA-256 追溯；旧纯文字任务明确标注未参考图片
- 镜头生成页支持“自动生成首帧 / 查看候选”：自动整理单幅静态画面提示词，发送已关联角色的本地母版（最多 3 位）；空镜纯文生图。确认后生成 1–4 张 1K/2K 候选，审阅选定后持久保存并自动用于首帧/首尾帧视频请求，仍需独立确认视频生成
- 项目对应整部剧集，新增分集目录与按集审阅；每集可保存多个剧本稿，批准分镜时复用现有分集
- 本地 deterministic mock provider，用于无密钥打通手动生成、刷新和历史查询
- Docker Compose 使用 PostgreSQL，并以独立 batch worker 执行批量任务；Redis、MinIO 仍为后续能力预留
- API 契约测试覆盖 identity/outfit 隔离、分镜、生成历史、版本冲突和 provider 安全边界

## 目录

```text
ai-drama-studio/
├── backend/
│   ├── app/
│   │   ├── api/                 # REST endpoints and dependencies
│   │   ├── agents/              # Script/Storyboard/QA/Retry boundaries
│   │   ├── providers/
│   │   │   ├── llm/
│   │   │   ├── image/
│   │   │   └── video/           # base, Seedance 方舟 API, Kling HTTP, mock
│   │   ├── models/              # SQLAlchemy domain entities
│   │   ├── services/            # generation application service
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── main.py
│   │   └── schemas.py
│   └── tests/
├── config/providers/            # volatile provider API details
├── data/                         # SQLite 与上传素材（仅本机）
├── docs/ARCHITECTURE.md
├── frontend/                    # React + TypeScript + Vite UI
├── .env.example
└── docker-compose.yml
```

## 本地启动（SQLite，无需 API key）

### 1. Python 虚拟环境

macOS/Homebrew 的 Python 受 PEP 668 保护，不要使用系统级 `pip`，也不要加
`--break-system-packages`。在项目根目录创建 venv；机器上没有 `python3.12` 时直接使用
已经存在的 `python3`：

```bash
cd ai-drama-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements-dev.txt
cd backend
alembic upgrade head
cd ..
```

已有的、尚未带 Alembic 版本号的数据库不能直接执行基线建表；先按
[数据库迁移说明](backend/migrations/README.md) 在备份副本完成
`stamp 20260915_01` → `upgrade head` → `check`。

### 2. 启动后端与 UI

终端 A：

```bash
cd ai-drama-studio
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8100
```

终端 B：

```bash
cd ai-drama-studio
source .venv/bin/activate
cd backend
python -m app.batch_worker
```

终端 C：

```bash
cd ai-drama-studio/frontend
npm install
npm run dev
```

访问：

- 制作台：<http://localhost:3000>
- OpenAPI：<http://localhost:8100/docs>
- 健康检查：<http://localhost:8100/api/v1/health>
- 就绪检查：<http://localhost:8100/api/v1/readyz>
- Video provider 状态：<http://localhost:8100/api/v1/providers/video>

运行测试：

```bash
cd ai-drama-studio/backend
../.venv/bin/python -m pytest
../.venv/bin/ruff check app tests
cd ../frontend
npm run build
```

## 镜头首帧工作台

对话中生成的 GPT 图片可通过[对话生图与批量入库](docs/CONVERSATION_IMAGE_BATCHES.md)
按清单上传并一次绑定多个镜头，无需逐张手工选首帧。导入保留版本与图片指纹校验，
不触发远端生成。

在「镜头生成」选择镜头，点击「自动生成首帧 / 查看候选」。关联角色使用母版图片生成
镜头画面，不是再次生成肖像；不使用视频主体 ID 替代母版。缺少母版会阻止提交，
不会自动降级为纯文字。无人物镜头按文字生成，可在提交前修改提示词和画幅。

候选图只有点击「确认选用此图作为首帧」才成为当前首帧，不修改角色资产、可灵主体、
剧本或对白。视频页自动带入已选首帧；手动上传可覆盖本次输入，不删除已选图片。
镜头或角色素材变化后会标记首帧过期，禁止静默使用。首尾帧模式仍需自行提供尾帧。

首帧与视频是两次独立、可能收费的 API 操作。默认 1 张 1K，不会自动生成整集或视频。
关闭窗口不会取消远端图片任务，重新打开后可查询；下载失败只重试下载，提交超时
保留原任务和幂等标识。输入图片保留不可变快照与 SHA-256，候选图片存于项目
`shot_keyframes` 素材目录。`shot_image_jobs` / `shot_keyframes` 等表结构由 Alembic
迁移管理。

接口：`GET /shots/{id}/keyframe-studio`、`POST /shots/{id}/keyframe-jobs`、
`POST /shots/{id}/keyframe-jobs/{job}/refresh`、`POST /shots/{id}/keyframe-jobs/{job}/select`。
视频请求可携带 `keyframe_selection_token`，后端核对镜头归属、版本、输入摘要及文件哈希，
与 `first_frame` 手动输入互斥。所有路径均省略 `/api/v1` 前缀。

接口依据：[可灵官方 Omni 图片文档](https://kling.ai/document-api/api/image/3-0-omni/image-omni)，
2026-09-07 核对；本功能测试使用本地模拟响应，不代表当前账户的真实图片额度或质量已验证。

## Docker 启动

```bash
cd ai-drama-studio
cp .env.example .env
# 至少修改示例密码；Kling 等真实 API key 只写入未追踪的 .env
docker compose up --build
```

Compose 会先运行 `schema-migrate`，迁移成功后才启动 API 和独立 worker。已有旧版
PostgreSQL 数据卷时，先备份并按
[数据库迁移说明](backend/migrations/README.md) 标记旧基线。

后端监听 `127.0.0.1:8100`，MinIO API/Console 分别监听
`127.0.0.1:9100/9101`。Compose 会同时启动独立 `batch-worker`；扩展 Web 进程
不会额外启动消费者。Redis 和 MinIO 尚未接入业务代码，默认不会启动；需要检查
预留基础设施时使用 `docker compose --profile reserved-infra up --build`。

当前 Docker 镜像没有下载可变的远程 CLI 二进制，也不能直接运行宿主机的 macOS
`dreamina` 文件。因此 Seedance 方舟 API 默认用于本地 venv 后端；容器化前应固定 Linux CLI
版本和校验值，并持久化独立的 OAuth 状态目录。

## 最小 API 流程

1. `POST /api/v1/projects`
2. `POST /api/v1/projects/{project_id}/characters`
3. `POST /api/v1/characters/{character_id}/outfits`
4. `POST /api/v1/projects/{project_id}/scripts`
5. `POST /api/v1/projects/{project_id}/episodes`
6. `POST /api/v1/episodes/{episode_id}/scenes`
7. `POST /api/v1/scenes/{scene_id}/shots`
8. `POST /api/v1/projects/{project_id}/assets`（可选关键帧或参考素材）
9. `POST /api/v1/shots/{shot_id}/generations`，请求体必须包含
   `request_token`（UUID）、`confirmed: true` 和 provider 参数
10. `POST /api/v1/generations/{generation_id}/refresh`
11. `GET /api/v1/generations?shot_id=...`

创建 Shot 时角色绑定采用独立 ID：

```json
{
  "id": "EP001_SC001_SH001",
  "order_index": 1,
  "duration": 6,
  "characters": [
    {
      "character_id": "CHAR_LINXIA_001",
      "outfit_id": "OUTFIT_OFFICE_001"
    }
  ],
  "location_id": "LOC_OFFICE_001",
  "shot_type": "medium_close_up",
  "camera_angle": "eye_level",
  "camera_movement": "slow_push_in",
  "visual_description": "林夏站在夜间会议室长桌尽头。",
  "manual_provider_override": "seedance",
  "generation_mode": "image_to_video"
}
```

人工修改使用乐观版本号，避免页面并发覆盖：

```json
PATCH /api/v1/shots/EP001_SC001_SH001
{
  "expected_version": 1,
  "duration": 4,
  "status": "approved_for_generation"
}
```

## Seedance 2.0 · 火山方舟 API

在 `ai-drama-studio/.env.local` 填写：

```dotenv
ARK_API_KEY=你的方舟API密钥
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_VIDEO_MODEL=doubao-seedance-2-0-260128
```

本地后端和 batch worker 启动时加载 `.env.local`，进程环境优先；修改后重启相关进程。
Docker Compose 使用 `.env`。不要将密钥放入前端或 `VITE_` 变量。
API Key 在[方舟控制台](https://ark.volcengine.com/region:cn-beijing/apikey)创建，
不需要 Access Key / Secret Access Key，也不使用即梦会员的 CLI 登录态。

适配文生视频、首帧、首尾帧及多模态参考；默认模型 Seedance 2.0、720p、有声、无水印。
时长 4–15 秒；支持 480p / 720p / 1080p / 4k。
`api_parameters` 支持布尔参数 `generate_audio`、`watermark`、`return_last_frame`、`camera_fixed`。
本地图片和 MP3/WAV 音频须位于 `DATA_ROOT` 内，会编码为 Base64；参考视频需要公网 URL
或方舟 `asset://` 素材 ID。多模态参考最多 9 张图片、3 段视频和 3 段音频，不能仅传音频。

`POST /contents/generations/tasks` 提交，`GET /contents/generations/tasks/{id}` 查询。
提交超时不自动重发，以免重复计费。网页刷新通过只读任务列表接口核验鉴权，不生成视频，
不把鉴权通过当作模型权限或余额核验。取消排队任务请使用方舟控制台。
旧 CLI 生成历史保留，但旧任务 ID 不能在方舟查询。

协议依据：[创建任务](https://www.volcengine.com/docs/82379/1520757)、
[查询任务](https://www.volcengine.com/docs/82379/1521309)。

## Kling HTTP adapter

在启动后端的终端 `export KLING_API_KEY=...`，并按账号区域配置 `KLING_BASE_URL`：
`https://api-singapore.klingai.com` 或 `https://api-beijing.klingai.com`。
默认模型为 `kling-3.0`，界面可选择 Turbo 和 Omni。后端直接使用官方协议；旧 `config/providers/kling.yaml` 已不参与运行。
本地后端启动时还会加载项目根目录 `.env.local` 中的 provider 环境变量（进程环境优先）。该文件被 Git 忽略，建议权限设为 `600`。修改后需重启后端；不要在 frontend 目录或 VITE_ 变量中放密钥。
本地不自动读取 `.env`；Docker Compose 通过 `env_file` 加载 `.env`。生产数据库等应用 Settings 仍通过进程环境配置。
真实视频生成须用户二次确认计费；余额查询可单独验证鉴权，不能代替各模型真实生成联调。网页会员不视为 API 额度。

自动剧本需要设置 `PLANNING_API_URL`（完整 Chat Completions 地址）、`PLANNING_API_KEY`
和 `PLANNING_MODEL`。未配置时仍可手工新建草稿并审阅。配置后重启后端。

### 外部改稿 → 分镜审阅

Claude 已完成逐镜头分镜时，可直接在“故事与审阅”的“导入 Claude / 外部分镜”
粘贴 JSON 新建审阅稿，不再调用文本模型拆分。交接提示词、格式和当前自动化边界见
[Claude 分镜交接](docs/CLAUDE_STORYBOARD_HANDOFF.md)。

OpenAI Sol 可设置 `PLANNING_API_URL=https://api.openai.com/v1/chat/completions`、
`PLANNING_MODEL=gpt-5.6-sol`。密钥可直接设 `PLANNING_API_KEY`，或使用 `OPENAI_API_KEY`。
也可显式设置 `PLANNING_OPENAI_ENV_FILE` 指向已有本地 `.env`，仅读取其中的 `OPENAI_API_KEY`，
不复制密钥、不加载其他变量。后两种复用方式仅允许发送到上述 OpenAI 官方地址。
Sol 使用 `max_completion_tokens=32000`（含推理）、medium 推理和标准服务层，`store=false`；
不启用自动重试，返回截断内容不会覆盖原稿。API 调用费用以 OpenAI 账单为准。

在「故事与审阅」选择分集及审阅稿，将其他模型修改后的完整剧本粘贴到「剧本审阅」，
点击「根据当前剧本生成分镜」。无需先保存；二次确认后只调用配置的文本模型拆分镜头，
本地保留剧本原文，不采用模型对原文的改写。成功后替换并保存待审分镜、撤销剧本确认；
失败或版本冲突不覆盖原稿。画面、对白、视频生成提示词仍可手工调整。
审核完成后点击「确认剧本并保存」，再「批准分镜」。不会自动调用图片或视频生成。

已发布稿先点击「撤回分镜，重新编辑」。撤回时从当前制作镜头建立审阅稿，保留现场修改、
场景/镜头 ID、角色关联、首帧及视频历史。本集暂停新的制作提交，但不会取消已提交的远端任务。
重新批准按稳定 ID 更新镜头；新增镜头分配新 ID，移除的镜头归档，不删除素材。
模型重拆时可能将大幅改写的镜头视为新镜头，请在批准前核对。旧素材若对应镜头改变会提示复核。
「取消本次修订」恢复原发布状态，已保存修订稿留在审阅历史中。

业务服务不读取 provider-specific 字段，因此未来接入 Vidu、Veo 时只需新增
adapter 和配置，不需要修改 Episode/Scene/Shot 或 generation history 模型。

## MiniMax Token Plan：空镜与补充素材

本地生成页选择 `MiniMax · Token Plan 素材`。当前仅接入历史测试成功的
`MiniMax-Hailuo-2.3`，支持文生、首帧图生：768P 6/10 秒，1080P 6 秒。
不支持此通道的尾帧、多主体/多模态参考、原生声音或自定义 seed。
文生不发送画幅参数；竖屏建议上传竖版首帧，图生画幅由图片决定。
首帧须 JPG/PNG/WebP、小于 20 MB、短边大于 300px、长宽比 2:5–5:2。
负向约束会合并到提示词，总长度上限 2000 字符，关闭自动提示词优化。

在 `.env.local` 设置专用 `MINIMAX_TOKEN_PLAN_KEY`；或者显式设置
`MINIMAX_TOKEN_PLAN_ENV_FILE=/absolute/path/to/ContentGenerator/.env`，
复用旧项目文件中的 `MINIMAX_API_KEY` 与 `MINIMAX_API_BASE`。
仅引用这两个值，不加载旧项目其他环境变量；专用 Key 优先。
不自动使用进程中的通用 `MINIMAX_API_KEY`，也不自动切换 PAYG Key。
`MINIMAX_VIDEO_BASE_URL` 可显式指定官方端点，默认 `https://api.minimaxi.com/v1`。
更改配置后重启后端。密钥留在后端；配置就绪不等于已验证权益或余额。

每日 3 次是用户当前账号声明的权益，**不是本应用核验的剩余次数**。
两个工作台共用同一账号额度；应用未接入官方配额查询，也不伪造每日重置倒计时。
每次提交前需核对订阅 Key、Hailuo 2.3 权益与剩余额度，并勾选确认。
API 调用须携带 `api_parameters.token_plan_confirmed=true`；用途
`material_purpose` 支持 `broll`、`transition`、`supplement`、`test`，随任务保存。
专用变量命名无法验证密钥的套餐类型，也不能阻止平台侧自动补充额度扣费。
不把成本估算为零；超时不自动重试，请到 MiniMax 后台核对任务后再决定。

生成后进入“生成历史”，手动刷新状态并预览候选片段，打开原视频另存到剪辑软件。
每次生成独立保存，不覆盖旧版本、不自动合入成片；可以只剪出满意的 1–2 秒使用。
本轮未内置时间线裁剪或自动合成，不采用素材不代表取消任务或返还额度。
MiniMax 已完成任务仍可刷新，以重新取得可能过期的下载链接。

参考：[文生视频](https://platform.minimaxi.com/docs/api-reference/video-generation-t2v)、
[首帧图生视频](https://platform.minimaxi.com/docs/api-reference/video-generation-i2v)、
[任务查询](https://platform.minimaxi.com/docs/api-reference/video-generation-query)、
[视频下载](https://platform.minimaxi.com/docs/api-reference/video-generation-download)。
本轮通过模拟 HTTP 完成提交/查询/下载与错误路径测试，未提交真实生成任务。

## 批量首帧与视频

### 场景母版

左侧「场景资产」可在项目内创建共享场景母版，上传一张主视角和最多两张补充
视角（JPG/PNG，每张 ≤10 MB，两边至少 300px，宽高比 2:5–5:2），并填写固定
空间布局、家具和光线约束。推荐无人物的空景，补充图应来自同一空间。
本次提供上传与管理，不会凭空生成场景母版，也不会在保存时调用可灵。

到「分镜」，每个场景标题下选择母版并保存关联。同一地点的多个场景甚至不同
分集可共用同一母版；仅允许当前项目资产，不会自动按地点名称猜测关联。
这些关联独立于剧本内容保存，原场景 ID 不变的撤回/重新发布会保留关联。
新建或替换为新 ID 的场景需重新关联。撤回修订期间不能修改场景关联。

单镜头首帧、批量首帧和完整工作流会自动传入角色母版及场景图片。即使没有
人物，仅有场景图片也会使用 Image 3.0 Omni 图像参考；缺失或损坏的母版会拦截
生成，不会静默退回纯文字。确认窗口显示将发送的图片，实际请求使用本地副本
及 SHA-256 校验。人物参考身份，场景参考布局与照明，不复制场景参考中的人物。
当前每镜最多三位角色主图和三张场景图；总提示词仍受接口长度上限约束。

更新母版或重新关联会使相关镜头旧首帧失效，并标记历史视频对应旧镜头版本；
旧图片、输入快照和视频不会删除。排队或等待中的任务会检查版本，避免将旧场景
结果自动接入新视频。文生视频模式不发送场景图，需使用首帧生视频才能通过
首帧继承场景；当前没有额外的视频场景主体 ID 同步或三维空间锁定。

进入「分镜」→「批量制作 · 可灵」，展开选择镜头，支持逐项勾选和全选本集。
先点「批量生成首帧」，核对数量、母版传输和费用，再确认入队。
默认跳过已有可用首帧；新任务完成后自动采用索引 0 的第一张候选。
第一张下载失败不会改选第二张，可到该镜头首帧工作台重试下载并选用。
取消“跳过”后会重新生成并替换已选首帧，旧候选和历史视频仍保留。
生成期间镜头或母版变化时不自动选用过期候选。

首帧完成后点「批量提交视频」，使用各镜头自己的已保存提示词、原时长、
已选首帧及关联主体。单镜头和批量默认「原生音画同步」，允许手动改为无声。
当前批量支持 Kling 3.0 / Turbo / Omni 的文生、首帧图生，时长须为 3–15 整数秒；
首尾帧和多模态输入请逐镜头提交。预检逐项列出缺失素材、角色绑定和参数问题，
只提交通过预检的镜头。视频不会因为已有历史片段而跳过，提交前请核对费用。

批次与任务存入本地数据库，在分镜页显示进度，视频结果在「生成历史」查看。
视频提交后批次继续保持运行态；独立 worker 查询远端状态并将成功成片下载到
`DATA_ROOT/assets/generations/<generation_id>/result.*`，记录 SHA-256 与字节数后才标记
为 `ready`。远端失败、结果不确定或下载失败均不会重新提交付费生成。
当前自动查询和落盘只覆盖批量制作；「镜头生成」中的单条人工任务仍由生成历史页
手动刷新，并保留 provider 返回的结果地址。
原有“批量生成首帧”和“批量提交视频”仍为两个独立确认操作。
新增「首帧 → 自动提交视频」：一次确认同时授权两个阶段，预检会先检查视频
参数与主体绑定，避免首帧付费生成后才发现视频参数不合法。每镜独立推进，
新首帧完成后自动采用第一张，再提交该镜视频，无需等待整批首帧完成。
按设置复用已有可用首帧；文生视频镜头不生成无用首帧，直接提交视频。
首帧失败或结果不确定时，该镜后续视频不提交；其他镜头继续。镜头、已选首帧、
母版或主体绑定被修改后，后续视频会拦截，要求重新审阅。
工作流不会改写正向提示词，包含其中的对白、画外音和旁白指示。
视频阶段等待关系也保存在数据库，重启后继续；重复点击不会重建同一批次。
与已有批次重叠的镜头会在预检中拦截，不自动接管之前单独提交的首帧任务。
独立 worker 顺序提交并查询首帧任务；关闭浏览器不影响执行，但需保持 API 与 worker
运行。任务通过数据库租约领取，可安全启动多个 worker；Web 进程不会隐式启动 worker。
重启可恢复排队和已知首帧查询任务；在提交中被中断的任务标为“结果待核对”，
不自动重新提交。超时或不确定结果请核对远端与生成历史，避免重复付费。
“停止尚未提交的任务”停止排队项及仍在等待首帧的后续视频，不取消已发送任务
或返还费用；已经发出的首帧仍可完成并保存，但不会因此再提交已停止的视频。
相同提交标识可找回原批次，浏览器确认超时后不要新建另一批重复提交。

## Phase 边界

当前 UI 聚焦人工可控的制作链路。尚未实现 Router、PromptCompiler、跨镜头
Continuity QA、Benchmark、TTS、Assembler 和全自动 Episode orchestration。
它们的目录边界与数据接口已经预留，后续仍应保留人工确认关口。

本地测试仍可使用 SQLAlchemy `create_all` 快速创建临时库；Compose 设置
`AUTO_CREATE_SCHEMA=false` 并在进程启动前执行 Alembic。进入共享生产环境前，还需完成
对象存储上传下载、worker 长任务心跳和 provider 沙箱集成测试。
