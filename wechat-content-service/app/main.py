from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .admin import router as admin_router
from .api import router as api_router
from .config import settings
from .database import Base, SessionLocal, engine, ensure_runtime_schema
from .repositories import seed_audiences


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    with SessionLocal() as session:
        seed_audiences(session)
    yield


app = FastAPI(
    title="WeChat Content Generator",
    version="0.1.0",
    lifespan=lifespan,
)

assets_dir = Path(settings.generated_assets_dir)
assets_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/generated-assets",
    StaticFiles(directory=str(assets_dir)),
    name="generated-assets",
)

app.include_router(api_router)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "image_provider": settings.image_provider,
        "wechat_enabled": settings.wechat_enabled,
    }
