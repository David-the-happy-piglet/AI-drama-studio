from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(slots=True)
class Settings:
    app_name: str = field(
        default_factory=lambda: os.getenv("APP_NAME", "AI Drama Studio")
    )
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    api_prefix: str = field(default_factory=lambda: os.getenv("API_PREFIX", "/api/v1"))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            f"sqlite:///{PROJECT_ROOT / 'data' / 'ai_drama_studio.db'}",
        )
    )
    auto_create_schema: bool = field(
        default_factory=lambda: _as_bool(os.getenv("AUTO_CREATE_SCHEMA"), True)
    )
    data_root: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_ROOT", str(PROJECT_ROOT / "data")))
    )
    provider_config_root: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "PROVIDER_CONFIG_ROOT", str(PROJECT_ROOT / "config" / "providers")
            )
        )
    )
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: _split_csv(
            os.getenv("CORS_ORIGINS", "http://localhost:3000")
        )
    )
    provider_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "120"))
    )
    max_generation_retries: int = field(
        default_factory=lambda: int(os.getenv("MAX_GENERATION_RETRIES", "3"))
    )
    max_upload_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("MAX_UPLOAD_BYTES", str(250 * 1024 * 1024))
        )
    )

    def ensure_directories(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        for name in ("characters", "locations", "projects", "generations", "assets"):
            (self.data_root / name).mkdir(parents=True, exist_ok=True)
