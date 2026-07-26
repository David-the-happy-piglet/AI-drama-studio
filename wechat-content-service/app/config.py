import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./wechat-content.db",
    )
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    llm_provider: str = os.getenv("LLM_PROVIDER", "mock")
    llm_api_base: str = os.getenv("LLM_API_BASE", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))

    deepseek_api_base: str = os.getenv(
        "DEEPSEEK_API_BASE",
        "https://api.deepseek.com",
    )
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

    minimax_api_base: str = os.getenv(
        "MINIMAX_API_BASE",
        "https://api.minimaxi.com/v1",
    )
    minimax_api_key: str = os.getenv("MINIMAX_API_KEY", "")
    minimax_model: str = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
    minimax_image_model: str = os.getenv("MINIMAX_IMAGE_MODEL", "image-01")

    kimi_api_base: str = os.getenv(
        "KIMI_API_BASE",
        "https://api.moonshot.cn/v1",
    )
    moonshot_api_key: str = os.getenv("MOONSHOT_API_KEY", "")
    kimi_model: str = os.getenv("KIMI_MODEL", "kimi-k3")

    image_provider: str = os.getenv("IMAGE_PROVIDER", "placeholder")
    image_api_base: str = os.getenv("IMAGE_API_BASE", "")
    image_api_key: str = os.getenv("IMAGE_API_KEY", "")
    image_model: str = os.getenv("IMAGE_MODEL", "")
    image_size: str = os.getenv("IMAGE_SIZE", "1024x1024")
    image_timeout_seconds: float = float(os.getenv("IMAGE_TIMEOUT_SECONDS", "120"))
    aigc_watermark: bool = _as_bool(os.getenv("AIGC_WATERMARK"), True)
    generated_assets_dir: str = os.getenv(
        "GENERATED_ASSETS_DIR",
        "/app/generated-assets" if os.path.isdir("/app") else "./generated-assets",
    )

    wechat_enabled: bool = _as_bool(os.getenv("WECHAT_ENABLED"), False)
    wechat_app_id: str = os.getenv("WECHAT_APP_ID", "")
    wechat_app_secret: str = os.getenv("WECHAT_APP_SECRET", "")
    wechat_author: str = os.getenv("WECHAT_AUTHOR", "")
    wechat_api_base: str = os.getenv(
        "WECHAT_API_BASE",
        "https://api.weixin.qq.com",
    )
    allow_flagged_publish: bool = _as_bool(
        os.getenv("ALLOW_FLAGGED_PUBLISH"),
        False,
    )

    automation_poll_seconds: int = int(
        os.getenv("AUTOMATION_POLL_SECONDS", "30")
    )
    automation_max_retries: int = int(
        os.getenv("AUTOMATION_MAX_RETRIES", "3")
    )
    automation_stale_minutes: int = int(
        os.getenv("AUTOMATION_STALE_MINUTES", "120")
    )


settings = Settings()
