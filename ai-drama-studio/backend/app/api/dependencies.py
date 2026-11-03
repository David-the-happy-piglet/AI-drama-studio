from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from ..providers.video.registry import VideoProviderRegistry


def get_db(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.database.session()


def get_provider_registry(request: Request) -> VideoProviderRegistry:
    return request.app.state.video_providers
