"""Prepare the revised storyboard locally, preserving approved identities and V1.

No model, database or video API calls. Source IDs (including S22A) remain stable;
timeline order and generated versus edited durations are represented separately.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

SHOT = re.compile(
    r"^\*\*(S\d{2,3}[A-Z]?)｜场(\d+)｜([^｜]+)｜(.+?)\*\*\s*$", re.MULTILINE
)


def parse(text):
    settings = {}
    for line in text.splitlines():
        if re.match(r"\| [CLOP]\d{2} \|", line):
            cells = [part.strip() for part in line.strip().strip("|").split("|")]
            if cells[0] in settings:
                raise ValueError(f"重复设定 ID：{cells[0]}")
            settings[cells[0]] = cells[1:]
    matches = list(SHOT.finditer(text))
    if not matches or len({m[1] for m in matches}) != len(matches):
        raise ValueError("镜头缺失或重复")
    headers = list(re.finditer(r"^### 场.*$", text, re.MULTILINE))
    shots = []
    for order, match in enumerate(matches, 1):
        source_id, scene, duration, route = match.groups()
        simple = re.fullmatch(r"(\d+)秒", duration)
        trimmed = re.fullmatch(r"成片(\d+)秒（生成(\d+)秒）", duration)
        if not (simple or trimmed):
            raise ValueError(f"不能解析 {source_id} 时长：{duration}")
        kind = next(
            (
                kind
                for prefix, kind in (
                    ("可灵", "video"),
                    ("界面", "ui"),
                    ("剪辑复用", "reuse"),
                    ("后期", "post"),
                )
                if route.startswith(prefix)
            ),
            None,
        )
        if not kind:
            raise ValueError(f"未知制作路线：{route}")
        edit_seconds = int(simple[1] if simple else trimmed[1])
        generate_seconds = (
            int(simple[1] if simple else trimmed[2]) if kind == "video" else None
        )
        if kind == "video" and not 3 <= generate_seconds <= 15:
            raise ValueError(f"{source_id} 生成时长不在 3–15 秒")
        end = matches[order].start() if order < len(matches) else len(text)
        block = text[match.end() : end]
        block = re.split(r"^### |^## |^---", block, maxsplit=1, flags=re.MULTILINE)[0]
        fields = dict(re.findall(r"^- ([^：\n]+)：(.*)$", block, re.MULTILINE))
        if "首帧" not in fields or "动作" not in fields:
            raise ValueError(f"{source_id} 缺少首帧或动作")
        heading = next(
            (h[0] for h in reversed(headers) if h.start() < match.start()), ""
        )
        binding = fields.get("人物/服装/场景", "")
        characters = [
            {"id": c, "outfit": outfit}
            for c, outfit in re.findall(r"(C\d{2})·(O\d{2})", binding)
        ]
        locations = list(dict.fromkeys(re.findall(r"L\d{2}", binding + " " + heading)))
        props = list(dict.fromkeys(re.findall(r"P\d{2}", binding)))
        for cid in re.findall(r"C\d{2}", binding):
            if not any(c["id"] == cid for c in characters):
                characters.append({"id": cid, "outfit": None})
        for cid in characters:
            if cid["id"] not in settings or (
                cid["outfit"] and settings.get(cid["outfit"], [None])[0] != cid["id"]
            ):
                raise ValueError(f"{source_id} 的人物/服装归属不匹配")
        if any(key not in settings for key in locations + props):
            raise ValueError(f"{source_id} 使用了未定义素材")
        shots.append(
            {
                "id": source_id,
                "order_index": order,
                "scene_number": int(scene),
                "scene_heading": heading,
                "duration": edit_seconds,
                "generation_duration": generate_seconds,
                "trim_required": generate_seconds is not None
                and edit_seconds < generate_seconds,
                "kind": kind,
                "source_kind": route,
                "fields": fields,
                "characters": characters,
                "locations": locations,
                "props": props,
                "status": "planned",
                "studio_shot_id": None,
            }
        )
    return settings, shots


def validate_dag(tasks):
    by_id = {t["id"]: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("任务 ID 重复")
    done, active = set(), set()

    def visit(key):
        if key not in by_id:
            raise ValueError(f"缺失任务依赖：{key}")
        if key in active:
            raise ValueError(f"任务依赖成环：{key}")
        if key in done:
            return
        active.add(key)
        for dep in by_id[key]["requires"]:
            visit(dep)
        active.remove(key)
        done.add(key)

    for key in by_id:
        visit(key)


def build_tasks(settings, shots):
    tasks = []

    def add(key, kind, deps=(), **fields):
        task = {
            "id": key,
            "kind": kind,
            "requires": list(dict.fromkeys(deps)),
            "status": "pending",
            "file": None,
            **fields,
        }
        tasks.append(task)
        return task

    for key, spec in settings.items():
        kind = {"C": "character", "O": "outfit", "L": "scene", "P": "prop"}[key[0]]
        add(key, kind, [spec[0]] if key.startswith("O") else [], source_spec=spec)
    by_id = {t["id"]: t for t in tasks}
    # These photographs are the ACTUAL chosen source clips' freeze frames.
    # Do not generate visually similar replacements for downstream first frames.
    photos = {"P01": "S05", "P08": "S12", "P02": "S14"}
    photo_inputs = {"S29": "P01", "S33": "P08", "S36": "P02"}
    for photo, source in photos.items():
        by_id[photo].update(
            kind="freeze_frame",
            requires=["VIDEO_" + source],
            source_shot=source,
            rule="从已选源视频提取实际定格帧；后续首帧引用同一文件与 SHA-256",
        )
    by_id["P03"].update(
        kind="photo_plate",
        requires=["C01", "O03", "L02"],
        rule="菜肴照片保留她的手臂与银手链；裁剪与发送 UI 后期制作",
    )
    by_id["P04"].update(
        kind="ui_asset", requires=["P01", "P02", "P08"], text_verbatim="time of life"
    )
    by_id["P05"].update(kind="ui_asset")
    by_id["P06"].update(kind="ui_asset", requires=["P03"])
    by_id["L03"]["requires"] = ["P09"]
    add(
        "CAR01",
        "vehicle",
        ["L05"],
        rule="同一深灰轿车外观，米色座椅、黑色中控；内外景使用相同车身设定",
    )
    reused_clips = {
        "S49": ["S31", "S35", "S37", "S40", "S46"],
        "S51": ["S22"],
        "S53": ["S23"],
    }
    for shot in shots:
        sid, fields = shot["id"], shot["fields"]
        # Props mentioned incidentally are not necessarily new generated images.
        # In particular, S26's phone glow needs no pre-rendered album screenshot.
        deps = [
            v for c in shot["characters"] for v in (c["id"], c["outfit"]) if v
        ] + shot["locations"]
        props = {
            "S05": ["P10", "P11"],
            "S12": ["P10"],
            "S24": ["P13"],
            "S30": ["P11"],
            "S56": ["P07", "P13", "O10"],
            "S58": ["P10"],
            "S59": ["P10"],
        }.get(sid, [])
        deps += props
        if sid in {"S62", "S63"}:
            deps.append("CAR01")
        if shot["kind"] == "video":
            if sid in photo_inputs:
                add(
                    sid,
                    "photo_keyframe",
                    [photo_inputs[sid]],
                    source_asset=photo_inputs[sid],
                    rule="直接使用定格照片文件，不再次调用生图",
                )
            else:
                prompt = "单幅静态电影首帧，沿用已确认人物与对应服装和空间；对白、字幕、UI 文字不画进首帧。\n"
                prompt += "\n".join(
                    f"{k}：{v}"
                    for k, v in fields.items()
                    if k in {"首帧", "镜头", "连续性", "人物/服装/场景"}
                )
                add(sid, "keyframe", deps, prompt=prompt)
            video = add(
                "VIDEO_" + sid,
                "video",
                [sid],
                duration=shot["generation_duration"],
                edit_duration=shot["duration"],
                trim_required=shot["trim_required"],
                action=fields["动作"],
                dialogue={k: v for k, v in fields.items() if k.startswith("对白")},
                sound=fields.get("环境音", ""),
            )
            if sid == "S42":
                video["rule"] = (
                    "只生成两人状态的关灯动作；换装与他消失在后期黑帧处切换，禁止模型渐变换人"
                )
            add(
                "EDIT_" + sid,
                "edit",
                [video["id"]] + (["S39"] if sid == "S42" else []),
                duration=shot["duration"],
                rule="剪至成片时长；需要的定格、黑帧、字幕与调色后期完成",
            )
        elif shot["kind"] == "ui":
            add(
                "EDIT_" + sid,
                "ui_composite",
                shot["props"],
                duration=shot["duration"],
                source_fields=fields,
            )
        elif shot["kind"] == "reuse":
            add(
                "EDIT_" + sid,
                "reused_edit",
                ["EDIT_" + s for s in reused_clips[sid]],
                duration=shot["duration"],
                source_fields=fields,
            )
        else:
            add(
                "EDIT_" + sid,
                "post_black_or_title",
                duration=shot["duration"],
                source_fields=fields,
            )
    validate_dag(tasks)
    return tasks


def apply_confirmed_direction(plan):
    """Apply the user's V2 replies without changing source descriptions or files."""
    hair = "女主沿用已确认母版的黑色微卷长发、中分及原有人脸；覆盖原稿默认长直发，具体镜头的扎发造型仍按分镜。"
    camera = "S30/S32 取消反打，沿用 S03 正面偏左平视的相机方向和轴线，女主画左靠窗、男主画右，并排同座。保持原机位，可采用已确认正面宽幅参考容纳腿上手机；禁止过肩、背面、侧后机位。男主手机在左手、黑表在左腕、墨镜在头顶。"
    plan["user_overrides"] = [
        {
            "id": "hair",
            "status": "confirmed",
            "answer": "保留已确认母版的微卷长发",
            "rule": hair,
        },
        {
            "id": "train_camera",
            "status": "confirmed",
            "answer": "反打的位置不对，还是保持一个角度吧",
            "rule": camera,
        },
    ]
    female_shots = {
        s["id"] for s in plan["shots"] if any(c["id"] == "C01" for c in s["characters"])
    }
    reasons = {
        "C01": "已确认复用人物身份与微卷长发，覆盖新版默认直发",
        "O01": "白色吊带裙和微卷长发可复用，仍为待核对候选",
        "S03": "旧正面构图与女主微卷长发可参考；男主须补头顶墨镜",
        "S04": "拨发前的旧构图与女主微卷长发可参考；男主须补墨镜",
        "S30": "沿用正面机位与同座位置；手机改左手并补头顶墨镜，反打候选已被用户否决",
        "S31": "已确认微卷长发可复用，仍需核对眼睛三分线与构图",
    }
    for task in plan["tasks"]:
        constraints = []
        if (
            task["id"] == "C01"
            or task["kind"] == "outfit"
            and "C01" in task["requires"]
            or task["id"] in female_shots
            or task["kind"] == "video"
            and task["id"].removeprefix("VIDEO_") in female_shots
        ):
            constraints.append(hair)
        if task["id"] in {"S30", "S32", "VIDEO_S30", "VIDEO_S32"}:
            constraints.append(camera)
        if constraints:
            task["visual_constraints"] = constraints
            # Keep the raw prompt separately so repeated application is idempotent.
            if "prompt" in task:
                task.setdefault("source_prompt", task["prompt"])
                task["prompt"] = (
                    task["source_prompt"]
                    + "\n用户已确认的制作规则（优先于原稿冲突项）：\n"
                    + "\n".join(constraints)
                )
        if task["id"] in reasons and "inherited" in task:
            task["inherited"]["reason"] = reasons[task["id"]]
    for issue in plan["issues"]:
        if issue["id"] in {"hair", "train_camera"}:
            issue.update(
                status="resolved_by_user",
                detail=hair if issue["id"] == "hair" else camera,
            )
    return plan


def prepare(source, previous_root, output, story_source=None):
    if output.exists():
        raise ValueError("输出目录已存在；不得覆盖制作进度")
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    settings, shots = parse(text)
    tasks = build_tasks(settings, shots)
    old_plan = json.loads((previous_root / "production-plan.json").read_text())
    old = {t["id"]: t for t in old_plan["tasks"]}
    current = {t["id"]: t for t in tasks}
    # Explicit semantic mapping, never join revisions by a bare source ID.
    reuse = [
        ("C01", "C01", "approved", "人物身份复用；发型差异单列，不另造人脸"),
        ("C02", "C02", "approved", "人物身份复用；睡觉场景按 O06 去手表"),
        ("O01", "O01", "reuse_candidate", "白色吊带裙可复用；待确定女主发型"),
        ("O02", "O02", "needs_edit", "新版增加头顶墨镜，旧图只能作为换装编辑输入"),
        ("L01", "L01", "candidate_ready", "沿用红车厢、木椅、左窗及光向"),
        (
            "S001",
            "S03",
            "needs_edit",
            "旧正面构图可参考；男主须补头顶墨镜，女主发型待确定",
        ),
        (
            "S002",
            "S04",
            "needs_edit",
            "拨发前的旧构图可参考；男主须补墨镜，女主发型待确定",
        ),
        (
            "S023",
            "S30",
            "needs_edit",
            "新版手机改左手且增加墨镜；正面/反打取舍待用户说明",
        ),
        ("S024", "S31", "reuse_candidate", "侧脸可参考；发型与眼睛三分线需核对"),
    ]
    copies = []
    for old_id, new_id, status, reason in reuse:
        item = old[old_id]
        path = (previous_root / item["file"]).resolve()
        if not path.is_relative_to(previous_root.resolve()):
            raise ValueError("旧版素材路径逃逸")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != item["sha256"] or item["status"] in {
            "rejected",
            "needs_regeneration",
        }:
            raise ValueError(f"旧版素材无效：{old_id}")
        if new_id.startswith("C") and item["status"] != "approved":
            raise ValueError("只能继承已确认人物母版")
        kind = (
            "characters"
            if new_id.startswith("C")
            else "scenes"
            if new_id.startswith("L")
            else "source_inputs"
        )
        relative = f"assets/{kind}/v1_{old_id}{path.suffix}"
        copies.append((relative, data))
        inherited = {
            "revision": "v1",
            "id": old_id,
            "sha256": sha,
            "file": relative,
            "status": item["status"],
            "reason": reason,
        }
        current[new_id].update(status=status, inherited=inherited)
        if status in {"approved", "candidate_ready"}:
            current[new_id].update(file=relative, sha256=sha)
        else:
            current[new_id]["reference_files"] = [relative]
        if status == "approved":
            current[new_id]["approval_note"] = (
                "沿用用户已确认的人物；本轮用户明确人物模型可以复用"
            )
    summary = {
        "shots": len(shots),
        "duration": sum(s["duration"] for s in shots),
        "routes": dict(Counter(s["kind"] for s in shots)),
        "generation_seconds": sum(s["generation_duration"] or 0 for s in shots),
        "short_shots_to_trim": [s["id"] for s in shots if s["trim_required"]],
        "independent_keyframes": sum(t["kind"] == "keyframe" for t in tasks),
        "photo_keyframes": sum(t["kind"] == "photo_keyframe" for t in tasks),
        "characters": sum(k.startswith("C") for k in settings),
        "outfits": sum(k.startswith("O") for k in settings),
        "locations": sum(k.startswith("L") for k in settings),
        "props": sum(k.startswith("P") for k in settings),
    }
    issues = [
        {
            "id": "hair",
            "status": "question_sent",
            "shots": [
                s["id"] for s in shots if any(c["id"] == "C01" for c in s["characters"])
            ],
            "detail": "新版 C01 写长直发，用户已确认母版为微卷。人物脸复用；发型等待选择，保留旧母版原图。",
        },
        {
            "id": "train_camera",
            "status": "question_sent",
            "shots": ["S30", "S32"],
            "detail": "用户之前明确不要改变视角，新稿却又写反打。原始新稿原文保留，正面/反打选择等待回复。",
        },
        {
            "id": "photo_camera",
            "status": "needs_production_resolution",
            "shots": ["S14", "S36"],
            "detail": "P02 是她拍的照片，S14 却是能看见她的侧面机位。制作时应明确快门处切到照片视角还是保留侧面定格，不能把两种机位当同一图片。",
        },
        {
            "id": "S42_blackout",
            "status": "planned_postprocess",
            "shots": ["S42"],
            "detail": "O05→O13、C02→无人在黑帧处后期切换；不能要求图生视频在亮处变衣或消失。末状态参考 S39。",
        },
        {
            "id": "album_name",
            "status": "source_inconsistency",
            "shots": ["S26", "S27"],
            "detail": "完整剧本写相册名‘人生时刻’，P04 写 time of life。界面任务暂保留 P04 的逐字文本，并记录差异。",
        },
        {
            "id": "S16_S40",
            "status": "source_inconsistency",
            "shots": ["S16", "S40"],
            "detail": "S16 连续性说构图供 S40 沿用，S40 细分镜头却是头顶斜拍脸部。空间和卧姿共用，具体机位按细分镜头准备，不把同空间等同同构图。",
        },
        {
            "id": "timing",
            "status": "needs_voice_timing",
            "shots": ["S25", "S46", "S49", "S54", "S61"],
            "detail": "逐镜时长合计244秒无误；旁白、打字删除与多动作镜头需实测。S49五段各约2.5秒加四个0.3秒黑帧为13.7秒，13秒成片需缩短片段，不直接叠加。",
        },
    ]
    plan = {
        "title": "第二遍",
        "revision": "v2",
        "source_path": str(source.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "previous_revision": str(previous_root.resolve()),
        "generation_route": "conversation_imagegen",
        "aspect_ratio": "16:9",
        "project_id": None,
        "episode_id": None,
        "summary": summary,
        "settings": settings,
        "shots": shots,
        "tasks": tasks,
        "issues": issues,
        "rules": [
            "新版分镜的剧本、对白、旁白逐字保留；不因旧故事版自动恢复已删旁白。",
            "S22A 保留为来源编号，顺序另存 order_index；绝不通过改号破坏复用引用。",
            "旧版人物审批可继承，旧版首帧不能因编号相似自动选中。",
            "L09/L10/L11 与 O11/O12、P 系列均有语义变更；按内容映射。",
            "素材候选、已批准母版、视频生成完成是不同状态。当前没有提交新视频。",
        ],
    }
    apply_confirmed_direction(plan)
    # Complete validation and read inherited images before creating the new bundle.
    output.mkdir(parents=True)
    (output / "source-storyboard.md").write_bytes(raw)
    if story_source:
        story_raw = story_source.read_bytes()
        (output / "source-story-v2.txt").write_bytes(story_raw)
        plan["story_reference"] = {
            "path": str(story_source.resolve()),
            "sha256": hashlib.sha256(story_raw).hexdigest(),
            "role": "对照材料；发生叙事差异时不覆盖新版分镜",
        }
    for relative, data in copies:
        p = output / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    if (previous_root / "character-prompts.json").is_file():
        (output / "inherited-character-prompts.json").write_bytes(
            (previous_root / "character-prompts.json").read_bytes()
        )
    (output / "production-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2)
    )
    lines = [
        "# 《第二遍》V2 制作清单与复用报告",
        "",
        "新版逐镜核对：65镜、244秒；57个视频镜头、3个界面合成、3个剪辑复用、2个黑场/字幕。",
        "",
        "人物2位、服装19套、场景15个、道具/界面13项。57个视频任务中，54个需要独立首帧；另外3个直接继承照片定格帧。",
        "",
        "7个短镜头生成后再剪短。视频请求合计214秒（不含重试）；这与244秒剪辑总长不是同一个数。",
        "",
        "本轮只迁移制作计划与可复用素材，未调用图片/视频服务，未写入 Studio 数据库。",
        "",
        "## 人物与已有素材",
        "",
        "| V1来源 | V2用途 | 处理 |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {old_id} | {new_id} | {current[new_id]['inherited']['reason']} |"
        for old_id, new_id, _, reason in reuse
    ]
    lines += [
        "",
        "旧 S025 背后机位已失效，不加入新版可用素材。旧版文件与进度全部保留。",
        "",
        "## 关键变化",
        "",
        "- 开头新增公路、相拥、沙滩、星空、营地、夜市和海狮闪现；结尾用同车同地的空副驾对照。",
        "- 第一遍明确拍照，第二遍从照片继续；P01/P08/P02 必须来自实际定格帧，不能另生成近似照片。",
        "- 新增‘现在’的卧室/超市、分手时未听完的台词；按新版保存旁白与表演层次。",
        "- 男主旅行装增加头顶墨镜，小火车手机统一在左手；旧 S023 修正版是右手持机，需修改。",
        "- 卧室窗改到床左侧，床头灯和女主移到床右侧；睡觉时男主不戴手表。",
        "- O11/O12 的角色归属对调；旧 L09玄关并入新L07，旧L10公路→新L09，旧L11观景点→新L10，新L11是沙滩。",
        "",
        "## 制作依赖",
        "",
        "| 照片 | 取得方式 | 后续首帧 |",
        "| --- | --- | --- |",
        "| P01 | S05实际定格帧 | S29 |",
        "| P08 | S12实际定格帧 | S33 |",
        "| P02 | S14实际定格帧（需明确照片机位） | S36 |",
        "",
        "S49复用S31/S35/S37/S40/S46，S51复用S22，S53复用S23。S42在黑帧处切换两人/独处状态。",
        "",
        "## 制作规则与待核对项",
        "",
    ]
    lines += ["- " + issue["detail"] for issue in issues]
    lines += [
        "",
        "## 镜头顺序与时长",
        "",
        "| 顺序 | 来源镜号 | 路线 | 成片秒数 | API生成秒数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {s['order_index']} | {s['id']} | {s['kind']} | {s['duration']} | {s['generation_duration'] or '—'} |"
        for s in shots
    ]
    (output / "制作清单与复用报告.md").write_text("\n".join(lines) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--story-source", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.source, args.previous, args.output, args.story_source),
            ensure_ascii=False,
        )
    )
