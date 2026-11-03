"""Kling Image 3.0 text-to-image; official contract verified 2026-09-06.

https://kling.ai/document-api/api/image/3-0-omni/image-generation.md
"""

from __future__ import annotations

import base64
import hashlib
import io
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from ..video.base import ProviderResponseError
from ..video.kling import KlingProvider

IMAGE_PATH = "/v1/images/generations"
OMNI_IMAGE_PATH = "/v1/images/omni-image"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Provider result hosts observed in official documentation. Never fetch arbitrary URLs,
# credentials, redirects or private addresses from a task result.
CDN_DOMAINS = ("inkwai.com", "klingai.com", "klingai-cdn-aws.com")


class KlingImageProvider:
    def __init__(self, provider: KlingProvider):
        self.provider = provider

    async def create(self, job):
        source = job.input_snapshot
        if job.model == "kling-v3-omni":
            if not source or source.mode != "reference" or not source.snapshot_path:
                raise ProviderResponseError(
                    "参考图任务缺少已确认的母版快照，禁止退化为纯文生图"
                )
            from ...services.character_elements import validate_images

            content = validate_images(
                self.provider.data_root, job.project_id, [source.snapshot_path]
            )[0]
            if hashlib.sha256(content).hexdigest() != source.reference_sha256:
                raise ProviderResponseError("母版快照校验失败，未发送图片")
            return await self.provider.call(
                "POST",
                OMNI_IMAGE_PATH,
                json={
                    "model_name": "kling-v3-omni",
                    "prompt": source.effective_prompt,
                    "image_list": [
                        {"image": base64.b64encode(content).decode("ascii")}
                    ],
                    "result_type": "single",
                    "n": job.count,
                    "resolution": job.resolution,
                    "aspect_ratio": job.aspect_ratio,
                    "external_task_id": job.id,
                },
            )
        return await self.provider.call(
            "POST",
            IMAGE_PATH,
            json={
                "model_name": "kling-v3",
                "prompt": job.prompt,
                "negative_prompt": job.negative_prompt,
                "n": job.count,
                "resolution": job.resolution,
                "aspect_ratio": job.aspect_ratio,
                "external_task_id": job.id,
            },
        )

    async def query(self, job):
        from urllib.parse import quote

        path = OMNI_IMAGE_PATH if job.model == "kling-v3-omni" else IMAGE_PATH
        return await self.provider.call(
            "GET", f"{path}/{quote(job.task_id or job.id, safe='')}"
        )

    async def create_keyframe(self, job):
        """Official image-omni contract checked 2026-09-07; never use video element IDs implicitly."""
        from ...services.character_elements import validate_images

        body = {
            "model_name": job.model,
            "prompt": job.effective_prompt,
            "n": job.count,
            "resolution": job.resolution,
            "aspect_ratio": job.aspect_ratio,
            "external_task_id": job.id,
        }
        if job.model == "kling-v3-omni":
            if not job.references_snapshot:
                raise ProviderResponseError("缺少已确认的角色/场景母版，禁止退化为纯文生图")
            images = []
            for reference in job.references_snapshot:
                content = validate_images(
                    self.provider.data_root,
                    job.project_id,
                    [reference["snapshot_path"]],
                )[0]
                if hashlib.sha256(content).hexdigest() != reference["sha256"]:
                    raise ProviderResponseError("首帧输入快照校验失败，未发送图片")
                images.append({"image": base64.b64encode(content).decode("ascii")})
            body.update(image_list=images, result_type="single")
            return await self.provider.call("POST", OMNI_IMAGE_PATH, json=body)
        body["negative_prompt"] = job.negative_prompt
        return await self.provider.call("POST", IMAGE_PATH, json=body)

    async def download(
        self,
        url: str,
        project_id: str,
        job_id: str,
        index: int,
        *,
        asset_kind: str = "character_candidates",
    ) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or not any(
                host == domain or host.endswith("." + domain) for domain in CDN_DOMAINS
            )
        ):
            raise ProviderResponseError(
                "返回图片不在已核验的可灵 HTTPS 素材域名中，已阻止下载"
            )
        root = (self.provider.data_root / "assets" / "projects").resolve()
        project_root = (root / project_id).resolve()
        if project_root == root or not project_root.is_relative_to(root):
            raise ProviderResponseError("项目素材路径无效")
        if asset_kind not in {"character_candidates", "shot_keyframes"}:
            raise ProviderResponseError("素材类型无效")
        folder = (project_root / asset_kind / job_id).resolve()
        if not folder.is_relative_to(project_root):
            raise ProviderResponseError("候选图目录无效")
        content = bytearray()
        try:
            # No API credentials, cookies, proxy auth or redirects go to image CDNs.
            async with (
                httpx.AsyncClient(
                    timeout=self.provider.timeout,
                    transport=self.provider.transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code != 200:
                    raise ProviderResponseError(
                        f"候选图下载失败：HTTP {response.status_code}"
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        raise ProviderResponseError(
                            "候选图超过 10 MB，未保存为主体参考图"
                        )
        except httpx.HTTPError as exc:
            raise ProviderResponseError(
                "候选图下载超时或失败；可刷新重试下载，不会重新生成"
            ) from exc
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                if (
                    image.format not in {"PNG", "JPEG"}
                    or min(width, height) < 300
                    or not 0.4 <= width / height <= 2.5
                ):
                    raise ProviderResponseError("候选图格式或尺寸不符合角色参考图要求")
                extension = ".png" if image.format == "PNG" else ".jpg"
                image.verify()
        except (
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as exc:
            raise ProviderResponseError("候选图片损坏或尺寸过大，未保存") from exc
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"candidate-{index}{extension}"
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
            temporary_path.replace(destination)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()
        return destination.relative_to(self.provider.data_root.resolve()).as_posix()
