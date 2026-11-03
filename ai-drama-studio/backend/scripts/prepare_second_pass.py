"""Build a source-preserving local production queue for 《第二遍》 Markdown."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def prepare(source, output):
    text = source.read_text()
    tables = {}
    for line in text.splitlines():
        if re.match(r"\| [CLOP]\d{2} \|", line):
            cells = [part.strip() for part in line.strip().strip("|").split("|")]
            tables[cells[0]] = cells[1:]
    for prefix, count in (("C", 2), ("O", 14), ("L", 11), ("P", 8)):
        if sum(key.startswith(prefix) for key in tables) != count:
            raise ValueError(f"{prefix} 设定表数量变化，请核对新稿")
    pattern = (
        r"^\*\*(S\d{3})｜场(\d+)｜(\d+)秒｜(.+?)\*\*\n(.*?)(?=^\*\*S\d|^### |^---|\Z)"
    )
    shots = []
    for sid, scene, seconds, source_kind, block in re.findall(
        pattern, text, re.MULTILINE | re.DOTALL
    ):
        fields = dict(re.findall(r"^- ([^：\n]+)：(.*)$", block, re.MULTILINE))
        kind = (
            "video"
            if source_kind.startswith("可灵")
            else "ui"
            if source_kind.startswith("录屏")
            else "reuse"
        )
        binding = fields.get("人物/服装/场景", "")
        characters = re.findall(r"(C\d{2})·(O\d{2})", binding)
        locations = re.findall(r"L\d{2}", binding)
        if sid in {"S054", "S055", "S056", "S059", "S060"}:
            locations = list(dict.fromkeys([*locations, "L11"]))
        same = re.fullmatch(r"同(S\d{3})首帧。", fields.get("首帧", ""))
        shots.append(
            {
                "id": sid,
                "scene_number": int(scene),
                "duration": int(seconds),
                "kind": kind,
                "source_kind": source_kind,
                "fields": fields,
                "characters": [{"id": c, "outfit": o} for c, o in characters],
                "locations": locations,
                "reuse_keyframe_from": same[1] if same else None,
                "status": "planned",
                "studio_shot_id": None,
            }
        )
    if [s["id"] for s in shots] != [f"S{i:03}" for i in range(1, 62)]:
        raise ValueError("镜头编号不完整或重复，未生成清单")
    tasks = []
    for cid in ("C01", "C02"):
        tasks.append(
            {
                "id": cid,
                "kind": "character",
                "requires": [],
                "status": "pending",
                "file": None,
                "prompt": "人物身份母版，单幅写实电影选角照片。设定："
                + "；".join(tables[cid]),
            }
        )
    for oid, spec in tables.items():
        if oid.startswith("O"):
            tasks.append(
                {
                    "id": oid,
                    "kind": "outfit",
                    "requires": [spec[0]],
                    "status": "pending",
                    "file": None,
                    "prompt": "沿用人物母版中的同一人，生成单幅全身服装参考。"
                    + "；".join(spec),
                }
            )
    for lid, spec in tables.items():
        if lid.startswith("L"):
            tasks.append(
                {
                    "id": lid,
                    "kind": "scene",
                    "requires": [],
                    "status": "pending",
                    "file": None,
                    "prompt": "固定空间母版，单幅写实电影场景，不出现人物。严格保持布局，人物站位只作空间规划。"
                    + "；".join(spec),
                }
            )
    tasks.append(
        {
            "id": "CAR01",
            "kind": "vehicle",
            "requires": ["L05"],
            "status": "pending",
            "file": None,
            "prompt": "同一辆深灰色左舵轿车的外观参考，米色织物座椅、黑色中控。用于公路和停车点镜头保持车辆一致，无品牌标志。",
        }
    )
    tasks.append(
        {
            "id": "P08",
            "kind": "photo",
            "requires": ["C01", "C02", "O01", "O02", "L01"],
            "status": "pending",
            "file": None,
            "prompt": "小火车笑脸合照原始完整照片。复用人物、服装、车厢母版。右下边缘必须保留他垂在腿上的手和亮屏的黑色手机；不能裁掉此剧情线索。无 UI。",
        }
    )
    for shot in shots:
        if shot["kind"] != "video":
            continue
        deps = list(
            dict.fromkeys(
                [x for c in shot["characters"] for x in (c["id"], c["outfit"])]
                + shot["locations"]
            )
        )
        if shot["id"] in {"S053", "S061"}:
            deps.append("CAR01")
        if shot["reuse_keyframe_from"]:
            deps = [shot["reuse_keyframe_from"]]
        prompt = "单幅静态电影首帧，保持对应人物、服装、场景参考。不是分镜拼图，不生成字幕或界面文字。\n"
        prompt += "\n".join(
            f"{k}：{v}"
            for k, v in shot["fields"].items()
            if k in {"首帧", "镜头", "连续性", "人物/服装/场景"}
        )
        tasks.append(
            {
                "id": shot["id"],
                "kind": "reuse_keyframe" if shot["reuse_keyframe_from"] else "keyframe",
                "requires": deps,
                "status": "pending",
                "file": None,
                "prompt": prompt,
            }
        )
    known = {t["id"] for t in tasks}
    if any(dep not in known for t in tasks for dep in t["requires"]):
        raise ValueError("生成任务存在未定义依赖")
    seconds = sum(s["duration"] for s in shots)
    queue = {
        "title": "第二遍",
        "source_path": str(source.resolve()),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "aspect_ratio": "16:9",
        "aspect_ratio_status": "source_default_pending_user_preference",
        "generation_route": "conversation_imagegen",
        "project_id": None,
        "episode_id": None,
        "summary": {
            "shots": len(shots),
            "duration": seconds,
            "routes": dict(Counter(s["kind"] for s in shots)),
            "unique_keyframes": sum(t["kind"] == "keyframe" for t in tasks),
        },
        "settings": tables,
        "shots": shots,
        "tasks": tasks,
        "review_notes": [
            "第一遍/第二遍保持同一光位、人物与空间；暖冷调后期统一处理。",
            "S026 复用 S004 首帧。S034/S059 光线发生变化，不能直接使用之前的首帧。",
            "S037 台词较长且仅 4 秒；S043–S051 部分旁白按 4.5 字/秒可能超时，录音后校准时长，不静默删词。",
            "S053 三秒包含公路行驶、入停车点和停车，S040/S052 同样动作密集；实测后考虑拆分。",
            "S017、S019–S021、S027–S029 走 UI 合成；S047 使用剪辑复用，不新建视频生成任务。",
            "生成前须为首帧任务记录 Studio 镜头版本和 context_hash。当前清单尚未绑定 Studio ID。",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "production-plan.json"
    if destination.exists():
        raise ValueError("计划已存在；避免覆盖执行进度，请为新稿使用新目录")
    (output / "source-storyboard.md").write_text(text)
    destination.write_text(json.dumps(queue, ensure_ascii=False, indent=2))
    report = [
        "# 《第二遍》对话生图制作清单",
        "",
        f"原稿：61 镜，{seconds} 秒；53 个视频镜头、7 个 UI 合成镜头、1 个剪辑复用镜头。",
        "",
        "素材基线：2 位人物、14 套服装、11 个空间；另设同车外观和关键合照参考。53 个视频镜头需要 52 张独立首帧，S026 复用 S004。候选重做及场景补充机位另计。",
        "",
        "人物母版先生成候选；后续依赖已选母版的任务按场景分批执行。所有 status=pending 的素材尚未生成。",
        "",
        "自动保存、命名及入库由 Codex 执行。你只需按批查看结果并指出需要调整的图片。",
        "",
        "## 分批安排",
        "",
        "| 批次 | 镜头 | 空间 |",
        "| --- | --- | --- |",
        "| 小火车两遍 | S001–S002、S023–S025 | L01 |",
        "| 厨房餐桌两遍 | S003–S005、S026、S030 | L02 |",
        "| 卧室睡前两遍 | S006、S031–S034 | L03 |",
        "| 超市两遍 | S007–S008、S035–S036 | L04 |",
        "| 海湾步道两遍 | S009–S010、S039–S042 | L06 |",
        "| 分手客厅 | S011–S014 | L07 |",
        "| 分手后生活 | S015–S016、S018、S022 | L08、L03 |",
        "| 黑暗旁白 | S043–S046、S048–S051 | L03 |",
        "| 车内回家 | S037–S038 | L05 |",
        "| 玄关与落日 | S052–S061 | L09、L10、L05、L11 |",
        "",
        "## 制作时需核对",
        "",
    ]
    report += ["- " + note for note in queue["review_notes"]]
    (output / "制作清单.md").write_text("\n".join(report) + "\n")
    return queue["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), ensure_ascii=False))
