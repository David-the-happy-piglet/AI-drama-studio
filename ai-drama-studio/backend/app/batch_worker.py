from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from .config import PROJECT_ROOT, Settings
from .database import Database
from .providers.video.registry import VideoProviderRegistry
from .services.production_batches import worker


async def run(batch_id=None) -> None:
    # Match the API configuration order: local dotenv first, then Settings.
    load_dotenv(PROJECT_ROOT / ".env.local", override=False)
    settings = Settings()
    settings.ensure_directories()
    database = Database(settings.database_url)
    if settings.auto_create_schema:
        database.create_all()
    registry = VideoProviderRegistry.from_settings(settings)
    try:
        await worker(database, registry, data_root=settings.data_root, batch_id=batch_id)
    finally:
        database.engine.dispose()


def main() -> None:
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--batch-id")
        args = parser.parse_args()
        asyncio.run(run(args.batch_id))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
