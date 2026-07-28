# ContentGenerator

微信公众号健康生活内容生成器。系统按照可配置的受众画像生成标题、正文和配图，
渲染为公众号文章，并预留草稿上传、发布和阅读数据回流能力。

原来的 YouTube 视频摄取原型保留在 `youtube-video-service/`，新的公众号服务位于
`wechat-content-service/`。

## 当前能力

- 内置“中老年养生”和“中年健康管理”两套受众画像，支持在管理界面继续添加和编辑
- 每篇文章生成多个候选标题并选择一个标题
- 生成结构化正文、摘要和配图提示词
- 使用统一模板渲染微信公众号 HTML
- 默认生成本地占位图片，支持接入兼容的文生图 API
- 支持 AI 配图与 Wikimedia Commons 开放许可图片混合使用
- 原生支持 DeepSeek、MiniMax、Kimi，并保留兼容 `/chat/completions` 的通用接口
- 支持同一主题一键生成三篇文章，便于比较标题、正文和排版质量
- 支持 MiniMax `image-01` 生成统一风格的公众号横版配图
- 提供轻量健康风险表达扫描
- 支持微信公众号草稿与发布接口；未配置密钥时使用 mock 模式
- 保存生成过程、发布任务和每日文章指标

## 快速启动

```bash
cp .env.example .env
docker compose up --build
```

启动后访问：

- 管理界面：<http://localhost:8000/admin>
- API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>
- MinIO 控制台：<http://localhost:9001>

默认使用本地 mock 文本生成和占位图片，不需要任何外部密钥。

## 配置三家模型

本地 `.env` 已创建并被 Git 忽略。在该文件中找到下面三行，把 Key 填在等号后：

```dotenv
DEEPSEEK_API_KEY=
MINIMAX_API_KEY=
MOONSHOT_API_KEY=
```

当前默认模型和接口为：

| 厂商 | 文字模型 | API Base |
| --- | --- | --- |
| DeepSeek | `deepseek-v4-pro` | `https://api.deepseek.com` |
| MiniMax | `MiniMax-M2.7` | `https://api.minimaxi.com/v1` |
| Kimi | `kimi-k3` | `https://api.moonshot.cn/v1` |

图片统一使用 MiniMax `image-01`，复用 `MINIMAX_API_KEY`。三篇文章使用同一个
图片模型，能减少对文字模型质量判断的干扰。请求设置
`AIGC_WATERMARK=false`，新生成图片不附加 MiniMax 水印；公众号 HTML 末尾也不
额外添加 AI 辅助生成声明。开启公开自动发布前，应根据适用的平台规则和法规重新确认
AI 内容标识要求。

### 混合使用 AI 与网络图片

推荐使用下面的配置：

```dotenv
IMAGE_PROVIDER=minimax
IMAGE_SOURCE_MODE=mixed
WEB_IMAGE_PROVIDER=openverse
WEB_IMAGE_FALLBACK_TO_AI=true
WEB_IMAGE_USER_AGENT=ContentGenerator/0.1
```

`mixed` 模式保留 AI 封面，并让后续正文图片在 AI 与开放图库之间
交替选择。网络搜索没有合适结果、下载失败或许可不符合要求时，会自动回退到
`IMAGE_PROVIDER`，避免整篇文章生成失败。也可以使用 `ai` 强制全部走文生图，或使用
`web` 强制全部走开放图库。

系统优先通过 Openverse 搜索，Wikimedia Commons 作为备用来源，只接受 JPEG/PNG
且许可标记为 Public Domain、CC0、CC BY 或 CC BY-SA 的图片；图片会下载到本地素材
目录，不直接热链。作者、许可名称、许可链接和原始文件页会保存到数据库，并自动写入
公众号 HTML 的图片来源说明。正式运营前建议将
`WEB_IMAGE_USER_AGENT` 改为包含你的网站或联系邮箱的明确标识，并在发布前人工核对
人物肖像、商标和每张图片文件页上的额外限制。

本地启动：

```bash
source .venv/bin/activate
cd wechat-content-service
uvicorn app.main:app --env-file ../.env
```

启动后可以在管理界面逐个选择模型，或一键运行三模型同题对比：

```bash
cd wechat-content-service
python scripts/generate_comparison.py \
  --topic "过了55岁，晚饭后做哪些小事更有利于睡眠"
```

每篇文章会生成 5 个候选标题、完整排版正文和 3 张横版图片。结果保存在数据库，
可通过管理界面查看；对比命令还会在 `comparison-results/` 保存一份 JSON 索引。

标题吸睛程度可以在管理界面选择 `1–5`：

- `1`：克制可信，偏信息型标题
- `3`：可信度与传播性平衡
- `4`：强吸引，默认推荐
- `5`：最大化数字、反差、悬念和人群代入感

高等级仍禁止编造数字、虚构权威、恐吓、治愈承诺及题文不符。API 请求使用
`headline_intensity` 参数；三模型对比命令可以追加
`--headline-intensity 5`。

生成页面和每日自动计划都可以单独选择文章篇幅：短篇约 800 字、标准约 1200 字、
长篇约 1800 字或深度约 2500 字。不指定时沿用受众画像中的默认字数；API 使用
`target_length`，允许范围为 300–5000 字。目标字数会保存到文章记录，模型提示词会
要求正文尽量控制在目标长度的 90%–110%。

默认启用 `Humanizer-zh` 二次润色。模型先生成结构化文章，再进行一次专门的中文编辑，
减少机械排比、AI 高频词、空泛总结和模板化结尾，同时锁定标题、健康事实与图片提示词。
这会增加一次文字模型调用，可以在管理页面取消勾选，API 传入
`"humanize": false`，或在对比命令中使用 `--no-humanize` 关闭。

也可以直接调用 API：

```bash
curl -X POST http://127.0.0.1:8000/api/articles/compare \
  -H 'Content-Type: application/json' \
  -d '{"topic":"过了55岁，晚饭后做哪些小事更有利于睡眠","headline_intensity":4,"humanize":true}'
```

`GET /api/providers` 只返回模型名和是否已配置，不会返回密钥。

`GET /api/token-usage` 按供应商和模型返回文字生成的输入、输出及总 token。
管理页面会展示同样的统计。计量器接入前的历史调用没有 API usage 数据，会显示为
“—”；此后每次成功的文字生成都会自动累计。

## 修改目标受众

打开管理界面，在“受众画像”中选择：

- `中老年养生`
- `中年健康管理`

可以修改年龄范围、兴趣、写作风格、标题偏好、文章长度和图片风格。文章生成时会动态
读取这些设置，不需要修改代码或 Prompt 文件。

也可以通过 API 修改：

```bash
curl http://localhost:8000/api/audiences
```

## 接入外部 LLM

在 `.env` 中配置：

```dotenv
LLM_PROVIDER=compatible
LLM_API_BASE=https://your-provider.example/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model
```

服务会调用 `${LLM_API_BASE}/chat/completions`，并要求模型返回结构化 JSON。

文生图接口使用相同的适配思路：

```dotenv
IMAGE_PROVIDER=compatible
IMAGE_API_BASE=https://your-provider.example/v1
IMAGE_API_KEY=your-key
IMAGE_MODEL=your-image-model
```

## 接入微信公众号

确认公众号具有草稿和发布接口权限后配置：

```dotenv
WECHAT_ENABLED=true
WECHAT_APP_ID=your-app-id
WECHAT_APP_SECRET=your-app-secret
WECHAT_AUTHOR=公众号作者名
```

默认 `WECHAT_ENABLED=false`。此时上传草稿和发布接口只产生 mock ID，不会影响真实公众号。

## 每日自动生成并进入草稿箱

系统支持下面这条自动化链路：

1. 按设定时间读取“内容大方向”和受众画像
2. 参考最近 30 篇文章自动选择一个不重复的具体主题
3. 自动生成正文、候选标题、摘要和配图
4. 自动执行 Humanizer-zh 润色和健康风险扫描
5. 安全扫描通过后自动上传公众号草稿箱
6. 由运营者在公众号后台检查并手动发布

管理页面的“定时任务列表”会汇总展示全部计划的受众、每日执行时间、模型、篇幅、
草稿箱策略和启用状态，并提供编辑与立即运行入口；详细配置默认折叠，便于同时管理
多个生产计划。

使用 Docker 启动时，`content-scheduler` 服务会和 Web 服务一起启动：

```bash
docker compose up --build -d
docker compose logs -f content-scheduler
```

访问 <http://localhost:8000/admin>，在“每日自动生产”区域：

- 填写内容大方向，例如“睡眠、普通家庭饮食、适度活动；不写疾病治疗”
- 选择文字模型和目标受众
- 设置每天执行时间，默认使用 `Asia/Shanghai`
- 勾选“自动润色”“自动推送草稿箱”和“启用每日执行”
- 保存后可先点“立即运行一次”验证整条链路

任务失败会自动重试，默认最多三次。文章已经生成、但微信草稿上传失败时，
重试会继续上传同一篇文章，不会重复生成。任务状态和错误信息显示在管理页的
“自动任务记录”中。

本地不用 Docker 时，需要分别启动 Web 服务和调度器：

```bash
source .venv/bin/activate
cd wechat-content-service
uvicorn app.main:app --env-file ../.env
```

另开一个终端：

```bash
source .venv/bin/activate
cd wechat-content-service
python -m app.scheduler
```

真实推送前还需要在公众号后台确认 AppID/AppSecret、接口权限和服务器出口 IP 白名单。
建议先保持 `WECHAT_ENABLED=false` 跑一次，此时会完整生成文章并创建 mock 草稿；
确认任务状态为 `completed` 后，再启用真实微信接口。

### Docker 从 SQLite 切换到 PostgreSQL

Docker Compose 会强制让 Web 服务和调度器连接同一个 PostgreSQL，避免两个容器各自
使用独立 SQLite 文件。`DATABASE_URL=sqlite:///...` 只保留给非 Docker 的本地开发。

已有 SQLite 数据时，先从旧 Web 容器备份数据库：

```bash
mkdir -p backups
docker cp \
  contentgenerator-wechat-content-service-1:/app/wechat-content.db \
  backups/wechat-content-before-postgres.db
```

确认 PostgreSQL 目标库为空后，使用迁移脚本复制全部受众、文章、图片、生成记录和
自动任务：

```bash
docker cp \
  backups/wechat-content-before-postgres.db \
  contentgenerator-wechat-content-service-1:/tmp/source.db
docker cp \
  wechat-content-service/scripts/migrate_sqlite_to_postgres.py \
  contentgenerator-wechat-content-service-1:/tmp/migrate.py
docker compose exec -T wechat-content-service sh -lc \
  'PYTHONPATH=/app DATABASE_URL="postgresql+psycopg://content_generator:${DB_PASSWORD:-change-me}@db:5432/${DB_NAME:-content_generator}" python /tmp/migrate.py /tmp/source.db'
```

迁移脚本只接受 PostgreSQL 目标库，并且目标表只要存在任何数据就会停止，不会覆盖或
合并已有数据。成功后重新构建服务：

```bash
docker compose up --build -d
```

文章详情页提供“一键上传到公众号草稿箱”。微信返回 `media_id` 后，系统会调用
`/cgi-bin/draft/get` 再次读取草稿并核对标题；核验成功后状态为 `wechat_draft`。
手动生成文章时也可以勾选“生成完成后立即推送到公众号草稿箱”，让生成、配图和草稿
上传在同一次操作中完成；该选项只创建并核验草稿，不调用正式发布接口。

## 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r wechat-content-service/requirements-dev.txt
cd wechat-content-service
uvicorn app.main:app --reload
```

运行测试：

```bash
cd wechat-content-service
pytest
```
