from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml

from .base import (
    CostEstimate,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderResponseError,
    VideoGenerationRequest,
    VideoGenerationStatus,
    VideoGenerationSubmission,
    VideoProvider,
)


def _read_path(data: Any, dotted_path: str | None) -> Any:
    if not dotted_path:
        return None
    value = data
    for part in dotted_path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _write_path(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    cursor = target
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


class ConfiguredHTTPVideoProvider(VideoProvider):
    """HTTP adapter whose volatile API details live entirely in YAML.

    The checked-in configs are intentionally disabled. An adapter only sends a
    request after an official endpoint, field mapping, response mapping, and
    authentication mode have been explicitly configured.
    """

    name = "configured"

    def __init__(self, config_path: Path, timeout_seconds: float = 120) -> None:
        self.config_path = config_path
        self.timeout_seconds = timeout_seconds
        self.config = self._load_config(config_path)

    @staticmethod
    def _load_config(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"enabled": False, "configuration_error": f"Missing config: {path}"}
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _env_or_value(self, section: dict[str, Any], key: str) -> str:
        env_name = section.get(f"{key}_env")
        if env_name:
            return os.getenv(env_name, "").strip()
        return str(section.get(key) or "").strip()

    def _configuration_issues(self) -> list[str]:
        issues: list[str] = []
        connection = self.config.get("connection", {})
        request_config = self.config.get("request", {})
        response_config = self.config.get("response", {})
        auth_config = self.config.get("auth", {})

        if not self.config.get("enabled", False):
            issues.append(
                "provider config is disabled pending official API verification"
            )
        if not self._env_or_value(connection, "base_url"):
            issues.append("base URL is missing")
        if not connection.get("create_path"):
            issues.append("create endpoint path is missing")
        if not request_config.get("field_map", {}).get("prompt"):
            issues.append("official prompt request-field mapping is missing")
        if not response_config.get("task_id_path"):
            issues.append("official task-id response path is missing")
        if auth_config.get("mode") not in {"bearer", "headers", "none"}:
            issues.append("authentication mode is not configured")
        return issues

    def _assert_configured(self) -> None:
        issues = self._configuration_issues()
        if issues:
            raise ProviderConfigurationError(f"{self.name}: " + "; ".join(issues))

    def resolve_model(self, requested_model: str | None = None) -> str | None:
        return (
            requested_model
            or self._env_or_value(self.config.get("model", {}), "name")
            or None
        )

    def _headers(self) -> dict[str, str]:
        auth = self.config.get("auth", {})
        headers = {str(k): str(v) for k, v in auth.get("static_headers", {}).items()}
        mode = auth.get("mode")
        if mode == "bearer":
            token = os.getenv(auth.get("token_env", ""), "").strip()
            if not token:
                raise ProviderConfigurationError(
                    f"{self.name}: bearer token is missing"
                )
            headers[auth.get("header_name", "Authorization")] = (
                f"{auth.get('prefix', 'Bearer')} {token}".strip()
            )
        elif mode == "headers":
            for header_name, env_name in auth.get("header_env_map", {}).items():
                value = os.getenv(str(env_name), "").strip()
                if not value:
                    raise ProviderConfigurationError(
                        f"{self.name}: required credential {env_name} is missing"
                    )
                headers[str(header_name)] = value
        for header_name, env_name in auth.get("extra_header_env_map", {}).items():
            value = os.getenv(str(env_name), "").strip()
            if value:
                headers[str(header_name)] = value
        return headers

    def _query_params(self) -> dict[str, str]:
        connection = self.config.get("connection", {})
        params = {
            str(key): str(value)
            for key, value in connection.get("static_query", {}).items()
        }
        for parameter_name, env_name in connection.get("query_env_map", {}).items():
            value = os.getenv(str(env_name), "").strip()
            if value:
                params[str(parameter_name)] = value
        return params

    def _url(self, path: str) -> str:
        connection = self.config.get("connection", {})
        base_url = self._env_or_value(connection, "base_url")
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))

    def _build_payload(self, request: VideoGenerationRequest) -> dict[str, Any]:
        config = self.config.get("request", {})
        field_map: dict[str, str] = config.get("field_map", {})
        internal = request.model_dump(exclude_none=True)
        payload: dict[str, Any] = {}
        for internal_name, provider_name in field_map.items():
            if internal_name == "model":
                value = self.resolve_model(request.model)
            else:
                value = internal.get(internal_name)
            if value not in (None, "", [], {}):
                _write_path(payload, provider_name, value)
        if config.get("allow_api_parameters", False):
            payload.update(request.api_parameters)
        return payload

    def _normalize_status(self, provider_status: Any) -> str:
        raw = str(provider_status or "submitted")
        mapping = self.config.get("status_mapping", {})
        return str(mapping.get(raw, raw.lower()))

    async def generate(
        self, request: VideoGenerationRequest
    ) -> VideoGenerationSubmission:
        self._assert_configured()
        connection = self.config["connection"]
        payload = self._build_payload(request)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self._url(connection["create_path"]),
                    headers=self._headers(),
                    params=self._query_params(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderResponseError(
                f"{self.name}: generation request failed: {exc}"
            ) from exc
        response_config = self.config.get("response", {})
        task_id = _read_path(data, response_config.get("task_id_path"))
        if not task_id:
            raise ProviderResponseError(
                f"{self.name}: response does not contain a task id"
            )
        status = self._normalize_status(
            _read_path(data, response_config.get("status_path"))
        )
        return VideoGenerationSubmission(
            external_id=str(task_id),
            status=status,
            output_url=_read_path(data, response_config.get("output_url_path")),
            raw_response=data,
        )

    async def get_status(self, external_id: str) -> VideoGenerationStatus:
        self._assert_configured()
        connection = self.config["connection"]
        status_path = connection.get("status_path")
        if not status_path:
            raise ProviderConfigurationError(
                f"{self.name}: status endpoint path is missing"
            )
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self._url(status_path.format(task_id=external_id)),
                    headers=self._headers(),
                    params=self._query_params(),
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderResponseError(
                f"{self.name}: status request failed: {exc}"
            ) from exc
        config = self.config.get("response", {})
        return VideoGenerationStatus(
            external_id=external_id,
            status=self._normalize_status(_read_path(data, config.get("status_path"))),
            progress=_read_path(data, config.get("progress_path")),
            output_url=_read_path(data, config.get("output_url_path")),
            error_message=_read_path(data, config.get("error_path")),
            raw_response=data,
        )

    async def cancel(self, external_id: str) -> VideoGenerationStatus:
        self._assert_configured()
        cancel_path = self.config.get("connection", {}).get("cancel_path")
        if not cancel_path:
            raise ProviderConfigurationError(
                f"{self.name}: cancel endpoint path is missing"
            )
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self._url(cancel_path.format(task_id=external_id)),
                    headers=self._headers(),
                    params=self._query_params(),
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderResponseError(
                f"{self.name}: cancel request failed: {exc}"
            ) from exc
        return VideoGenerationStatus(
            external_id=external_id, status="cancelled", raw_response=data
        )

    async def download_result(self, external_id: str, destination: Path) -> Path:
        status = await self.get_status(external_id)
        if status.status not in {"succeeded", "completed"} or not status.output_url:
            raise ProviderResponseError(
                f"{self.name}: generation is not ready for download"
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(status.output_url, headers=self._headers())
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderResponseError(f"{self.name}: download failed: {exc}") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def estimate_cost(self, request: VideoGenerationRequest) -> CostEstimate:
        pricing = self.config.get("pricing", {})
        per_second = pricing.get("usd_per_second")
        if per_second is None or request.duration is None:
            return CostEstimate(basis="provider pricing is not configured")
        try:
            amount = round(float(per_second) * request.duration, 6)
        except (TypeError, ValueError):
            return CostEstimate(basis="provider pricing config is invalid")
        return CostEstimate(
            amount=amount, basis="configured usd_per_second × requested duration"
        )

    def capabilities(self) -> ProviderCapabilities:
        config = self.config.get("capabilities", {})
        return ProviderCapabilities(
            provider=self.name,
            configured=not self._configuration_issues(),
            configuration_issues=self._configuration_issues(),
            generation_modes=config.get("generation_modes", []),
            supports_first_frame=config.get("supports_first_frame", False),
            supports_last_frame=config.get("supports_last_frame", False),
            supports_reference_images=config.get("supports_reference_images", False),
            supports_reference_videos=config.get("supports_reference_videos", False),
            supports_reference_audio=config.get("supports_reference_audio", False),
            supported_durations=config.get("supported_durations", []),
            supported_aspect_ratios=config.get("supported_aspect_ratios", []),
            supported_resolutions=config.get("supported_resolutions", []),
            metadata={
                "config_path": str(self.config_path),
                "model": self.resolve_model(),
                "api_version": self._env_or_value(
                    self.config.get("model", {}), "api_version"
                )
                or None,
            },
        )
