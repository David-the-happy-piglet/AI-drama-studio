"""Save a built-in image result under its preplanned shot/version, preserving provenance."""

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from import_external_keyframes import save
from PIL import Image


def record(manifest_path, source_id, generated_path):
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    row = next(r for r in manifest["items"] if r["source_shot_id"] == source_id)
    source = Path(generated_path).resolve()
    dest = (root / row["file"]).resolve()
    if not dest.is_relative_to(root):
        raise ValueError("Output escapes production bundle")
    data = source.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with Image.open(source) as img:
        width, height = img.size
        img.verify()
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != sha:
        raise ValueError("Keep prior render; choose a new versioned filename")
    refs = []
    for relative in row["references"]:
        p = (root / relative).resolve()
        refs.append(
            {"file": relative, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(source, dest)
    row.update(
        status="candidate_ready",
        review_status="pending_user_review",
        sha256=sha,
        width=width,
        height=height,
        generator="built-in-imagegen",
        generated_source=str(source),
        reference_snapshot=refs,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest["review_status"] = (
        "ready"
        if all(r["status"] == "candidate_ready" for r in manifest["items"])
        else "generating"
    )
    save(manifest_path, manifest)
    plan_path = root / "production-plan.json"
    plan = json.loads(plan_path.read_text())
    task = next(t for t in plan["tasks"] if t["id"] == source_id)
    if task.get("file") and task["file"] != row["file"]:
        task.setdefault("history", []).append(
            {k: task.get(k) for k in ["file", "sha256", "status"]}
        )
    task.update(
        file=row["file"],
        sha256=sha,
        status="candidate_ready",
        review_status="pending_user_review",
        batch_id=manifest["batch_id"],
        actual_generation_prompt=row["prompt"],
    )
    save(plan_path, plan)
    print(
        json.dumps(
            {
                "shot": source_id,
                "file": str(dest),
                "size": [width, height],
                "sha256": sha,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument("shot")
    p.add_argument("generated", type=Path)
    a = p.parse_args()
    record(a.manifest, a.shot, a.generated)
