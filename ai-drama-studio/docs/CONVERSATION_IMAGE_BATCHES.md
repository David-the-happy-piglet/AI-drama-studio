# 对话生图与批量入库

Codex 在当前任务内按制作清单逐张调用内置 image_gen；每张成图保存到项目目录，
记录编号、提示词、依赖图、文件指纹及完成状态。批量是任务编排，并非一次请求生成
几十张独立图片；对话工具不是 Studio 可以自行调用的后台 API。工具可用性和用量限制
仍会影响连续执行，已落盘的清单可用于后续接续。

建议先生成角色母版候选，选定后生成换装、场景，再按固定空间成批生成首帧。
同一事件的不同视角共用人物与空间，分开记录机位和动作初态。图片命名与导入由 Codex
处理，用户按批审阅并指出需要调整的图片。

## 《第二遍》

制作包位于 `data/productions/second-pass-v1/`：

- `source-storyboard.md`：原稿副本，原始文件未改动。
- `production-plan.json`：61 镜、设定表、素材依赖和可续跑状态。
- `制作清单.md`：批次安排与需核对事项。
- `assets/characters/`：C01 / C02 母版候选。
- `character-prompts.json`：实际对话生图提示词。

计划中的 `studio_shot_id` 为空表示尚未导入 Studio。`candidate_ready` 代表图片生成成功，
不等于已被选作最终母版。7 个 UI 合成镜头和 1 个复用镜头保持独立制作路线。

## 外部首帧批量绑定

新增 `POST /api/v1/episodes/{episode_id}/external-keyframes`。先用现有项目素材上传
接口保存图片，再一次提交镜头与图片的绑定。此接口不调用任何图片或视频服务。

每条记录携带生成前保存的 `expected_version` / `context_hash`，上传图片路径及
SHA-256。整批先校验，再在一个事务中绑定首帧；任一条无效时不部分选中。相同请求
重试复用导入记录；镜头、人物或场景已变化时要求重新核对。外部图片标记为
`codex-imagegen` 或 `external-image`，不伪装成可灵生图任务，也不假称已验证它使用了哪些参考图。

本地脚本一次完成上传与绑定（由 Codex 执行，无需用户手工逐张上传）：

```bash
cd ai-drama-studio/backend
../.venv/bin/python scripts/import_external_keyframes.py /绝对路径/keyframes.json
../.venv/bin/python scripts/import_external_keyframes.py /绝对路径/keyframes.json --apply
```

清单示例（用实际 Studio ID、生成前版本及摘要替换示例值）：

```json
{
  "project_id": "项目ID",
  "episode_id": "分集ID",
  "aspect_ratio": "16:9",
  "items": [
    {
      "shot_id": "镜头ID",
      "file": "assets/keyframes/S001_v1.png",
      "expected_version": 1,
      "context_hash": "生成前从keyframe-studio取得的64位摘要",
      "prompt": "生成此图实际使用的提示词"
    }
  ]
}
```

文件路径相对清单所在目录，图片须在此目录内。脚本保存 `.receipt.json` 回执：成功
上传的文件不重复上传，绑定请求复用同一标识。回执结果代表当时的导入结果；之后
修改镜头会让已选首帧过期。上传成功但回执未写入时可能留下一份未绑定文件，不会
额外生成图片或视频。更换素材或清单应另存新文件，保留旧回执。

绑定后已选首帧进入现有 Studio 视频生成流程。真实可灵视频仍要求视频通道配置就绪；
现有批量视频默认自动绑定可灵角色主体，因此还需完成角色主体创建。图片参考和
角色主体都不能保证所有镜头完全不漂移，人物脸、手、服装、道具和空间布局仍需检查。
