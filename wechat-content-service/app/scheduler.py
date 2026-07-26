import logging
import time

from .config import settings
from .database import Base, SessionLocal, engine
from .repositories import seed_audiences
from .services.automation import (
    enqueue_due_plans,
    process_next_run,
    recover_stale_runs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("wechat-content-scheduler")


def scheduler_tick() -> None:
    with SessionLocal() as session:
        created = enqueue_due_plans(session)
        if created:
            logger.info("enqueued automation runs: %s", created)
        while True:
            run = process_next_run(session, settings)
            if not run:
                break
            logger.info(
                "automation run %s finished with status=%s article_id=%s",
                run.id,
                run.status,
                run.article_id,
            )


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        seed_audiences(session)
        recovered = recover_stale_runs(session, settings)
        if recovered:
            logger.warning("recovered %s stale automation runs", recovered)
    logger.info(
        "scheduler started; poll interval=%ss, max retries=%s",
        settings.automation_poll_seconds,
        settings.automation_max_retries,
    )
    while True:
        try:
            scheduler_tick()
        except Exception:
            logger.exception("scheduler tick failed")
        time.sleep(max(settings.automation_poll_seconds, 5))


if __name__ == "__main__":
    main()
