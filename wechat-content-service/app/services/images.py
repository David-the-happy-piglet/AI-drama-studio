import base64
import json
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw

from ..config import Settings


class ImageProviderError(RuntimeError):
    pass


class ImageProvider(ABC):
    name = "unknown"

    @abstractmethod
    def generate(self, prompt: str, title: str, position: int) -> dict[str, str | None]:
        raise NotImplementedError


class PlaceholderImageProvider(ImageProvider):
    name = "placeholder"

    def __init__(self, settings: Settings):
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")

    def generate(self, prompt: str, title: str, position: int) -> dict[str, str | None]:
        filename = f"{uuid4().hex}.png"
        path = self.output_dir / filename
        colors = [
            ((232, 243, 235), (112, 169, 135)),
            ((255, 241, 220), (210, 158, 99)),
            ((232, 241, 250), (104, 151, 185)),
        ]
        background, accent = colors[position % len(colors)]
        image = Image.new("RGB", (1200, 630), background)
        draw = ImageDraw.Draw(image, "RGBA")
        draw.ellipse((850, -120, 1250, 280), fill=(*accent, 85))
        draw.ellipse((-150, 420, 280, 850), fill=(255, 255, 255, 105))
        draw.rounded_rectangle(
            (180, 175, 1020, 455),
            radius=42,
            fill=(255, 255, 255, 165),
            outline=(*accent, 100),
            width=4,
        )
        draw.line((320, 315, 880, 315), fill=(*accent, 150), width=12)
        image.save(path, format="PNG", optimize=True)
        return {
            "local_path": str(path),
            "source_url": f"{self.public_base_url}/generated-assets/{filename}",
        }


class CompatibleImageProvider(ImageProvider):
    name = "compatible"

    def __init__(self, settings: Settings):
        if (
            not settings.image_api_base
            or not settings.image_api_key
            or not settings.image_model
        ):
            raise ImageProviderError(
                "IMAGE_PROVIDER=compatible requires IMAGE_API_BASE, "
                "IMAGE_API_KEY and IMAGE_MODEL"
            )
        self.api_base = settings.image_api_base.rstrip("/")
        self.api_key = settings.image_api_key
        self.model = settings.image_model
        self.size = settings.image_size
        self.timeout = settings.image_timeout_seconds
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")

    def generate(self, prompt: str, title: str, position: int) -> dict[str, str | None]:
        response = httpx.post(
            f"{self.api_base}/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "prompt": prompt,
                "size": self.size,
                "n": 1,
            },
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
            item = response.json()["data"][0]
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise ImageProviderError(f"Image request failed: {exc}") from exc

        if item.get("url"):
            try:
                image_response = httpx.get(item["url"], timeout=self.timeout)
                image_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ImageProviderError(
                    f"Failed to persist generated image: {exc}"
                ) from exc
            filename = f"{uuid4().hex}.png"
            path = self.output_dir / filename
            path.write_bytes(image_response.content)
            return {
                "local_path": str(path),
                "source_url": f"{self.public_base_url}/generated-assets/{filename}",
            }
        if item.get("b64_json"):
            filename = f"{uuid4().hex}.png"
            path = self.output_dir / filename
            path.write_bytes(base64.b64decode(item["b64_json"]))
            return {
                "local_path": str(path),
                "source_url": f"{self.public_base_url}/generated-assets/{filename}",
            }
        raise ImageProviderError("Image API returned neither url nor b64_json")


class MiniMaxImageProvider(ImageProvider):
    name = "minimax"

    def __init__(self, settings: Settings):
        if not settings.minimax_api_key:
            raise ImageProviderError(
                "MiniMax image generation is not configured. "
                "Fill MINIMAX_API_KEY in .env"
            )
        self.api_base = settings.minimax_api_base.rstrip("/")
        self.api_key = settings.minimax_api_key
        self.model = settings.minimax_image_model
        self.timeout = settings.image_timeout_seconds
        self.aigc_watermark = settings.aigc_watermark
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")

    def generate(self, prompt: str, title: str, position: int) -> dict[str, str | None]:
        try:
            response = httpx.post(
                f"{self.api_base}/image_generation",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "aspect_ratio": "16:9",
                    "response_format": "base64",
                    "n": 1,
                    "prompt_optimizer": True,
                    "aigc_watermark": self.aigc_watermark,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            encoded = response.json()["data"]["image_base64"][0]
            image_bytes = base64.b64decode(encoded)
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f" — {exc.response.text[:500]}"
            raise ImageProviderError(
                f"MiniMax image request failed: {exc}{detail}"
            ) from exc

        filename = f"{uuid4().hex}.jpg"
        path = self.output_dir / filename
        path.write_bytes(image_bytes)
        return {
            "local_path": str(path),
            "source_url": f"{self.public_base_url}/generated-assets/{filename}",
        }


def build_image_provider(settings: Settings) -> ImageProvider:
    if settings.image_provider == "placeholder":
        return PlaceholderImageProvider(settings)
    if settings.image_provider == "compatible":
        return CompatibleImageProvider(settings)
    if settings.image_provider == "minimax":
        return MiniMaxImageProvider(settings)
    raise ImageProviderError(f"Unsupported IMAGE_PROVIDER: {settings.image_provider}")
