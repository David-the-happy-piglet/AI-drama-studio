from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.character_images import router as character_images_router
from .api.director import router as director_router
from .api.planning import router as planning_router
from .api.production_batches import router as production_batches_router
from .api.routes import router
from .api.scene_assets import router as scene_assets_router
from .api.shot_images import router as shot_images_router
from .config import PROJECT_ROOT, Settings
from .database import Database
from .providers.video.registry import VideoProviderRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None and os.getenv("APP_ENV") != "test":
        # Load local credentials before Settings reads the process environment.
        # Explicit process environment still wins because override is disabled.
        load_dotenv(PROJECT_ROOT / ".env.local", override=False)
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.ensure_directories()
        database = Database(app_settings.database_url)
        if app_settings.auto_create_schema:
            database.create_all()
        app.state.settings = app_settings
        app.state.database = database
        app.state.video_providers = VideoProviderRegistry.from_settings(app_settings)
        try:
            yield
        finally:
            database.engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        description="Provider-neutral, shot-oriented AI short-drama generation backend.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount(
        "/media",
        StaticFiles(directory=app_settings.data_root / "assets", check_dir=False),
        name="media",
    )
    app.include_router(router, prefix=app_settings.api_prefix)
    app.include_router(planning_router, prefix=app_settings.api_prefix)
    app.include_router(character_images_router, prefix=app_settings.api_prefix)
    app.include_router(shot_images_router, prefix=app_settings.api_prefix)
    app.include_router(production_batches_router, prefix=app_settings.api_prefix)
    app.include_router(scene_assets_router, prefix=app_settings.api_prefix)
    app.include_router(director_router, prefix=app_settings.api_prefix)
    return app


app = create_app()
