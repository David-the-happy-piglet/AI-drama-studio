from __future__ import annotations

from ...config import Settings
from .base import ProviderConfigurationError, VideoProvider
from .kling import KlingProvider
from .minimax import MiniMaxProvider
from .mock import MockVideoProvider
from .seedance import SeedanceProvider


class VideoProviderRegistry:
    def __init__(self, providers: list[VideoProvider]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    @classmethod
    def from_settings(cls, settings: Settings) -> VideoProviderRegistry:
        return cls(
            [
                SeedanceProvider(
                    settings.provider_config_root,
                    settings.data_root,
                    settings.provider_timeout_seconds,
                ),
                KlingProvider(
                    settings.provider_config_root, settings.provider_timeout_seconds,
                    data_root=settings.data_root,
                ),
                MiniMaxProvider(settings.data_root, settings.provider_timeout_seconds),
                MockVideoProvider(),
            ]
        )

    def get(self, name: str) -> VideoProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderConfigurationError(f"unknown video provider: {name}") from exc

    def all(self) -> list[VideoProvider]:
        return list(self._providers.values())
