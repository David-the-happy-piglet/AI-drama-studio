"""MiniMax Token Plan video adapter; no PAYG fallback or automatic submission retry.

Protocol: https://platform.minimaxi.com/docs/api-reference/video-generation-i2v
Subscription entitlement must be checked in the account, not inferred from a key.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

from .base import (
    CostEstimate,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderRequestRejectedError,
    ProviderResponseError,
    VideoGenerationRequest,
    VideoGenerationStatus,
    VideoGenerationSubmission,
    VideoProvider,
)


class MiniMaxProvider(VideoProvider):
    name = "minimax"
    model = "MiniMax-Hailuo-2.3"

    def __init__(self, data_root: Path, timeout_seconds=120, transport=None):
        self.data_root = data_root
        self.timeout = timeout_seconds
        self.transport = transport
        # Explicit opt-in to the previously used subscription credential file.
        # Never load the whole legacy environment into this application.
        legacy = {}
        source = os.getenv("MINIMAX_TOKEN_PLAN_ENV_FILE", "")
        if source and not os.getenv("MINIMAX_TOKEN_PLAN_KEY"):
            path = Path(source)
            if path.is_file():
                legacy = dotenv_values(path, interpolate=False)
        self._key = (
            os.getenv("MINIMAX_TOKEN_PLAN_KEY") or legacy.get("MINIMAX_API_KEY") or ""
        ).strip()
        self.base_url = (
            os.getenv("MINIMAX_VIDEO_BASE_URL")
            or legacy.get("MINIMAX_API_BASE")
            or "https://api.minimaxi.com/v1"
        ).rstrip("/")

    def resolve_model(self, requested_model=None):
        return requested_model or self.model

    def capabilities(self):
        issues = (
            []
            if self._key
            else ["请配置 MINIMAX_TOKEN_PLAN_KEY，或显式引用旧项目订阅密钥文件"]
        )
        if self.base_url not in {
            "https://api.minimaxi.com/v1",
            "https://api.minimax.cn/v1",
            "https://api.minimax.io/v1",
        }:
            issues.append("MiniMax 必须使用官方 HTTPS API 地址")
        return ProviderCapabilities(
            provider=self.name,
            configured=not issues,
            configuration_issues=issues,
            generation_modes=["text_to_video", "image_to_video"],
            supports_first_frame=True,
            supported_durations=[6, 10],
            supported_resolutions=["768P", "1080P"],
            supported_aspect_ratios=["16:9"],
            metadata={
                "model": self.model,
                "transport": "http",
                "authentication": "Token Plan",
                "quota_verified": False,
                "payg_fallback": False,
                "native_audio": False,
                "usage": "空镜 / 转场 / 补充素材 / 试拍",
                "aspect_ratio_note": "文生默认画幅；图生由首帧决定，不发送 aspect_ratio 参数",
            },
        )

    def image_input(self, value):
        if value.startswith("https://"):
            return value
        path = Path(value)
        path = (path if path.is_absolute() else self.data_root / path).resolve()
        if not path.is_relative_to((self.data_root / "assets").resolve()):
            raise ProviderRequestRejectedError(
                "MiniMax 首帧必须使用已上传素材或 HTTPS URL"
            )
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(path.suffix.lower())
        if not path.is_file() or not mime or path.stat().st_size >= 20 * 1024 * 1024:
            raise ProviderRequestRejectedError(
                "首帧需要小于 20 MB 的 JPG/PNG/WebP 图片"
            )
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode(
            "ascii"
        )

    def build_request(self, request: VideoGenerationRequest):
        if self.resolve_model(request.model) != self.model:
            raise ProviderRequestRejectedError(
                "Token Plan 通道当前仅接入 MiniMax-Hailuo-2.3；未验证其他模型权益"
            )
        if request.generation_mode not in {"text_to_video", "image_to_video"}:
            raise ProviderRequestRejectedError(
                "Hailuo 2.3 当前仅支持文生视频或首帧图生视频"
            )
        if (
            request.last_frame
            or request.reference_images
            or request.reference_videos
            or request.reference_audios
        ):
            raise ProviderRequestRejectedError(
                "此通道不支持尾帧、多参考图或视频/声音参考"
            )
        if request.seed is not None:
            raise ProviderRequestRejectedError("此通道未提供 seed 参数")
        duration = request.duration if request.duration is not None else 6
        resolution = (request.resolution or "768P").upper()
        if (duration, resolution) not in {(6, "768P"), (10, "768P"), (6, "1080P")}:
            raise ProviderRequestRejectedError(
                "MiniMax 支持 768P 6/10 秒，或 1080P 6 秒"
            )
        if request.generation_mode == "text_to_video" and request.aspect_ratio not in {
            None,
            "16:9",
        }:
            raise ProviderRequestRejectedError(
                "文生视频不支持自定义画幅；竖屏请使用竖版首帧或后期裁剪"
            )
        params = request.api_parameters
        if params.get("token_plan_confirmed") is not True:
            raise ProviderConfigurationError(
                "请先确认账号剩余赠送额度及模型权益；本平台无法核验实时额度"
            )
        if set(params) - {"token_plan_confirmed", "material_purpose"}:
            raise ProviderRequestRejectedError("MiniMax 通道不接受未支持的额外参数")
        if params.get("material_purpose", "broll") not in {
            "broll",
            "transition",
            "supplement",
            "test",
        }:
            raise ProviderRequestRejectedError("请选择有效的素材用途")
        prompt = request.prompt + (
            "\n避免：" + request.negative_prompt if request.negative_prompt else ""
        )
        if len(prompt) > 2000:
            raise ProviderRequestRejectedError(
                "正向提示词与负向约束合计不能超过 2000 字符"
            )
        body = {
            "model": self.model,
            "prompt": prompt,
            "duration": int(duration),
            "resolution": resolution,
            "prompt_optimizer": False,
        }
        if request.generation_mode == "image_to_video":
            if not request.first_frame:
                raise ProviderRequestRejectedError("图生视频需要首帧图片")
            body["first_frame_image"] = self.image_input(request.first_frame)
        elif request.first_frame:
            raise ProviderRequestRejectedError("文生模式不能附带首帧，请切换图生模式")
        return body

    async def call(self, method, path, **kwargs):
        caps = self.capabilities()
        if not caps.configured:
            raise ProviderConfigurationError("; ".join(caps.configuration_issues))
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.request(
                    method,
                    self.base_url + path,
                    headers={"Authorization": f"Bearer {self._key}"},
                    **kwargs,
                )
            if response.is_error:
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    raise ProviderRequestRejectedError(
                        f"MiniMax 拒绝请求（HTTP {response.status_code}）；请核对额度、订阅密钥与参数。"
                    )
                raise ProviderResponseError(
                    f"MiniMax HTTP {response.status_code}；未切换付费密钥。提交异常请先到平台核对，勿直接重试。"
                )
            raw = response.json()
            if not isinstance(raw, dict) or not isinstance(raw.get("base_resp"), dict):
                raise ProviderResponseError(
                    "MiniMax 返回无效响应；提交结果不确定，请先在平台核对任务"
                )
            code = raw["base_resp"].get("status_code")
            if code != 0:
                # Do not reflect arbitrary provider strings or credentials to the browser.
                raise ProviderRequestRejectedError(
                    f"MiniMax 请求被拒绝（代码 {code}）；请核对额度、订阅密钥与参数。未自动重试或切换付费通道。"
                )
            return raw
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderResponseError(
                "MiniMax 请求结果不确定；请先在平台核对任务，避免重复消耗额度。不会自动重试。"
            ) from exc

    async def generate(self, request):
        body = self.build_request(request)
        raw = await self.call("POST", "/video_generation", json=body)
        if not raw.get("task_id"):
            raise ProviderResponseError(
                "MiniMax 未返回任务 ID；请先在平台核对，不要直接重复提交"
            )
        return VideoGenerationSubmission(
            external_id=str(raw["task_id"]),
            status="submitted",
            model=self.model,
            raw_response=raw,
        )

    async def get_status(self, external_id):
        raw = await self.call(
            "GET", "/query/video_generation", params={"task_id": external_id}
        )
        statuses = {
            "Preparing": "submitted",
            "Queueing": "queued",
            "Processing": "processing",
            "Success": "succeeded",
            "Fail": "failed",
        }
        status = statuses.get(raw.get("status"))
        if not status:
            raise ProviderResponseError("MiniMax 返回未知任务状态")
        output = None
        if status == "succeeded":
            if not raw.get("file_id"):
                raise ProviderResponseError(
                    "MiniMax 已完成但尚未返回文件 ID，请稍后刷新"
                )
            file = await self.call(
                "GET", "/files/retrieve", params={"file_id": raw["file_id"]}
            )
            output = (file.get("file") or {}).get("download_url")
            if not output or urlparse(output).scheme != "https":
                raise ProviderResponseError(
                    "MiniMax 未返回有效 HTTPS 视频地址，请稍后刷新"
                )
        return VideoGenerationStatus(
            external_id=external_id,
            status=status,
            model=self.model,
            output_url=output,
            error_message="MiniMax 生成失败；请在平台核对原因和额度"
            if status == "failed"
            else None,
            raw_response=raw,
        )

    async def cancel(self, external_id):
        raise ProviderConfigurationError(
            "此通道未提供取消接口；不采用素材不等于撤销生成或退还额度"
        )

    async def download_result(self, external_id, destination):
        status = await self.get_status(external_id)
        if status.status != "succeeded" or not status.output_url:
            raise ProviderResponseError("视频尚未完成")
        try:
            # Signed CDN links must never receive the subscription credential.
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.get(status.output_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderResponseError("视频下载失败，请刷新任务获取新地址") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def estimate_cost(self, request):
        return CostEstimate(
            amount=None,
            basis="Token Plan 权益与剩余额度未核验，以账号后台为准；不自动切换付费密钥",
        )
