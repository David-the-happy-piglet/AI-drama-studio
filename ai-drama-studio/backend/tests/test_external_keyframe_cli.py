import io
import json

import httpx
import pytest
from PIL import Image

from scripts.import_external_keyframes import run


def manifest(tmp_path):
    output = io.BytesIO()
    Image.new("RGB", (800, 450), "blue").save(output, format="PNG")
    (tmp_path / "frame.png").write_bytes(output.getvalue())
    path = tmp_path / "frames.json"
    path.write_text(
        json.dumps(
            {
                "project_id": "P",
                "episode_id": "E",
                "items": [
                    {
                        "shot_id": "S",
                        "file": "frame.png",
                        "expected_version": 1,
                        "context_hash": "a" * 64,
                    }
                ],
            }
        )
    )
    return path


def test_dry_run_reads_no_network_and_writes_no_receipt(tmp_path):
    path = manifest(tmp_path)
    assert run(path, "http://example.invalid/api/v1")["valid"]
    assert not path.with_suffix(".receipt.json").exists()


def test_resume_after_binding_failure_reuses_upload_and_token(tmp_path, monkeypatch):
    path = manifest(tmp_path)
    calls, attempts = [], []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/assets"):
            return httpx.Response(200, json={"path": "assets/projects/P/frame.png"})
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(409, json={"detail": "temporary conflict"})
        return httpx.Response(200, json={"items": [{"shot_id": "S"}]})

    client_class = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        run(path, "http://example.invalid/api/v1", True)
    result = run(path, "http://example.invalid/api/v1", True)
    assert attempts[0] == attempts[1]
    assert calls.count("/api/v1/projects/P/assets") == 1
    assert run(path, "http://example.invalid/api/v1", True) == result
    assert len(calls) == 3
    payload = json.loads(path.read_text())
    payload["items"][0]["expected_version"] = 2
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="清单、图片或服务器已变化"):
        run(path, "http://example.invalid/api/v1", True)
