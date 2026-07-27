import base64
import html
import io
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw

from ..config import Settings


class ImageProviderError(RuntimeError):
    pass


class ImageProvider(ABC):
    name = "unknown"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
        raise NotImplementedError


class PlaceholderImageProvider(ImageProvider):
    name = "placeholder"

    def __init__(self, settings: Settings):
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
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
            "source_kind": "placeholder",
        }


class CompatibleImageProvider(ImageProvider):
    name = "compatible"

    def __init__(self, settings: Settings):
        if not settings.image_api_base or not settings.image_api_key or not settings.image_model:
            raise ImageProviderError(
                "IMAGE_PROVIDER=compatible requires IMAGE_API_BASE, IMAGE_API_KEY and IMAGE_MODEL"
            )
        self.api_base = settings.image_api_base.rstrip("/")
        self.api_key = settings.image_api_key
        self.model = settings.image_model
        self.size = settings.image_size
        self.timeout = settings.image_timeout_seconds
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
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
                raise ImageProviderError(f"Failed to persist generated image: {exc}") from exc
            filename = f"{uuid4().hex}.png"
            path = self.output_dir / filename
            path.write_bytes(image_response.content)
            return {
                "local_path": str(path),
                "source_url": f"{self.public_base_url}/generated-assets/{filename}",
                "source_kind": "ai",
            }
        if item.get("b64_json"):
            filename = f"{uuid4().hex}.png"
            path = self.output_dir / filename
            path.write_bytes(base64.b64decode(item["b64_json"]))
            return {
                "local_path": str(path),
                "source_url": f"{self.public_base_url}/generated-assets/{filename}",
                "source_kind": "ai",
            }
        raise ImageProviderError("Image API returned neither url nor b64_json")


class MiniMaxImageProvider(ImageProvider):
    name = "minimax"

    def __init__(self, settings: Settings):
        if not settings.minimax_api_key:
            raise ImageProviderError(
                "MiniMax image generation is not configured. Fill MINIMAX_API_KEY in .env"
            )
        self.api_base = settings.minimax_api_base.rstrip("/")
        self.api_key = settings.minimax_api_key
        self.model = settings.minimax_image_model
        self.timeout = settings.image_timeout_seconds
        self.aigc_watermark = settings.aigc_watermark
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
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
            raise ImageProviderError(f"MiniMax image request failed: {exc}{detail}") from exc

        filename = f"{uuid4().hex}.jpg"
        path = self.output_dir / filename
        path.write_bytes(image_bytes)
        return {
            "local_path": str(path),
            "source_url": f"{self.public_base_url}/generated-assets/{filename}",
            "source_kind": "ai",
        }


def _plain_metadata(value: str | None) -> str:
    if not value:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


class WikimediaImageProvider(ImageProvider):
    name = "wikimedia"
    allowed_mime_types: ClassVar[dict[str, str]] = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }

    def __init__(self, settings: Settings):
        self.api_url = settings.wikimedia_api_url
        self.timeout = settings.image_timeout_seconds
        self.max_bytes = settings.web_image_max_bytes
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.headers = {"User-Agent": settings.web_image_user_agent}

    @staticmethod
    def _metadata_value(metadata: dict, key: str) -> str:
        item = metadata.get(key, {})
        return str(item.get("value", "")) if isinstance(item, dict) else ""

    @staticmethod
    def _license_is_supported(license_name: str) -> bool:
        normalized = license_name.lower().replace("_", " ")
        return any(
            marker in normalized for marker in ("cc by", "cc0", "public domain", "pdm")
        ) and not any(
            marker in normalized for marker in ("noncommercial", "non-commercial", "no derivatives")
        )

    def _search(self, query: str) -> list[dict[str, str]]:
        response = httpx.get(
            self.api_url,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 12,
                "prop": "imageinfo",
                "iiprop": "url|mime|size|extmetadata",
                "iiurlwidth": 1600,
                "format": "json",
                "formatversion": 2,
            },
            headers=self.headers,
            follow_redirects=True,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", [])
        except (httpx.HTTPError, json.JSONDecodeError, AttributeError) as exc:
            raise ImageProviderError(f"Wikimedia image search failed: {exc}") from exc

        candidates: list[dict[str, str]] = []
        for page in pages:
            image_info = (page.get("imageinfo") or [{}])[0]
            mime = image_info.get("mime", "")
            metadata = image_info.get("extmetadata") or {}
            license_name = _plain_metadata(
                self._metadata_value(metadata, "LicenseShortName")
                or self._metadata_value(metadata, "UsageTerms")
            )
            if mime not in self.allowed_mime_types or not self._license_is_supported(license_name):
                continue
            download_url = image_info.get("thumburl") or image_info.get("url")
            if not download_url:
                continue
            candidates.append(
                {
                    "download_url": str(download_url),
                    "mime": str(mime),
                    "source_page_url": str(
                        image_info.get("descriptionurl")
                        or image_info.get("descriptionshorturl")
                        or ""
                    ),
                    "source_author": _plain_metadata(
                        self._metadata_value(metadata, "Artist")
                        or self._metadata_value(metadata, "Credit")
                    ),
                    "license_name": license_name,
                    "license_url": _plain_metadata(self._metadata_value(metadata, "LicenseUrl")),
                }
            )
        return candidates

    def _download(self, candidate: dict[str, str], position: int) -> dict[str, str | None]:
        try:
            response = httpx.get(
                candidate["download_url"],
                headers=self.headers,
                follow_redirects=True,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageProviderError(f"Wikimedia image download failed: {exc}") from exc
        content = response.content
        if not content or len(content) > self.max_bytes:
            raise ImageProviderError("Wikimedia image is empty or exceeds WEB_IMAGE_MAX_BYTES")
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise ImageProviderError("Wikimedia returned an invalid image") from exc

        suffix = self.allowed_mime_types[candidate["mime"]]
        filename = f"{uuid4().hex}-{position}{suffix}"
        path = self.output_dir / filename
        path.write_bytes(content)
        return {
            "local_path": str(path),
            "source_url": f"{self.public_base_url}/generated-assets/{filename}",
            "source_kind": "web",
            "source_page_url": candidate["source_page_url"],
            "source_author": candidate["source_author"] or "Wikimedia Commons contributor",
            "license_name": candidate["license_name"],
            "license_url": candidate["license_url"],
        }

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
        query = (search_query or title).strip()
        candidates = self._search(query)
        if not candidates and query != title:
            candidates = self._search(title)
        if not candidates:
            raise ImageProviderError(f"No reusable Wikimedia image found for query: {query}")
        last_error: ImageProviderError | None = None
        for candidate in candidates:
            try:
                return self._download(candidate, position)
            except ImageProviderError as exc:
                last_error = exc
        raise last_error or ImageProviderError("No Wikimedia image could be downloaded")


class OpenverseImageProvider(ImageProvider):
    name = "openverse"

    def __init__(self, settings: Settings):
        self.api_url = settings.openverse_api_url.rstrip("/")
        self.timeout = settings.image_timeout_seconds
        self.max_bytes = settings.web_image_max_bytes
        self.output_dir = Path(settings.generated_assets_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.headers = {"User-Agent": settings.web_image_user_agent}

    @staticmethod
    def _license_name(item: dict) -> str:
        license_code = str(item.get("license") or "").lower()
        license_version = str(item.get("license_version") or "").strip()
        names = {
            "cc0": "CC0",
            "by": "CC BY",
            "by-sa": "CC BY-SA",
            "pdm": "Public Domain Mark",
        }
        name = names.get(license_code, license_code.upper())
        return f"{name} {license_version}".strip()

    def _search(self, query: str) -> list[dict[str, str]]:
        response = httpx.get(
            f"{self.api_url}/images/",
            params={
                "q": query[:200],
                "license": "cc0,by,by-sa,pdm",
                "page_size": 12,
                "category": "photograph",
                "aspect_ratio": "wide",
                "mature": "false",
            },
            headers=self.headers,
            follow_redirects=True,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
            results = response.json().get("results", [])
        except (httpx.HTTPError, json.JSONDecodeError, AttributeError) as exc:
            raise ImageProviderError(f"Openverse image search failed: {exc}") from exc

        candidates = []
        for item in results:
            download_url = item.get("thumbnail")
            source_page_url = (
                item.get("foreign_landing_url")
                or item.get("detail_url")
                or item.get("url")
            )
            license_url = item.get("license_url")
            if not download_url or not source_page_url or not license_url:
                continue
            candidates.append(
                {
                    "download_url": str(download_url),
                    "source_page_url": str(source_page_url),
                    "source_author": _plain_metadata(
                        str(item.get("creator") or "Openverse contributor")
                    ),
                    "license_name": self._license_name(item),
                    "license_url": str(license_url),
                }
            )
        return candidates

    def _download(
        self,
        candidate: dict[str, str],
        position: int,
    ) -> dict[str, str | None]:
        try:
            response = httpx.get(
                candidate["download_url"],
                headers=self.headers,
                follow_redirects=True,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageProviderError(f"Openverse image download failed: {exc}") from exc
        content = response.content
        if not content or len(content) > self.max_bytes:
            raise ImageProviderError(
                "Openverse image is empty or exceeds WEB_IMAGE_MAX_BYTES"
            )
        try:
            with Image.open(io.BytesIO(content)) as image:
                image_format = image.format
                image.verify()
        except (OSError, ValueError) as exc:
            raise ImageProviderError("Openverse returned an invalid image") from exc
        suffix = ".png" if image_format == "PNG" else ".jpg"
        filename = f"{uuid4().hex}-{position}{suffix}"
        path = self.output_dir / filename
        path.write_bytes(content)
        return {
            "local_path": str(path),
            "source_url": f"{self.public_base_url}/generated-assets/{filename}",
            "source_kind": "web",
            "source_page_url": candidate["source_page_url"],
            "source_author": candidate["source_author"],
            "license_name": candidate["license_name"],
            "license_url": candidate["license_url"],
        }

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
        query = (search_query or title).strip()
        candidates = self._search(query)
        if not candidates and query != title:
            candidates = self._search(title)
        if not candidates:
            raise ImageProviderError(
                f"No reusable Openverse image found for query: {query}"
            )
        last_error: ImageProviderError | None = None
        for candidate in candidates:
            try:
                return self._download(candidate, position)
            except ImageProviderError as exc:
                last_error = exc
        raise last_error or ImageProviderError("No Openverse image could be downloaded")


class FallbackWebImageProvider(ImageProvider):
    name = "web-fallback"

    def __init__(self, *providers: ImageProvider):
        self.providers = providers

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
        last_error: ImageProviderError | None = None
        for provider in self.providers:
            try:
                return provider.generate(prompt, title, position, search_query)
            except ImageProviderError as exc:
                last_error = exc
        raise last_error or ImageProviderError("All web image providers failed")


class MixedImageProvider(ImageProvider):
    name = "mixed"

    def __init__(
        self,
        ai_provider: ImageProvider,
        web_provider: ImageProvider,
        fallback_to_ai: bool,
    ):
        self.ai_provider = ai_provider
        self.web_provider = web_provider
        self.fallback_to_ai = fallback_to_ai

    def generate(
        self,
        prompt: str,
        title: str,
        position: int,
        search_query: str | None = None,
    ) -> dict[str, str | None]:
        # Keep the cover generated for visual consistency; use searched images
        # for every other inline position.
        if position % 2 == 0:
            return self.ai_provider.generate(prompt, title, position, search_query)
        try:
            return self.web_provider.generate(prompt, title, position, search_query)
        except ImageProviderError:
            if not self.fallback_to_ai:
                raise
            return self.ai_provider.generate(prompt, title, position, search_query)


def _build_ai_provider(settings: Settings) -> ImageProvider:
    if settings.image_provider == "placeholder":
        return PlaceholderImageProvider(settings)
    if settings.image_provider == "compatible":
        return CompatibleImageProvider(settings)
    if settings.image_provider == "minimax":
        return MiniMaxImageProvider(settings)
    raise ImageProviderError(f"Unsupported IMAGE_PROVIDER: {settings.image_provider}")


def _build_web_provider(settings: Settings) -> ImageProvider:
    if settings.web_image_provider == "openverse":
        return FallbackWebImageProvider(
            OpenverseImageProvider(settings),
            WikimediaImageProvider(settings),
        )
    if settings.web_image_provider == "wikimedia":
        return WikimediaImageProvider(settings)
    raise ImageProviderError(f"Unsupported WEB_IMAGE_PROVIDER: {settings.web_image_provider}")


def build_image_provider(settings: Settings) -> ImageProvider:
    ai_provider = _build_ai_provider(settings)
    if settings.image_source_mode == "ai":
        return ai_provider
    web_provider = _build_web_provider(settings)
    if settings.image_source_mode == "web":
        return web_provider
    if settings.image_source_mode == "mixed":
        return MixedImageProvider(
            ai_provider,
            web_provider,
            settings.web_image_fallback_to_ai,
        )
    raise ImageProviderError(f"Unsupported IMAGE_SOURCE_MODE: {settings.image_source_mode}")
