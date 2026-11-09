# 导演工作流：把错误挡在花钱之前

文字里能发现的错，不留到图上；图上能发现的错，不留到视频上。

## 1. 分镜导演字段 `shot.direction`

| 字段 | 含义 |
| --- | --- |
| `motion_source` | 本镜什么在动：摄影机 / 焦点 / 光线 / 前景 / 人物小动作，至少一项 |
| `screen_direction` | 谁往哪走，按画面左右写 |
| `frame_entry_exit` | 谁进画、谁出画 |
| `expression_beat` | 靠什么表情推进 |
| `transition_out` | 怎么转到下一镜 |
| `first_frame` / `last_frame` | 动作开始 / 结束时的单幅静态画面 |
| `beats` | 逐秒节拍 `[{start, end, text}]` |

文本模型生成剧本与分镜时会按这些要求填写（`services/planning.py` 中的 `DIRECTOR_RULES`）。

## 2. 分镜规则检查（不花钱，不改稿）

- `POST /api/v1/storyboard-lint`：检查未保存的草稿
- `GET /api/v1/scripts/{id}/storyboard-lint`：检查审阅中的稿
- `GET /api/v1/episodes/{id}/storyboard-lint`：检查已发布分镜

规则：平视正面超过 2 镜、连续 3 镜同景别、没写什么在动、固定机位死画面、
镜子/文字/交叉/换光/小道具/群体同步动作/一镜多动作/配角过多/俯拍看不到脸、
台词超时（约 5 字/秒 + 0.5 秒余量）、首尾帧模式缺尾帧描述、开场无钩子。
都是关键词启发式，结果是“再看一眼”，不是定论。

## 3. 七段式提示词

`GET /api/v1/shots/{id}/compiled-prompt` 预览，`POST .../compiled-prompt/apply`
写入 `generation_prompt`（带版本号，可再手改）。段落：画风、镜头、构图与配色、人物、
逐秒、声音、约束。画风与配色来自 `PUT /api/v1/projects/{id}/director-settings`
（`visual_style`、`palette`）。有台词的镜头保留原生对白，只要求非说话人闭嘴。

## 4. 冷读审核

审核模型每次都从零开始，只看被审材料，不看对话历史和作者结论，只挑错不改文件。

- `POST /api/v1/scripts/{id}/reviews` `{"stage": "story"}`：只发剧本正文
- `POST /api/v1/scripts/{id}/reviews` `{"stage": "storyboard"}`：只发分镜表
- `POST /api/v1/shots/{id}/reviews`：提交前审核，发送提示词、分镜行、人物设定、
  前后镜头、已选首帧和角色母版图
- `GET /api/v1/reviews?target_id=...`：历史（只追加）

出现“硬伤”时强制不通过。

## 5. 提交闸门 `REVIEW_GATE`

- `warn`（默认）：照常提交，在 `request_snapshot.metadata.pre_submit_review` 里记录审核状态
- `required`：没有与**当前镜头版本 + 当前提示词 + 当前首帧**对应的“通过”审核就拒绝提交；
  可以填写 `review_override_reason` 人工放行，放行理由会写进记录
- `off`：只记录模式

改了镜头、提示词或首帧，旧的“通过”自动失效。
