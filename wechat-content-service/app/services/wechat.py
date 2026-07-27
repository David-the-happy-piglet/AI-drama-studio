import mimetypes
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..models import Article, Asset
from .renderer import render_wechat_html


class WechatError(RuntimeError):
    pass


class WechatClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_base = settings.wechat_api_base.rstrip("/")
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.wechat_enabled

    def _check_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WechatError(
                f"WeChat HTTP error {response.status_code}: {response.text[:500]}"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise WechatError(
                f"WeChat API returned non-JSON response: {response.text[:500]}"
            ) from exc
        if data.get("errcode", 0) != 0:
            request_id = data.get("rid")
            suffix = f", rid={request_id}" if request_id else ""
            raise WechatError(
                f"WeChat API error {data.get('errcode')}: {data.get('errmsg')}{suffix}"
            )
        return data

    def access_token(self) -> str:
        if self._token and self._token_expires_at and datetime.now(UTC) < self._token_expires_at:
            return self._token
        if not self.settings.wechat_app_id or not self.settings.wechat_app_secret:
            raise WechatError("WECHAT_APP_ID and WECHAT_APP_SECRET are required")
        response = httpx.get(
            f"{self.api_base}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": self.settings.wechat_app_id,
                "secret": self.settings.wechat_app_secret,
            },
            timeout=30,
        )
        data = self._check_response(response)
        self._token = data["access_token"]
        self._token_expires_at = datetime.now(UTC) + timedelta(
            seconds=max(int(data.get("expires_in", 7200)) - 300, 60)
        )
        return self._token

    def _material_file(self, asset: Asset) -> tuple[Path, bool]:
        if asset.local_path and Path(asset.local_path).exists():
            return Path(asset.local_path), False
        if not asset.source_url:
            raise WechatError(f"Asset {asset.id} has no usable file")
        suffix = Path(asset.source_url.split("?")[0]).suffix or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            path = Path(temporary.name)
        response = httpx.get(asset.source_url, timeout=60)
        response.raise_for_status()
        path.write_bytes(response.content)
        return path, True

    def upload_inline_image(self, asset: Asset) -> str:
        path, remove_after = self._material_file(asset)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    f"{self.api_base}/cgi-bin/media/uploadimg",
                    params={"access_token": self.access_token()},
                    files={"media": (path.name, handle, content_type)},
                    timeout=60,
                )
            return self._check_response(response)["url"]
        finally:
            if remove_after:
                path.unlink(missing_ok=True)

    def upload_cover(self, asset: Asset) -> str:
        path, remove_after = self._material_file(asset)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    f"{self.api_base}/cgi-bin/material/add_material",
                    params={
                        "access_token": self.access_token(),
                        "type": "image",
                    },
                    files={"media": (path.name, handle, content_type)},
                    timeout=60,
                )
            return self._check_response(response)["media_id"]
        finally:
            if remove_after:
                path.unlink(missing_ok=True)

    def create_draft(self, article: Article) -> str:
        assets = sorted(article.assets, key=lambda item: item.position)
        if not assets:
            raise WechatError("Article needs at least one image")

        cover = assets[0]
        cover.wechat_media_id = cover.wechat_media_id or self.upload_cover(cover)
        urls = [cover.source_url or ""]
        for asset in assets[1:]:
            asset.wechat_url = asset.wechat_url or self.upload_inline_image(asset)
            urls.append(asset.wechat_url)

        content = render_wechat_html(article_content(article), urls)
        response = httpx.post(
            f"{self.api_base}/cgi-bin/draft/add",
            params={"access_token": self.access_token()},
            json={
                "articles": [
                    {
                        "title": article.selected_title,
                        "author": self.settings.wechat_author,
                        "digest": article.summary,
                        "content": content,
                        "content_source_url": "",
                        "thumb_media_id": cover.wechat_media_id,
                        "need_open_comment": 0,
                        "only_fans_can_comment": 0,
                    }
                ]
            },
            timeout=60,
        )
        article.content_html = content
        return self._check_response(response)["media_id"]

    def get_draft(self, media_id: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.api_base}/cgi-bin/draft/get",
            params={"access_token": self.access_token()},
            json={"media_id": media_id},
            timeout=60,
        )
        return self._check_response(response)

    def verify_draft(self, media_id: str, expected_title: str) -> None:
        draft = self.get_draft(media_id)
        items = draft.get("news_item")
        if not isinstance(items, list) or not items:
            raise WechatError(f"WeChat draft verification returned no articles for {media_id}")
        title = items[0].get("title")
        if title != expected_title:
            raise WechatError(
                "WeChat draft verification title mismatch: "
                f"expected {expected_title!r}, got {title!r}"
            )

    def publish(self, media_id: str) -> str:
        response = httpx.post(
            f"{self.api_base}/cgi-bin/freepublish/submit",
            params={"access_token": self.access_token()},
            json={"media_id": media_id},
            timeout=60,
        )
        return self._check_response(response)["publish_id"]


def article_content(article: Article) -> dict[str, Any]:
    import json

    return json.loads(article.content_json)
