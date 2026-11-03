"""Upload and bind an entire local keyframe manifest without any generation calls.

Manifest: {project_id, episode_id, aspect_ratio, items: [{shot_id, file,
expected_version, context_hash, prompt?}]}. File paths are relative to the manifest.
Version/hash must be captured when preparing generation, never silently refreshed.
Default is validation only; --apply uploads and binds. A durable receipt permits
safe resume after interruption. No API keys are read or transmitted by this tool.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def run(manifest_path, api_url, apply=False):
    manifest_path = Path(manifest_path).resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    rows = manifest["items"]
    if not rows or len(rows) > 200 or len({r["shot_id"] for r in rows}) != len(rows):
        raise ValueError("清单须包含 1–200 个不重复的镜头")
    images = []
    for row in rows:
        path = (manifest_path.parent / row["file"]).resolve()
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError("图片须保存在清单目录内")
        data = path.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError(f"{path.name}: 图片超过 10 MB")
        with Image.open(io.BytesIO(data)) as img:
            if (
                img.format not in {"PNG", "JPEG"}
                or min(img.size) < 300
                or not 0.4 <= img.width / img.height <= 2.5
            ):
                raise ValueError(f"{path.name}: 需有效的 JPG/PNG，短边至少 300px")
            img.verify()
        images.append((path, data, hashlib.sha256(data).hexdigest()))
        if not row.get("context_hash") or not row.get("expected_version"):
            raise ValueError("清单缺少生成前的镜头版本和 context_hash，请先核对素材")
    fingerprint = hashlib.sha256(
        raw + "".join(i[2] for i in images).encode()
    ).hexdigest()
    receipt_path = manifest_path.with_suffix(".receipt.json")
    receipt = (
        json.loads(receipt_path.read_text())
        if receipt_path.exists()
        else {
            "fingerprint": fingerprint,
            "api_url": api_url,
            "request_token": str(uuid4()),
            "uploads": {},
        }
    )
    if receipt["fingerprint"] != fingerprint or receipt["api_url"] != api_url:
        raise ValueError("清单、图片或服务器已变化，请另存新清单，保留旧回执")
    if not apply:
        return {"valid": True, "images": len(rows), "applied": False}
    if receipt.get("result"):
        return receipt["result"]
    save(receipt_path, receipt)
    with httpx.Client(base_url=api_url.rstrip("/") + "/", timeout=120) as client:
        items = []
        for row, (path, data, sha) in zip(rows, images, strict=True):
            key = row["shot_id"]
            if key not in receipt["uploads"]:
                response = client.post(
                    f"projects/{manifest['project_id']}/assets",
                    data={"asset_kind": "shot_keyframe"},
                    files={"file": (path.name, data)},
                )
                response.raise_for_status()
                receipt["uploads"][key] = response.json()["path"]
                save(receipt_path, receipt)
            items.append(
                {
                    "shot_id": key,
                    "path": receipt["uploads"][key],
                    "sha256": sha,
                    "expected_version": row["expected_version"],
                    "context_hash": row["context_hash"],
                    "prompt": row.get("prompt", ""),
                }
            )
        response = client.post(
            f"episodes/{manifest['episode_id']}/external-keyframes",
            json={
                "request_token": receipt["request_token"],
                "confirmed": True,
                "source": "codex-imagegen",
                "aspect_ratio": manifest.get("aspect_ratio", "16:9"),
                "items": items,
            },
        )
        response.raise_for_status()
        receipt["result"] = response.json()
        save(receipt_path, receipt)
        return receipt["result"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:8100/api/v1")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.manifest, args.api_url, args.apply), ensure_ascii=False, indent=2
        )
    )
