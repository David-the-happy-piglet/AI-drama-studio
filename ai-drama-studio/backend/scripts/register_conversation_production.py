"""Register a local production bundle and approved identities via Studio's API.

No image or video provider calls. Writes durable IDs; never updates unrelated work.
"""

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def register(root, api_url):
    root = Path(root).resolve()
    plan_path = root / "production-plan.json"
    plan = json.loads(plan_path.read_text())
    receipt_path = root / "studio-registration.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    project = "SECOND_PASS"
    prefix = "SP_V2"
    if receipt.get("api_url", api_url) != api_url:
        raise ValueError("Registration belongs to another server")
    receipt["api_url"] = api_url
    tasks = {t["id"]: t for t in plan["tasks"]}
    with httpx.Client(base_url=api_url.rstrip("/") + "/", timeout=120) as client:

        def request(method, path, **kw):
            r = client.request(method, path, **kw)
            if r.is_error:
                raise ValueError(f"{method} {path}: {r.status_code} {r.text[:1500]}")
            return r.json()

        def ensure(path, item, known):
            return next((r for r in known if r["id"] == item["id"]), None) or request(
                "POST", path, json=item
            )

        def upload(key, relative, kind):
            uploads = receipt.setdefault("uploads", {})
            if key not in uploads:
                p = root / relative
                uploads[key] = request(
                    "POST",
                    f"projects/{project}/assets",
                    data={"asset_kind": kind},
                    files={"file": (p.name, p.read_bytes(), "image/png")},
                )
                save(receipt_path, receipt)
            return uploads[key]["path"]

        ensure(
            "projects",
            {
                "id": project,
                "name": "第二遍",
                "description": "旅行回忆与第二次观看。Claude 分镜，Codex 对话分批生图，按来源镜号绑定。",
                "settings_json": {
                    "source": "conversation_imagegen",
                    "aspect_ratio": "16:9",
                },
            },
            request("GET", "projects"),
        )
        chars = request("GET", f"projects/{project}/characters")
        character_ids = {}
        for cid in ("C01", "C02"):
            spec = plan["settings"][cid]
            char_id = prefix + "_" + cid
            character_ids[cid] = char_id
            path = upload(cid, tasks[cid]["file"], "character_reference")
            ensure(
                f"projects/{project}/characters",
                {
                    "id": char_id,
                    "name": f"{cid} · {spec[0]}",
                    "gender": "female" if cid == "C01" else "male",
                    "apparent_age": spec[1],
                    "face_description": spec[2],
                    "hairstyle": "黑色中分及胸微卷长发，沿用已确认母版"
                    if cid == "C01"
                    else spec[3],
                    "height": spec[4],
                    "distinctive_features": [spec[5]],
                    "visual_style": "电影写实，人物身份始终与已确认母版一致",
                    "master_reference_path": path,
                },
                chars,
            )
        outfit_ids = {}
        for cid, char_id in character_ids.items():
            existing = request("GET", f"characters/{char_id}/outfits")
            for oid, spec in plan["settings"].items():
                if not oid.startswith("O") or spec[0] != cid:
                    continue
                outfit_id = prefix + "_" + oid
                outfit_ids[oid] = outfit_id
                ensure(
                    f"characters/{char_id}/outfits",
                    {
                        "id": outfit_id,
                        "name": oid + " · " + spec[2][:60],
                        "description": spec[2],
                        "components": [],
                        "reference_paths": [],
                    },
                    existing,
                )
        scenes = OrderedDict()
        for shot in plan["shots"]:
            sid = shot["id"]
            fields = shot["fields"]
            scene = scenes.setdefault(
                shot["scene_number"],
                {
                    "location": shot["scene_heading"].removeprefix("### ").strip(),
                    "time_of_day": "｜".join(shot["scene_heading"].split("｜")[-2:]),
                    "summary": shot["scene_heading"],
                    "shots": [],
                },
            )
            camera = fields.get("镜头", "").split("｜")
            rules = [
                fields.get("连续性", ""),
                f"来源镜号：{sid}；制作路线：{shot['kind']}；成片{shot['duration']}秒；生成{shot['generation_duration']}秒。",
            ]
            rules += tasks.get(sid, {}).get("visual_constraints", [])
            if shot["kind"] != "video":
                rules.append("此镜为界面/剪辑/后期任务，不提交视频生成。")
            dialogue = "\n".join(
                k + "：" + v for k, v in fields.items() if k.startswith("对白")
            )
            scene["shots"].append(
                {
                    "order_index": len(scene["shots"]) + 1,
                    "duration": shot["duration"],
                    "characters": [
                        {
                            "character_id": character_ids[c["id"]],
                            "outfit_id": outfit_ids.get(c["outfit"]),
                        }
                        for c in shot["characters"]
                    ],
                    "shot_type": camera[0] or shot["kind"],
                    "camera_angle": camera[1] if len(camera) > 1 else "",
                    "composition": fields.get("镜头", ""),
                    "camera_movement": camera[-1] if len(camera) > 2 else "",
                    "action": fields["动作"],
                    "dialogue": dialogue,
                    "visual_description": f"[{sid}] " + fields["首帧"],
                    "continuity_requirements": [r for r in rules if r],
                    "generation_prompt": "\n".join(
                        [
                            f"[{sid}]",
                            fields["首帧"],
                            fields["动作"],
                            dialogue,
                            "环境音：" + fields.get("环境音", ""),
                            *rules,
                        ]
                    ),
                    "generation_mode": "image_to_video",
                }
            )
        draft = {
            "screenplay": (root / "source-storyboard.md").read_text(),
            "scenes": list(scenes.values()),
        }
        request_token = str(uuid5(NAMESPACE_URL, str(root) + ":external-storyboard"))
        if not receipt.get("script_id"):
            script = request(
                "POST",
                f"projects/{project}/external-storyboards",
                json={
                    "request_token": request_token,
                    "title": "第二遍 · V2",
                    "source_idea": "新版65镜，244秒。保留微卷长发；S30/S32沿用正面机位且同座并排。",
                    "draft": draft,
                },
            )
            receipt["script_id"] = script["id"]
            save(receipt_path, receipt)
        script = request("GET", f"scripts/{receipt['script_id']}")
        if not script["structured_content"].get("published_episode_id"):
            if not script["structured_content"].get("screenplay_approved"):
                script = request(
                    "PUT",
                    f"scripts/{script['id']}/review",
                    json={
                        "expected_version": script["version"],
                        "draft": draft,
                        "screenplay_approved": True,
                    },
                )
            result = request(
                "POST",
                f"scripts/{script['id']}/publish-storyboard",
                json={"expected_version": script["version"]},
            )
            episode = result["episode_id"]
        else:
            episode = script["structured_content"]["published_episode_id"]
        receipt.update(
            project_id=project,
            episode_id=episode,
            characters=character_ids,
            outfits=outfit_ids,
        )
        save(receipt_path, receipt)
        board = request("GET", f"episodes/{episode}/storyboard")
        flat = [s for scene in board["scenes"] for s in scene["shots"]]
        if len(flat) != len(plan["shots"]):
            raise ValueError("Studio shot count changed; do not map by position")
        mapped = {}
        for scene in board["scenes"]:
            for s in scene["shots"]:
                source = s["visual_description"].split("]", 1)[0].removeprefix("[")
                if source in mapped:
                    raise ValueError("Duplicate source shot")
                mapped[source] = (s, scene)
        scene_assets = request("GET", f"projects/{project}/scene-assets")
        train = next((a for a in scene_assets if a["name"] == "L01 · Dole小火车"), None)
        if not train:
            path = upload("L01", tasks["L01"]["file"], "scene_reference")
            train = request(
                "POST",
                f"projects/{project}/scene-assets",
                json={
                    "name": "L01 · Dole小火车",
                    "description": "；".join(plan["settings"]["L01"])
                    + "。S30/S32沿用正面机位，男女始终并排同座。",
                    "master_reference_path": path,
                },
            )
        train_scene_ids = {
            mapped[s["id"]][1]["id"] for s in plan["shots"] if "L01" in s["locations"]
        }
        for scene_id in train_scene_ids:
            binding = request("GET", f"scenes/{scene_id}/scene-master")
            if binding["asset_id"] != train["id"]:
                request(
                    "PUT",
                    f"scenes/{scene_id}/scene-master",
                    json={
                        "expected_version": binding["scene_version"],
                        "asset_id": train["id"],
                    },
                )
        for shot in plan["shots"]:
            shot["studio_shot_id"] = mapped[shot["id"]][0]["id"]
        plan.update(
            project_id=project, episode_id=episode, studio_script_id=script["id"]
        )
        save(plan_path, plan)
        receipt["shots"] = {s["id"]: s["studio_shot_id"] for s in plan["shots"]}
        receipt["scene_assets"] = {"L01": train["id"]}
        save(receipt_path, receipt)
        print(
            json.dumps(
                {"project_id": project, "episode_id": episode, "shots": len(mapped)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:8100/api/v1")
    args = parser.parse_args()
    register(args.root, args.api_url)
