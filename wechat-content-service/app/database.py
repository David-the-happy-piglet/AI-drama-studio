from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_runtime_schema() -> None:
    inspector = inspect(engine)
    if "generation_runs" not in inspector.get_table_names():
        return
    existing = {
        column["name"] for column in inspector.get_columns("generation_runs")
    }
    required = {
        "prompt_tokens": "INTEGER",
        "completion_tokens": "INTEGER",
        "total_tokens": "INTEGER",
    }
    missing = {
        name: sql_type for name, sql_type in required.items() if name not in existing
    }
    if not missing:
        return
    with engine.begin() as connection:
        for name, sql_type in missing.items():
            connection.execute(
                text(
                    f"ALTER TABLE generation_runs ADD COLUMN {name} {sql_type}"
                )
            )


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
